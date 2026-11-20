"""R7 一次性补全 helper 与 AI JSON 解析（纯逻辑）。"""

import os
import sys
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.errors import ProviderError
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.providers.complete import complete
from flyai.schemas import Message
from flyai.lore.gen import _extract_json


class _FakeProvider:
    def __init__(self, events):
        self._events = iter(events)

    async def stream_chat(self, messages, tool_specs, **kwargs):
        for ev in self._events:
            yield ev


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def test_complete_concats_content_ignores_thinking():
    p = _FakeProvider([
        StreamEvent(StreamEventType.THINKING_DELTA, "思考内"),
        StreamEvent(StreamEventType.CONTENT_DELTA, "大家好"),
        StreamEvent(StreamEventType.CONTENT_DELTA, "，世界"),
    ])
    out = _run(complete(p, [Message(role="user", content="hi")], system_prompt="sys"))
    assert out == "大家好，世界"


def test_complete_forwards_max_tokens_and_session_id():
    class _Capture:
        def __init__(self):
            self.kwargs = None

        async def stream_chat(self, messages, tool_specs, **kwargs):
            self.kwargs = dict(kwargs)
            yield StreamEvent(StreamEventType.CONTENT_DELTA, "ok")

    p = _Capture()
    out = _run(complete(p, [Message(role="user", content="hi")],
                        max_tokens=32, session_id="s1"))
    assert out == "ok"
    assert p.kwargs["max_tokens"] == 32
    assert p.kwargs["session_id"] == "s1"


class _TruncatingProvider:
    """模拟思考模型：先吐一段正文，再因 max_tokens 截断报错。"""

    def __init__(self, text="京都"):
        self._text = text

    async def stream_chat(self, messages, tool_specs, **kwargs):
        if self._text:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, self._text)
        raise ProviderError("模型输出达到 token 上限被截断")


def test_complete_partial_ok_keeps_text_before_truncation():
    out = _run(complete(
        _TruncatingProvider(), [Message(role="user", content="hi")],
        max_tokens=512, partial_ok=True,
    ))
    assert out == "京都"


def test_complete_truncation_raises_by_default():
    with pytest.raises(ProviderError):
        _run(complete(
            _TruncatingProvider(), [Message(role="user", content="hi")],
            max_tokens=512,
        ))


def test_complete_partial_ok_still_raises_without_text():
    with pytest.raises(ProviderError):
        _run(complete(
            _TruncatingProvider(text=""), [Message(role="user", content="hi")],
            max_tokens=512, partial_ok=True,
        ))


def test_extract_json_object():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n[{"key": "x"}]\n```') == [{"key": "x"}]


def test_extract_json_with_prose():
    assert _extract_json('结果如下：{"character": {"name": "A"}}。') == {"character": {"name": "A"}}


def test_extract_json_garbage_raises():
    with pytest.raises(ValueError):
        _extract_json("完全不是 JSON 的内容")