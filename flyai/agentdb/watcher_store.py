"""监测进程登记 DAL（P4）：表 watchers。

watcher 是脱离主进程的 detached 短脚本进程，agent.db 是重启后
reconcile 的唯一权威依据。刻意不做崩溃自动重启：探活发现死透就
置 stopped，由用户/AI 决定是否再拉起（技术书 §8.3）。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import open_agent_db

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"


class WatcherStore:
    def __init__(self, data_dir) -> None:
        self.data_dir = Path(data_dir)

    def _conn(self, uid: str) -> sqlite3.Connection:
        return open_agent_db(self.data_dir, uid)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return dict(row)

    def register(self, uid: str, session_id: str, script_path: str,
                 pid: int, log_path: str = "", source: str = "watcher",
                 now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "INSERT INTO watchers(session_id, script_path, source, pid, "
                "status, log_path, created_at, stopped_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, 0)",
                (session_id, script_path, source, int(pid),
                 STATUS_RUNNING, log_path, now),
            )
            row = conn.execute(
                "SELECT * FROM watchers WHERE id=?", (cur.lastrowid,)
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def list(self, uid: str, session_id: Optional[str] = None,
             status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM watchers WHERE 1=1"
        params: List[Any] = []
        if session_id is not None:
            sql += " AND session_id=?"
            params.append(session_id)
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC"
        conn = self._conn(uid)
        try:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get(self, uid: str, watcher_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT * FROM watchers WHERE id=?", (int(watcher_id),)
            ).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def count_running(self, uid: str, session_id: str) -> int:
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM watchers "
                "WHERE session_id=? AND status=?",
                (session_id, STATUS_RUNNING),
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    def mark_stopped(self, uid: str, watcher_id: int,
                     now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "UPDATE watchers SET status=?, stopped_at=? "
                "WHERE id=? AND status=?",
                (STATUS_STOPPED, now, int(watcher_id), STATUS_RUNNING),
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_running(self, uid: str) -> List[Dict[str, Any]]:
        return self.list(uid, status=STATUS_RUNNING)
