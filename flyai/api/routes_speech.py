"""朗读与语音识别接口：前端改走后端，由管理员统一配置 DeepSeek Key。

- POST /api/speech/tts：文本 → 裸 PCM 流（16bit 单声道）。
  长文本按计费字符分块请求上游，DeepSeek 优先，失败回退豆包；
  每一块合成成功即按字符单价计费。
- POST /api/speech/asr：录音 → 文本，走 DeepSeek ASR，按实际时长计费。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..deepseek_tools import DeepSeekToolsClient, usable
from ..errors import DeepSeekError
from ..metering import asr_cost, billable_chars, make_usage_meter, tts_cost
from ..tts import DoubaoTtsClient
from ..tts.protocol import TtsError
from ..user import ADMIN_UID

logger = logging.getLogger("flyai.speech.api")

router = APIRouter(prefix="/api/speech", tags=["speech"])

TTS_SAMPLE_RATE = 24000
TTS_TEXT_MAX = 50000
# 单块按计费字符（汉字=2）限制在 9000，留出上游 10000 的上限余量
TTS_CHUNK_BILLED = 9000
DOUBAO_CHUNK_CHARS = 1000
ASR_MAX_BYTES = 8 * 1024 * 1024
ASR_MAX_SECONDS = 120

_PUNCT = "。！？!?；;\n"


class SpeechTtsBody(BaseModel):
    text: str
    voice: Optional[str] = None
    format: Optional[str] = None


def _tts_price(ds) -> float:
    try:
        return max(0.0, float(getattr(ds, "tts_price", 3.0)))
    except (TypeError, ValueError):
        return 3.0


def _asr_price(ds) -> float:
    try:
        return max(0.0, float(getattr(ds, "asr_price", 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _check_level(sc, tts) -> None:
    if sc.uid == ADMIN_UID:
        return
    required = int(getattr(tts, "min_level", 0) or 0)
    level = int(getattr(sc, "level", 0) or 0)
    if level < required:
        raise HTTPException(
            403,
            "权限等级不足（语音功能需要 Lv%d，当前 Lv%d）" % (required, level),
        )


def _split_text(text: str, limit: int) -> List[str]:
    """按计费字符切块（汉字/全角=2），尽量在句读处断开。"""
    chunks: List[str] = []
    rest = text
    while rest:
        billed = 0
        cut = 0
        last_punct = 0
        for i, ch in enumerate(rest):
            billed += billable_chars(ch)
            if billed > limit:
                break
            cut = i + 1
            if ch in _PUNCT:
                last_punct = cut
        if cut <= 0:
            cut = 1
        elif cut < len(rest) and last_punct >= cut // 2:
            cut = last_punct
        chunk = rest[:cut].strip()
        rest = rest[cut:].strip()
        if chunk:
            chunks.append(chunk)
    return chunks or [text]


async def _deepseek_chunk(ds, text: str, voice: str) -> bytes:
    return await asyncio.wait_for(
        DeepSeekToolsClient(ds).tts(text, voice, "pcm"),
        timeout=float(getattr(ds, "timeout_seconds", 60) or 60),
    )


async def _doubao_chunk(tts, text: str, speaker: str) -> bytes:
    result = await asyncio.wait_for(
        DoubaoTtsClient(tts).synthesize(text, speaker),
        timeout=tts.timeout_seconds,
    )
    return result.audio


@router.post("/tts")
async def speech_tts(body: SpeechTtsBody, request: Request):
    """朗读：返回裸 PCM 流；长文本分块合成，逐块输出。"""
    state = request.app.state.flyai
    sc = state.user(request)
    cfg = state.config
    tts = cfg.tts
    if not getattr(tts, "enabled", True):
        raise HTTPException(400, "语音合成未启用")
    _check_level(sc, tts)

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "文本为空")
    if len(text) > TTS_TEXT_MAX:
        raise HTTPException(400, "文本过长（超过 %d 字）" % TTS_TEXT_MAX)

    ds = getattr(cfg, "deepseek", None)
    meter = make_usage_meter(state, sc)
    price = _tts_price(ds)
    wanted = (body.voice or "").strip()
    if tts_cost(billable_chars(text), price) > 0 and not meter.available():
        raise HTTPException(402, "余额不足，请充值")

    chunks = _split_text(text, TTS_CHUNK_BILLED)
    prefer_ds = getattr(tts, "engine", "deepseek") == "deepseek"
    ds_ok = prefer_ds and usable(ds, "tts")
    ds_note = ""
    if prefer_ds and not ds_ok:
        ds_note = "DeepSeek 未启用或未配置 API Key"
    engine = ""
    pcm_tts: Any = None
    first: bytes = b""

    if ds_ok:
        default_voice = getattr(ds, "tts_voice", "stella")
        speaker = wanted if wanted in ("stella", "tide") else (
            default_voice if default_voice in ("stella", "tide") else "stella"
        )
        try:
            first = await _deepseek_chunk(ds, chunks[0], speaker)
            engine = "deepseek"
        except (DeepSeekError, asyncio.TimeoutError) as exc:
            logger.warning("朗读 DeepSeek TTS 失败，回退豆包: %s", exc)
            ds_note = "DeepSeek 调用失败：%s" % (str(exc) or type(exc).__name__)

    if not engine:
        # 回退豆包：请求 PCM，按 ≤1000 字切块；只有这条路要求豆包 Key。
        # 失败原因里带上 DeepSeek 的失败信息，管理员据此判断该修哪个 Key。
        if not (tts.api_key or "").strip():
            raise HTTPException(
                400,
                "语音合成未配置：未配置豆包语音 API Key"
                + ("（%s）" % ds_note if ds_note else ""),
            )
        speaker = wanted or (tts.default_speaker or "").strip()
        if not speaker:
            raise HTTPException(400, "未配置默认音色（default_speaker）")
        chunks = _split_text(text, DOUBAO_CHUNK_CHARS)
        pcm_tts = tts.model_copy(update={"format": "pcm"})
        try:
            first = await _doubao_chunk(pcm_tts, chunks[0], speaker)
        except asyncio.TimeoutError:
            raise HTTPException(
                504,
                "语音合成超时" + ("（%s）" % ds_note if ds_note else ""),
            )
        except TtsError as exc:
            raise HTTPException(
                502,
                "语音合成失败：%s" % (exc.message or exc.code)
                + ("（%s）" % ds_note if ds_note else ""),
            )
        except Exception as exc:
            logger.exception("朗读豆包 TTS 调用失败")
            raise HTTPException(
                502,
                "语音合成失败：%s: %s" % (type(exc).__name__, exc)
                + ("（%s）" % ds_note if ds_note else ""),
            )
        engine = "doubao"

    sample_rate = TTS_SAMPLE_RATE if engine == "deepseek" else int(tts.sample_rate)
    ds_client = ds
    price_per_char = price

    async def stream():
        for idx, chunk in enumerate(chunks):
            if idx == 0:
                audio = first
            else:
                try:
                    if engine == "deepseek":
                        audio = await _deepseek_chunk(ds_client, chunk, speaker)
                    else:
                        audio = await _doubao_chunk(pcm_tts, chunk, speaker)
                except Exception as exc:  # noqa: BLE001 - 已出流只能中断
                    logger.warning(
                        "朗读流中断（已出 %d/%d 块）: %s: %s",
                        idx, len(chunks), type(exc).__name__, exc,
                    )
                    return
            meter.charge(
                "tts",
                billable_chars(chunk),
                "字符",
                tts_cost(billable_chars(chunk), price_per_char),
                engine,
            )
            yield audio

    logger.info(
        "朗读开始: engine=%s chunks=%d chars=%d", engine, len(chunks), len(text)
    )
    return StreamingResponse(
        stream(),
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(sample_rate),
            "Cache-Control": "no-store",
        },
    )


@router.post("/asr")
async def speech_asr(request: Request, file: UploadFile = File(...)):
    """语音识别：multipart 上传录音，返回文本与时长。"""
    state = request.app.state.flyai
    sc = state.user(request)
    cfg = state.config
    tts = cfg.tts
    if not getattr(tts, "enabled", True):
        raise HTTPException(400, "语音功能未启用")
    _check_level(sc, tts)

    ds = getattr(cfg, "deepseek", None)
    if not usable(ds, "asr"):
        raise HTTPException(400, "语音识别未配置，请联系管理员")

    data = await file.read()
    if not data:
        raise HTTPException(400, "录音文件为空")
    if len(data) > ASR_MAX_BYTES:
        raise HTTPException(400, "录音文件过大（超过 8MB）")

    meter = make_usage_meter(state, sc)
    price = _asr_price(ds)
    if price > 0 and not meter.available():
        raise HTTPException(402, "余额不足，请充值")

    filename = file.filename or "audio.wav"
    content_type = file.content_type or "audio/wav"
    try:
        result = await DeepSeekToolsClient(ds).asr(data, filename, content_type)
    except DeepSeekError as exc:
        logger.warning("语音识别失败: %s", exc)
        raise HTTPException(502, "语音识别失败：%s" % exc)
    except Exception as exc:  # noqa: BLE001 - 上游传输层异常
        logger.exception("语音识别调用失败")
        raise HTTPException(
            502, "语音识别失败：%s: %s" % (type(exc).__name__, exc)
        )

    duration_ms = int(result.get("duration_ms") or 0)
    if duration_ms > ASR_MAX_SECONDS * 1000:
        raise HTTPException(400, "录音过长（超过 %d 秒）" % ASR_MAX_SECONDS)
    seconds = int(math.ceil(duration_ms / 1000.0)) if duration_ms > 0 else 0
    meter.charge("asr", seconds, "秒", asr_cost(duration_ms, price), "deepseek")
    return {"text": result.get("text") or "", "duration_ms": duration_ms}