"""提供商抽象层基类。"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import AsyncIterator, Dict, List, Optional

import httpx

from ..config import ProviderConfig
from ..errors import ProviderError
from ..schemas import Message, ToolCall


# opencode zen 网关：思考由 reasoning_effort 控制（low/high/max，部分型号
# 还接受 none）；不显式关闭时会默认输出思考。这里把二元开关映射为
# high / none。显式的 thinking_params / thinking_off_params 仍优先。
OPENCODE_THINKING_ON_EFFORT = "high"
OPENCODE_THINKING_OFF_EFFORT = "none"


def is_opencode_gateway(base_url: str) -> bool:
    """base_url 是否指向 opencode 网关（zen）。"""
    return "opencode" in (base_url or "").lower()


def gateway_headers(base_url: str, session_id: str = "") -> Dict[str, str]:
    """网关类中转（base_url 含 opencode）要求的会话头。

    ``x-opencode-session`` 标识同一段对话，上游据此做会话粘滞；
    没有 FlyAI 会话 ID 的旁路调用（摘要、生图参数生成等）退回一次性 ID。
    """
    if not is_opencode_gateway(base_url):
        return {}
    return {"x-opencode-session": session_id or uuid.uuid4().hex}


class StreamEventType(str, Enum):
    THINKING_DELTA = "thinking_delta"
    CONTENT_DELTA = "content_delta"
    TOOL_CALLS = "tool_calls"
    USAGE = "usage"               # 本次调用 token 用量 {prompt_tokens, completion_tokens}
    # 整段思考块（Anthropic 带 signature，多轮工具调用回放必需）
    THINKING_BLOCKS = "thinking_blocks"
    # 正文整体替换：个别厂商会把工具调用 JSON 同时塞进 content 与 tool_calls，
    # 适配器在流末识别出这种回声后下发本事件（text 为去噪后的正文），
    # 覆盖此前逐字推送、已显示在界面上的回声文本
    CONTENT_REPLACE = "content_replace"


class StreamEvent:
    """适配器产出的统一流事件。"""

    def __init__(
        self,
        type: StreamEventType,
        text: str = "",
        tool_calls: Optional[List[ToolCall]] = None,
        usage: Optional[dict] = None,
        blocks: Optional[List[dict]] = None,
    ):
        self.type = type
        self.text = text
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.blocks = blocks or []


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
        """校验 API Key：空 key 会让 ``Authorization: Bearer `` 触发 httpx
        LocalProtocolError，非 ASCII 同样不是合法头值，提前给出清晰错误。"""
        key = self.cfg.api_key or ""
        if not key.strip():
            raise ProviderError(
                "该模型未配置 API Key，请在「模型设置」中填写后重试",
                status_code=400,
            )
        if any(ord(c) > 127 for c in key):
            raise ProviderError("API Key 含非 ASCII 字符，请在设置中检查", status_code=400)

    def _gateway_headers(self, session_id: str = "") -> Dict[str, str]:
        """按 base_url 判断是否需要网关专属请求头（当前仅 opencode）。"""
        return gateway_headers(self.cfg.base_url, session_id)

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