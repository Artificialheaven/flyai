"""市场 JSON/zip 存储，原子写入。全局唯一，跨用户共享。

磁盘布局::

    data/market/
      entries/<entry_id>.json     # 元数据（MarketEntry）
      payloads/<entry_id>.json    # character/world：导出信封；session：v3 信封 dict
      payloads/<entry_id>.zip     # session：自包含 zip 原始字节（含素材）

所有外部 id 一律走 ids.safe_child 收口，防路径穿越。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ids import safe_child
from .models import MarketEntry


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class MarketStore:
    def __init__(self, base_dir: Path):
        self._base = Path(base_dir)
        self._entry_dir = self._base / "entries"
        self._payload_dir = self._base / "payloads"
        self._entry_dir.mkdir(parents=True, exist_ok=True)
        self._payload_dir.mkdir(parents=True, exist_ok=True)

    def list(
        self,
        kind: Optional[str] = None,
        q: Optional[str] = None,
        viewer_level: Optional[int] = None,
        author_uid: Optional[str] = None,
    ) -> List[MarketEntry]:
        """列出条目。viewer_level 给定（非 admin 场景）时只返回门槛达标的；
        author_uid 给定只返回该发布者的；按 updated_at 倒序。"""
        out: List[MarketEntry] = []
        for p in self._entry_dir.glob("*.json"):
            try:
                e = MarketEntry.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
            if kind and e.kind != kind:
                continue
            if author_uid is not None and e.author_uid != author_uid:
                continue
            if viewer_level is not None and e.min_level > viewer_level:
                continue
            if q:
                needle = q.lower()
                hay = (e.title + "\n" + e.intro + "\n" + e.author_name).lower()
                if needle not in hay:
                    continue
            out.append(e)
        out.sort(key=lambda e: e.updated_at or "", reverse=True)
        return out

    def get(self, mid: str) -> Optional[MarketEntry]:
        try:
            p = safe_child(self._entry_dir, f"{mid}.json")
        except Exception:
            return None
        if not p.exists():
            return None
        try:
            return MarketEntry.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save_meta(self, e: MarketEntry) -> None:
        self._write_json(self._entry_dir, f"{e.id}.json", e.to_dict())

    def write_payload(self, mid: str, raw: Any, ext: str) -> None:
        """写快照：ext="json" 时 raw 为 dict（信封）；ext="zip" 时 raw 为 bytes。"""
        p = safe_child(self._payload_dir, f"{mid}.{ext}")
        tmp = p.with_suffix(f".{ext}.tmp")
        if ext == "json":
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
        else:
            with open(tmp, "wb") as f:
                f.write(raw)
        os.replace(tmp, p)

    def read_payload_json(self, mid: str) -> Optional[Dict[str, Any]]:
        return self._read_json(safe_child(self._payload_dir, f"{mid}.json"))

    def read_payload_bytes(self, mid: str, ext: str = "zip") -> Optional[bytes]:
        try:
            p = safe_child(self._payload_dir, f"{mid}.{ext}")
        except Exception:
            return None
        if not p.exists():
            return None
        try:
            return p.read_bytes()
        except Exception:
            return None

    def remove(self, mid: str) -> None:
        safe_child(self._entry_dir, f"{mid}.json").unlink(missing_ok=True)
        for ext in ("json", "zip"):
            safe_child(self._payload_dir, f"{mid}.{ext}").unlink(missing_ok=True)

    @staticmethod
    def _read_json(p: Path) -> Optional[Dict[str, Any]]:
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _write_json(directory: Path, name: str, data: Dict[str, Any]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        p = safe_child(directory, name)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
