"""工具回合后的续写提示：第二次调用必须收到「工具结果不是用户发言」的系统提示。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import _POST_TOOL_NUDGE, run_agent_loop
from flyai.config import ToolSettings
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.tools.base import ToolResult

SEAT = "seat_1"


class _DiagProvider:
    def __init__(self):
        self.received = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.received.append(list(messages))
        if len(self.received) == 1:
            yield StreamEvent(
                StreamEventType.TOOL_CALLS,
                tool_calls=[ToolCall(id="c1", name="update_attributes",
                                     arguments={"changes": []})],
            )
        else:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, "最终回复")


class _FakeRegistry:
    def set_timeout(self, seconds):
        pass

    def specs(self, allowed=None):
        return []

    async def execute(self, call, ctx):
        return ToolResult(output="[属性已更新] x: 1")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _go():
    history = [
        Message(role="user", content="旧请求"),
        Message(role="assistant", content="旧回答"),
        Message(role="user", content="拉她拐进旁边的小巷，把她按在墙上"),
    ]
    provider = _DiagProvider()
    out = []
    async for ev, data in run_agent_loop(
        history, provider, _FakeRegistry(),
        ToolSettings(enabled=True, max_iterations=5),
        system_prompt="sys",
        actor=SEAT,
        keep_turns=2,
    ):
        out.append((ev, data))
    return provider, history, out


def test_nudge_appended_after_tool_round():
    provider, history, out = _run(_go())

    # 第一次调用：无续写提示
    first = provider.received[0]
    assert all(m.content != _POST_TOOL_NUDGE for m in first)

    # 第二次调用：尾部追加系统提示，且用户消息仍完整存在
    second = provider.received[1]
    assert second[-1].role == "system"
    assert second[-1].content == _POST_TOOL_NUDGE
    user_msgs = [m for m in second if m.role == "user"]
    assert any("拉她拐进旁边的小巷" in (m.content or "") for m in user_msgs)

    # 提示只用于请求，不落 history
    assert all(getattr(m, "content", None) != _POST_TOOL_NUDGE for m in history)

    assert [ev for ev, _ in out if ev == "done"]
