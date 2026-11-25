"""API Key 头值校验：允许留空（部分自建/本地网关不鉴权），
但非 ASCII 不是合法头值，必须提前给出可读错误。

空 key 不再直接报错；各 provider 只有在 key 非空时才发送
Authorization / x-api-key 头，避免 ``Bearer `` 触发 httpx
LocalProtocolError。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.errors import ProviderError
from flyai.providers.base import BaseProvider


def _guard(key):
    # 只测校验方法本身，避免创建 httpx 客户端
    return BaseProvider._assert_key_ascii(
        SimpleNamespace(cfg=SimpleNamespace(api_key=key))
    )


def test_empty_key_allowed():
    _guard("")


def test_blank_key_allowed():
    _guard("   ")


def test_non_ascii_key_still_rejected():
    with pytest.raises(ProviderError) as ei:
        _guard("sk-密钥")
    assert ei.value.status_code == 400


def test_ascii_key_passes():
    _guard("sk-abc-123")


def test_openai_compat_omits_authorization_when_key_empty():
    """空 key 时不应发出 ``Authorization: Bearer ``（httpx 会直接报错）。"""
    import asyncio

    from flyai.config import ProviderConfig
    from flyai.providers.openai_compat import OpenAICompatProvider
    from flyai.schemas import Message

    class Probe(OpenAICompatProvider):
        seen = None

        async def _run_stream(self, url, body, headers, stream_options):
            self.seen = headers
            return
            yield  # pragma: no cover - 让方法保持 async generator

    async def run():
        cfg = ProviderConfig(id="k", base_url="http://127.0.0.1:1/v1",
                             model="m", api_key="")
        provider = Probe(cfg)
        try:
            async for _ in provider.stream_chat(
                    [Message(role="user", content="hi")], []):
                pass
        finally:
            await provider.aclose()
        return provider.seen

    seen = asyncio.run(run())
    assert "Authorization" not in seen
