"""L4-A 续写：增量并回同一条消息、MVU 只看增量、只读/群聊/非末条拒绝。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.runs import ChatRunManager
from flyai.api.routes_chat import _chat_in_thread
from flyai.lore.models import Character, LoreEntry
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
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


def _dm_room(store, character=None):
    """你 + 1 个 AI 的私聊窗口。"""
    s = Session(title="私聊")
    s.members.append(RoomMember(name="你", is_user=True))
    ch = character or Character(id="c1", name="角色1")
    m = RoomMember(name=ch.name, character_id=ch.id)
    s.members.append(m)
    t = Thread(kind="dm", title=m.name, member_ids=[m.id])
    s.threads.append(t)
    store.save(s)
    return s, [ch], t, m


def _group_room(store, n=2):
    s = Session(title="群聊")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    ais = []
    for i in range(n):
        ch = Character(id="c%d" % (i + 1), name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=ch.id)
        s.members.append(m)
        ais.append(m)
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)
    return s, chars, g, ais


def _state_scope(store, chars):
    from flyai.config import RoleplaySettings, ToolSettings

    state = SimpleNamespace()
    state.config = SimpleNamespace(
        roleplay=RoleplaySettings(format_example=""),
        tools=ToolSettings(enabled=False),
        image_models=[],
        providers=[],
    )
    from flyai.tools.registry import ToolRegistry

    state.registry = ToolRegistry()
    state.run_manager = ChatRunManager()
    state.galgame_tts_overlay = {}
    sc = SimpleNamespace(store=store, lore=_FakeLore(chars), uid="admin", level=256)
    return state, sc


def _req(thread_id, **kw):
    base = dict(
        message="",
        thread_id=thread_id,
        target_member_ids=[],
        shuffle_speakers=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def _drain(resp):
    async for _frame in resp.body_iterator:
        pass


class _RecordingProvider:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls.append({"specs": tool_specs, "kwargs": kwargs, "messages": list(messages)})
        yield StreamEvent(StreamEventType.CONTENT_DELTA, self.text)


def _append_assistant(thread, content, **kw):
    msg = Message(role="assistant", content=content, **kw)
    thread.messages.append(msg)
    return msg


def test_continue_merges_into_same_message(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, thread, _member = _dm_room(store)
        target = _append_assistant(thread, "开头")
        store.save(s)
        state, sc = _state_scope(store, chars)
        prov = _RecordingProvider("尾部")

        resp = await _chat_in_thread(
            state, sc, s,
            _req(thread.id, continue_message_id=target.id),
            prov,
        )
        await _drain(resp)

        s2 = store.get(s.id)
        msgs = list(s2.thread(thread.id).messages)
        assert len(msgs) == 1
        assert msgs[0].id == target.id
        assert msgs[0].content == "开头尾部"
        # 续写不新增用户消息，原回复作为 assistant prefill 发给模型；工具关闭
        sent = prov.calls[0]["messages"]
        assert sent[-1].role == "assistant"
        assert sent[-1].content.startswith("开头")
        assert prov.calls[0]["specs"] == []

    _run(body())


def test_continue_mvu_only_applies_delta(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        char = Character(
            id="c1",
            name="角色1",
            table=[LoreEntry("[InitVar]", "hp: 1")],
        )
        s, chars, thread, _member = _dm_room(store, char)
        # 基础文本里带一个更新块：若把它也重放，other 会被写进状态
        base = (
            "开头<UpdateVariable><JSONPatch>"
            '[{"op":"add","path":"/other","value":"x"}]'
            "</JSONPatch></UpdateVariable>"
        )
        target = _append_assistant(thread, base, stat_data={"hp": 1})
        store.save(s)
        state, sc = _state_scope(store, chars)
        prov = _RecordingProvider(
            "尾部<UpdateVariable><JSONPatch>"
            '[{"op":"replace","path":"/hp","value":5}]'
            "</JSONPatch></UpdateVariable>"
        )

        resp = await _chat_in_thread(
            state, sc, s,
            _req(thread.id, continue_message_id=target.id),
            prov,
        )
        await _drain(resp)

        s2 = store.get(s.id)
        merged = s2.thread(thread.id).messages[0]
        assert merged.stat_data == {"hp": 5}
        assert s2.mvu["stat_data"] == {"hp": 5}

    _run(body())


def test_continue_marks_origin_on_new_messages(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, thread, _member = _dm_room(store)
        state, sc = _state_scope(store, chars)
        prov = _RecordingProvider("好")

        resp = await _chat_in_thread(
            state, sc, s,
            _req(thread.id, message="来自卡片", origin="card"),
            prov,
        )
        await _drain(resp)

        s2 = store.get(s.id)
        msgs = s2.thread(thread.id).messages
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].origin == "card"
        assert msgs[1].origin == "card"

    _run(body())


def test_continue_rejects_group_window(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, _ais = _group_room(store)
        target = _append_assistant(g, "开头")
        store.save(s)
        state, sc = _state_scope(store, chars)
        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s,
                _req(g.id, continue_message_id=target.id),
                _RecordingProvider("尾部"),
            )
        assert ei.value.status_code == 400
        assert "群聊" in ei.value.detail

    _run(body())


def test_continue_rejects_read_only_session(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, thread, _member = _dm_room(store)
        s.mode = "galgame"
        target = _append_assistant(thread, "开头")
        store.save(s)
        state, sc = _state_scope(store, chars)
        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s,
                _req(thread.id, continue_message_id=target.id),
                _RecordingProvider("尾部"),
            )
        assert ei.value.status_code == 400
        assert "只读" in ei.value.detail

    _run(body())


def test_continue_rejects_non_last_message(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, thread, _member = _dm_room(store)
        target = _append_assistant(thread, "开头")
        _append_assistant(thread, "后面")
        store.save(s)
        state, sc = _state_scope(store, chars)
        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s,
                _req(thread.id, continue_message_id=target.id),
                _RecordingProvider("尾部"),
            )
        assert ei.value.status_code == 400
        assert "最后一条" in ei.value.detail

    _run(body())


def test_continue_rejects_tool_calls_and_empty_target(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, thread, _member = _dm_room(store)
        state, sc = _state_scope(store, chars)

        tool_target = _append_assistant(
            thread, "调工具", tool_calls=[ToolCall(id="t1", name="x", arguments={})]
        )
        store.save(s)
        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s,
                _req(thread.id, continue_message_id=tool_target.id),
                _RecordingProvider("尾部"),
            )
        assert ei.value.status_code == 400
        assert "工具调用" in ei.value.detail

        thread.messages[:] = [_append_assistant(thread, "   ")]
        store.save(s)
        with pytest.raises(HTTPException) as ei2:
            await _chat_in_thread(
                state, sc, s,
                _req(thread.id, continue_message_id=thread.messages[0].id),
                _RecordingProvider("尾部"),
            )
        # 空白回复不算可见锚点，直接按「不是最后一条」拒绝
        assert ei2.value.status_code == 400
        assert "最后一条" in ei2.value.detail

    _run(body())


def test_continue_rejects_when_run_active(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, thread, _member = _dm_room(store)
        target = _append_assistant(thread, "开头")
        store.save(s)
        state, sc = _state_scope(store, chars)
        occupied = state.run_manager.create("admin", s.id, thread.id)
        assert state.run_manager.try_lock_for("admin", s.id, occupied)

        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s,
                _req(thread.id, continue_message_id=target.id),
                _RecordingProvider("尾部"),
            )
        assert ei.value.status_code == 409

    _run(body())
