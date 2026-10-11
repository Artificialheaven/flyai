"""结构化 spec -> 标准 MIDI 文件（纯标准库，无第三方依赖）。

供 ``galgame_compose_music`` 工具使用：AI 输出音符级 JSON，这里渲染为 format 1
多轨 MIDI，前端用 FluidSynth WASM 播放。实现提炼自 test/make_music.py 与
test/make_multitrack.py（VLQ / Track / tempo 轨 / ch9 鼓轨）。

spec 结构::

    {
      "bpm": 76, "bars": 8, "beats_per_bar": 4,
      "tracks": [
        {"program": 0, "drums": false, "volume": 70, "pan": 64, "reverb": 50,
         "notes": [{"t": 0.0, "d": 1.0, "n": 60, "v": 64}, ...]}
      ]
    }
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple

PPQ = 480

# 硬上限（防模型刷爆）
_MIN_BPM, _MAX_BPM = 40, 200
_MAX_TRACKS = 8
_MAX_NOTES = 600
_MAX_BARS = 16
_MAX_BEATS = 64
_DRUM_CH = 9  # MIDI 规定的打击乐专用通道


class MidiSpecError(ValueError):
    """spec 不合法（无法通过 clamp 修复的问题）。"""


def vlq(value: int) -> bytes:
    """变长整数编码（MIDI delta-time）。"""
    bytes_ = [value & 0x7F]
    value >>= 7
    while value:
        bytes_.append((value & 0x7F) | 0x80)
        value >>= 7
    bytes_.reverse()
    return bytes(bytes_)


class _Track:
    def __init__(self, channel: int):
        self.channel = channel
        # (tick, status, data1, data2)；data2 为 None 时是两字节事件
        self.events: List[Tuple[int, int, int, Any]] = []

    def add(self, tick: int, status: int, d1: int, d2=None) -> None:
        self.events.append((tick, status, d1, d2))

    def program(self, tick: int, program: int) -> None:
        self.add(tick, 0xC0 | self.channel, program)

    def cc(self, tick: int, controller: int, value: int) -> None:
        self.add(tick, 0xB0 | self.channel, controller, value)

    def note(self, tick: int, dur_ticks: int, note: int, velocity: int) -> None:
        off = tick + max(1, dur_ticks - 10)  # 轻微收短，避免粘连
        self.add(tick, 0x90 | self.channel, note, velocity)
        self.add(off, 0x80 | self.channel, note, 0)

    def render(self) -> bytes:
        self.events.sort(key=lambda e: e[0])
        out = bytearray()
        prev = 0
        for tick, status, d1, d2 in self.events:
            out += vlq(tick - prev)
            prev = tick
            out += struct.pack("BB", status, d1)
            if d2 is not None:
                out += struct.pack("B", d2)
        out += vlq(0) + b"\xff\x2f\x00"  # End of Track
        return b"MTrk" + struct.pack(">I", len(out)) + bytes(out)


def _clamp(v: float, lo: int, hi: int, default: int) -> int:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    if n != n or n in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return max(lo, min(hi, int(round(n))))


def _tempo_track(bpm: int, total_beats: int, bpb: int) -> bytes:
    uspq = int(60_000_000 / bpm)  # 每四分音符微秒数
    raw = bytearray()
    raw += vlq(0) + b"\xff\x51\x03" + struct.pack(">I", uspq)[1:]  # Set Tempo
    # 拍号：第 4 字节是每四分音符中的三十二分音符数，固定 8
    raw += vlq(0) + b"\xff\x58\x04" + bytes([bpb, 2, 24, 8])
    raw += vlq(int(total_beats * PPQ)) + b"\xff\x2f\x00"
    return b"MTrk" + struct.pack(">I", len(raw)) + bytes(raw)


def _normalize_note(raw: Any) -> Dict[str, int]:
    if not isinstance(raw, dict):
        raise MidiSpecError("note 必须是对象")
    t = raw.get("t", raw.get("start", 0))
    d = raw.get("d", raw.get("dur", raw.get("duration", 1)))
    try:
        t = float(t)
        d = float(d)
    except (TypeError, ValueError):
        raise MidiSpecError("note 的 t/d 必须是数字")
    if t != t or t < 0 or d != d or d <= 0:
        raise MidiSpecError("note 的时间非法")
    return {
        "tick": max(0, int(round(t * PPQ))),
        "dur": max(1, int(round(d * PPQ))),
        "note": _clamp(raw.get("n", raw.get("note", 0)), 0, 127, 60),
        "vel": _clamp(raw.get("v", raw.get("velocity", 64)), 1, 127, 64),
    }


def render_midi(spec: Dict[str, Any]) -> bytes:
    """把 spec 渲染为标准 MIDI 字节串。数值类问题 clamp；结构问题抛 MidiSpecError。"""
    if not isinstance(spec, dict):
        raise MidiSpecError("spec 必须是对象")

    bpm = _clamp(spec.get("bpm", 76), _MIN_BPM, _MAX_BPM, 76)
    bpb = _clamp(spec.get("beats_per_bar", 4), 2, 8, 4)
    bars = _clamp(spec.get("bars", 8), 1, _MAX_BARS, 8)
    total_beats = bars * bpb
    if total_beats > _MAX_BEATS:
        raise MidiSpecError(
            "总拍数超过上限（%d 拍）：缩短小节数或拍号" % _MAX_BEATS
        )

    raw_tracks = spec.get("tracks") or []
    if not isinstance(raw_tracks, list) or not raw_tracks:
        raise MidiSpecError("至少提供 1 条音轨")
    if len(raw_tracks) > _MAX_TRACKS:
        raise MidiSpecError("音轨数超过上限（%d）" % _MAX_TRACKS)

    note_count = sum(len(tr.get("notes") or []) for tr in raw_tracks
                     if isinstance(tr, dict))
    if note_count > _MAX_NOTES:
        raise MidiSpecError("音符总数超过上限（%d）" % _MAX_NOTES)

    # 通道分配：鼓轨固定 ch9，其余依次占 0-8、10-15
    free_channels = [c for c in range(16) if c != _DRUM_CH]
    built: List[_Track] = []
    for tr in raw_tracks:
        if not isinstance(tr, dict):
            raise MidiSpecError("track 必须是对象")
        is_drums = bool(tr.get("drums", False))
        if is_drums:
            ch = _DRUM_CH
        else:
            if not free_channels:
                raise MidiSpecError("非鼓音轨数超过上限（14）")
            ch = free_channels.pop(0)
        track = _Track(ch)

        # 音色 / 音量 / 声像 / 混响（鼓轨不发 program change）
        if not is_drums:
            program = _clamp(tr.get("program", 0), 0, 127, 0)
            track.program(0, program)
        track.cc(0, 7, _clamp(tr.get("volume", 70), 0, 127, 70))
        track.cc(0, 10, _clamp(tr.get("pan", 64), 0, 127, 64))
        track.cc(0, 91, _clamp(tr.get("reverb", 40), 0, 127, 40))

        max_tick = total_beats * PPQ
        for raw_note in (tr.get("notes") or []):
            n = _normalize_note(raw_note)
            # 超出曲目长度范围的音符直接丢弃
            if n["tick"] >= max_tick or n["dur"] <= 0:
                continue
            if n["tick"] + n["dur"] > max_tick:
                n["dur"] = max_tick - n["tick"]
            track.note(n["tick"], n["dur"], n["note"], n["vel"])
        built.append(track)

    body = [_tempo_track(bpm, total_beats, bpb)]
    body.extend(t.render() for t in built)
    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(body), PPQ)
    return header + b"".join(body)
