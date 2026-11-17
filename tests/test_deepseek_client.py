"""DeepSeek 官方工具客户端：URL 拼接、鉴权头、成功/错误解析。

全部走 ``httpx.MockTransport``，不发真实网络请求。
"""

import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import DeepSeekSettings
from flyai.deepseek_tools import (
    DeepSeekToolsClient,
    _tools_url,
    configured,
    usable,
)
from flyai.errors import DeepSeekError


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _client(handler, **overrides):
    cfg = DeepSeekSettings(api_key="sk-test", **overrides)
    return DeepSeekToolsClient(cfg, transport=httpx.MockTransport(handler))


class _Recorder:
    """记录 MockTransport 收到的请求，便于断言 URL/头/体。"""

    def __init__(self, response=None):
        self.requests = []
        self.response = response

    def __call__(self, request):
        self.requests.append(request)
        return self.response if self.response is not None else httpx.Response(
            200, json={})


# ---- 基础判定 ----

def test_configured_and_usable_flags():
    assert configured(DeepSeekSettings(api_key="k")) is True
    assert configured(DeepSeekSettings(api_key="   ")) is False
    assert configured(DeepSeekSettings(api_key="k", enabled=False)) is False
    assert configured(None) is False
    cfg = DeepSeekSettings(api_key="k", search_enabled=False)
    assert usable(cfg, "search") is False
    assert usable(cfg, "fetch") is True
    # 服务名未知：只要总开关与 Key 可用即放行
    assert usable(cfg, "unknown") is True


def test_tools_url_handles_v1_suffix_and_default():
    assert _tools_url("https://api.deepseek.com", "search") == \
        "https://api.deepseek.com/v1/tools/search"
    assert _tools_url("https://api.deepseek.com/", "search") == \
        "https://api.deepseek.com/v1/tools/search"
    assert _tools_url("https://proxy.example/v1", "tts") == \
        "https://proxy.example/v1/tools/tts"
    assert _tools_url("", "fetch") == \
        "https://api.deepseek.com/v1/tools/fetch"


# ---- search ----

def test_search_success_sends_json_and_parses_results():
    rec = _Recorder(httpx.Response(200, json={
        "results": [{"title": "A", "url": "https://a.example", "content": "aa"}],
    }))
    out = _run(_client(rec).search("你好", 12))
    assert out == [{"title": "A", "url": "https://a.example", "content": "aa"}]
    req = rec.requests[0]
    assert str(req.url) == "https://api.deepseek.com/v1/tools/search"
    assert req.headers["authorization"] == "Bearer sk-test"
    assert json.loads(req.content) == {"query": "你好", "count": 10}  # clamp


def test_search_accepts_data_and_bare_list_shapes():
    rec = _Recorder(httpx.Response(200, json={"data": [{"title": "B"}]}))
    assert _run(_client(rec).search("q", 3)) == [{"title": "B"}]
    rec2 = _Recorder(httpx.Response(200, json=[{"title": "C"}, "junk"]))
    assert _run(_client(rec2).search("q", 3)) == [{"title": "C"}]


def test_search_error_json_raises_with_status():
    rec = _Recorder(httpx.Response(
        401, json={"error": {"message": "invalid api key"}}))
    with pytest.raises(DeepSeekError) as ei:
        _run(_client(rec).search("q"))
    assert ei.value.status_code == 401
    assert "invalid api key" in str(ei.value)


def test_error_falls_back_to_plain_text_body():
    rec = _Recorder(httpx.Response(502, text="bad gateway"))
    with pytest.raises(DeepSeekError) as ei:
        _run(_client(rec).search("q"))
    assert ei.value.status_code == 502
    assert "bad gateway" in str(ei.value)


# ---- fetch ----

def test_fetch_success_and_title_content_types():
    rec = _Recorder(httpx.Response(200, json={
        "title": 7, "content": "正文"}))  # 数值 title 也转字符串
    out = _run(_client(rec).fetch("https://x.example"))
    assert out == {"title": "7", "content": "正文"}
    assert json.loads(rec.requests[0].content) == {"url": "https://x.example"}


def test_fetch_non_object_response_raises():
    rec = _Recorder(httpx.Response(200, json=["nope"]))
    with pytest.raises(DeepSeekError):
        _run(_client(rec).fetch("https://x.example"))


def test_non_json_success_body_raises():
    rec = _Recorder(httpx.Response(200, text="<html>"))
    with pytest.raises(DeepSeekError):
        _run(_client(rec).fetch("https://x.example"))


# ---- tts ----

def test_tts_returns_raw_bytes_and_normalizes_fields():
    rec = _Recorder(httpx.Response(200, content=b"RIFF...."))
    audio = _run(_client(rec).tts("你好", voice="ghost", fmt="mp3"))
    assert audio == b"RIFF...."
    body = json.loads(rec.requests[0].content)
    assert body == {
        "text": "你好",
        "voice_id": "stella",   # 非法音色回落
        "format": "wav",        # 非法格式回落
        "filter_markdown": True,
    }


def test_tts_empty_audio_raises():
    rec = _Recorder(httpx.Response(200, content=b""))
    with pytest.raises(DeepSeekError):
        _run(_client(rec).tts("你好"))


# ---- asr ----

def test_asr_posts_multipart_and_parses_duration():
    rec = _Recorder(httpx.Response(200, json={"text": "嗨", "duration_ms": 1234}))
    out = _run(_client(rec).asr(b"WAVDATA", "clip.wav", "audio/wav"))
    assert out == {"text": "嗨", "duration_ms": 1234}
    req = rec.requests[0]
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    assert b'name="file"' in body and b"WAVDATA" in body


def test_asr_missing_duration_defaults_to_zero():
    rec = _Recorder(httpx.Response(200, json={"text": ""}))
    assert _run(_client(rec).asr(b"WAV")) == {"text": "", "duration_ms": 0}


# ---- Key 守卫 ----

def test_empty_key_rejected_before_network():
    rec = _Recorder(httpx.Response(200, json={}))
    client = DeepSeekToolsClient(
        DeepSeekSettings(api_key="  "), transport=httpx.MockTransport(rec))
    with pytest.raises(DeepSeekError) as ei:
        _run(client.search("q"))
    assert ei.value.status_code == 400
    assert rec.requests == []


def test_non_ascii_key_rejected_before_network():
    rec = _Recorder(httpx.Response(200, json={}))
    client = DeepSeekToolsClient(
        DeepSeekSettings(api_key="sk-中文"), transport=httpx.MockTransport(rec))
    with pytest.raises(DeepSeekError) as ei:
        _run(client.search("q"))
    assert ei.value.status_code == 400
    assert rec.requests == []


def test_transport_error_wrapped_as_deepseek_error():
    def boom(request):
        raise httpx.ConnectError("offline", request=request)

    client = DeepSeekToolsClient(
        DeepSeekSettings(api_key="k"), transport=httpx.MockTransport(boom))
    with pytest.raises(DeepSeekError) as ei:
        _run(client.search("q"))
    assert "offline" in str(ei.value)