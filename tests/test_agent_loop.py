"""Agent loop 双轮数预算：通用工具轮数 vs web_search/web_fetch 独立轮数。"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import run_agent_loop
from flyai.config import ToolSettings
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.tools.base import ToolResult


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _ScriptedProvider:
    """按脚本逐轮返回：每个元素是一次 LLM 响应。

    ("final", "文字") → 最终回复；("calls", [(name, args), ...]) → 工具调用轮。
    """

    def __init__(self, turns):
        self._turns = list(turns)
        self._idx = 0

    async def stream_chat(self, messages, tool_specs, **kwargs):
        kind, payload = self._turns[self._idx]
        self._idx += 1
        if kind == "final":
            yield StreamEvent(StreamEventType.CONTENT_DELTA, payload)
        else:
            calls = [
                ToolCall(id="call_%d_%d" % (self._idx, i), name=name, arguments=args)
                for i, (name, args) in enumerate(payload)
            ]
            yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=calls)


class _FakeRegistry:
    """记录每个工具被执行了多少次。"""

    def __init__(self):
        self.executed = []

    def set_timeout(self, seconds):
        pass

    def specs(self):
        return []

    async def execute(self, call, ctx):
        self.executed.append(call.name)
        return ToolResult(output="结果:" + call.name)


def _web_turns(n):
    return [("calls", [("web_fetch", {"url": "http://x/%d" % i})]) for i in range(n)]


def _other_turns(n):
    return [("calls", [("read_file", {"path": "/f/%d" % i})]) for i in range(n)]


async def _collect(turns, general_max, web_max=None):
    provider = _ScriptedProvider(turns)
    registry = _FakeRegistry()
    settings = ToolSettings(enabled=True, max_iterations=general_max)
    history = [Message(role="user", content="hi")]
    out = []
    async for ev, data in run_agent_loop(
        history,
        provider,
        registry,
        settings,
        system_prompt="sys",
        web_max_iterations=web_max,
    ):
        out.append((ev, data))
    return out, registry


def _done(events):
    return [d for ev, d in events if ev == "done"][-1]


def _errors(events):
    return [d for ev, d in events if ev == "error"]


def test_web_tools_exceed_general_limit():
    # 通用只有 3 轮，联网有独立 15 轮：15 次 web_fetch 后正常收尾
    turns = _web_turns(15) + [("final", "答完了")]
    events, registry = _run(_collect(turns, general_max=3, web_max=15))
    assert _done(events)["reason"] == "stop"
    assert registry.executed.count("web_fetch") == 15
    assert "read_file" not in registry.executed


def test_web_budget_exhausted():
    # 联网预算 4：4 轮执行完，第 5 次还想抓 → 拦截，reason=max_web_iterations
    turns = _web_turns(5)
    events, registry = _run(_collect(turns, general_max=3, web_max=4))
    assert _done(events)["reason"] == "max_web_iterations"
    assert registry.executed.count("web_fetch") == 4
    assert _errors(events) and "联网工具最大轮数" in _errors(events)[0]["message"]


def test_other_tools_remain_bounded_by_general():
    # 非联网工具不享受放宽：通用 3 轮，第 4 次调用时被拦（第 4 次不执行）
    turns = _other_turns(4)
    events, registry = _run(_collect(turns, general_max=3, web_max=15))
    assert _done(events)["reason"] == "max_iterations"
    assert registry.executed.count("read_file") == 3


def test_other_rounds_do_not_consume_web_budget():
    # 3 轮其他工具耗尽通用预算；第 4 次其他调用被拦，联网一轮都没发生
    turns = _other_turns(4)
    events, registry = _run(_collect(turns, general_max=3, web_max=15))
    assert _done(events)["reason"] == "max_iterations"
    assert registry.executed.count("web_fetch") == 0


def test_interleaved_general_caps_first():
    # 交替进行：3 轮其他 + 3 轮联网；通用先耗尽（3），此时联网未到 15
    turns = []
    for i in range(3):
        turns.append(("calls", [("read_file", {"path": "/f/%d" % i})]))
        turns.append(("calls", [("web_search", {"query": "q%d" % i})]))
    turns.append(("calls", [("read_file", {"path": "/blocked"})]))
    events, registry = _run(_collect(turns, general_max=3, web_max=15))
    assert _done(events)["reason"] == "max_iterations"
    assert registry.executed.count("read_file") == 3
    assert registry.executed.count("web_search") == 3


def test_mixed_round_consumes_both_budgets():
    # 同一轮里既有联网又有其他工具：两套计数各 +1
    turns = [("calls", [("web_fetch", {"url": "http://x"}),
                        ("read_file", {"path": "/f"})])]
    turns += _other_turns(2)  # 再 2 轮其他 → 通用 3 满
    turns.append(("final", "答"))
    events, registry = _run(_collect(turns, general_max=3, web_max=15))
    assert _done(events)["reason"] == "stop"
    assert registry.executed.count("read_file") == 3
    assert registry.executed.count("web_fetch") == 1


def test_web_max_defaults_to_general_when_omitted():
    # 不传 web_max_iterations：联网轮也受通用上限约束（旧行为）
    turns = _web_turns(4)
    events, registry = _run(_collect(turns, general_max=3, web_max=None))
    assert _done(events)["reason"] == "max_web_iterations"
    assert registry.executed.count("web_fetch") == 3


def test_other_round_still_allowed_after_web_capped():
    # 联网 3 轮用满后，通用工具预算还是满的：read_file 照常执行，然后收尾
    turns = _web_turns(3)
    turns.append(("calls", [("read_file", {"path": "/g"})]))
    turns.append(("final", "答"))
    events, registry = _run(_collect(turns, general_max=3, web_max=3))
    assert _done(events)["reason"] == "stop"
    assert registry.executed.count("web_fetch") == 3
    assert registry.executed.count("read_file") == 1


class _SlowProvider:
    """先吐一帧，然后阻塞在 Event 上等取消。"""

    def __init__(self):
        self.gate = asyncio.Event()

    async def stream_chat(self, messages, tool_specs, **kwargs):
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "半截")
        await self.gate.wait()


def test_cancel_midstream_persists_partial_reply():
    async def body():
        provider = _SlowProvider()
        history = [Message(role="user", content="hi")]

        async def consume():
            async for _ev, _d in run_agent_loop(
                history, provider, _FakeRegistry(),
                ToolSettings(enabled=True, max_iterations=3),
                system_prompt="",
            ):
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return history

    history = _run(body())
    assert history[-1].role == "assistant"
    assert history[-1].content == "半截"


def test_cancel_before_any_content_adds_nothing():
    class _BlockedProvider:
        def __init__(self):
            self.gate = asyncio.Event()

        async def stream_chat(self, messages, tool_specs, **kwargs):
            await self.gate.wait()  # 永远到不了 yield
            yield  # pragma: no cover

    async def body():
        provider = _BlockedProvider()
        history = [Message(role="user", content="hi")]

        async def consume():
            async for _ev, _d in run_agent_loop(
                history, provider, _FakeRegistry(),
                ToolSettings(enabled=True, max_iterations=3),
                system_prompt="",
            ):
                pass

        # 立即取消（可能在 provider 产出第一帧之前）
        task = asyncio.ensure_future(consume())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return history

    history = _run(body())
    assert all(m.role != "assistant" for m in history)


def test_web_search_and_web_fetch_share_web_budget():
    # web_search 与 web_fetch 共用联网预算
    turns = [
        ("calls", [("web_search", {"query": "q1"})]),
        ("calls", [("web_fetch", {"url": "http://a"})]),
        ("calls", [("web_search", {"query": "q2"})]),
        ("calls", [("web_fetch", {"url": "http://b"})]),
        ("calls", [("web_fetch", {"url": "http://c"})]),
    ]
    events, registry = _run(_collect(turns, general_max=3, web_max=4))
    assert _done(events)["reason"] == "max_web_iterations"
    assert registry.executed.count("web_search") == 2
    assert registry.executed.count("web_fetch") == 2
