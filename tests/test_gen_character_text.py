"""从文本经 AI 解析角色卡：JSON 规范化 + 路由校验。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.providers.base import StreamEvent, StreamEventType
from flyai.lore.gen import MAX_CHAR_SOURCE_CHARS, parse_character_from_text


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _ScriptedProvider:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls.append(list(messages))
        yield StreamEvent(StreamEventType.CONTENT_DELTA, self._reply)


def _state(reply=None, provider=True):
    p = _ScriptedProvider(reply) if provider else None
    return SimpleNamespace(get_provider=lambda: p), p


_GOOD_REPLY = """```json
{
  "name": "  艾莉 ",
  "intro": "银发的魔法使。",
  "background": "出生于边境小镇。",
  "first_mes": "{{char}}抬眼看向{{user}}。",
  "mes_example": "{{user}}：早<START>{{char}}：早呀",
  "post_history_instructions": "保持冷静语气。",
  "system_prompt": "不该被采纳的字段",
  "table": [
    {"title": "魔术", "content": "擅长无吟唱"},
    {"title": "", "content": "无标题也保留"},
    {"content": "   "},
    "坏条目"
  ],
  "attributes": [
    {"key": "favor", "label": "好感度", "initial": 0, "min": -100, "max": 100},
    {"key": "", "label": "缺 key 丢弃"}
  ]
}
```"""


def test_parse_success_normalizes_fields():
    state, p = _state(_GOOD_REPLY)
    card = _run(parse_character_from_text(state, "一段角色文本"))

    assert card["name"] == "艾莉"  # 首尾空白被剥
    assert card["intro"] == "银发的魔法使。"
    assert card["background"] == "出生于边境小镇。"
    assert "{{char}}抬眼" in card["first_mes"]
    assert "<START>" in card["mes_example"]
    assert card["post_history_instructions"] == "保持冷静语气。"
    # 未纳入自动填充的字段不存在
    assert "system_prompt" not in card
    # 内容表：空内容/非字典丢弃，空标题兜底
    assert card["table"] == [
        {"title": "魔术", "content": "擅长无吟唱"},
        {"title": "(无标题)", "content": "无标题也保留"},
    ]
    # 属性规范化
    assert card["attributes"] == [
        {"key": "favor", "label": "好感度", "initial": 0.0, "min": -100.0, "max": 100.0}
    ]
    # 原文确实随请求送给了模型
    assert any("一段角色文本" in (m.content or "") for m in p.calls[0])


def test_parse_missing_name_raises():
    state, _ = _state('{"intro": "没有名字", "table": []}')
    with pytest.raises(ValueError, match="名称"):
        _run(parse_character_from_text(state, "文本"))


def test_parse_non_object_raises():
    state, _ = _state('["不是", "对象"]')
    with pytest.raises(ValueError):
        _run(parse_character_from_text(state, "文本"))


def test_parse_bad_json_raises():
    state, _ = _state("模型胡说八道")
    with pytest.raises(ValueError):
        _run(parse_character_from_text(state, "文本"))


def test_parse_without_provider():
    state, _ = _state(provider=False)
    with pytest.raises(RuntimeError, match="提供商"):
        _run(parse_character_from_text(state, "文本"))


# ---- 路由 ----

def _request(state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_route_rejects_blank_and_oversize():
    from flyai.api.routes_gen import CharacterFromTextRequest, gen_character_from_text

    state, _ = _state(_GOOD_REPLY)
    req = _request(state)
    with pytest.raises(HTTPException) as ei:
        _run(gen_character_from_text(CharacterFromTextRequest(text="  "), req))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei:
        _run(gen_character_from_text(
            CharacterFromTextRequest(text="x" * (MAX_CHAR_SOURCE_CHARS + 1)), req))
    assert ei.value.status_code == 400


def test_route_no_provider_is_400():
    from flyai.api.routes_gen import CharacterFromTextRequest, gen_character_from_text

    state, _ = _state(provider=False)
    with pytest.raises(HTTPException) as ei:
        _run(gen_character_from_text(
            CharacterFromTextRequest(text="文本"), _request(state)))
    assert ei.value.status_code == 400


def test_route_success_returns_character():
    from flyai.api.routes_gen import CharacterFromTextRequest, gen_character_from_text

    state, _ = _state(_GOOD_REPLY)
    out = _run(gen_character_from_text(
        CharacterFromTextRequest(text="文本"), _request(state)))
    assert out["character"]["name"] == "艾莉"
