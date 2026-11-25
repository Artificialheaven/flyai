"""用量流水（SQLite）。

历史实现是 `data/<uid>/usage.jsonl` 追加式文本；现在落 `usage.db`
（`usage` 表，一行一条记录，doc 保留原始 JSON 字段），旧 JSONL 首次
打开时自动导入并改名备份。billing / metering 仍按原路径构造本类，
路由读取也走同一实现，调用方无需感知存储细节。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterator

from .sqlite_store import SqliteDB, backup_path


class UsageLedger:
    """单个用户的用量流水库；构造参数为旧的 `usage.jsonl` 路径。"""

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS usage ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts REAL NOT NULL DEFAULT 0, "
        "date TEXT NOT NULL DEFAULT '', "
        "kind TEXT NOT NULL DEFAULT '', "
        "session_id TEXT NOT NULL DEFAULT '', "
        "cost REAL NOT NULL DEFAULT 0, "
        "doc TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS usage_date ON usage(date)",
    )

    def __init__(self, path: Path):
        self.path = Path(path)
        self.db_path = self.path.with_suffix(".db")
        self.db = SqliteDB(self.db_path)
        self.db.ensure(*self._SCHEMA)
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        if not self.path.exists():
            return
        if self.db.scalar("SELECT COUNT(*) FROM usage"):
            return
        rows = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(rec, dict):
                continue
            rows.append(self._row(rec))
        if rows:
            self.db.executemany(
                "INSERT INTO usage(ts, date, kind, session_id, cost, doc) "
                "VALUES(?,?,?,?,?,?)", rows)
        try:
            self.path.replace(backup_path(self.path))
        except OSError:
            pass

    @staticmethod
    def _row(rec: Dict) -> tuple:
        def _float(value) -> float:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        return (
            _float(rec.get("ts")),
            str(rec.get("date") or ""),
            str(rec.get("kind") or ""),
            str(rec.get("session_id") or ""),
            _float(rec.get("cost")),
            json.dumps(rec, ensure_ascii=False, separators=(",", ":")),
        )

    def append(self, record: Dict) -> None:
        """追加一条流水；写失败只记日志，不影响主流程。"""
        try:
            self.db.execute(
                "INSERT INTO usage(ts, date, kind, session_id, cost, doc) "
                "VALUES(?,?,?,?,?,?)", self._row(record))
        except Exception:
            pass

    def records(self) -> Iterator[Dict]:
        for (doc,) in self.db.query("SELECT doc FROM usage ORDER BY id"):
            try:
                rec = json.loads(doc)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                yield rec
