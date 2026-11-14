"""提示词注册表覆盖 DAL（P4）：表 prompt_overrides。

默认分区文本全部在代码里（proactor/prompt_registry.py），库里只存
被管理员改过的覆盖项；删除覆盖即恢复默认。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import open_agent_db


class PromptOverrideStore:
    def __init__(self, data_dir) -> None:
        self.data_dir = Path(data_dir)

    def _conn(self, uid: str) -> sqlite3.Connection:
        return open_agent_db(self.data_dir, uid)

    def get(self, uid: str, key: str) -> Optional[str]:
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT text FROM prompt_overrides WHERE key=?", (key,)
            ).fetchone()
            return row["text"] if row else None
        finally:
            conn.close()

    def all(self, uid: str) -> Dict[str, str]:
        conn = self._conn(uid)
        try:
            rows = conn.execute(
                "SELECT key, text FROM prompt_overrides"
            ).fetchall()
            return {r["key"]: r["text"] for r in rows}
        finally:
            conn.close()

    def list_rows(self, uid: str) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            rows = conn.execute(
                "SELECT key, text, updated_at FROM prompt_overrides "
                "ORDER BY key ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set(self, uid: str, key: str, text: str,
            now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            conn.execute(
                "INSERT INTO prompt_overrides(key, text, updated_at) "
                "VALUES(?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "text=excluded.text, updated_at=excluded.updated_at",
                (key, text, now),
            )
        finally:
            conn.close()

    def delete(self, uid: str, key: str) -> bool:
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "DELETE FROM prompt_overrides WHERE key=?", (key,)
            )
            return cur.rowcount > 0
        finally:
            conn.close()
