"""API Key 守卫：空 key 必须转成可读的 ProviderError。

否则 OpenAI 兼容请求会拼出 ``Authorization: Bearer ``，httpx 直接抛
``LocalProtocolError: Illegal header value b'Bearer '``，聊天流异常中断。
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


def test_empty_key_raises_friendly_error():
    with pytest.raises(ProviderError) as ei:
        _guard("")
    assert ei.value.status_code == 400
    assert "API Key" in str(ei.value)


def test_blank_key_raises_friendly_error():
    with pytest.raises(ProviderError) as ei:
        _guard("   ")
    assert ei.value.status_code == 400


def test_non_ascii_key_still_rejected():
    with pytest.raises(ProviderError) as ei:
        _guard("sk-密钥")
    assert ei.value.status_code == 400


def test_ascii_key_passes():
    _guard("sk-abc-123")