"""撤回整轮消息：纯逻辑 / 版本互操作 / 持久化 / 模型隔离 / DELETE 端点。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.runs import ChatRunManager
from flyai.lore.gen import messages_to_script
from flyai.lore.models import Character
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import ImageAttachment, Message, ToolCall
from flyai.sessions import versions
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.sessions.recall import (
    TOMBSTONE_ROLE,
    find_target,
    is_tombstone,
    recall_turn,
    turn_span,
)


def _user(content="你好", name=None, images=None):
    return Message(role="user", content=content, name=name, images=images)


# ---- 纯逻辑 ----

def test_recall_first_and_last_turn():
    u1, a1 = _user("第一条"), Message(role="assistant", content="回1")
    u2, a2 = _user("第二条"), Message(role="assistant", content="回2")
    msgs = [u1, a1, u2, a2]

    i, end = turn_span(msgs, u1.id)
    assert (i, end) == (0, 2)

    payload = recall_turn(msgs, u2.id)
    assert [m.role for m in msgs] == ["user", "assistant", TOMBSTONE_ROLE]
    stone = msgs[-1]
    assert stone.id == u2.id and is_tombstone(stone)
    assert payload == {"content": "第二条", "images": [], "name": None}

    payload1 = recall_turn(msgs, u1.id)
    assert [m.role for m in msgs] == [TOMBSTONE_ROLE, TOMBSTONE_ROLE]
    assert msgs[0].id == u1.id
    assert payload1["content"] == "第一条"


def test_recall_group_turn_removes_fanout_chain_and_hidden():
    u = _user("散会")
    call = ToolCall(id="tc1", name="search", arguments={})
    a1 = Message(role="assistant", content=None, tool_calls=[call],
                 actor_id="seat1", name="角色1")
    t1 = Message(role="tool", tool_call_id="tc1", content="结果",
                 actor_id="seat1", ok=True)
    r1 = Message(role="assistant", content="我先走了", actor_id="seat1", name="角色1")
    skip = Message(role="assistant", content="略", actor_id="seat2",
                   name="角色2", hidden=True)
    later_u, later_a = _user("明天见"), Message(role="assistant", content="好")
    msgs = [u, a1, t1, r1, skip, later_u, later_a]

    recall_turn(msgs, u.id)
    assert [m.role for m in msgs] == [TOMBSTONE_ROLE, "user", "assistant"]
    # 接缝处没有孤立的 tool 消息
    assert not any(m.role == "tool" for m in msgs)
    assert msgs[0].id == u.id


def test_recall_preserves_images_and_name_payload():
    img = ImageAttachment(media_type="image/png", data="AAAA")
    u = Message(role="user", content=None, name="我", images=[img])
    a = Message(role="assistant", content="图不错")
    msgs = [u, a]
    payload = recall_turn(msgs, u.id)
    assert payload["content"] is None
    assert payload["name"] == "我"
    assert payload["images"] == [{"media_type": "image/png", "data": "AAAA"}]
    # 墓碑本体不带图片（图片只在载荷里供回填）
    assert msgs[0].images is None


def test_recall_pops_edits_versions():
    t = Thread(kind="dm")
    u1, a1 = _user("一"), Message(role="assistant", content="a1")
    u2, a2 = _user("二"), Message(role="assistant", content="a2")
    t.messages.extend([u1, a1, u2, a2])
    versions.ensure_snapshot(t, u1.id)
    versions.ensure_snapshot(t, u2.id)
    assert set(t.edits.keys()) == {u1.id, u2.id}

    recall_turn(t.messages, u2.id, edits=t.edits)
    assert u2.id not in t.edits and u1.id in t.edits


def test_turn_span_errors():
    u, a = _user("x"), Message(role="assistant", content="y")
    msgs = [u, a]
    recall_turn(msgs, u.id)
    with pytest.raises(ValueError):
        turn_span(msgs, u.id)             # 同 id 已变成墓碑
    with pytest.raises(ValueError):
        turn_span([Message(role="assistant", content="z")], "missing")
    fresh_u = _user("m")
    with pytest.raises(ValueError):
        turn_span([fresh_u], "nope")


def test_legacy_list_with_edits_none():
    u, a = _user("旧"), Message(role="assistant", content="式")
    msgs = [u, a]
    payload = recall_turn(msgs, u.id, edits=None)
    assert payload["content"] == "旧" and msgs[0].role == TOMBSTONE_ROLE


# ---- 与历史编辑/重生成版本系统的互操作 ----

def _three_turn_thread():
    t = Thread(kind="dm")
    rows = []
    for text in ("一", "二", "三"):
        u, a = _user(text), Message(role="assistant", content="回" + text)
        t.messages.extend([u, a])
        rows.append((u, a))
    return t, rows


def _three():
    t = Thread(kind="dm")
    rows = []
    for text in ("一", "二", "三"):
        u, a = _user(text), Message(role="assistant", content="回" + text)
        t.messages.extend([u, a])
        rows.append((u, a))
    return t, rows[0][0], rows[1][0], rows[2][0]


def test_edit_then_model_reply_keeps_tombstone_position():
    t, u1, u2, u3 = _three()
    recall_turn(t.messages, u2.id, edits=t.edits)
    # [u1,a1, TOMB(u2), u3,a3]
    assert [m.role for m in t.messages] == [
        "user", "assistant", TOMBSTONE_ROLE, "user", "assistant"]

    prev = versions.begin_edit(t, u1.id, _user("一改"))
    # prefix 被替换，suffix=[TOMB,u3,a3] 拆走
    assert [m.role for m in t.messages] == ["user"]
    head = len(t.messages)
    t.messages.append(Message(role="assistant", content="回一改"))
    versions.commit_run(t, u1.id, head, versions.MODE_EDIT, prev)

    roles = [m.role for m in t.messages]
    assert roles == ["user", "assistant", TOMBSTONE_ROLE, "user", "assistant"]
    stone = t.messages[2]
    assert stone.id == u2.id and stone.tombstone["content"] == "二"
    assert t.messages[1].content == "回一改"


def test_regen_after_recall_does_not_swallow_tombstone():
    t, u1, u2, u3 = _three()
    recall_turn(t.messages, u1.id, edits=t.edits)
    # begin_regen 第三轮：suffix 为空，locate 终点 len
    prev = versions.begin_regen(t, u3.id)
    assert t.messages[0].role == TOMBSTONE_ROLE
    head = len(t.messages)
    t.messages.append(Message(role="assistant", content="回三新"))
    versions.commit_run(t, u3.id, head, versions.MODE_REGEN, prev)
    assert t.messages[0].role == TOMBSTONE_ROLE
    assert t.messages[-1].content == "回三新"


def test_heal_thread_restores_suffix_with_tombstone():
    t, u1, u2, u3 = _three()
    recall_turn(t.messages, u2.id, edits=t.edits)
    versions.ensure_snapshot(t, u1.id)
    # 模拟 begin_edit 后硬 kill：suffix 存在 pending_suffix，扁平只剩新 user
    versions.begin_edit(t, u1.id, _user("一改"))
    assert not any(is_tombstone(m) for m in t.messages)
    versions.heal_thread(t)
    roles = [m.role for m in t.messages]
    assert TOMBSTONE_ROLE in roles  # suffix 接回，墓碑回来


# ---- 持久化 / 定位 ----

def test_store_roundtrip_keeps_tombstone(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = Session(title="s")
    s.members.append(RoomMember(name="你", is_user=True))
    ai = RoomMember(name="AI", character_id="c1")
    s.members.append(ai)
    t = Thread(kind="dm", title="dm", member_ids=[ai.id])
    u, a = _user("存盘"), Message(role="assistant", content="ok")
    t.messages.extend([u, a])
    s.threads.append(t)
    recall_turn(t.messages, u.id, edits=t.edits)
    store.save(s)

    s2 = store.get(s.id)
    t2 = s2.threads[0]
    stone = t2.messages[0]
    assert stone.role == TOMBSTONE_ROLE and stone.id == u.id
    assert stone.tombstone["content"] == "存盘"
    # 导出形状（threads 原样序列化）里墓碑保留
    d = t2.to_dict()
    assert d["messages"][0]["tombstone"]["content"] == "存盘"


def test_find_target_threads_then_legacy():
    s = Session(title="s")
    t = Thread(kind="dm")
    u1 = _user("窗口内")
    t.messages.append(u1)
    s.threads.append(t)
    legacy_u = _user("旧形态")
    s.messages.append(legacy_u)

    th, msgs = find_target(s, u1.id)
    assert th is t and msgs is t.messages
    th2, msgs2 = find_target(s, legacy_u.id)
    assert th2 is None and msgs2 is s.messages
    assert find_target(s, "nope") == (None, None)


# ---- 模型隔离 ----

class _CapturingProvider:
    def __init__(self):
        self.calls = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        # 拷贝快照，避免后续 append 污染断言
        self.calls.append([(m.role, bool(getattr(m, "tombstone", None)))
                           for m in messages])
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "发言")


def _state_scope(tmp_path, chars):
    from flyai.config import RoleplaySettings, ToolSettings
    from flyai.tools.registry import ToolRegistry

    class _Lore:
        def __init__(self, cs):
            self._cs = {c.id: c for c in cs}

        def get_character(self, cid):
            return self._cs.get(cid)

    state = SimpleNamespace(
        config=SimpleNamespace(
            roleplay=RoleplaySettings(format_example=""),
            tools=ToolSettings(enabled=False),
            image_models=[],
        ),
        registry=ToolRegistry(),
    )
    sc = SimpleNamespace(store=JsonSessionStore(tmp_path / "s"), lore=_Lore(chars),
                         member=lambda mid: None, uid="admin", level=256)
    return state, sc


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


async def _drain(resp):
    async for _ in resp.body_iterator:
        pass


def _seed_room(char_ids, kind="dm"):
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    seats = []
    for i, cid in enumerate(char_ids):
        ch = Character(id=cid, name="角色%d" % i)
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        seats.append(m)
    if kind == "group":
        t = Thread(kind="group", title="群", member_ids=[m.id for m in seats])
    else:
        t = Thread(kind="dm", title=seats[0].name, member_ids=[seats[0].id])
    s.threads.append(t)
    return s, t, chars, seats


def _pre_recall(t, anchor_seats, text):
    """造一轮并撤回，留下墓碑。"""
    u = _user(text)
    t.messages.append(u)
    for i, seat in enumerate(anchor_seats):
        t.messages.append(Message(
            role="assistant", content="旧回复%d" % i,
            actor_id=seat.id, name=seat.name))
    recall_turn(t.messages, u.id, edits=t.edits)
    return u


def test_provider_never_sees_tombstone_dm(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread

    s, t, chars, seats = _seed_room(["c1"], "dm")
    _pre_recall(t, seats, "被撤回的话")
    store = JsonSessionStore(tmp_path / "s")
    store.save(s)
    state, sc = _state_scope(tmp_path, chars)
    sc.store = store

    provider = _CapturingProvider()
    req = SimpleNamespace(message="新一轮", thread_id=t.id)
    resp = _run(_chat_in_thread(state, sc, store.get(s.id), req, provider))
    _run(_drain(resp))

    assert provider.calls, "provider 应至少被调用一次"
    for call in provider.calls:
        assert all(role != TOMBSTONE_ROLE for role, _ in call)
        assert all(not is_stone for _, is_stone in call)
    # 落盘历史里墓碑仍在
    saved = store.get(s.id).thread(t.id)
    assert any(is_tombstone(m) for m in saved.messages)


def test_provider_never_sees_tombstone_group(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread

    s, g, chars, seats = _seed_room(["c1", "c2"], "group")
    _pre_recall(g, seats, "群里撤回")
    store = JsonSessionStore(tmp_path / "s")
    store.save(s)
    state, sc = _state_scope(tmp_path, chars)
    sc.store = store

    provider = _CapturingProvider()
    req = SimpleNamespace(message="群里新一轮", thread_id=g.id)
    resp = _run(_chat_in_thread(state, sc, store.get(s.id), req, provider))
    _run(_drain(resp))

    assert provider.calls
    for call in provider.calls:
        assert all(role != TOMBSTONE_ROLE for role, _ in call)


def test_messages_to_script_skips_tombstone():
    stone = Message(role=TOMBSTONE_ROLE, id="t1",
                    tombstone={"content": "可见", "images": [], "name": None})
    a = Message(role="assistant", content="回复")
    # 墓碑自身既不输出；其后的正常消息照常
    script = messages_to_script([stone, a])
    assert "可见" not in script
    assert "回复" in script


# ---- DELETE 端点 ----

def _api_env(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    ch = Character(id="c1", name="角色1")
    ai = RoomMember(name=ch.name, character_id="c1")
    s.members.append(ai)
    t = Thread(kind="dm", title="dm", member_ids=[ai.id])
    s.threads.append(t)
    store.save(s)

    class _Lore:
        def get_character(self, cid):
            return ch if cid == "c1" else None

    state = SimpleNamespace(
        config=SimpleNamespace(providers=[]),
        run_manager=ChatRunManager(),
    )
    sc = SimpleNamespace(uid="admin", level=256, store=store, lore=_Lore())
    state.user = lambda request: sc
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return state, sc, request, store, s, t


def test_delete_message_turn_endpoint_ok(tmp_path):
    from flyai.api.routes_session import delete_message_turn

    state, sc, request, store, s, t = _api_env(tmp_path)
    u, a = _user("撤回我"), Message(role="assistant", content="好")
    t.messages.extend([u, a])
    store.save(s)

    out = delete_message_turn(s.id, u.id, request)
    assert out["ok"] is True and out["anchor"] == u.id
    assert out["thread_id"] == t.id
    assert out["tombstone"]["content"] == "撤回我"

    t2 = store.get(s.id).thread(t.id)
    assert len(t2.messages) == 1
    assert t2.messages[0].role == TOMBSTONE_ROLE


def test_delete_message_turn_404_twice_and_bad_session(tmp_path):
    from flyai.api.routes_session import delete_message_turn

    state, sc, request, store, s, t = _api_env(tmp_path)
    u = _user("x")
    t.messages.append(u)
    store.save(s)
    delete_message_turn(s.id, u.id, request)
    with pytest.raises(HTTPException) as ei:
        delete_message_turn(s.id, u.id, request)  # 二次撤回：id 在但锚点是墓碑
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei:
        delete_message_turn(s.id, "nope-id", request)
    assert ei.value.status_code == 404

    with pytest.raises(HTTPException) as ei:
        delete_message_turn("nope-session", "whatever", request)
    assert ei.value.status_code == 404


def test_delete_message_turn_only_user_message(tmp_path):
    from flyai.api.routes_session import delete_message_turn

    state, sc, request, store, s, t = _api_env(tmp_path)
    a = Message(role="assistant", content="开场白")
    t.messages.append(a)
    store.save(s)
    with pytest.raises(HTTPException) as ei:
        delete_message_turn(s.id, a.id, request)
    assert ei.value.status_code == 400
    assert store.get(s.id).thread(t.id).messages[0].content == "开场白"


def test_delete_message_turn_active_run_409(tmp_path):
    from flyai.api.routes_session import delete_message_turn

    state, sc, request, store, s, t = _api_env(tmp_path)
    u = _user("x")
    t.messages.append(u)
    store.save(s)
    state.run_manager.create(sc.uid, s.id)
    with pytest.raises(HTTPException) as ei:
        delete_message_turn(s.id, u.id, request)
    assert ei.value.status_code == 409
    # 未被撤回
    assert store.get(s.id).thread(t.id).messages[0].role == "user"
