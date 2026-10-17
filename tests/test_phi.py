"""角色卡 PHI（post_history_instructions）：每轮追加在历史之后、生成之前。

工具续写轮顺序为 历史 → PHI → 续写 nudge；PHI 只存在于请求，不写入历史。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import _POST_TOOL_NUDGE, run_agent_loop
from flyai.config import ToolSettings
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.tools.base import ToolResult

PHI = "每轮回复前自检：保持角色语气，不得透露角色未得知的信息。"
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


class _PlainProvider:
    """无工具轮：一轮直接出正文。"""

    def __init__(self):
        self.received = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.received.append(list(messages))
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "你好")


class _FakeRegistry:
    def set_timeout(self, seconds):
        pass

    def specs(self, allowed=None):
        return []

    async def execute(self, call, ctx):
        return ToolResult(output="[属性已更新]")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _go_tool(phi):
    history = [
        Message(role="user", content="你好"),
        Message(role="assistant", content="你好呀"),
        Message(role="user", content="走进房间"),
    ]
    provider = _DiagProvider()
    out = []
    async for ev, data in run_agent_loop(
        history, provider, _FakeRegistry(),
        ToolSettings(enabled=True, max_iterations=5),
        system_prompt="sys",
        actor=SEAT,
        keep_turns=2,
        post_history_instructions=phi,
    ):
        out.append((ev, data))
    return provider, history, out


async def _go_plain(phi):
    history = [Message(role="user", content="在吗")]
    provider = _PlainProvider()
    async for _ev, _data in run_agent_loop(
        history, provider, _FakeRegistry(),
        ToolSettings(enabled=False),
        system_prompt="sys",
        actor=SEAT,
        post_history_instructions=phi,
    ):
        pass
    return provider, history


def test_phi_appended_each_round_and_ordered_before_nudge():
    provider, history, out = _run(_go_tool(PHI))

    first = provider.received[0]
    # 第一次请求：PHI 紧贴末尾（此时还没有续写 nudge）
    assert first[-1].role == "system"
    assert first[-1].content == PHI

    second = provider.received[1]
    # 第二次请求：历史 → PHI → nudge，nudge 必须是最后一句
    assert second[-2].role == "system" and second[-2].content == PHI
    assert second[-1].role == "system" and second[-1].content == _POST_TOOL_NUDGE
    # 历史与工具结果原样保留在 PHI 之前（紧接 PHI 的是本轮工具结果）
    assert second[-3].role == "tool"
    user_msgs = [m for m in second if m.role == "user"]
    assert any("走进房间" in (m.content or "") for m in user_msgs)

    # PHI 只用于请求，不落 history
    assert all(getattr(m, "content", None) != PHI for m in history)
    assert [ev for ev, _ in out if ev == "done"]


def test_phi_plain_round_is_last_message():
    provider, history = _run(_go_plain(PHI))
    req = provider.received[0]
    assert req[-1].role == "system" and req[-1].content == PHI
    assert all(m.content != PHI for m in history)


def test_phi_blank_omitted():
    provider, history = _run(_go_plain("   \n\t "))
    req = provider.received[0]
    # 只有头部的 system_prompt，没有追加末位 system
    assert [m.role for m in req].count("system") == 1
    assert req[0].content == "sys"
    assert req[-1].role == "user"
