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

from flyai.config import DeepSeekSettings, TtsSettings
from flyai.errors import DeepSeekError
from flyai.tools import tts_tool as tts_mod
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


# ---- DeepSeek 主路径 / 回退 / 计费 ----

_WAV = b"RIFFFAKE-DS-WAV"


class _FakeDsClient:
    calls = []
    fail = False

    def __init__(self, settings):
        self.settings = settings

    async def tts(self, text, voice, fmt):
        _FakeDsClient.calls.append((text, voice, fmt))
        if _FakeDsClient.fail:
            raise DeepSeekError("upstream down", status_code=502)
        return _WAV


@pytest.fixture
def fake_ds(monkeypatch):
    _FakeDsClient.calls = []
    _FakeDsClient.fail = False
    monkeypatch.setattr(tts_mod, "DeepSeekToolsClient", _FakeDsClient)
    return _FakeDsClient


class _Meter:
    def __init__(self, available=True):
        self._available = available
        self.charges = []

    def available(self):
        return self._available

    def charge(self, kind, quantity, unit, cost, engine="", session_id=""):
        self.charges.append((kind, quantity, unit, cost, engine))
        return cost


def _ds(**kw):
    kw.setdefault("api_key", "sk-ds")
    return DeepSeekSettings(**kw)


def _meta(result):
    return json.loads(
        re.search(r"^\[语音参数\] (\{.*\})$", result.output, re.M).group(1)
    )


def test_deepseek_success_saves_wav_and_bills(tmp_path, fake_ds):
    tool = _tool(default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    meter = _Meter()
    ctx = _ctx(tmp_path, deepseek_settings=_ds(tts_voice="tide"),
              usage_meter=meter)
    result = asyncio.run(tool.run({"text": "「你好。」"}, ctx))

    assert result.ok
    assert fake_ds.calls == [("「你好。」", "tide", "wav")]
    assert "speaker" not in captured  # 未走豆包
    m = re.fullmatch(
        r"\[语音已生成] (/uploads/u/tts_.+\.wav)",
        result.output.splitlines()[0],
    )
    assert m
    assert (tmp_path / os.path.basename(m.group(1))).read_bytes() == _WAV
    info = _meta(result)
    assert info["speaker"] == "tide" and info["voice_source"] == "default"
    assert info["format"] == "wav" and info["sample_rate"] == 24000
    # 10 计费字符 × 3 元/万字符
    assert meter.charges == [("tts", 10, "字符", pytest.approx(0.003),
                              "deepseek")]


def test_deepseek_voice_mapping(tmp_path, fake_ds):
    tool = _tool(default_speaker="S_DEFAULT")
    ds = _ds(tts_voice="tide")

    # 显式参数合法 → arg
    asyncio.run(tool.run(
        {"text": "你好", "speaker": "stella"},
        _ctx(tmp_path, deepseek_settings=ds)))
    # 参数非法 → 角色卡绑定 tide
    asyncio.run(tool.run(
        {"text": "你好", "speaker": "S_ARG"},
        _ctx(tmp_path, deepseek_settings=ds, tts_voice="tide")))
    # 参数与绑定都非法 → 默认 tide
    result = asyncio.run(tool.run(
        {"text": "你好"},
        _ctx(tmp_path, deepseek_settings=ds, tts_voice="S_BOUND")))

    assert [(c[1],) for c in fake_ds.calls] == [
        ("stella",), ("tide",), ("tide",)]
    assert _meta(result)["voice_source"] == "default"


def test_deepseek_failure_falls_back_to_doubao_and_bills(
        tmp_path, fake_ds):
    tool = _tool(default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    fake_ds.fail = True
    meter = _Meter()
    result = asyncio.run(tool.run(
        {"text": "你好"},
        _ctx(tmp_path, deepseek_settings=_ds(), usage_meter=meter,
             tts_voice="S_BOUND")))

    assert result.ok
    assert captured["speaker"] == "S_BOUND"  # 豆包继续走原音色解析
    assert _meta(result)["format"] == "mp3"
    # 回退成功同样按 DeepSeek 字符单价计费
    assert meter.charges == [("tts", 4, "字符", pytest.approx(0.0012),
                              "doubao")]


def test_deepseek_failure_reason_surfaces_when_doubao_fails(
        tmp_path, fake_ds):
    """回退豆包也失败时，错误里带上 DeepSeek 的失败原因（便于判断修哪个 Key）。"""
    from flyai.tts.protocol import TtsError

    tool = _tool(default_speaker="S_DEFAULT")
    _patch_synthesize(
        tool, raises=TtsError("speaker permission denied", code=45000000))
    fake_ds.fail = True
    result = asyncio.run(tool.run(
        {"text": "你好"}, _ctx(tmp_path, deepseek_settings=_ds())))
    assert not result.ok
    assert "音色未授权" in result.output
    assert "DeepSeek 优先调用失败" in result.output
    assert "upstream down" in result.output


def test_deepseek_available_without_doubao_key(tmp_path, fake_ds):
    # DeepSeek 可用时不得因豆包 Key 为空提前报错
    tool = TextToSpeechTool(TtsSettings(api_key="", engine="deepseek"))
    result = asyncio.run(tool.run(
        {"text": "你好"}, _ctx(tmp_path, deepseek_settings=_ds())))
    assert result.ok
    assert fake_ds.calls and _meta(result)["format"] == "wav"


def test_doubao_engine_skips_deepseek(tmp_path, fake_ds):
    tool = _tool(engine="doubao", default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    meter = _Meter()
    result = asyncio.run(tool.run(
        {"text": "你好"},
        _ctx(tmp_path, deepseek_settings=_ds(), usage_meter=meter)))
    assert result.ok
    assert fake_ds.calls == []
    assert captured["speaker"] == "S_DEFAULT"
    assert meter.charges[0][4] == "doubao"


def test_insufficient_balance_blocks_before_synthesis(tmp_path, fake_ds):
    tool = _tool(default_speaker="S_DEFAULT")
    captured = _patch_synthesize(tool, returns=_tts_result())
    meter = _Meter(available=False)
    result = asyncio.run(tool.run(
        {"text": "你好"},
        _ctx(tmp_path, deepseek_settings=_ds(), usage_meter=meter)))
    assert not result.ok and "余额不足" in result.output
    assert fake_ds.calls == [] and "speaker" not in captured
