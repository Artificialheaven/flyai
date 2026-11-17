"""opencode 网关会话头：base_url 含 opencode 时给模型调用带上 x-opencode-session。"""

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import ProviderConfig
from flyai.providers.anthropic import AnthropicProvider
from flyai.providers.base import gateway_headers
from flyai.providers.openai_compat import OpenAICompatProvider
from flyai.schemas import Message

HEADER = "x-opencode-session"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _drain(gen):
    async for _ev in gen:
        pass


def _openai_provider(base_url):
    prov = OpenAICompatProvider(
        ProviderConfig(id="x", type="openai", base_url=base_url, api_key="k", model="m")
    )
    captured = {}

    def handler(request: httpx.Request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, content="data: [DONE]\n\n")

    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return prov, captured


def _anthropic_provider(base_url):
    prov = AnthropicProvider(
        ProviderConfig(id="x", type="anthropic", base_url=base_url, api_key="k", model="m")
    )
    captured = {}

    def handler(request: httpx.Request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, content="")

    prov._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return prov, captured


def test_helper_only_for_opencode_base_url():
    assert gateway_headers("https://opencode.ai/v1", "s1") == {HEADER: "s1"}
    assert gateway_headers("https://api.openai.com/v1", "s1") == {}
    assert gateway_headers("", "s1") == {}


def test_helper_case_insensitive_and_uuid_fallback():
    assert gateway_headers("https://API.OpenCode.ai/v1", "s1") == {HEADER: "s1"}
    headers = gateway_headers("https://opencode.ai/v1")
    # 没有会话 ID 的旁路调用也要带头，退化为一次性 ID
    assert headers[HEADER]


def test_openai_compat_sends_session_header_for_opencode():
    prov, captured = _openai_provider("https://opencode.ai/v1")
    _run(_drain(prov.stream_chat(
        [Message(role="user", content="hi")], [], session_id="sess-42"
    )))
    assert captured["headers"].get(HEADER) == "sess-42"
    # 原有鉴权头不受影响
    assert captured["headers"].get("authorization") == "Bearer k"


def test_openai_compat_omits_header_for_regular_provider():
    prov, captured = _openai_provider("https://api.openai.com/v1")
    _run(_drain(prov.stream_chat(
        [Message(role="user", content="hi")], [], session_id="sess-42"
    )))
    assert HEADER not in captured["headers"]


def test_openai_compat_falls_back_to_generated_session():
    prov, captured = _openai_provider("https://opencode.ai/v1")
    _run(_drain(prov.stream_chat([Message(role="user", content="hi")], [])))
    assert captured["headers"].get(HEADER)


def test_anthropic_sends_session_header_for_opencode():
    prov, captured = _anthropic_provider("https://opencode.ai/v1")
    _run(_drain(prov.stream_chat(
        [Message(role="user", content="hi")], [], session_id="sess-7"
    )))
    assert captured["headers"].get(HEADER) == "sess-7"
    assert captured["headers"].get("x-api-key") == "k"


def test_anthropic_omits_header_for_regular_provider():
    prov, captured = _anthropic_provider("https://api.anthropic.com")
    _run(_drain(prov.stream_chat(
        [Message(role="user", content="hi")], [], session_id="sess-7"
    )))
    assert HEADER not in captured["headers"]
