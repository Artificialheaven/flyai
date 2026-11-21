"""深度思考：OpenAI 兼容厂商 thinking_params 合并 + Anthropic thinking 块捕获/回放。"""

import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_chat import ChatRequest
from flyai.config import ProviderConfig
from flyai.providers.anthropic import AnthropicProvider
from flyai.providers.base import StreamEventType
from flyai.providers.openai_compat import OpenAICompatProvider
from flyai.schemas import Message, ToolCall


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _drain(gen):
    async def go():
        out = []
        async for ev in gen:
            out.append(ev)
        return out
    return _run(go())


# ---- OpenAI 兼容：思考参数按开关状态合并 ----

def _openai_provider(thinking_params, thinking_off_params=None,
                     base_url="http://t/v1", params=None):
    kw = {} if thinking_off_params is None else {"thinking_off_params": thinking_off_params}
    prov = OpenAICompatProvider(
        ProviderConfig(
            id="x", type="openai", base_url=base_url, api_key="k",
            model="m", params={"temperature": 0.7} if params is None else params,
            thinking_params=thinking_params,
            **kw,
        )
    )
    captured = {}

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, content="data: [DONE]\n\n")

    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return prov, captured


def test_openai_thinking_params_merged_when_on():
    prov, captured = _openai_provider({"reasoning_effort": "high"})
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=True))
    body = captured["body"]
    assert body["reasoning_effort"] == "high"
    # 基础参数照常保留
    assert body["temperature"] == 0.7


def test_openai_thinking_params_omitted_when_off():
    prov, captured = _openai_provider({"reasoning_effort": "high"})
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=False))
    assert "reasoning_effort" not in captured["body"]
    assert captured["body"]["temperature"] == 0.7


def test_openai_deep_thinking_without_config_is_plain_request():
    prov, captured = _openai_provider({})
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=True))
    assert captured["body"]["temperature"] == 0.7


def test_openai_off_params_merged_when_switch_off():
    # 默认思考的模型：开关关闭时合并 thinking_off_params 显式关闭
    prov, captured = _openai_provider(
        {"thinking": {"type": "enabled"}},
        thinking_off_params={"thinking": {"type": "disabled"}},
    )
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=False))
    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_openai_no_off_params_keeps_model_default():
    prov, captured = _openai_provider({"thinking": {"type": "enabled"}}, thinking_off_params={})
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=False))
    assert "thinking" not in captured["body"]


def test_openai_on_params_override_base_params_key():
    prov, captured = _openai_provider({"thinking": {"type": "enabled"}})
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=True))
    assert captured["body"]["thinking"] == {"type": "enabled"}


# ---- opencode 网关：深度思考开关 → reasoning_effort ----

OPENCODE_URL = "https://opencode.ai/zen/go/v1"


def test_opencode_thinking_on_defaults_to_high():
    prov, captured = _openai_provider({}, base_url=OPENCODE_URL)
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=True))
    assert captured["body"]["reasoning_effort"] == "high"


def test_opencode_thinking_off_defaults_to_none():
    prov, captured = _openai_provider({}, base_url=OPENCODE_URL)
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=False))
    assert captured["body"]["reasoning_effort"] == "none"


def test_opencode_configured_effort_wins_when_on():
    prov, captured = _openai_provider(
        {}, base_url=OPENCODE_URL, params={"reasoning_effort": "low"})
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=True))
    assert captured["body"]["reasoning_effort"] == "low"


def test_opencode_explicit_thinking_params_override_defaults():
    prov, captured = _openai_provider(
        {"reasoning_effort": "max"},
        thinking_off_params={"thinking": {"type": "disabled"}},
        base_url=OPENCODE_URL,
    )
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=True))
    assert captured["body"]["reasoning_effort"] == "max"
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=False))
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in captured["body"]


def test_opencode_rejected_effort_retries_without_param():
    # 少数型号（kimi-k2.7-code）只接受开启思考：none 被拒时退回模型默认
    bodies = []

    def handler(request: httpx.Request):
        body = json.loads(request.content.decode("utf-8"))
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(400, json={
                "error": {"message": "invalid thinking: only type=enabled is allowed"}
            })
        return httpx.Response(200, content="data: [DONE]\n\n")

    prov = OpenAICompatProvider(
        ProviderConfig(
            id="x", type="openai", base_url=OPENCODE_URL, api_key="k",
            model="m", params={},
        )
    )
    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    _drain(prov._stream_chat([Message(role="user", content="hi")], [], deep_thinking=False))
    assert bodies[0]["reasoning_effort"] == "none"
    assert "reasoning_effort" not in bodies[1]


# ---- Anthropic：signature 块组装 ----

class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


_THINKING_TOOL_SSE = [
    "event: content_block_start",
    'data: {"index":0,"content_block":{"type":"thinking","thinking":""}}',
    "",
    "event: content_block_delta",
    'data: {"index":0,"delta":{"type":"thinking_delta","thinking":"先想想"}}',
    "",
    "event: content_block_delta",
    'data: {"index":0,"delta":{"type":"signature_delta","signature":"SIG"}}',
    "",
    "event: content_block_stop",
    'data: {"index":0}',
    "",
    "event: content_block_start",
    'data: {"index":1,"content_block":{"type":"tool_use","id":"t1","name":"web_search"}}',
    "",
    "event: content_block_delta",
    'data: {"index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\":\\"x\\"}"}}',
    "",
    "event: content_block_stop",
    'data: {"index":1}',
    "",
    "event: message_delta",
    'data: {"delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":12}}',
    "",
]


def _anthropic():
    return AnthropicProvider(
        ProviderConfig(id="x", type="anthropic", base_url="http://t", api_key="k", model="m")
    )


def test_anthropic_stream_captures_thinking_blocks():
    prov = _anthropic()
    events = _drain(prov._parse_stream(_FakeResp(_THINKING_TOOL_SSE), thinking_on=True))
    types = [e.type for e in events]
    assert StreamEventType.THINKING_DELTA in types
    assert StreamEventType.THINKING_BLOCKS in types
    blocks_ev = next(e for e in events if e.type == StreamEventType.THINKING_BLOCKS)
    assert blocks_ev.blocks == [
        {"type": "thinking", "thinking": "先想想", "signature": "SIG"}
    ]
    calls_ev = next(e for e in events if e.type == StreamEventType.TOOL_CALLS)
    assert calls_ev.tool_calls[0].name == "web_search"


def test_anthropic_stream_no_blocks_when_thinking_off():
    # 开关关闭：即使流里出现思考事件，也不组装 signature 块（不会回放非法块）
    prov = _anthropic()
    events = _drain(prov._parse_stream(_FakeResp(_THINKING_TOOL_SSE), thinking_on=False))
    assert all(e.type != StreamEventType.THINKING_BLOCKS for e in events)


def test_anthropic_placeholder_for_legacy_tool_history():
    # 开启思考前的历史里有工具调用：补空签名 thinking 占位块，否则 API 400
    msgs = [
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c1", name="web_search", arguments={"q": "a"})],
        ),
        Message(role="tool", tool_call_id="c1", name="web_search", content="r"),
    ]
    prov = _anthropic()
    _, body = prov._encode_request(msgs, thinking_on=True)
    first = body[0]["content"][0]
    assert first == {"type": "thinking", "thinking": "", "signature": ""}
    # 纯文本历史消息不需要占位块
    _, body2 = prov._encode_request(
        [Message(role="assistant", content="你好")], thinking_on=True
    )
    assert body2[0]["content"][0]["type"] == "text"


def test_anthropic_replay_only_when_thinking_on():
    msgs = [
        Message(
            role="assistant",
            content=None,
            thinking_blocks=[{"type": "thinking", "thinking": "x", "signature": "sig"}],
            tool_calls=[ToolCall(id="c1", name="web_search", arguments={"q": "a"})],
        ),
        Message(role="tool", tool_call_id="c1", name="web_search", content="r"),
    ]
    prov = _anthropic()
    _, body_on = prov._encode_request(msgs, thinking_on=True)
    assert body_on[0]["content"][0] == {
        "type": "thinking", "thinking": "x", "signature": "sig"
    }
    _, body_off = prov._encode_request(msgs, thinking_on=False)
    assert body_off[0]["content"][0]["type"] == "tool_use"


def _anthropic_stream_provider(captured):
    prov = _anthropic()

    def handler(request: httpx.Request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        sse = (
            "event: message_start\n"
            'data: {"message":{"usage":{"input_tokens":5}}}\n\n'
            "event: message_delta\n"
            'data: {"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":7}}\n\n'
        )
        return httpx.Response(200, content=sse)

    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return prov


def test_anthropic_request_flag_enables_thinking_body():
    captured = {}
    prov = _anthropic_stream_provider(captured)
    _drain(prov._stream_chat(
        [Message(role="user", content="hi")], [], deep_thinking=True
    ))
    assert captured["body"]["thinking"] == {
        "type": "enabled", "budget_tokens": 2048
    }


def test_anthropic_no_thinking_body_when_off():
    captured = {}
    prov = _anthropic_stream_provider(captured)
    _drain(prov._stream_chat(
        [Message(role="user", content="hi")], [], deep_thinking=False
    ))
    assert "thinking" not in captured["body"]


# ---- 请求契约 ----

def test_chat_request_new_fields_defaults():
    req = ChatRequest(message="hi")
    assert req.deep_thinking is False
    assert req.disabled_tools == []


def test_chat_request_parses_new_fields():
    req = ChatRequest(
        message="hi", deep_thinking=True, disabled_tools=["web_search"]
    )
    assert req.deep_thinking is True
    assert req.disabled_tools == ["web_search"]
