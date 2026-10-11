"""公共市场：发布 / 门槛可见性 / 安装复制 / 发布者管理。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.lore.models import Character, World
from flyai.lore.store import LoreStore
from flyai.market.store import MarketStore
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Message, Session, bootstrap_room
from flyai.user import ADMIN_LEVEL, ADMIN_UID, UserStore
from flyai.api.routes_market import (
    EditBody,
    PublishBody,
    delete_entry,
    edit_entry,
    get_entry,
    install_entry,
    list_entries,
    publish,
    refresh_entry,
)


def _scope(data_dir, uid, level):
    user_dir = data_dir / uid
    lore = LoreStore(user_dir, shared_base=data_dir / "shared")
    store = JsonSessionStore(user_dir / "sessions")
    upload_dir = data_dir / "uploads" / uid
    upload_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(uid=uid, level=level, lore=lore, store=store,
                           upload_dir=upload_dir)


@pytest.fixture
def env(tmp_path):
    data_dir = tmp_path
    user_store = UserStore(data_dir)
    alice_user = user_store.create("alice", "pw", level=10)
    bob_user = user_store.create("bob", "pw", level=5)

    sc = {
        "alice": _scope(data_dir, "alice", 10),
        "bob": _scope(data_dir, "bob", 5),
        ADMIN_UID: _scope(data_dir, ADMIN_UID, ADMIN_LEVEL),
    }
    current = {"uid": "alice"}

    state = SimpleNamespace()
    state.market = MarketStore(data_dir / "market")
    state.user_store = user_store
    state.config = SimpleNamespace(
        auth=SimpleNamespace(admin=SimpleNamespace(username="管理员"))
    )
    state.user = lambda request: sc[current["uid"]]

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))

    def use(uid):
        current["uid"] = uid

    return SimpleNamespace(state=state, request=request, sc=sc, use=use,
                           data_dir=data_dir)


def _publish_char(env, uid, min_level=0, avatar=""):
    env.use(uid)
    c = Character(name="猫娘", intro="一只猫娘", avatar=avatar, author=uid)
    env.sc[uid].lore.save_character(c)
    body = PublishBody(kind="character", source_id=c.id, title="共享猫娘",
                       intro="简介文本", min_level=min_level)
    return publish(body, env.request), c


# ---- 发布 ----

def test_publish_all_kinds(env):
    out, c = _publish_char(env, "alice")
    assert out["kind"] == "character"
    assert out["title"] == "共享猫娘"
    assert out["author_name"] == "alice"
    assert out["revision"] == 1 and out["installs"] == 0

    # 世界观
    env.use("alice")
    w = World(name="奇幻世界", content="魔法存在")
    env.sc["alice"].lore.save_world(w)
    out = publish(PublishBody(kind="world", source_id=w.id), env.request)
    assert out["kind"] == "world" and out["cover"] == ""

    # 历史对话
    s = env.sc["alice"].store.create(title="测试对话")
    thread = bootstrap_room(s)
    thread.messages.append(Message(role="user", content="你好"))
    env.sc["alice"].store.save(s)
    out = publish(PublishBody(kind="session", source_id=s.id), env.request)
    assert out["kind"] == "session"
    assert env.state.market.read_payload_bytes(out["id"], "zip") is not None


def test_publish_requires_own_character(env):
    # alice 发布自己的卡成功；bob 拿 alice 的卡 id 发布被拒
    out, c = _publish_char(env, "alice")
    env.use("bob")
    with pytest.raises(HTTPException) as ei:
        publish(PublishBody(kind="character", source_id=c.id), env.request)
    assert ei.value.status_code == 400


def test_publish_duplicate_rejected(env):
    out, c = _publish_char(env, "alice")
    with pytest.raises(HTTPException) as ei:
        publish(PublishBody(kind="character", source_id=c.id), env.request)
    assert ei.value.status_code == 400


def test_publish_bad_kind(env):
    env.use("bob")
    with pytest.raises(HTTPException) as ei:
        publish(PublishBody(kind="deck", source_id="x"), env.request)
    assert ei.value.status_code == 400


# ---- 门槛 ----

def test_min_level_hides_entry(env):
    out, c = _publish_char(env, "alice", min_level=10)
    mid = out["id"]

    env.use("bob")  # level 5
    assert list_entries(env.request)["entries"] == []
    with pytest.raises(HTTPException) as ei:
        get_entry(mid, env.request)
    assert ei.value.status_code == 404

    env.use(ADMIN_UID)  # admin 不受门槛限制
    entries = list_entries(env.request)["entries"]
    assert any(e["id"] == mid for e in entries)
    assert get_entry(mid, env.request)["id"] == mid


# ---- 安装 ----

def test_install_character_copies_assets_and_rewrites(env):
    # alice 的卡带头像素材
    avatar_rel = "avatar.png"
    src = env.sc["alice"].upload_dir / avatar_rel
    src.write_bytes(b"PNG-BYTES")
    out, c = _publish_char(env, "alice", avatar="/uploads/alice/avatar.png")
    mid = out["id"]

    env.use("bob")
    res = install_entry(mid, env.request)
    card = res["character"]
    assert card["id"] != c.id           # 新 id
    assert card["author"] == "bob"      # 作者变为安装者
    assert card["parent_id"] == ""
    assert card["avatar"] == "/uploads/bob/avatar.png"
    # 素材真的复制过来了
    copied = env.sc["bob"].upload_dir / avatar_rel
    assert copied.read_bytes() == b"PNG-BYTES"
    assert res["entry"]["installs"] == 1


def test_install_world_copies_assets(env):
    env.use("alice")
    bg_rel = "world-bg.png"
    (env.sc["alice"].upload_dir / bg_rel).write_bytes(b"BG")
    w = World(name="星际", avatar="/uploads/alice/world-bg.png")
    env.sc["alice"].lore.save_world(w)
    out = publish(PublishBody(kind="world", source_id=w.id), env.request)

    env.use("bob")
    res = install_entry(out["id"], env.request)
    assert res["world"]["id"] != w.id
    assert res["world"]["avatar"] == "/uploads/bob/world-bg.png"
    assert (env.sc["bob"].upload_dir / bg_rel).read_bytes() == b"BG"


def test_install_session_creates_new_session(env):
    env.use("alice")
    s = env.sc["alice"].store.create(title="长对话")
    thread = bootstrap_room(s)  # 实际会话均为房间形态：消息在线程里
    thread.messages.append(Message(role="user", content="第一句"))
    thread.messages.append(Message(role="assistant", content="第二句"))
    env.sc["alice"].store.save(s)
    out = publish(PublishBody(kind="session", source_id=s.id), env.request)

    env.use("bob")
    res = install_entry(out["id"], env.request)
    new_sid = res["id"]
    assert new_sid != s.id
    installed = env.sc["bob"].store.get(new_sid)
    assert installed is not None
    total_msgs = sum(len(t.messages) for t in installed.threads)
    assert total_msgs == 2


def test_unpublish_keeps_installed_copy(env):
    out, c = _publish_char(env, "alice")
    mid = out["id"]
    env.use("bob")
    res = install_entry(mid, env.request)
    new_id = res["character"]["id"]

    # alice 下架，bob 的副本仍在
    env.use("alice")
    delete_entry(mid, env.request)
    assert env.sc["bob"].lore.get_character(new_id) is not None


# ---- 发布者管理 ----

def test_edit_refresh_delete_permissions(env):
    out, c = _publish_char(env, "alice")
    mid = out["id"]

    # bob 不能编辑 / 更新；也不能下架
    env.use("bob")
    with pytest.raises(HTTPException) as ei:
        edit_entry(mid, EditBody(title="x"), env.request)
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        refresh_entry(mid, env.request)
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        delete_entry(mid, env.request)
    assert ei.value.status_code == 403

    # 作者编辑标题/门槛
    env.use("alice")
    updated = edit_entry(mid, EditBody(title="新标题", min_level=20), env.request)
    assert updated["title"] == "新标题" and updated["min_level"] == 20


def test_refresh_resnapshots_and_bumps_revision(env):
    out, c = _publish_char(env, "alice")
    mid = out["id"]

    c.intro = "改过的简介"
    env.sc["alice"].lore.save_character(c)
    updated = refresh_entry(mid, env.request)
    assert updated["revision"] == 2
    payload = env.state.market.read_payload_json(mid)
    assert payload["data"]["intro"] == "改过的简介"


def test_admin_can_delete_any_entry(env):
    out, c = _publish_char(env, "alice")
    mid = out["id"]
    env.use(ADMIN_UID)
    assert delete_entry(mid, env.request)["ok"] is True
    assert env.state.market.get(mid) is None


def test_mine_list_only_own(env):
    _publish_char(env, "alice")
    env.use("alice")
    own = list_entries(env.request, mine=1)["entries"]
    assert len(own) == 1

    env.use("bob")
    assert list_entries(env.request, mine=1)["entries"] == []
