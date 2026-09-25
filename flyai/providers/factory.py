"""提供商工厂：按 type 分发创建适配器。"""

from __future__ import annotations

from ..config import ProviderConfig
from .anthropic import AnthropicProvider
from .base import BaseProvider
from .openai_compat import OpenAICompatProvider

# 新协议只需在此注册
_TYPES = {
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}


def create_provider(cfg: ProviderConfig) -> BaseProvider:
    cls = _TYPES.get(cfg.type)
    if cls is None:
        raise ValueError(f"不支持的提供商类型: {cfg.type}")
    return cls(cfg)