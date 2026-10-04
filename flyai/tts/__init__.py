"""豆包语音合成大模型 V3（WebSocket 单向流式）客户端。"""

from __future__ import annotations

from .client import DoubaoTtsClient, TtsResult
from .protocol import (
    FrameInfo,
    TtsError,
    build_finish_frame,
    build_request_frame,
    parse_frame,
)

__all__ = [
    "DoubaoTtsClient",
    "TtsResult",
    "FrameInfo",
    "TtsError",
    "build_finish_frame",
    "build_request_frame",
    "parse_frame",
]
