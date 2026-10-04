"""定向发言（@ 优先）、发言顺序、会话级并发锁、座位级 provider。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.runs import ChatRunManager
from flyai.api.routes_chat import _chat_in_thread
from flyai.lore.models import Character
from flyai.providers.base import StreamEvent, StreamEventType
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


def _group_room(store, n=3):
    """你 + n 个 AI；每个 AI 一条 dm 窗口 + 一条全员 group 窗口。"""
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(n):
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
    return s, chars, g, ais


def _state_scope(tmp_path, store, chars):
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
    sc = SimpleNamespace(
        store=store, lore=_FakeLore(chars), uid="admin", level=256
    )
    return state, sc


def _req(thread, **kw):
    base = dict(message="hi", thread_id=thread,
                target_member_ids=[], shuffle_speakers=False)
    base.update(kw)
    return SimpleNamespace(**base)


async def _drain(resp):
    async for _frame in resp.body_iterator:
        pass


async def _speaker_order(resp):
    order = []
    async for frame in resp.body_iterator:
        if isinstance(frame, str) and "event: speaker\n" in frame:
            payload = frame.split("data:", 1)[1].strip()
            order.append(json.loads(payload)["member_id"])
    return order


# ---- @ 定向与顺序 ----

def test_directed_speakers_first_in_at_order(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, ais = _group_room(store)
        state, sc = _state_scope(tmp_path, store, chars)

        class P:
            async def stream_chat(self, messages, tool_specs, **kwargs):
                yield StreamEvent(StreamEventType.CONTENT_DELTA, "好")

        resp = await _chat_in_thread(
            state, sc, s,
            _req(g.id, target_member_ids=[ais[2].id, ais[0].id]),
            P(),
        )
        order = await _speaker_order(resp)
        # 被 @ 者按 @ 顺序最先；未被 @ 的 m2 跟后
        assert order == [ais[2].id, ais[0].id, ais[1].id]

    _run(body())


def test_shuffle_keeps_directed_first(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, ais = _group_room(store)
        state, sc = _state_scope(tmp_path, store, chars)

        class P:
            async def stream_chat(self, messages, tool_specs, **kwargs):
                yield StreamEvent(StreamEventType.CONTENT_DELTA, "好")

        resp = await _chat_in_thread(
            state, sc, s,
            _req(g.id, target_member_ids=[ais[1].id], shuffle_speakers=True),
            P(),
        )
        order = await _speaker_order(resp)
        assert order[0] == ais[1].id  # 被 @ 者恒最先
        assert sorted(order) == sorted(m.id for m in ais)  # 全员都发言

    _run(body())


# ---- 会话级并发锁 ----

class _BlockingProvider:
    """发出首字后阻塞在 gate，模拟进行中的回答。"""

    def __init__(self):
        self.gate = asyncio.Event()

    async def stream_chat(self, messages, tool_specs, **kwargs):
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "开")
        await self.gate.wait()
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "完")


def test_concurrent_chat_same_session_any_thread_409(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, ais = _group_room(store)
        state, sc = _state_scope(tmp_path, store, chars)
        prov = _BlockingProvider()

        resp = await _chat_in_thread(state, sc, s, _req(g.id), prov)
        await asyncio.sleep(0.05)  # 让 drive 进入阻塞中的生成
        try:
            # 同会话的【另一窗口】并发发起 → 409
            with pytest.raises(HTTPException) as ei:
                await _chat_in_thread(
                    state, sc, s, _req(s.threads[0].id), prov
                )
            assert ei.value.status_code == 409
            # 锁仍在，且第二请求没有落下用户消息
            s2 = store.get(s.id)
            t2 = s2.thread(s.threads[0].id)
            assert all(m.role != "user" for m in t2.messages)
        finally:
            prov.gate.set()
            await _drain(resp)

    _run(body())


# ---- 座位级 provider ----

class _NamedProvider:
    def __init__(self, tag):
        self.tag = tag

    async def stream_chat(self, messages, tool_specs, **kwargs):
        yield StreamEvent(StreamEventType.CONTENT_DELTA, self.tag)


def test_seat_provider_overrides_room_default(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, ais = _group_room(store)
        ais[0].provider_id = "px"
        state, sc = _state_scope(tmp_path, store, chars)
        state.config.providers = [
            SimpleNamespace(id="px", owner="admin", shared=True, min_level=0)
        ]
        seat_prov = _NamedProvider("座位专属")
        state.get_provider = lambda pid: seat_prov if pid == "px" else None
        default_prov = _NamedProvider("房间默认")

        resp = await _chat_in_thread(state, sc, s, _req(g.id), default_prov)
        await _drain(resp)

        s2 = store.get(s.id)
        g2 = s2.thread(g.id)
        contents = [m.content for m in g2.messages if m.role == "assistant"]
        assert contents == ["座位专属", "房间默认", "房间默认"]

    _run(body())


def test_seat_provider_not_visible_403(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, ais = _group_room(store)
        ais[0].provider_id = "secret"
        state, sc = _state_scope(tmp_path, store, chars)
        # 非 admin 用户：等级不足
        sc.uid = "bob"
        sc.level = 0
        state.config.providers = [
            SimpleNamespace(id="secret", owner="admin", shared=True, min_level=200)
        ]

        with pytest.raises(HTTPException) as ei:
            await _chat_in_thread(
                state, sc, s, _req(g.id), _NamedProvider("默认")
            )
        assert ei.value.status_code == 403

    _run(body())
