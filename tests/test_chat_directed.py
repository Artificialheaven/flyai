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


# ---- 单窗口 max_tokens / disabled_tools ----

class _RecordingProvider:
    def __init__(self):
        self.calls = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls.append({
            "specs": [sp["function"]["name"] for sp in tool_specs],
            "max_tokens": kwargs.get("max_tokens"),
            "messages": list(messages),
        })
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "好")


def _max_tokens_case(tmp_path, room_mt, user_mt):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, _g, ais = _group_room(store, n=1)
        s.max_tokens = room_mt
        state, sc = _state_scope(tmp_path, store, chars)
        sc.user_settings = SimpleNamespace(max_tokens=user_mt)
        prov = _RecordingProvider()
        resp = await _chat_in_thread(state, sc, s, _req(s.threads[0].id), prov)
        await _drain(resp)
        return prov.calls[0]["max_tokens"]

    return _run(body())


def test_room_max_tokens_passed_to_provider(tmp_path):
    assert _max_tokens_case(tmp_path, 300, None) == 300


def test_user_max_tokens_used_when_room_unset(tmp_path):
    assert _max_tokens_case(tmp_path, 0, 700) == 700


def test_room_max_tokens_overrides_user_global(tmp_path):
    assert _max_tokens_case(tmp_path, 256, 700) == 256


def test_effective_max_tokens_none_when_both_unset(tmp_path):
    assert _max_tokens_case(tmp_path, 0, None) is None


def _lore_inject_case(tmp_path, auto_inject):
    from flyai.lore.models import LoreEntry

    async def body():
        store = JsonSessionStore(tmp_path / ("s_%s" % auto_inject))
        s, chars, _g, _ais = _group_room(store, n=1)
        chars[0].table = [LoreEntry("佩剑", "寒铁短剑，随身佩戴",
                                    keys=["铁剑"])]
        state, sc = _state_scope(tmp_path, store, chars)
        sc.user_settings = SimpleNamespace(
            lore_auto_inject=auto_inject, auto_recall=False)
        prov = _RecordingProvider()
        resp = await _chat_in_thread(
            state, sc, s,
            _req(s.threads[0].id, message="我的铁剑放哪了"),
            prov,
        )
        await _drain(resp)
        sys_text = "\n".join(
            m.content for m in prov.calls[0]["messages"] if m.role == "system"
        )
        return sys_text

    return _run(body())


def test_lore_trigger_block_injected_by_default(tmp_path):
    sys_text = _lore_inject_case(tmp_path, True)
    assert "【相关设定】" in sys_text and "寒铁短剑" in sys_text


def test_lore_trigger_block_skipped_when_disabled(tmp_path):
    sys_text = _lore_inject_case(tmp_path, False)
    assert "【相关设定】" not in sys_text and "寒铁短剑" not in sys_text


def test_session_and_request_disabled_tools_are_unioned(tmp_path):
    """服务端强制合并：房间持久禁用 ∪ 请求禁用，旧客户端也生效。"""
    from flyai.config import ToolSettings
    from flyai.tools.base import Tool as _Tool, ToolResult as _ToolResult

    class _FakeTool(_Tool):
        def __init__(self, name):
            self.name = name
            self.description = name
            self.parameters = {"type": "object", "properties": {}}

        async def run(self, arguments, ctx):
            return _ToolResult(output="x")

    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, _ais = _group_room(store)
        s.disabled_tools = ["web_search"]          # 房间级（服务端持久）
        state, sc = _state_scope(tmp_path, store, chars)
        state.config.tools = ToolSettings(enabled=True)
        for tname in ("query_lore", "web_search", "web_fetch"):
            state.registry.register(_FakeTool(tname))
        prov = _RecordingProvider()
        req = _req(g.id, disabled_tools=["web_fetch"])  # 请求级（前端勾选）
        resp = await _chat_in_thread(state, sc, s, req, prov)
        await _drain(resp)
        # 三位发言者各一轮，每轮暴露的工具集合一致
        assert prov.calls
        for call in prov.calls:
            assert set(call["specs"]) == {"query_lore"}

    _run(body())

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
