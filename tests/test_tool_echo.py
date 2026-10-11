# -*- coding: utf-8 -*-
"""厂商把工具调用 JSON 混进 content 时的去噪：识别回声 → CONTENT_REPLACE。"""

import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import run_agent_loop
from flyai.config import ProviderConfig, ToolSettings
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.providers.openai_compat import OpenAICompatProvider, _content_is_tool_echo
from flyai.schemas import Message, ToolCall
from flyai.tools.base import ToolResult


def _call(name="web_search", cid="c1", args=None):
    return ToolCall(id=cid, name=name, arguments=args if args is not None else {"q": "x"})


# ---- 纯函数判定 ----

def test_plain_object_echo_detected():
    text = '{"name": "web_search", "arguments": {"q": "x"}}'
    assert _content_is_tool_echo(text, [_call()]) is True


def test_fenced_json_echo_detected():
    text = '```json\n{"name": "web_search", "arguments": {"q": "x"}}\n```'
    assert _content_is_tool_echo(text, [_call()]) is True


def test_tool_call_tag_echo_detected():
    text = '<tool_call>{"name": "web_search", "arguments": {"q": "x"}}</tool_call>'
    assert _content_is_tool_echo(text, [_call()]) is True


def test_stringified_arguments_echo_detected():
    text = '{"name": "web_search", "arguments": "{\\"q\\": \\"x\\"}"}'
    assert _content_is_tool_echo(text, [_call()]) is True


def test_bare_arguments_object_single_call():
    assert _content_is_tool_echo('{"q": "x"}', [_call()]) is True


def test_array_echo_multiple_calls():
    text = json.dumps([
        {"name": "query_lore", "arguments": {"kind": "character"}},
        {"name": "query_lore", "arguments": {"kind": "world"}},
    ], ensure_ascii=False)
    calls = [
        _call("query_lore", "a", {"kind": "character"}),
        _call("query_lore", "b", {"kind": "world"}),
    ]
    assert _content_is_tool_echo(text, calls) is True


def test_normal_prose_not_flagged():
    assert _content_is_tool_echo("好的，我来帮你查一下。", [_call()]) is False


def test_prose_around_json_not_flagged():
    text = '我先调用工具：\n{"name": "web_search", "arguments": {"q": "x"}}\n然后总结'
    assert _content_is_tool_echo(text, [_call()]) is False


def test_mismatched_arguments_not_flagged():
    text = '{"name": "web_search", "arguments": {"q": "完全不同的参数"}}'
    assert _content_is_tool_echo(text, [_call()]) is False


def test_mismatched_name_not_flagged():
    text = '{"name": "generate_image", "arguments": {"q": "x"}}'
    assert _content_is_tool_echo(text, [_call()]) is False


def test_empty_content_and_no_calls():
    assert _content_is_tool_echo("", [_call()]) is False
    assert _content_is_tool_echo('{"name": "x"}', []) is False


# ---- 流式解析：回声轮下发 CONTENT_REPLACE，正常轮不下发 ----

def _sse_provider(lines):
    prov = OpenAICompatProvider(
        ProviderConfig(
            id="x", type="openai", base_url="http://t/v1", api_key="k", model="m"
        )
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, content="".join(lines))

    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return prov


def _line(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _drain(gen):
    async def go():
        out = []
        async for ev in gen:
            out.append(ev)
        return out
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def test_stream_emits_content_replace_for_echo():
    lines = [
        _line({"choices": [{"delta": {"content": '{"name": "web_search",'}}]}),
        _line({"choices": [{"delta": {"content": ' "arguments": {"q": "x"}}'}}]}),
        _line({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1",
             "function": {"name": "web_search", "arguments": '{"q": "x"}'}},
        ]}}]}),
        _line({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]\n\n",
    ]
    prov = _sse_provider(lines)
    events = _drain(prov._run_stream(
        "http://t/v1/chat/completions",
        {"model": "m", "stream": True, "messages": []},
        {}, False,
    ))
    types = [e.type for e in events]
    assert types.count(StreamEventType.CONTENT_DELTA) == 2  # 回声先逐字推送
    assert StreamEventType.CONTENT_REPLACE in types
    replace = next(e for e in events if e.type == StreamEventType.CONTENT_REPLACE)
    assert replace.text == ""
    calls_ev = next(e for e in events if e.type == StreamEventType.TOOL_CALLS)
    assert calls_ev.tool_calls[0].name == "web_search"
    # CONTENT_REPLACE 必须在 TOOL_CALLS 之前，前端才能在卡片出现时清掉回声
    assert types.index(StreamEventType.CONTENT_REPLACE) < types.index(StreamEventType.TOOL_CALLS)


def test_stream_no_replace_for_clean_response():
    lines = [
        _line({"choices": [{"delta": {"content": "这是正文"}}]}),
        _line({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]\n\n",
    ]
    prov = _sse_provider(lines)
    events = _drain(prov._run_stream(
        "http://t/v1/chat/completions",
        {"model": "m", "stream": True, "messages": []},
        {}, False,
    ))
    assert all(e.type != StreamEventType.CONTENT_REPLACE for e in events)
    assert any(e.type == StreamEventType.CONTENT_DELTA for e in events)


# ---- agent loop：回声被替换后落库的 assistant 消息正文为空 ----

class _FakeRegistry:
    def __init__(self):
        self.executed = 0

    def set_timeout(self, seconds):
        pass

    def specs(self, allowed=None):
        return [{
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "搜索",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }]

    async def execute(self, call, ctx):
        self.executed += 1
        return ToolResult(ok=True, output="结果")


class _ScriptedProvider:
    """第一轮：content 回声 + 工具调用；第二轮：正常最终回复。"""

    def __init__(self):
        self.calls=0

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(StreamEventType.CONTENT_DELTA,
                              text='{"name": "web_search", "arguments": {"q": "x"}}')
            yield StreamEvent(StreamEventType.CONTENT_REPLACE, text="")
            yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=[_call()])
        else:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, text="最终答案")


def test_loop_persists_replaced_content_as_none():
    history = [Message(role="user", content="查一下 x")]
    provider = _ScriptedProvider()
    registry = _FakeRegistry()
    settings = ToolSettings(enabled=True, max_iterations=5, timeout_seconds=10,
                            allowed_read_dirs=[])

    async def go():
        out = []
        async for ev, data in run_agent_loop(
            history, provider, registry, settings,
            system_prompt="",
        ):
            out.append((ev, data))
        return out

    loop = asyncio.new_event_loop()
    try:
        events = loop.run_until_complete(go())
    finally:
        loop.close()

    assert any(ev == "content_replace" for ev, _ in events)
    # 第一条 assistant（工具轮）：回声被清空，只剩 tool_calls
    first_assistant = next(m for m in history if m.role == "assistant")
    assert first_assistant.content is None
    assert first_assistant.tool_calls and first_assistant.tool_calls[0].name == "web_search"
    # 最终回复正常落库
    assert history[-1].role == "assistant"
    assert history[-1].content == "最终答案"
