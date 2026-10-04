"""text_to_speech 工具的离线单测。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.config import TtsSettings
from flyai.tools.base import ToolContext
from flyai.tools.tts_tool import TextToSpeechTool, pcm_to_wav


def _settings(**kw):
    kw.setdefault("api_key", "test-api-key")
    return TtsSettings(**kw)


def _tool(**kw):
    return TextToSpeechTool(_settings(**kw))


def _ctx(tmp_path, url_base="/uploads/u", **extra):
    services = {"upload_dir": tmp_path, "upload_url_base": url_base}
    services.update(extra)
    return ToolContext(services=services)


def _patch_synthesize(tool, returns=None, raises=None):
    captured = {}

    async def fake_synthesize(text, speaker):
        captured["text"] = text
        captured["speaker"] = speaker
        if raises is not None:
            raise raises
        return returns

    tool._synthesize = fake_synthesize
    return captured


def _tts_result(audio=b"ID3FAKE-MP3-DATA"):
    from flyai.tts import TtsResult

    return TtsResult(
        audio=audio,
        session_id="sess-1",
        request_id="req-1",
        logid="log-1",
        usage={"text_words": 5},
    )


def test_tts_success_real(tmp_path):
    tool = _tool()
    captured = _patch_synthesize(tool, returns=_tts_result())
    result = asyncio.run(
        tool.run({"text": "你好呀", "speaker": "speaker-1"}, _ctx(tmp_path))
    )

    assert result.ok
    assert captured["text"] == "你好呀"
    assert captured["speaker"] == "speaker-1"

    first_line = result.output.splitlines()[0]
    m = re.fullmatch(
        r"\[语音已生成] (/uploads/u/tts_.+\.mp3)", first_line
    )
    assert m, first_line
    saved = tmp_path / os.path.basename(m.group(1))
    assert saved.read_bytes() == b"ID3FAKE-MP3-DATA"

    mm = re.search(r"^\[语音参数\] (\{.*\})$", result.output, re.M)
    assert mm, result.output
    info = json.loads(mm.group(1))
    assert info["speaker"] == "speaker-1"
    assert info["text_len"] == 3
    assert info["format"] == "mp3"
    assert info["sample_rate"] == 24000


def test_tts_uses_default_speaker(tmp_path):
    tool = _tool(default_speaker="S_BcqE5org2")
    captured = _patch_synthesize(tool, returns=_tts_result())
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert result.ok
    assert captured["speaker"] == "S_BcqE5org2"


def test_tts_uses_character_bound_voice(tmp_path):
    tool = _tool(default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    ctx = _ctx(tmp_path, tts_voice="S_BOUND")
    result = asyncio.run(tool.run({"text": "你好"}, ctx))
    assert result.ok
    assert captured["speaker"] == "S_BOUND"
    info = json.loads(
        re.search(r"^\[语音参数\] (\{.*\})$", result.output, re.M).group(1)
    )
    assert info["voice_source"] == "character"


def test_tts_arg_speaker_overrides_bound_voice(tmp_path):
    tool = _tool(default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    ctx = _ctx(tmp_path, tts_voice="S_BOUND")
    result = asyncio.run(
        tool.run({"text": "你好", "speaker": "S_ARG"}, ctx)
    )
    assert result.ok
    assert captured["speaker"] == "S_ARG"
    info = json.loads(
        re.search(r"^\[语音参数\] (\{.*\})$", result.output, re.M).group(1)
    )
    assert info["voice_source"] == "arg"


def test_tts_falls_back_to_default_when_unbound(tmp_path):
    tool = _tool(default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    ctx = _ctx(tmp_path, tts_voice="")
    result = asyncio.run(tool.run({"text": "你好"}, ctx))
    assert result.ok
    assert captured["speaker"] == "S_DEFAULT"
    info = json.loads(
        re.search(r"^\[语音参数\] (\{.*\})$", result.output, re.M).group(1)
    )
    assert info["voice_source"] == "default"


def test_tts_level_below_min_rejected(tmp_path):
    tool = _tool(min_level=10)
    captured = _patch_synthesize(tool, returns=_tts_result())
    ctx = _ctx(tmp_path, tts_voice="S_BOUND", user_level=5)
    result = asyncio.run(tool.run({"text": "你好"}, ctx))
    assert not result.ok
    assert "权限等级不足" in result.output
    assert "Lv10" in result.output
    assert "Lv5" in result.output
    assert "speaker" not in captured


def test_tts_min_level_zero_always_allowed(tmp_path):
    tool = _tool(min_level=0)
    captured = _patch_synthesize(tool, returns=_tts_result())
    ctx = _ctx(tmp_path, user_level=0)
    result = asyncio.run(tool.run({"text": "你好"}, ctx))
    assert result.ok
    assert captured["speaker"] == "S_BcqE5org2"


def test_tts_admin_level_bypasses_min_level(tmp_path):
    # admin=256：min_level 最高 255，恒可通过
    tool = _tool(min_level=255)
    captured = _patch_synthesize(tool, returns=_tts_result())
    ctx = _ctx(tmp_path, user_level=256)
    result = asyncio.run(tool.run({"text": "你好"}, ctx))
    assert result.ok
    assert captured["speaker"] == "S_BcqE5org2"


def test_tts_pcm_saved_as_wav(tmp_path):
    tool = _tool(format="pcm", sample_rate=24000)
    pcm = b"\x00\x00" * 200
    _patch_synthesize(tool, returns=_tts_result(audio=pcm))
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert result.ok
    first_line = result.output.splitlines()[0]
    m = re.fullmatch(
        r"\[语音已生成] (/uploads/u/tts_.+\.wav)", first_line
    )
    assert m, first_line
    saved = tmp_path / os.path.basename(m.group(1))
    data = saved.read_bytes()
    assert data[:4] == b"RIFF"

    info = json.loads(
        re.search(r"^\[语音参数\] (\{.*\})$", result.output, re.M).group(1)
    )
    assert info["format"] == "pcm"
    assert info["container"] == "wav"


def test_tts_empty_text(tmp_path):
    result = asyncio.run(
        _tool().run({"text": "   "}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "[参数错误]" in result.output


def test_tts_too_long_text(tmp_path):
    result = asyncio.run(
        _tool().run({"text": "好" * 1001}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "过长" in result.output


def test_tts_missing_upload_context():
    result = asyncio.run(
        _tool().run({"text": "你好"}, ToolContext(services={}))
    )
    assert not result.ok
    assert "[工具错误]" in result.output


def test_tts_missing_api_key(tmp_path):
    tool = TextToSpeechTool(TtsSettings(api_key=""))
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "API Key" in result.output


def test_tts_missing_default_speaker(tmp_path):
    tool = _tool(default_speaker="")
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "default_speaker" in result.output


def test_tts_speaker_unauthorized(tmp_path):
    from flyai.tts.protocol import TtsError

    tool = _tool()
    _patch_synthesize(
        tool,
        raises=TtsError("speaker permission denied", code=45000000),
    )
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "音色未授权" in result.output


def test_tts_concurrency_limit(tmp_path):
    from flyai.tts.protocol import TtsError

    tool = _tool()
    _patch_synthesize(
        tool,
        raises=TtsError("quota exceeded for types: concurrency",
                        code=45000000),
    )
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "并发" in result.output


def test_tts_speaker_resource_mismatch(tmp_path):
    from flyai.tts.protocol import TtsError

    tool = _tool()
    _patch_synthesize(
        tool,
        raises=TtsError(
            "resource ID is mismatched with speaker related resource",
            code=55000000,
        ),
    )
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "音色与资源版本不匹配" in result.output


def test_tts_timeout(tmp_path):
    tool = _tool(timeout_seconds=5)
    _patch_synthesize(tool, raises=asyncio.TimeoutError())
    result = asyncio.run(
        tool.run({"text": "你好"}, _ctx(tmp_path))
    )
    assert not result.ok
    assert "超时" in result.output


def test_pcm_to_wav_header():
    pcm = b"\x01\x02" * 10
    wav = pcm_to_wav(pcm, 24000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    fmt_size, audio_format, channels, sample_rate, byte_rate, block_align, bits = \
        struct.unpack("<IHHIIHH", wav[16:36])
    assert fmt_size == 16
    assert audio_format == 1
    assert channels == 1
    assert sample_rate == 24000
    assert byte_rate == 48000
    assert block_align == 2
    assert bits == 16
    assert wav[36:40] == b"data"
    data_size = struct.unpack("<I", wav[40:44])[0]
    assert data_size == len(pcm)
    assert wav[44:] == pcm
