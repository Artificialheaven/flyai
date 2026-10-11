"""Galgame MIDI 渲染器：MThd/tempo 轨/ch9 鼓轨、数值 clamp、硬上限。"""

import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.galgame.midi import PPQ, MidiSpecError, render_midi, vlq


def _chunks(data):
    """遍历 (magic, body) 块（跳过 14 字节 MThd）。"""
    pos = 14
    out = []
    while pos < len(data):
        magic = data[pos:pos + 4]
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        out.append((magic, data[pos + 8:pos + 8 + length]))
        pos += 8 + length
    return out


def _spec_one(tracks=None, **over):
    spec = {"bpm": 76, "bars": 2, "tracks": tracks or [
        {"program": 0, "notes": [{"t": 0, "d": 1, "n": 60}]}]}
    spec.update(over)
    return spec


# ---- 文件结构 ----

def test_header_format1_ppq():
    data = render_midi(_spec_one())
    assert data[:4] == b"MThd"
    length, fmt, tracks, div = struct.unpack(">IHHH", data[4:14])
    assert length == 6 and fmt == 1 and div == PPQ
    assert tracks == 2  # tempo 轨 + 1 条音乐轨
    magics = [m for m, _b in _chunks(data)]
    assert magics == [b"MTrk", b"MTrk"]


def test_tempo_and_timesig():
    data = render_midi(_spec_one(bpm=120, beats_per_bar=3))
    body = _chunks(data)[0][1]
    i = body.find(b"\xff\x51\x03")
    assert i >= 0
    uspq = struct.unpack(">I", b"\x00" + body[i + 3:i + 6])[0]
    assert uspq == 500000  # 60_000_000 / 120
    j = body.find(b"\xff\x58\x04")
    assert body[j + 3] == 3  # 每小节 3 拍


def test_drums_fixed_channel9_no_program():
    tracks = [
        {"program": 0, "notes": [{"t": 0, "d": 1, "n": 60}]},
        {"drums": True, "program": 5,
         "notes": [{"t": 0, "d": 0.5, "n": 36}]},
    ]
    data = render_midi(_spec_one(tracks=tracks))
    bodies = [b for _m, b in _chunks(data)]
    drum_body = bodies[2]
    # note on = 0x90|9 = 0x99，后跟鼓编号 36
    assert bytes([0x99, 36]) in drum_body
    # 鼓轨不允许 program change（0xC9）
    assert 0xC9 not in drum_body


def test_channel_assignment_and_drum9():
    # 8 轨上限：7 条普通轨依次占 ch0-6，鼓轨固定 ch9
    tracks = [{"program": 0, "notes": [{"t": 0, "d": 1, "n": 60}]}
              for _ in range(7)]
    tracks.append({"drums": True,
                   "notes": [{"t": 0, "d": 0.5, "n": 36}]})
    data = render_midi(_spec_one(tracks=tracks))
    bodies = [b for _m, b in _chunks(data)]
    assert bytes([0x90, 60]) in bodies[1]   # 第 1 轨 ch0
    assert bytes([0x96, 60]) in bodies[7]   # 第 7 轨 ch6
    assert bytes([0x99, 36]) in bodies[8]   # 鼓轨 ch9


# ---- clamp / 丢弃 ----

def test_out_of_range_values_clamped():
    tracks = [{"program": 999,
               "notes": [{"t": 0, "d": 1, "n": 999, "v": 999}]}]
    data = render_midi(_spec_one(tracks=tracks))
    body = _chunks(data)[1][1]
    assert bytes([0x7F, 0x7F]) in body  # n/v 都夹到 127


def test_bpm_clamped_and_garbage_default():
    data = render_midi(_spec_one(bpm=99999))
    body = _chunks(data)[0][1]
    i = body.find(b"\xff\x51\x03")
    uspq = struct.unpack(">I", b"\x00" + body[i + 3:i + 6])[0]
    assert uspq == 300000  # bpm 夹到 200
    data2 = render_midi(_spec_one(bpm="慢一点"))
    body2 = _chunks(data2)[0][1]
    i2 = body2.find(b"\xff\x51\x03")
    uspq2 = struct.unpack(">I", b"\x00" + body2[i2 + 3:i2 + 6])[0]
    assert uspq2 == 789473  # 默认 76


def test_notes_beyond_length_dropped():
    tracks = [{"program": 0, "notes": [
        {"t": 0, "d": 1, "n": 60},
        {"t": 50, "d": 1, "n": 62},  # 2 小节共 8 拍，超界
    ]}]
    data = render_midi(_spec_one(tracks=tracks))
    body = _chunks(data)[1][1]
    assert bytes([0x90, 62]) not in body
    assert bytes([0x90, 60]) in body


# ---- 硬上限 ----

def test_missing_tracks_raises():
    with pytest.raises(MidiSpecError):
        render_midi({"bars": 2})


def test_too_many_tracks_raises():
    tracks = [{"program": 0, "notes": []} for _ in range(9)]
    with pytest.raises(MidiSpecError):
        render_midi(_spec_one(tracks=tracks))


def test_too_many_notes_raises():
    notes = [{"t": 0, "d": 0.1, "n": 60} for _ in range(601)]
    with pytest.raises(MidiSpecError):
        render_midi(_spec_one(tracks=[{"program": 0, "notes": notes}], bars=16))


def test_too_many_beats_raises():
    with pytest.raises(MidiSpecError):
        render_midi({"bars": 16, "beats_per_bar": 8,
                     "tracks": [{"program": 0, "notes": []}]})


# ---- VLQ ----

def test_vlq_encoding():
    assert vlq(0) == b"\x00"
    assert vlq(127) == b"\x7f"
    assert vlq(128) == b"\x81\x00"
    assert vlq(16384) == b"\x81\x80\x00"
