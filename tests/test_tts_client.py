"""豆包语音 V3 二进制帧编解码与客户端合成流程的离线测试。"""

from __future__ import annotations

import asyncio
import gzip
import json
import struct

import pytest

from flyai.tts import client as tts_client_mod
from flyai.tts.client import DoubaoTtsClient
from flyai.tts.protocol import (
    TtsError,
    build_finish_frame,
    build_request_frame,
    parse_frame,
)


def make_frame(event, sid, payload, msg_type=0x9, serialization=0x1, gzip_payload=False):
    """按服务端帧布局手构一帧（flags 恒 0x4：携带 event）。"""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    byte2 = (serialization << 4)
    if gzip_payload:
        payload = gzip.compress(payload)
        byte2 |= 0x1
    sid_b = sid.encode("utf-8")
    return (
        bytes([0x11, (msg_type << 4) | 0x4, byte2, 0x00])
        + struct.pack(">I", event)
        + struct.pack(">I", len(sid_b))
        + sid_b
        + struct.pack(">I", len(payload))
        + payload
    )


def make_error_frame(code, message_obj):
    """错误帧真实布局：头 + 错误码 u32 + payload 长度 u32 + JSON payload。"""
    body = json.dumps(message_obj, ensure_ascii=False).encode("utf-8")
    return (
        bytes([0x11, 0xF0, 0x10, 0x00])
        + struct.pack(">I", code)
        + struct.pack(">I", len(body))
        + body
    )


# ---------- parse_frame ----------

def test_parse_sentence_start():
    frame = parse_frame(make_frame(350, "sid1", json.dumps({"text": "你好"})))
    assert frame.event == 350
    assert frame.session_id == "sid1"
    assert frame.is_audio is False
    assert frame.data == {"text": "你好"}


def test_parse_sentence_end():
    frame = parse_frame(make_frame(351, "sid1", json.dumps({})))
    assert frame.event == 351
    assert frame.is_audio is False


def test_parse_audio_frame():
    raw_audio = b"\xff\xfb\x90\x00some-audio"
    frame = parse_frame(
        make_frame(352, "sid1", raw_audio, msg_type=0xB, serialization=0x0)
    )
    assert frame.event == 352
    assert frame.is_audio is True
    assert frame.payload == raw_audio
    assert frame.data is None


def test_parse_session_finished():
    body = {"status_code": 20000000, "message": "ok",
            "usage": {"text_words": 12}}
    frame = parse_frame(make_frame(152, "sid1", json.dumps(body)))
    assert frame.event == 152
    assert frame.data["status_code"] == 20000000
    assert frame.data["usage"]["text_words"] == 12


def test_parse_big_endian():
    # payload 长度 256：长度字段四字节应为 00 00 01 00
    raw = make_frame(352, "s", b"x" * 256, msg_type=0xB, serialization=0x0)
    # 布局：4 头 + 4 event + 4 sid_len + 1 sid + 4 payload_len ...
    payload_len_field = raw[13:17]
    assert payload_len_field == b"\x00\x00\x01\x00"
    event_field = raw[4:8]
    assert event_field == b"\x00\x00\x01\x60"  # 352


def test_parse_error_frame():
    raw = make_error_frame(45000000, {"message": "speaker permission denied"})
    with pytest.raises(TtsError) as exc:
        parse_frame(raw)
    assert exc.value.code == 45000000
    assert "permission denied" in exc.value.message


def test_parse_error_frame_error_key():
    # 真实服务端错误帧用 {"error": ...} 键；消息不得带长度字段的残留首字节
    raw = make_error_frame(
        55000000, {"error": "resource ID is mismatched with speaker related resource"}
    )
    with pytest.raises(TtsError) as exc:
        parse_frame(raw)
    assert exc.value.code == 55000000
    assert exc.value.message.startswith("resource ID is mismatched")


def test_parse_error_frame_too_short():
    with pytest.raises(TtsError):
        parse_frame(bytes([0x11, 0xF0, 0x10, 0x00, 0x00, 0x00]))


def test_parse_gzip_payload():
    body = json.dumps({"text": "压缩的 JSON"})
    raw = make_frame(350, "sid", body, gzip_payload=True)
    assert raw[2] == 0x11  # JSON + gzip
    frame = parse_frame(raw)
    assert frame.data == {"text": "压缩的 JSON"}


def test_parse_frame_too_short():
    with pytest.raises(TtsError):
        parse_frame(b"\x11\x00")


# ---------- build_* ----------

def test_build_request_frame_bytes():
    frame = build_request_frame({
        "user": {"uid": "u1"},
        "req_params": {
            "text": "你好",
            "speaker": "S_BcqE5org2",
            "audio_params": {"format": "mp3", "sample_rate": 24000},
        },
    })
    assert frame[0] == 0x11
    assert frame[1] == 0x10
    assert frame[2] == 0x10
    body_len = struct.unpack(">I", frame[4:8])[0]
    body = frame[8:]
    assert body_len == len(body)
    parsed = json.loads(body.decode("utf-8"))
    assert parsed["user"]["uid"] == "u1"
    assert parsed["req_params"]["text"] == "你好"
    assert parsed["req_params"]["speaker"] == "S_BcqE5org2"
    assert parsed["req_params"]["audio_params"]["sample_rate"] == 24000


def test_build_finish_frame():
    frame = build_finish_frame()
    assert frame[1] == 0x14
    body_len = struct.unpack(">I", frame[4:8])[0]
    assert frame[8:8 + body_len] == b"{}"


# ---------- DoubaoTtsClient.synthesize（fake websocket）----------

class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


class _FakeWS:
    """按脚本返回帧序列；记录发送内容。"""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if not self._frames:
            raise EOFError("fake frames exhausted")
        return self._frames.pop(0)


def _settings(**kw):
    from flyai.config import TtsSettings

    kw.setdefault("api_key", "test-key")
    return TtsSettings(**kw)


def test_synthesize_collects_audio(monkeypatch):
    chunk1 = b"AUDIO-CHUNK-1"
    chunk2 = b"AUDIO-CHUNK-2"
    frames = [
        make_frame(350, "sess-x", json.dumps({"text": "你好"})),
        make_frame(352, "sess-x", chunk1, msg_type=0xB, serialization=0x0),
        make_frame(352, "sess-x", chunk2, msg_type=0xB, serialization=0x0),
        make_frame(351, "sess-x", json.dumps({})),
        make_frame(
            152, "sess-x",
            json.dumps({"status_code": 20000000, "message": "ok",
                        "usage": {"text_words": 2}}),
        ),
        # 关闭阶段：ConnectionFinished
        make_frame(52, "sess-x", json.dumps({"status_code": 20000000})),
    ]
    fake = _FakeWS(frames)

    def fake_connect(*args, **kwargs):
        fake.response = _FakeResponse({"X-Tt-Logid": "log-123"})
        return fake

    monkeypatch.setattr(tts_client_mod.websockets, "connect", fake_connect)

    result = asyncio.run(
        DoubaoTtsClient(_settings()).synthesize("你好", "S_BcqE5org2")
    )
    assert result.audio == chunk1 + chunk2
    assert result.session_id == "sess-x"
    assert result.logid == "log-123"
    assert result.usage == {"text_words": 2}
    # 发送了请求帧 + FinishConnection 帧
    assert len(fake.sent) == 2
    assert fake.sent[1][1] == 0x14


def test_synthesize_icl_finished_without_status(monkeypatch):
    # seed-icl-2.0 的结束帧 payload 为 {}，不带 status_code：有音频即成功
    audio = b"AUDIO-OK"
    frames = [
        make_frame(350, "sess-x", json.dumps({"text": ""})),
        make_frame(352, "sess-x", audio, msg_type=0xB, serialization=0x0),
        make_frame(351, "sess-x", json.dumps({})),
        make_frame(152, "sess-x", json.dumps({})),
        make_frame(52, "sess-x", json.dumps({})),
    ]
    fake = _FakeWS(frames)
    monkeypatch.setattr(
        tts_client_mod.websockets, "connect", lambda *a, **k: fake
    )
    result = asyncio.run(
        DoubaoTtsClient(_settings(resource_id="seed-icl-2.0")).synthesize(
            "你好", "S_NkpD5org2"
        )
    )
    assert result.audio == audio


def test_synthesize_bad_status_raises(monkeypatch):
    frames = [
        make_frame(
            152, "s",
            json.dumps({"status_code": 55000000, "message": "server error"}),
        ),
    ]
    fake = _FakeWS(frames)
    monkeypatch.setattr(
        tts_client_mod.websockets, "connect", lambda *a, **k: fake
    )
    with pytest.raises(TtsError) as exc:
        asyncio.run(
            DoubaoTtsClient(_settings()).synthesize("你好", "S_BcqE5org2")
        )
    assert exc.value.code == 55000000


def test_synthesize_no_audio_raises(monkeypatch):
    frames = [
        make_frame(
            152, "s",
            json.dumps({"status_code": 20000000, "message": "ok"}),
        ),
    ]
    fake = _FakeWS(frames)
    monkeypatch.setattr(
        tts_client_mod.websockets, "connect", lambda *a, **k: fake
    )
    with pytest.raises(TtsError):
        asyncio.run(
            DoubaoTtsClient(_settings()).synthesize("你好", "S_BcqE5org2")
        )
