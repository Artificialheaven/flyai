"""语音合成工具：AI 调用豆包语音大模型把文本朗读为音频。

音频落进用户上传目录（data/uploads/<uid>/），工具 output 首行打标记
``[语音已生成] <url>``，前端据此在工具卡内渲染独立语音条。
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from ..tts import DoubaoTtsClient
from ..tts.protocol import TtsError
from .base import Tool, ToolContext, ToolResult

TEXT_MAX_LEN = 1000

_FORMAT_EXT = {
    "mp3": ".mp3",
    "ogg_opus": ".ogg",
    "pcm": ".wav",
}


class TextToSpeechTool(Tool):
    """把文本合成为语音。用户要求朗读/语音，或 AI 判断有必要时调用。"""

    name = "text_to_speech"
    description = (
        "文本转语音（朗读）。当用户要求语音、朗读、念出来、读一遍、用语音说，"
        "或你判断有必要把内容用语音呈现时调用本工具。\n"
        "- text 放需要朗读的完整原文（不是解释说明），一次调用生成一条语音；\n"
        "- 长文本请分句或分段后多次调用，单次不超过 1000 字；\n"
        "- 调用成功后正文回复从简（如「已经为你朗读啦」），不要重复朗读文本，"
        "不要输出链接或文件路径。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "要朗读的完整文本，1~1000 字",
                "minLength": 1,
                "maxLength": TEXT_MAX_LEN,
            },
            "speaker": {
                "type": "string",
                "description": "音色 ID；不传则使用管理员配置的默认音色",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    def __init__(self, settings: Any):
        self.settings = settings

    async def run(
        self, arguments: Dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        text = str(arguments.get("text") or "").strip()
        if not text:
            return ToolResult(
                ok=False, output="[参数错误] 请提供要朗读的文本（text）"
            )
        if len(text) > TEXT_MAX_LEN:
            return ToolResult(
                ok=False,
                output=(
                    "[参数错误] 朗读文本过长（超过 %d 字），"
                    "请缩短或分段调用" % TEXT_MAX_LEN
                ),
            )

        services = ctx.services or {}
        if "upload_dir" not in services or "upload_url_base" not in services:
            return ToolResult(
                ok=False,
                output="[工具错误] 当前对话没有可用的语音保存目录上下文",
            )

        s = self.settings
        # 权限等级：不足则拒绝（工具仍对 AI 可见，被拒后可向用户解释）
        user_level = int(services.get("user_level") or 0)
        required_level = int(s.min_level or 0)
        if user_level < required_level:
            return ToolResult(
                ok=False,
                output=(
                    "[工具错误] 你的权限等级不足（语音合成需要 Lv%d，"
                    "当前 Lv%d）" % (required_level, user_level)
                ),
            )
        if not (s.api_key or "").strip():
            return ToolResult(
                ok=False,
                output=(
                    "[工具错误] 未配置豆包语音 API Key，"
                    "请管理员在「设置 → 语音合成」中填写"
                ),
            )
        # 音色解析：AI 显式 speaker → 角色卡绑定音色（tts_voice）→ 默认音色
        arg_speaker = str(arguments.get("speaker") or "").strip()
        bound_voice = str(services.get("tts_voice") or "").strip()
        speaker = arg_speaker or bound_voice or (s.default_speaker or "").strip()
        if arg_speaker:
            voice_source = "arg"
        elif bound_voice:
            voice_source = "character"
        else:
            voice_source = "default"
        if not speaker:
            return ToolResult(
                ok=False,
                output="[工具错误] 未配置默认音色（default_speaker）",
            )

        try:
            result = await asyncio.wait_for(
                self._synthesize(text, speaker),
                timeout=s.timeout_seconds,
            )
        except asyncio.TimeoutError:
            ctx.logger.exception("TTS 合成超时")
            return ToolResult(
                ok=False,
                output="[工具错误] 语音合成超时（%d 秒）" % s.timeout_seconds,
            )
        except TtsError as exc:
            ctx.logger.exception("TTS 服务返回错误")
            return ToolResult(ok=False, output=_describe_tts_error(exc))
        except Exception as exc:
            ctx.logger.exception("TTS 调用失败")
            return ToolResult(
                ok=False, output=_describe_transport_error(exc)
            )

        audio_format = (s.format or "mp3").strip()
        ext = _FORMAT_EXT.get(audio_format, ".mp3")
        content = result.audio
        if audio_format == "pcm":
            content = pcm_to_wav(content, int(s.sample_rate))

        audio_meta = {
            "speaker": speaker,
            "voice_source": voice_source,
            "text_len": len(text),
            "format": audio_format,
            "sample_rate": int(s.sample_rate),
        }
        if audio_format == "pcm":
            audio_meta["container"] = "wav"

        saved = save_generated_audio(services, content, ext, audio_meta)
        return ToolResult(output=saved["output"])

    async def _synthesize(self, text: str, speaker: str):
        """实际合成调用；测试时可打桩替换（同 image_tool._generate 模式）。"""
        return await DoubaoTtsClient(self.settings).synthesize(text, speaker)


def pcm_to_wav(
    pcm: bytes,
    sample_rate: int,
    channels: int = 1,
    bits: int = 16,
) -> bytes:
    """给裸 PCM 封 44 字节标准 WAV 头。

    假设豆包返回单声道 16bit PCM（现网规格）；前端因此可直接播放 pcm 结果。
    """
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                      byte_rate, block_align, bits)
        + b"data"
        + struct.pack("<I", data_size)
        + pcm
    )


def save_generated_audio(
    services: Dict[str, Any],
    content: bytes,
    ext: str,
    tts_meta: Dict[str, Any],
) -> Dict[str, str]:
    """把音频落进上传目录；返回 {url, output}。

    tts_meta 以 ``[语音参数] <单行 JSON>`` 随工具消息持久化，卡片不外显。
    """
    upload_dir = Path(services["upload_dir"])
    upload_url_base = (services["upload_url_base"] or "").rstrip("/")
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = "tts_%s_%s%s" % (stamp, uuid.uuid4().hex[:8], ext)
    (upload_dir / filename).write_bytes(content)

    url = "%s/%s" % (upload_url_base, filename)
    output = "\n".join([
        "[语音已生成] %s" % url,
        "[语音参数] "
        + json.dumps(tts_meta, ensure_ascii=False, separators=(",", ":")),
        "语音已成功生成。请用一句简短自然的话告知用户（如「已经为你朗读啦」），"
        "不要重复朗读文本，不要输出链接或文件路径。",
    ])
    return {"url": url, "output": output}


def _describe_tts_error(exc: TtsError) -> str:
    code = exc.code
    if code == 45000000:
        if "concurrency" in (exc.message or "").lower():
            return "[工具错误] 语音并发数超限（限流），请稍后重试"
        return (
            "[工具错误] 音色未授权或音色 ID 有误：请在火山控制台确认该音色"
            "已授权并与资源版本匹配（%s）" % exc.message
        )
    if code == 55000000:
        low_msg = (exc.message or "").lower()
        if "mismatched with speaker" in low_msg:
            return (
                "[工具错误] 音色与资源版本不匹配：该音色不属于当前资源版本"
                "（如 seed-tts-2.0 只能用 2.0 音色），请更换音色 ID，"
                "或更换匹配的资源版本（%s）" % exc.message
            )
        return "[工具错误] 语音服务通用错误：%s" % exc.message
    if code is not None:
        return "[工具错误] 合成未正常完成：%s %s" % (code, exc.message)
    return "[工具错误] %s" % exc.message


def _describe_transport_error(exc: Exception) -> str:
    import websockets.exceptions as ws_exc

    if isinstance(exc, ws_exc.InvalidStatus):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", "?")
        detail = ""
        body = getattr(response, "body", b"") or b""
        try:
            parsed = json.loads(body.decode("utf-8"))
            detail = str(parsed.get("error") or parsed.get("message") or "")
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = body.decode("utf-8", "ignore")[:120]
        if "resource not granted" in detail.lower():
            return (
                "[工具错误] 当前 API Key 未获得该资源版本的授权（HTTP %s）："
                "请在火山语音控制台开通对应版本后再用（%s）" % (status, detail)
            )
        if detail:
            return (
                "[工具错误] 语音服务握手失败（HTTP %s）：%s"
                "—— 请检查 API Key / 资源 ID" % (status, detail)
            )
        return (
            "[工具错误] 语音服务握手失败（HTTP %s），"
            "请检查 API Key / 资源 ID" % status
        )
    if isinstance(exc, (ws_exc.InvalidHandshake, ws_exc.InvalidURI)):
        return "[工具错误] 语音服务握手失败：%s" % exc
    if isinstance(exc, ws_exc.ConnectionClosed):
        return "[工具错误] 语音连接被中断，请重试"
    return "[工具错误] 语音合成失败: %s: %s" % (
        type(exc).__name__, exc
    )
