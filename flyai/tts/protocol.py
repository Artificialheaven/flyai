"""豆包语音 V3 WebSocket 二进制帧编解码（大端）。

帧布局（至少 4 字节可变 header + payload size + payload）：

- Byte0：高 4 位协议版本（恒 1），低 4 位 header 尺寸（以 4 字节计，恒 1）
- Byte1：高 4 位消息类型，低 4 位类型相关标志
  * 客户端：sendText=0x10（类型 1/标志 0），FinishConnection=0x14（类型 1/标志 4）
  * 服务端：音频帧类型 0xB、其他帧类型 0x9，低 4 位恒 0x4（携带 event number）
  * 错误帧：整字节 0xF0
- Byte2：高 4 位序列化方式（0=Raw，1=JSON），低 4 位压缩方式（0=无，1=gzip）
- Byte3：保留
- 服务端正常帧随后：event(u32)、session_id(u32 长度 + utf-8)、payload(u32 长度 + payload)
"""

from __future__ import annotations

import gzip
import json
import struct
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Byte0：版本 v1 + 4 字节 header
BYTE0 = 0x11
# Byte1
CLIENT_SEND_TEXT = 0x10
CLIENT_FINISH = 0x14
SERVER_MASK = 0xF0
SERVER_AUDIO_TYPE = 0xB
SERVER_OTHER_TYPE = 0x9
SERVER_FLAGS = 0x4
ERROR_FRAME = 0xF0
# Byte2：高 4 位序列化
SER_RAW = 0x0
SER_JSON = 0x1
# 低 4 位压缩
COMP_NONE = 0x0
COMP_GZIP = 0x1
BYTE3 = 0x00


class TtsError(Exception):
    """TTS 协议/服务错误。code 为服务端错误码（如 45000000）。"""

    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        payload: Optional[dict] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload


@dataclass
class FrameInfo:
    """解析后的一帧。"""

    event: int
    session_id: str
    payload: bytes
    is_audio: bool
    data: Optional[dict]


def build_request_frame(payload_obj: dict) -> bytes:
    """打包一帧客户端请求：JSON 序列化、无压缩。"""
    body = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")
    return (
        bytes([BYTE0, CLIENT_SEND_TEXT, (SER_JSON << 4) | COMP_NONE, BYTE3])
        + struct.pack(">I", len(body))
        + body
    )


def build_finish_frame() -> bytes:
    """打包 FinishConnection 帧：payload 为空 JSON（文档约定 ``{}``）。"""
    body = b"{}"
    return (
        bytes([BYTE0, CLIENT_FINISH, (SER_JSON << 4) | COMP_NONE, BYTE3])
        + struct.pack(">I", len(body))
        + body
    )


def parse_frame(raw: bytes) -> FrameInfo:
    """解析一帧服务端响应。错误帧直接抛 :class:`TtsError`。"""
    if len(raw) < 8:
        raise TtsError("帧长度非法: 不足 8 字节")

    b1 = raw[1]
    if b1 == ERROR_FRAME:
        # 错误帧：头 4B + 错误码 u32 + payload 长度 u32 + payload（实测确认）
        if len(raw) < 12:
            raise TtsError("错误帧长度非法: 不足 12 字节")
        code = struct.unpack(">I", raw[4:8])[0]
        payload_len = struct.unpack(">I", raw[8:12])[0]
        payload = raw[12:12 + payload_len]
        parsed = _try_json(payload)
        message = ""
        if isinstance(parsed, dict):
            message = str(
                parsed.get("message") or parsed.get("Message") or parsed.get("error") or ""
            )
        if not message:
            message = payload.decode("utf-8", "ignore")
        raise TtsError(message or "服务端返回错误帧", code=code, payload=parsed)

    msg_type = (b1 & SERVER_MASK) >> 4
    flags = b1 & 0x0F
    b2 = raw[2]
    serial = (b2 & 0xF0) >> 4
    compression = b2 & 0x0F

    pos = 4
    event = -1
    if flags & SERVER_FLAGS:
        event = _u32(raw, pos)
        pos += 4

    sid_len = _u32(raw, pos)
    pos += 4
    session_id = raw[pos:pos + sid_len].decode("utf-8", "ignore")
    pos += sid_len

    payload_len = _u32(raw, pos)
    pos += 4
    payload = raw[pos:pos + payload_len]

    if compression == COMP_GZIP:
        try:
            payload = gzip.decompress(payload)
        except OSError as exc:
            raise TtsError("gzip 解压失败", code=event) from exc

    is_audio = msg_type == SERVER_AUDIO_TYPE
    data: Optional[Dict[str, Any]] = None
    if serial == SER_JSON:
        parsed = _try_json(payload)
        if isinstance(parsed, dict):
            data = parsed
    return FrameInfo(
        event=event,
        session_id=session_id,
        payload=payload,
        is_audio=is_audio,
        data=data,
    )


def _u32(buf: bytes, pos: int) -> int:
    return struct.unpack(">I", buf[pos:pos + 4])[0]


def _try_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
