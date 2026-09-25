"""提供商抽象层基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import AsyncIterator, List, Optional

import httpx

from ..config import ProviderConfig
from ..errors import ProviderError
from ..schemas import Message, ToolCall


class StreamEventType(str, Enum):
    THINKING_DELTA = "thinking_delta"
    CONTENT_DELTA = "content_delta"
    TOOL_CALLS = "tool_calls"


class StreamEvent:
    """适配器产出的统一流事件。"""

    def __init__(self, type: StreamEventType, text: str = "", tool_calls: Optional[List[ToolCall]] = None):
        self.type = type
        self.text = text
        self.tool_calls = tool_calls or []


class BaseProvider(ABC):
    """提供商抽象基类。每个适配器子类实现消息编解码与流式解析。"""

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self._client = self._build_client()

    def _build_client(self) -> httpx.AsyncClient:
        # 流式读取需要放宽 read timeout
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=60.0)
        )

    def _assert_key_ascii(self) -> None:
        """HTTP 头不允许非 ASCII，提前给出清晰错误而非底层崩溃。"""
        if any(ord(c) > 127 for c in self.cfg.api_key):
            raise ProviderError("API Key 含非 ASCII 字符，请在设置中检查", status_code=400)

    async def aclose(self) -> None:
        await self._client.aclose()

    @abstractmethod
    def stream_chat(
        self,
        messages: List[Message],
        tool_specs: List[dict],
        **kwargs,
    ) -> AsyncIterator[StreamEvent]:
        """流式对话，产出统一 StreamEvent 序列（思考/正文/工具调用）。"""
        raise NotImplementedError