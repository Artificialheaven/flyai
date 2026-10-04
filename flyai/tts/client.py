"""豆包语音合成 V3 WebSocket 客户端：一次性输入文本，流式收取音频。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import websockets

from .protocol import (
    TtsError,
    build_finish_frame,
    build_request_frame,
    parse_frame,
)

logger = logging.getLogger("flyai.tts")

ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"

# 下行事件
EVENT_SENTENCE_START = 350
EVENT_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352
EVENT_SESSION_FINISHED = 152
# 关闭阶段
EVENT_CONNECTION_FINISHED = 52

STATUS_OK = 20000000


@dataclass
class TtsResult:
    """一次合成的结果。"""

    audio: bytes
    session_id: str
    request_id: str
    logid: str
    usage: Dict[str, Any] = field(default_factory=dict)


class DoubaoTtsClient:
    """按 TtsSettings 发起一次合成；每次调用新建 WebSocket 连接。"""

    def __init__(self, settings: Any):
        # 只存配置，不创建任何 asyncio 对象（Python 3.9 无循环时构造会出错）
        self.settings = settings

    async def synthesize(
        self,
        text: str,
        speaker: Optional[str] = None,
        audio_format: Optional[str] = None,
        sample_rate: Optional[int] = None,
    ) -> TtsResult:
        s = self.settings
        request_id = str(uuid.uuid4())
        speaker = speaker or s.default_speaker
        audio_format = audio_format or s.format
        sample_rate = sample_rate or s.sample_rate

        headers = {
            "X-Api-Key": s.api_key,
            "X-Api-Resource-Id": s.resource_id,
            "X-Api-Request-Id": request_id,
            # 让 SessionFinished 事件携带计费用量
            "X-Control-Require-Usage-Tokens-Return": "*",
        }
        payload = self._build_payload(
            text, speaker, audio_format, sample_rate
        )

        proxy = (s.proxy or "").strip() or None  # None=不读环境变量，强制直连
        chunks = []
        usage: Dict[str, Any] = {}
        session_id = ""
        logid = ""

        async with websockets.connect(
            ENDPOINT,
            additional_headers=headers,
            open_timeout=s.timeout_seconds,
            ping_interval=20,
            max_size=16 * 1024 * 1024,
            proxy=proxy,
        ) as ws:
            response = getattr(ws, "response", None)
            if response is not None:
                logid = response.headers.get("X-Tt-Logid", "") or ""
            await ws.send(build_request_frame(payload))

            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    continue  # 协议规定只有二进制帧；文本帧忽略
                frame = parse_frame(message)
                if frame.session_id:
                    session_id = frame.session_id
                event = frame.event
                if event == EVENT_TTS_RESPONSE:
                    chunks.append(frame.payload)
                elif event == EVENT_SENTENCE_START or event == EVENT_SENTENCE_END:
                    continue
                elif event == EVENT_SESSION_FINISHED:
                    meta = frame.data or {}
                    status = meta.get("status_code")
                    # 只有明确返回非 OK 状态码才算失败；seed-icl-2.0 的结束帧
                    # payload 为 {}、不带 status_code，已收到音频即视为成功
                    if status is not None and status != STATUS_OK:
                        raise TtsError(
                            str(meta.get("message") or "合成未正常完成"),
                            code=status,
                            payload=meta,
                        )
                    usage = meta.get("usage") or {}
                    break
                # 其他事件忽略

            if not chunks:
                raise TtsError("未收到任何音频数据")

            # best-effort 优雅关闭：发 FinishConnection，收 ConnectionFinished
            try:
                await ws.send(build_finish_frame())
                await asyncio.wait_for(ws.recv(), timeout=5)
            except Exception:
                logger.debug("TTS 优雅关闭未完成（不影响结果）", exc_info=True)

        return TtsResult(
            audio=b"".join(chunks),
            session_id=session_id,
            request_id=request_id,
            logid=logid,
            usage=usage,
        )

    def _build_payload(
        self,
        text: str,
        speaker: str,
        audio_format: str,
        sample_rate: int,
    ) -> dict:
        s = self.settings
        audio_params: Dict[str, Any] = {
            "format": audio_format,
            "sample_rate": int(sample_rate),
        }
        if int(s.speech_rate) != 0:
            audio_params["speech_rate"] = int(s.speech_rate)
        if int(s.loudness_rate) != 0:
            audio_params["loudness_rate"] = int(s.loudness_rate)
        return {
            "user": {"uid": "flyai-" + uuid.uuid4().hex[:12]},
            "req_params": {
                "text": text,
                "speaker": speaker,
                "audio_params": audio_params,
            },
        }
