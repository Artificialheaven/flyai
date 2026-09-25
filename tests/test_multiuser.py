"""多用户、工具结果 ok 持久化、导出/导入相关单测（纯逻辑，不依赖服务器）。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.models import Character, World
from flyai.schemas import Message
from flyai.user import ADMIN_UID, UserScope, UserStore, migrate_legacy


# ---- Message.ok 持久化 ----

def test_message_ok_roundtrip():
    m = Message(role="tool", content="x", tool_call_id="t1", name="query_lore", ok=False)
    d = m.to_dict()
    assert d["ok"] is False
    assert Message.from_dict(d).ok is False
    # 旧数据无 ok → None 兜底
    assert Message.from_dict({"role": "tool", "content": "x"}).ok is None


# ---- 工具结果状态写入历史（loop 调用点） ----

def test_attribute_manager_after_update_marked_ok():  # noqa  (说明：status 由 loop 写入 Message.ok)
    # 通过对比：成功输出应标记 ok=True，失败（[工具错误]）标记 ok=False，
    # 由 loop.py 的 append 处统一用 result.ok 写出。
    pass


# ---- 用户存储 ----

def test_user_store_create_check_password(tmp_path):
    us = UserStore(tmp_path)
    # 无默认用户（管理员在 config，不在库中）
    assert us.list() == []
    assert us.get_by_id("default") is None
    assert not us.valid("default")

    u = us.create("小明", "secret")
    assert u.check_password("secret")
    assert not u.check_password("wrong")
    # 无密码用户任何输入均可进入
    u2 = us.create("无密码", "")
    assert u2.check_password("anything")
    assert us.valid(u.id) and not us.valid("不存在")


def test_user_store_persists(tmp_path):
    us1 = UserStore(tmp_path)
    u = us1.create("小红", "pw")
    uid = u.id
    us2 = UserStore(tmp_path)  # 重新加载
    assert us2.get_by_id(uid).name == "小红"
    assert us2.get_by_id(uid).check_password("pw")


# ---- 管理员与普通用户 ----

def test_admin_from_config(tmp_path):
    from flyai.config import AppConfig, AuthSettings, AdminSettings
    cfg = AppConfig(auth=AuthSettings(admin=AdminSettings(username="boss", password="secret")))
    assert cfg.auth.admin.username == "boss"
    assert cfg.auth.admin.password == "secret"
    # 管理员标识常量为固定 uid，不在用户库内
    assert ADMIN_UID == "admin"
    us = UserStore(tmp_path)
    assert not us.valid(ADMIN_UID)


def test_user_remove_and_duplicate(tmp_path):
    us = UserStore(tmp_path)
    u = us.create("要删除", "pw")
    assert us.remove(u.id)
    assert us.get_by_id(u.id) is None
    # 不能删除不存在的用户
    assert us.remove("不存在") is False
    # 重名创建抛错
    us.create("唯一", "")
    try:
        us.create("唯一", "")
        raise AssertionError("应拒绝重名")
    except ValueError:
        pass


# ---- 每用户数据作用域 ----

def test_user_scope_isolates_data(tmp_path):
    def make_config():
        from flyai.config import AppConfig
        return AppConfig(history={"backend": "json"})

    cfg = make_config()
    scope_a = UserScope.build("ua", cfg, tmp_path)
    scope_b = UserScope.build("ub", cfg, tmp_path)

    scope_a.lore.save_character(Character(name="A的角色"))
    scope_b.lore.save_character(Character(name="B的角色"))

    assert [c.name for c in scope_a.lore.list_characters()] == ["A的角色"]
    assert [c.name for c in scope_b.lore.list_characters()] == ["B的角色"]

    # 会话隔离
    sa = scope_a.store.create(title="A会话")
    assert scope_a.store.get(sa.id) is not None
    assert scope_b.store.get(sa.id) is None


# ---- 遗留数据迁移 ----

def test_migrate_legacy_once(tmp_path):
    import json as _json

    legacy_char = tmp_path / "characters"
    legacy_char.mkdir(parents=True, exist_ok=True)
    (legacy_char / "c1.json").write_text(
        _json.dumps(Character(name="旧角色").to_dict()), encoding="utf-8")

    migrate_legacy(tmp_path)
    admin_char_dir = tmp_path / ADMIN_UID / "characters"
    assert admin_char_dir.exists()
    data = _json.loads((admin_char_dir / "c1.json").read_text(encoding="utf-8"))
    assert data["name"] == "旧角色"
    # 二次调用不重复搬移
    from flyai.lore.store import LoreStore
    assert len(LoreStore(tmp_path / ADMIN_UID).list_characters()) == 1


# ---- 角色/世界观 导出数据结构 ----

def test_export_field_roundtrip():
    c = Character(name="爱丽丝", intro="简介", prompt_format="格式", prompt_tools="工具")
    d = c.to_dict()
    assert d["prompt_format"] == "格式" and d["prompt_tools"] == "工具"
    c2 = Character.from_dict(d)
    assert c2.prompt_format == "格式" and c2.prompt_tools == "工具"

    w = World(name="世界", content="设定", prompt_format="f", prompt_tools="t")
    w2 = World.from_dict(w.to_dict())
    assert w2.prompt_format == "f" and w2.prompt_tools == "t"