"""市场 SQLite 存储。全局唯一，跨用户共享。

布局::

    data/market/market.db
      entries(k, doc, updated_at)             # 元数据（MarketEntry JSON）
      payloads(k, ext, blob, updated_at)      # character/world 信封 JSON（BLOB）
                                              # session 的 zip 原始字节（BLOB）

旧布局 data/market/{entries,payloads}/* 首次打开自动导入，文件改名
``.bak-<时间戳>``，重复启动不会重复导入。payload 统一以 BLOB 保存：
JSON 信封是 UTF-8 编码的 JSON 文本，zip 等二进制原样入库。

所有外部 id / 扩展名一律走 ids.check_safe_name 收口（写入路径抛
InvalidName；读取路径返回 None，与旧实现一致）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ids import check_safe_name
from ..sqlite_store import (
    JsonTable,
    SqliteDB,
    backup_path,
    migrate_legacy_dir,
)
from .models import MarketEntry


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _encode_payload(raw: Any, ext: str) -> bytes:
    if ext == "json":
        return json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8")
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    return str(raw).encode("utf-8")


class MarketStore:
    def __init__(self, base_dir: Path):
        self._base = Path(base_dir)
        # 旧 JSON 布局（迁移来源）
        self._entry_dir = self._base / "entries"
        self._payload_dir = self._base / "payloads"
        self._db = SqliteDB(self._base / "market.db")
        self._entries = JsonTable(self._db, "entries")
        self._db.ensure(
            "CREATE TABLE IF NOT EXISTS payloads ("
            " k TEXT NOT NULL,"
            " ext TEXT NOT NULL,"
            " blob BLOB NOT NULL,"
            " updated_at REAL NOT NULL,"
            " PRIMARY KEY(k, ext)"
            ")"
        )
        self._migrate_legacy()

    # ---- 旧 JSON 导入 ----

    def _migrate_legacy(self) -> None:
        try:
            if self._entry_dir.exists():
                migrate_legacy_dir(
                    self._entry_dir, lambda k, d: self._entries.put(k, d)
                )
            if self._payload_dir.exists():
                for p in sorted(self._payload_dir.iterdir()):
                    if not p.is_file() or p.name.endswith(".tmp") or ".bak-" in p.name:
                        continue
                    if not p.suffix:
                        continue
                    key, ext = p.stem, p.suffix[1:]
                    try:
                        raw = p.read_bytes()
                    except OSError:
                        continue
                    self._put_payload(key, ext, raw)
                    try:
                        p.replace(backup_path(p))
                    except OSError:
                        pass
        except OSError:
            # 旧目录不可读/不可改名不影响打开；已导入内容重复导入是幂等的
            pass

    # ---- 条目 ----

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
        for row in self._db.query("SELECT k, doc FROM entries"):
            try:
                e = MarketEntry.from_dict(json.loads(row[1]))
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
            check_safe_name(mid)
        except Exception:
            return None
        doc = self._entries.get(mid)
        if doc is None:
            return None
        try:
            return MarketEntry.from_dict(doc)
        except Exception:
            return None

    def save_meta(self, e: MarketEntry) -> None:
        check_safe_name(e.id)
        self._entries.put(e.id, e.to_dict())

    # ---- payload ----

    def write_payload(self, mid: str, raw: Any, ext: str) -> None:
        """写快照：ext="json" 时 raw 为 dict（信封）；ext="zip" 时 raw 为 bytes。"""
        check_safe_name(mid)
        check_safe_name(ext, "扩展名")
        self._put_payload(mid, ext, _encode_payload(raw, ext))

    def read_payload_json(self, mid: str) -> Optional[Dict[str, Any]]:
        blob = self._read_payload_bytes(mid, "json")
        if blob is None:
            return None
        try:
            data = json.loads(blob.decode("utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def read_payload_bytes(self, mid: str, ext: str = "zip") -> Optional[bytes]:
        return self._read_payload_bytes(mid, ext)

    def remove(self, mid: str) -> None:
        check_safe_name(mid)
        self._entries.delete(mid)
        for ext in ("json", "zip"):
            self._db.execute(
                "DELETE FROM payloads WHERE k=? AND ext=?", (mid, ext)
            )

    # ---- 内部 ----

    def _put_payload(self, key: str, ext: str, raw: bytes) -> None:
        self._db.execute(
            "INSERT INTO payloads(k, ext, blob, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(k, ext) DO UPDATE SET blob=excluded.blob, "
            "updated_at=excluded.updated_at",
            (key, ext, raw, time.time()),
        )

    def _read_payload_bytes(self, mid: str, ext: str) -> Optional[bytes]:
        try:
            check_safe_name(mid)
            check_safe_name(ext, "扩展名")
        except Exception:
            return None
        row = self._db.one(
            "SELECT blob FROM payloads WHERE k=? AND ext=?", (mid, ext)
        )
        if row is None:
            return None
        try:
            return bytes(row[0])
        except Exception:
            return None
