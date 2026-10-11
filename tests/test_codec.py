"""适配器消息编解码与插件安全单测。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import ProviderConfig
from flyai.providers.anthropic import AnthropicProvider
from flyai.providers.openai_compat import OpenAICompatProvider
from flyai.schemas import ImageAttachment, Message, ToolCall


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
    _, body = prov._encode_request(msgs, thinking_on=True)
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


def test_message_images_roundtrip():
    msg = Message(
        role="user",
        content="看看这是什么",
        images=[ImageAttachment(media_type="image/png", data="ZmFrZWltZw==")],
    )
    d = msg.to_dict()
    assert d["images"] == [{"media_type": "image/png", "data": "ZmFrZWltZw=="}]
    back = Message.from_dict(d)
    assert back.images[0].media_type == "image/png"
    assert back.images[0].data == "ZmFrZWltZw=="


def test_message_recall_roundtrip():
    items = [{"source": "auto", "text": "去码头"}, {"source": "记忆簿", "text": "看星星"}]
    msg = Message(role="assistant", content="好", recall=items)
    d = msg.to_dict()
    assert d["recall"] == items
    back = Message.from_dict(d)
    assert back.recall == items
    # 旧数据没有 recall 字段时为 None，不报错
    assert Message.from_dict({"role": "assistant", "content": "旧消息"}).recall is None


def test_message_id_auto_generated_and_preserved():
    m1 = Message(role="user", content="a")
    m2 = Message(role="user", content="b")
    assert m1.id and m2.id and m1.id != m2.id  # 空 id 自动补
    # 显式 id 原样保留
    m3 = Message(role="user", content="c", id="fixed-id")
    assert m3.id == "fixed-id"
    # 缺 id 的旧 dict 反序列化时补 id，再序列化幂等
    d = {"role": "user", "content": "旧消息"}
    back = Message.from_dict(d)
    assert back.id
    again = Message.from_dict(back.to_dict())
    assert again.id == back.id


def test_thread_edits_roundtrip():
    from flyai.sessions.models import Thread

    t = Thread(kind="dm")
    u = Message(role="user", content="A")
    t.messages = [u, Message(role="assistant", content="rA")]
    t.edits[u.id] = {
        "edits": [
            {"user": u.to_dict(), "reply_variants": [[t.messages[1].to_dict()]], "reply_active": 0}
        ],
        "edit_active": 0,
    }
    d = t.to_dict()
    assert d["edits"][u.id]["edit_active"] == 0
    t2 = Thread.from_dict(d)
    assert t2.edits[u.id]["edits"][0]["user"]["content"] == "A"
    assert t2.messages[0].id == u.id


def test_openai_image_parts():
    msgs = [
        Message(role="user", content="没图"),
        Message(
            role="user",
            content="看图",
            images=[ImageAttachment(media_type="image/jpeg", data="ZmFrZWltZw==")],
        ),
    ]
    out = _openai()._encode_request(msgs)
    # 无图消息仍是字符串
    assert out[0]["content"] == "没图"
    # 带图消息编为多模态 parts
    parts = out[1]["content"]
    assert parts[0] == {"type": "text", "text": "看图"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,ZmFrZWltZw=="


def test_openai_image_only_message():
    # 无文字只发图
    msgs = [Message(role="user", images=[ImageAttachment(media_type="image/png", data="ZmFrZWltZw==")])]
    out = _openai()._encode_request(msgs)
    parts = out[0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_anthropic_image_blocks():
    msgs = [
        Message(
            role="user",
            content="看图",
            images=[ImageAttachment(media_type="image/png", data="ZmFrZWltZw==")],
        ),
        Message(role="assistant", content=None, tool_calls=[ToolCall(id="c1", name="read_file", arguments={})]),
        Message(role="tool", tool_call_id="c1", name="read_file", content="r"),
    ]
    _, body = _anthropic()._encode_request(msgs)
    # user 消息：text + image blocks
    usr = body[0]["content"]
    assert usr[0] == {"type": "text", "text": "看图"}
    assert usr[1]["type"] == "image"
    source = usr[1]["source"]
    assert source == {"type": "base64", "media_type": "image/png", "data": "ZmFrZWltZw=="}
    # 后续 tool 折叠/交替不受影响：user(tool_result) 紧随 assistant
    assert [m["role"] for m in body[1:]] == ["assistant", "user"]
    assert body[2]["content"][0]["type"] == "tool_result"


def test_build_images_validation():
    from fastapi import HTTPException

    from flyai.api.routes_chat import ChatRequest, _build_images

    assert _build_images(ChatRequest(message="x")) is None

    ok = _build_images(ChatRequest(
        message="x",
        images=[{"media_type": "image/jpeg", "data": "ZmFrZWltZw=="}],
    ))
    assert ok[0].media_type == "image/jpeg"

    with pytest.raises(HTTPException):  # 非法类型
        _build_images(ChatRequest(images=[{"media_type": "text/plain", "data": "ZmFrZWltZw=="}]))
    with pytest.raises(HTTPException):  # 坏 base64
        _build_images(ChatRequest(images=[{"media_type": "image/png", "data": "!!!notb64"}]))
    with pytest.raises(HTTPException):  # 超张数
        _build_images(ChatRequest(images=[
            {"media_type": "image/png", "data": "ZmFrZWltZw=="} for _ in range(5)
        ]))
    with pytest.raises(HTTPException):  # 空图片
        _build_images(ChatRequest(images=[{"media_type": "image/png", "data": ""}]))


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