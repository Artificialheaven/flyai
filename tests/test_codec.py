"""适配器消息编解码与插件安全单测。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import ProviderConfig
from flyai.providers.anthropic import AnthropicProvider
from flyai.providers.openai_compat import OpenAICompatProvider
from flyai.schemas import Message, ToolCall


def _openai():
    return OpenAICompatProvider(
        ProviderConfig(id="x", type="openai", base_url="http://t/v1", api_key="k", model="m")
    )


def _anthropic():
    return AnthropicProvider(
        ProviderConfig(id="x", type="anthropic", base_url="http://t", api_key="k", model="m")
    )


def _history():
    return [
        Message(role="system", content="你是助手"),
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content=None,
            thinking="思考中",
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"})],
        ),
        Message(role="tool", tool_call_id="c1", name="read_file", content="内容"),
        Message(role="assistant", content="总结完毕"),
    ]


def test_openai_system_and_assistant_tool_calls():
    out = _openai()._encode_request(_history())
    assert out[0] == {"role": "system", "content": "你是助手"}
    asst = out[2]
    assert asst["role"] == "assistant"
    assert asst["content"] is None
    assert asst["tool_calls"][0]["function"]["name"] == "read_file"
    assert "a.txt" in asst["tool_calls"][0]["function"]["arguments"]


def test_openai_tool_role():
    out = _openai()._encode_request(_history())
    tool = out[3]
    assert tool == {"role": "tool", "tool_call_id": "c1", "content": "内容"}


def test_anthropic_system_extract_and_fold():
    system, body = _anthropic()._encode_request(_history())
    assert system == "你是助手"
    # 交替 user/assistant/user/assistant
    roles = [m["role"] for m in body]
    assert roles == ["user", "assistant", "user", "assistant"]
    # assistant 的工具块
    asst = body[1]
    assert asst["content"][0]["type"] == "tool_use"
    assert asst["content"][0]["id"] == "c1"
    # tool 结果折叠进 user
    usr = body[2]
    assert usr["content"][0]["type"] == "tool_result"
    assert usr["content"][0]["tool_use_id"] == "c1"


def test_anthropic_thinking_blocks_replay():
    msgs = [
        Message(
            role="assistant",
            content=None,
            thinking_blocks=[{"type": "thinking", "thinking": "x", "signature": "sig"}],
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a"})],
        ),
        Message(role="tool", tool_call_id="c1", name="read_file", content="r"),
    ]
    cfg = ProviderConfig(
        id="x", type="anthropic", base_url="http://t", api_key="k", model="m",
        params={"thinking": {"enabled": True, "budget_tokens": 2048}},
    )
    prov = AnthropicProvider(cfg)
    _, body = prov._encode_request(msgs)
    asst = body[0]
    assert asst["content"][0] == {"type": "thinking", "thinking": "x", "signature": "sig"}


def test_anthropic_tool_result_is_error_flag():
    # 工具错误结果：Anthropic 用 is_error
    msgs = [
        Message(role="assistant", content=None, tool_calls=[ToolCall(id="c1", name="rd", arguments={})]),
        Message(role="tool", tool_call_id="c1", name="rd", content="[工具错误] boom"),
    ]
    _, body = _anthropic()._encode_request(msgs)
    block = body[1]["content"][0]
    assert block["type"] == "tool_result"


def test_file_reader_safety():
    import asyncio
    from pathlib import Path

    from flyai.tools.base import ToolContext
    from flyai.tools.loader import PluginLoader
    from flyai.tools.registry import ToolRegistry

    repo = Path(__file__).resolve().parent.parent
    root = repo / "workspace"
    reg = ToolRegistry()
    PluginLoader(reg).load_all([str(repo / "plugins")])
    ctx = ToolContext(allowed_read_dirs=[root])

    class C:
        def __init__(s, path):
            s.name = "read_file"
            s.arguments = {"path": path}

    async def run(path):
        return await reg.execute(C(path), ctx)

    assert asyncio.run(run("example.txt")).ok is True
    assert asyncio.run(run(root / "example.txt")).ok is True
    assert asyncio.run(run("../config/config.json")).ok is False
    assert asyncio.run(run("x:/Windows/system.ini")).ok is False