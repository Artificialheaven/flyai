"""房间管理 API：移除成员 / 删除窗口 / 重命名窗口 / 座位设置校验。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.runs import ChatRunManager
from flyai.api.routes_room import (
    MemberUpdate,
    delete_thread,
    remove_member,
    rename_thread,
    update_member,
)
from flyai.lore.character_memory import CharacterMemoryStore
from flyai.lore.models import Character
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _FakeLore:
    def __init__(self, chars):
        self._chars = {c.id: c for c in chars}

    def get_character(self, cid):
        return self._chars.get(cid)


def _env(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(2):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    ais = list(s.members[1:])
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)

    state = SimpleNamespace()
    state.config = SimpleNamespace(providers=[])
    state.run_manager = ChatRunManager()
    sc = SimpleNamespace(
        uid="admin", level=256, store=store, lore=_FakeLore(chars),
        char_memory=CharacterMemoryStore(tmp_path),
    )
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    return state, sc, request, store, s, g, ais


# ---- 移除成员 ----

def test_remove_member_cascades_threads(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    m1_dm = s.threads[0]

    out = remove_member(s.id, ais[0].id, request)
    assert out["removed"] == ais[0].id

    s2 = store.get(s.id)
    assert s2.member(ais[0].id) is None
    # 其专属 dm 窗口删除
    assert s2.thread(m1_dm.id) is None
    # 群窗口保留（历史保留），但成员被摘除
    g2 = s2.thread(g.id)
    assert g2 is not None and ais[0].id not in g2.member_ids


def test_remove_member_who_empties_group_deletes_group(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    # 群里只剩 ais[1]：先把 ais[0] 摘掉再删 ais[1]
    g.member_ids = [ais[1].id]
    store.save(s)

    remove_member(s.id, ais[1].id, request)
    s2 = store.get(s.id)
    assert s2.thread(g.id) is None  # 空群随最后一人删除


def test_remove_member_404_and_user_seat_400(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        remove_member(s.id, "nope", request)
    assert ei.value.status_code == 404

    with pytest.raises(HTTPException) as ei:
        remove_member(s.id, s.members[0].id, request)
    assert ei.value.status_code == 400


def test_remove_member_active_run_409(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    state.run_manager.create(sc.uid, s.id)  # 默认 status=running
    with pytest.raises(HTTPException) as ei:
        remove_member(s.id, ais[0].id, request)
    assert ei.value.status_code == 409


# ---- 删除窗口 ----

def test_delete_thread_ok_and_last_thread_400(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    out = delete_thread(s.id, g.id, request)
    assert out["removed"] == g.id
    assert store.get(s.id).thread(g.id) is None

    # 再删剩两条 dm；删到最后一条时拒绝
    delete_thread(s.id, s.threads[0].id, request)
    with pytest.raises(HTTPException) as ei:
        delete_thread(s.id, store.get(s.id).threads[0].id, request)
    assert ei.value.status_code == 400


def test_delete_thread_active_run_409(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    state.run_manager.create(sc.uid, s.id)
    with pytest.raises(HTTPException) as ei:
        delete_thread(s.id, g.id, request)
    assert ei.value.status_code == 409


# ---- 重命名窗口 ----

def test_rename_thread_ok_and_empty_400(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    out = rename_thread(s.id, g.id, SimpleNamespace(title="新群名"), request)
    assert out["title"] == "新群名"
    assert store.get(s.id).thread(g.id).title == "新群名"

    with pytest.raises(HTTPException) as ei:
        rename_thread(s.id, g.id, SimpleNamespace(title="  "), request)
    assert ei.value.status_code == 400


# ---- 座位设置校验 ----

def test_update_member_provider_validation(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    body = MemberUpdate(provider_id="ghost")
    with pytest.raises(HTTPException) as ei:
        update_member(s.id, ais[0].id, body, request)
    assert ei.value.status_code == 400

    state.config.providers = [SimpleNamespace(id="p1")]
    body = MemberUpdate(provider_id="p1")
    out = update_member(s.id, ais[0].id, body, request)
    assert out["provider_id"] == "p1"


def test_update_member_memory_mode_validation(tmp_path):
    state, sc, request, store, s, g, ais = _env(tmp_path)
    with pytest.raises(HTTPException) as ei:
        update_member(s.id, ais[0].id, MemberUpdate(memory_mode="xx"), request)
    assert ei.value.status_code == 400

    # 挂载不存在的记忆簿 → 400
    with pytest.raises(HTTPException) as ei:
        update_member(
            s.id, ais[0].id,
            MemberUpdate(memory_mode="ro", memory_char_id="nobody"),
            request,
        )
    assert ei.value.status_code == 400

    # 记忆簿存在 → 通过
    sc.char_memory.append("c1", "一条旧记忆")
    out = update_member(
        s.id, ais[0].id,
        MemberUpdate(memory_mode="rw", memory_char_id="c1"),
        request,
    )
    assert out["memory_mode"] == "rw" and out["memory_char_id"] == "c1"
