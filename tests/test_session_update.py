"""会话 PATCH：重命名 / 置顶 / 单窗口 max_tokens 与禁用工具。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.api.routes_session import SessionUpdate, update_session
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore, session_from_dict, session_to_dict


def _req(store, tool_names=None):
    sc = SimpleNamespace(store=store)
    registry = SimpleNamespace(
        all=lambda: [SimpleNamespace(name=n) for n in (tool_names or [])])
    state = SimpleNamespace(user=lambda r: sc, registry=registry)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_rename_persists_and_trims(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="旧标题")
    r = update_session(s.id, SessionUpdate(title="  新名字  "), _req(store))
    assert r["title"] == "新名字"
    assert store.get(s.id).title == "新名字"


def test_rename_blank_keeps_old_title(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="保留我")
    update_session(s.id, SessionUpdate(title="   "), _req(store))
    assert store.get(s.id).title == "保留我"


def test_toggle_pin_persists(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="会话")
    r = update_session(s.id, SessionUpdate(pinned=True), _req(store))
    assert r["pinned"] is True
    assert store.get(s.id).pinned is True
    update_session(s.id, SessionUpdate(pinned=False), _req(store))
    assert store.get(s.id).pinned is False


def test_update_missing_session_404(tmp_path):
    store = JsonSessionStore(tmp_path)
    with pytest.raises(Exception) as exc:
        update_session("ghost", SessionUpdate(title="x"), _req(store))
    assert getattr(exc.value, "status_code", None) == 404


def test_stale_drive_copy_keeps_concurrent_rename(tmp_path):
    """长跑 drive 发消息时加载旧副本；流式期间用户改名/置顶已落盘。
    drive 收尾用 save_merged 时不得把旧标题写回去（线上真实竞态回归）。"""
    store = JsonSessionStore(tmp_path)
    s = store.create(title="旧标题")
    s.messages.append(Message(role="user", content="你好"))
    store.save(s)
    drive = store.get(s.id)  # drive 持有的副本（旧形态会话在此被迁移为房间形态）
    update_session(drive.id, SessionUpdate(title="新名字", pinned=True), _req(store))
    drive.threads[0].messages.append(Message(role="assistant", content="回复"))
    store.save_merged(drive)
    disk = store.get(drive.id)
    assert disk.title == "新名字"
    assert disk.pinned is True
    # 本轮消息照常落盘
    assert disk.threads[0].messages[-1].content == "回复"


# ---- 单窗口 max_tokens ----

def test_max_tokens_persists(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="会话")
    r = update_session(s.id, SessionUpdate(max_tokens=256),
                       _req(store, ["query_lore"]))
    assert r["max_tokens"] == 256
    # 落盘往返
    reloaded = store.get(s.id)
    assert reloaded.max_tokens == 256
    assert session_from_dict(session_to_dict(reloaded)).max_tokens == 256


def test_max_tokens_clamped(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="会话")
    assert update_session(s.id, SessionUpdate(max_tokens=-5),
                          _req(store))["max_tokens"] == 0
    assert update_session(s.id, SessionUpdate(max_tokens=10 ** 9),
                          _req(store))["max_tokens"] == 100000
    # 0 = 跟随上级配置，不影响其他字段
    r = update_session(s.id, SessionUpdate(max_tokens=0), _req(store))
    assert r["max_tokens"] == 0 and r["disabled_tools"] == []


def test_legacy_session_dict_defaults_zero_and_empty(tmp_path):
    # 旧会话文件没有这两个键：反序列化默认 0 / []
    s = session_from_dict({"id": "x", "title": "旧会话"})
    assert s.max_tokens == 0 and s.disabled_tools == []


# ---- 单窗口禁用工具 ----

def test_disabled_tools_persists_known_names(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="会话")
    r = update_session(
        s.id,
        SessionUpdate(disabled_tools=["web_search", "query_lore", "web_search"]),
        _req(store, ["web_search", "query_lore", "read_image"]),
    )
    # 去重保序；全量替换
    assert r["disabled_tools"] == ["web_search", "query_lore"]
    assert store.get(s.id).disabled_tools == ["web_search", "query_lore"]
    # 第二次 PATCH 全量替换为更少的集合
    r = update_session(s.id, SessionUpdate(disabled_tools=["read_image"]),
                       _req(store, ["web_search", "query_lore", "read_image"]))
    assert r["disabled_tools"] == ["read_image"]


def test_disabled_tools_ignores_unknown_names(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="会话")
    r = update_session(
        s.id,
        SessionUpdate(disabled_tools=["evil_tool", "  ", "web_search"]),
        _req(store, ["web_search"]),
    )
    # 未注册/空白名静默忽略，不 400
    assert r["disabled_tools"] == ["web_search"]
