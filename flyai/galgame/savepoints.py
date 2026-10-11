"""Galgame 存档点：会话完整快照（消息/场景/语音映射），按「用户 + 会话」存放。

目录结构::

    data/<uid>/savepoints/<sid>/
      index.json
      sp_<时间戳>_<hex8>.json   # 完整会话快照

资产（背景/立绘/音乐）文件不复制：它们永不删除、快照只引用 URL。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..ids import InvalidName, check_safe_name

# 每个会话最多保留的存档点数；超出后最旧的自动淘汰（连同快照文件）
MAX_SAVEPOINTS = 30


@dataclass
class SavePointRecord:
    id: str
    name: str
    created_at: str
    messages: int      # 快照时主房间第一条线程的消息数（展示用）
    file: str          # 目录内快照文件名
    thumb: str = ""    # 快照时场景背景图 url（清单缩略图，可空）

    def to_dict(self) -> Dict:
        return asdict(self)


def _extract_thumb(snapshot: Dict) -> str:
    """取快照场景背景图 URL；结构异常时静默返回空串。"""
    scene = snapshot.get("galgame")
    if not isinstance(scene, dict):
        return ""
    bg = scene.get("background")
    if not isinstance(bg, dict):
        return ""
    url = bg.get("url")
    return url if isinstance(url, str) else ""


class SavePointStore:
    """存档点文件 + index.json 存储（与资产库同一套原子写模式）。"""

    def __init__(self, data_dir: Path, uid: str):
        self.root = Path(data_dir) / uid / "savepoints"

    def _key_dir(self, sid: str) -> Path:
        check_safe_name(sid, "会话 ID")
        d = self.root / sid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_index(self, d: Path) -> List[Dict]:
        p = d / "index.json"
        if not p.exists():
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write_index(self, d: Path, items: List[Dict]) -> None:
        p = d / "index.json"
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def list(self, sid: str) -> List[SavePointRecord]:
        d = self._key_dir(sid)
        out = []
        for item in self._load_index(d):
            if isinstance(item, dict) and item.get("id") and item.get("file"):
                try:
                    out.append(SavePointRecord(**item))
                except TypeError:
                    continue
        return out

    def create(self, sid: str, snapshot: Dict, name: str = "") -> SavePointRecord:
        d = self._key_dir(sid)
        stamp = time.strftime("%Y%m%d%H%M%S")
        fname = "sp_%s_%s.json" % (stamp, secrets.token_hex(4))
        tmp = d / (fname + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp, d / fname)

        threads = snapshot.get("threads") or []
        msg_count = len(threads[0].get("messages") or []) if threads else 0
        default_name = "存档点 · " + time.strftime("%m-%d %H:%M")
        rec = SavePointRecord(
            id=secrets.token_hex(8),
            name=(name or "").strip()[:80] or default_name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            messages=msg_count,
            file=fname,
            thumb=_extract_thumb(snapshot),
        )
        items = self._load_index(d)
        items.append(rec.to_dict())
        # 超过上限：最旧的存档点连带快照文件一起淘汰
        if len(items) > MAX_SAVEPOINTS:
            expired = items[:len(items) - MAX_SAVEPOINTS]
            items = items[len(items) - MAX_SAVEPOINTS:]
            for it in expired:
                try:
                    (d / it["file"]).unlink()
                except (OSError, KeyError):
                    pass
        self._write_index(d, items)
        return rec

    def load_snapshot(self, sid: str, savepoint_id: str) -> Optional[Dict]:
        check_safe_name(savepoint_id, "存档点 ID")
        rec = None
        for item in self._load_index(self._key_dir(sid)):
            if isinstance(item, dict) and item.get("id") == savepoint_id:
                rec = item
                break
        if rec is None:
            return None
        p = self._key_dir(sid) / rec["file"]
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def delete(self, sid: str, savepoint_id: str) -> bool:
        d = self._key_dir(sid)
        items = self._load_index(d)
        kept, removed = [], None
        for item in items:
            if isinstance(item, dict) and item.get("id") == savepoint_id:
                removed = item
            else:
                kept.append(item)
        if removed is None:
            return False
        try:
            (d / removed["file"]).unlink()
        except OSError:
            pass
        self._write_index(d, kept)
        return True
