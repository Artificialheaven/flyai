"""朗读 / 语音识别接口：等级、余额、DeepSeek 流式合成分块与计费、回退。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api import routes_speech
from flyai.config import DeepSeekSettings, TtsSettings
from flyai.errors import DeepSeekError
from flyai.metering import asr_cost, tts_cost
from flyai.user import ADMIN_UID


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _env(tmp_path, tts=None, deepseek=None, level=5, uid="u1", total=10.0):
    tts = tts or TtsSettings(api_key="k", default_speaker="S_DEF",
                             min_level=0)
    users = {uid: SimpleNamespace(quota_total=total, quota_used=0.0)}
    charged = []

    class _Store:
        def get_by_id(self, uid):
            return users.get(uid)

        def add_used(self, uid, cost):
            charged.append(cost)
            users[uid].quota_used += cost

    sc = SimpleNamespace(uid=uid, level=level,
                         settings_path=tmp_path / "data" / uid / "settings.json")
    state = SimpleNamespace(
        config=SimpleNamespace(tts=tts, deepseek=deepseek),
        user_store=_Store())
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return state, sc, request, charged


def _ledger(tmp_path, uid="u1"):
    path = tmp_path / "data" / uid / "usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(x)
            for x in path.read_text(encoding="utf-8").splitlines()]


async def _collect(response):
    parts = []
    async for chunk in response.body_iterator:
        parts.append(chunk)
    return b"".join(parts)


class _FakeDs:
    calls = []
    fail = False
    asr_result = {"text": "嗨", "duration_ms": 1234}

    def __init__(self, settings):
        self.settings = settings

    async def tts(self, text, voice, fmt):
        _FakeDs.calls.append(("tts", text, voice, fmt))
        if _FakeDs.fail:
            raise DeepSeekError("boom", status_code=502)
        return b"|" + text.encode("utf-8")

    async def asr(self, data, filename, content_type):
        _FakeDs.calls.append(("asr", data, filename, content_type))
        if _FakeDs.fail:
            raise DeepSeekError("boom", status_code=502)
        return dict(_FakeDs.asr_result)


class _FakeDoubao:
    calls = []

    def __init__(self, settings):
        self.settings = settings

    async def synthesize(self, text, speaker):
        _FakeDoubao.calls.append((text, speaker, self.settings.format))
        return SimpleNamespace(audio=b"DB|" + text.encode("utf-8"))


@pytest.fixture
def fake_ds(monkeypatch):
    _FakeDs.calls = []
    _FakeDs.fail = False
    _FakeDs.asr_result = {"text": "嗨", "duration_ms": 1234}
    monkeypatch.setattr(routes_speech, "DeepSeekToolsClient", _FakeDs)
    return _FakeDs


@pytest.fixture
def fake_doubao(monkeypatch):
    _FakeDoubao.calls = []
    monkeypatch.setattr(routes_speech, "DoubaoTtsClient", _FakeDoubao)
    return _FakeDoubao


class _Upload:
    def __init__(self, data=b"WAVDATA", filename="clip.wav",
                 content_type="audio/wav"):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._data


_DS = DeepSeekSettings(api_key="sk-ds", tts_price=3.0, asr_price=1.0,
                       tts_voice="stella")


# ---- TTS ----

def test_tts_level_gate_403_and_admin_bypass(tmp_path, fake_ds):
    tts = TtsSettings(api_key="k", default_speaker="d", min_level=10)
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS, level=5)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="嗨"), request))
    assert ei.value.status_code == 403
    assert "Lv10" in ei.value.detail and "Lv5" in ei.value.detail
    assert _FakeDs.calls == []

    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS, level=0,
                                  uid=ADMIN_UID)
    resp = _run(routes_speech.speech_tts(
        routes_speech.SpeechTtsBody(text="嗨"), request))
    assert _run(_collect(resp)) == b"|" + "嗨".encode("utf-8")


def test_tts_empty_and_too_long_text_400(tmp_path, fake_ds):
    state, sc, request, _c = _env(tmp_path, deepseek=_DS)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="   "), request))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(
                text="x" * (routes_speech.TTS_TEXT_MAX + 1)), request))
    assert ei.value.status_code == 400
    assert "文本过长" in ei.value.detail


def test_tts_disabled_400(tmp_path, fake_ds):
    tts = TtsSettings(enabled=False, api_key="k")
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="嗨"), request))
    assert ei.value.status_code == 400
    assert "未启用" in ei.value.detail


def test_tts_insufficient_balance_402(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS, total=0.0)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="嗨"), request))
    assert ei.value.status_code == 402
    assert _FakeDs.calls == [] and charged == []


def test_tts_deepseek_streams_short_text_and_bills(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS)
    resp = _run(routes_speech.speech_tts(
        routes_speech.SpeechTtsBody(text="你好", voice="tide"), request))
    assert resp.media_type == "audio/pcm"
    assert resp.headers["x-sample-rate"] == "24000"
    body = _run(_collect(resp))
    assert body == b"|" + "你好".encode("utf-8")

    assert _FakeDs.calls == [("tts", "你好", "tide", "pcm")]
    assert charged == [pytest.approx(0.0012)]
    rows = _ledger(tmp_path)
    assert len(rows) == 1
    assert rows[0]["kind"] == "tts" and rows[0]["engine"] == "deepseek"
    assert rows[0]["quantity"] == 4 and rows[0]["unit"] == "字符"


def test_tts_invalid_voice_falls_back_to_default(tmp_path, fake_ds):
    ds = DeepSeekSettings(api_key="sk-ds", tts_voice="stella")
    state, sc, request, _c = _env(tmp_path, deepseek=ds)
    resp = _run(routes_speech.speech_tts(
        routes_speech.SpeechTtsBody(text="嗨", voice="ghost"), request))
    _run(_collect(resp))
    assert _FakeDs.calls == [("tts", "嗨", "stella", "pcm")]


def test_tts_long_text_chunked_by_billed_chars(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS)
    text = "啊" * 4500 + "你好"   # 9000 + 4 计费字符 → 两块
    resp = _run(routes_speech.speech_tts(
        routes_speech.SpeechTtsBody(text=text), request))
    body = _run(_collect(resp))

    chunk1, chunk2 = "啊" * 4500, "你好"
    assert body == b"|" + chunk1.encode("utf-8") + b"|" + chunk2.encode("utf-8")
    assert [c[1] for c in _FakeDs.calls] == [chunk1, chunk2]
    assert charged == [pytest.approx(2.7), pytest.approx(0.0012)]

    rows = _ledger(tmp_path)
    assert [r["quantity"] for r in rows] == [9000, 4]
    assert rows[0]["cost"] == pytest.approx(2.7)


def test_tts_deepseek_failure_falls_back_to_doubao_pcm(
        tmp_path, fake_ds, fake_doubao):
    tts = TtsSettings(api_key="k", default_speaker="S_DEF", min_level=0,
                      sample_rate=16000)
    state, sc, request, charged = _env(tmp_path, tts=tts, deepseek=_DS)
    fake_ds.fail = True

    resp = _run(routes_speech.speech_tts(
        routes_speech.SpeechTtsBody(text="嗨"), request))
    assert resp.headers["x-sample-rate"] == "16000"
    body = _run(_collect(resp))
    assert body == b"DB|" + "嗨".encode("utf-8")

    # 豆包回退请求 PCM 格式、默认音色；同样按字符单价计费
    assert _FakeDoubao.calls == [("嗨", "S_DEF", "pcm")]
    assert charged == [pytest.approx(0.0006)]
    assert _ledger(tmp_path)[0]["engine"] == "doubao"


def test_tts_unconfigured_400(tmp_path, fake_ds):
    tts = TtsSettings(api_key="", default_speaker="d", min_level=0)
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=None)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="嗨"), request))
    assert ei.value.status_code == 400
    assert "未配置" in ei.value.detail
    assert "DeepSeek 未启用或未配置 API Key" in ei.value.detail


def test_tts_unconfigured_400_includes_deepseek_reason(tmp_path, fake_ds):
    """DeepSeek 调用失败 + 未配豆包 Key：400 里带上 DeepSeek 的失败原因。"""
    tts = TtsSettings(api_key="", default_speaker="d", min_level=0)
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS)
    fake_ds.fail = True
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="嗨"), request))
    assert ei.value.status_code == 400
    assert "DeepSeek 调用失败" in ei.value.detail
    assert "boom" in ei.value.detail


def test_tts_doubao_failure_includes_deepseek_reason(
        tmp_path, fake_ds, monkeypatch):
    """豆包回退也失败（如握手 403）时，502 同时给出 DeepSeek 失败原因。"""

    class _BadDoubao:
        def __init__(self, settings):
            self.settings = settings

        async def synthesize(self, text, speaker):
            raise RuntimeError("handshake 403")

    monkeypatch.setattr(routes_speech, "DoubaoTtsClient", _BadDoubao)
    tts = TtsSettings(api_key="k", default_speaker="d", min_level=0)
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS)
    fake_ds.fail = True
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_tts(
            routes_speech.SpeechTtsBody(text="嗨"), request))
    assert ei.value.status_code == 502
    assert "handshake 403" in ei.value.detail
    assert "DeepSeek 调用失败" in ei.value.detail
    assert "boom" in ei.value.detail


def test_tts_doubao_engine_skips_deepseek(
        tmp_path, fake_ds, fake_doubao):
    tts = TtsSettings(engine="doubao", api_key="k", default_speaker="S_DEF",
                      min_level=0)
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS)
    resp = _run(routes_speech.speech_tts(
        routes_speech.SpeechTtsBody(text="嗨"), request))
    _run(_collect(resp))
    assert _FakeDs.calls == []
    assert _FakeDoubao.calls and _ledger(tmp_path)[0]["engine"] == "doubao"


# ---- ASR ----

def test_asr_level_gate_403(tmp_path, fake_ds):
    tts = TtsSettings(api_key="k", min_level=10)
    state, sc, request, _c = _env(tmp_path, tts=tts, deepseek=_DS, level=5)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_asr(request, _Upload()))
    assert ei.value.status_code == 403
    assert _FakeDs.calls == []


def test_asr_unconfigured_400(tmp_path, fake_ds):
    state, sc, request, _c = _env(tmp_path, deepseek=None)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_asr(request, _Upload()))
    assert ei.value.status_code == 400
    assert "未配置" in ei.value.detail


def test_asr_empty_file_400(tmp_path, fake_ds):
    state, sc, request, _c = _env(tmp_path, deepseek=_DS)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_asr(request, _Upload(data=b"")))
    assert ei.value.status_code == 400
    assert "为空" in ei.value.detail


def test_asr_success_bills_by_rounded_seconds(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS)
    out = _run(routes_speech.speech_asr(request, _Upload(data=b"WAVDATA")))
    assert out == {"text": "嗨", "duration_ms": 1234}
    assert _FakeDs.calls == [("asr", b"WAVDATA", "clip.wav", "audio/wav")]

    # 1234ms → 向上取整 2 秒 × 1 元/小时
    assert charged == [pytest.approx(asr_cost(1234, 1.0))]
    row = _ledger(tmp_path)[0]
    assert row["kind"] == "asr" and row["engine"] == "deepseek"
    assert row["quantity"] == 2 and row["unit"] == "秒"
    assert row["cost"] == pytest.approx(0.000556)


def test_asr_insufficient_balance_402(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS, total=0.0)
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_asr(request, _Upload()))
    assert ei.value.status_code == 402
    assert charged == []


def test_asr_too_long_400_after_call_no_charge(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS)
    fake_ds.asr_result = {"text": "x",
                          "duration_ms": routes_speech.ASR_MAX_SECONDS * 1000 + 1}
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_asr(request, _Upload()))
    assert ei.value.status_code == 400
    assert "录音过长" in ei.value.detail
    assert charged == [] and _ledger(tmp_path) == []


def test_asr_upstream_error_502(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS)
    fake_ds.fail = True
    with pytest.raises(HTTPException) as ei:
        _run(routes_speech.speech_asr(request, _Upload()))
    assert ei.value.status_code == 502
    assert charged == []


def test_asr_unknown_duration_charges_zero(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS)
    fake_ds.asr_result = {"text": "x", "duration_ms": 0}
    out = _run(routes_speech.speech_asr(request, _Upload()))
    assert out == {"text": "x", "duration_ms": 0}
    assert charged == []
    assert _ledger(tmp_path) == []


def test_admin_asr_not_charged(tmp_path, fake_ds):
    state, sc, request, charged = _env(tmp_path, deepseek=_DS,
                                      uid=ADMIN_UID, level=0)
    out = _run(routes_speech.speech_asr(request, _Upload()))
    assert out["text"] == "嗨"
    assert charged == [] and _ledger(tmp_path, uid=ADMIN_UID) == []