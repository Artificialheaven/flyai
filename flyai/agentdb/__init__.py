"""每用户 sqlite 代理数据库（data/{uid}/agent/agent.db）。

零新依赖：仅用标准库 sqlite3。WAL 提升并发读写体验，PRAGMA user_version
承载 schema 迁移，连接默认外键开启、行工厂为 sqlite3.Row。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .schema import DB_VERSION, MIGRATIONS

__all__ = [
    "DB_VERSION",
    "agent_db_path",
    "connect",
    "migrate",
    "open_agent_db",
    "get_meta",
    "set_meta",
]


def agent_db_path(data_dir: Path, uid: str) -> Path:
    return Path(data_dir) / uid / "agent" / "agent.db"


def connect(path: Path) -> sqlite3.Connection:
    """打开（必要时创建）agent.db，并应用基础 PRAGMA。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None：自动提交，迁移时显式 BEGIN/COMMIT
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """把库从当前 user_version 逐版本迁移到 DB_VERSION，返回迁移后版本。"""
    cur = conn.execute("PRAGMA user_version")
    version = int(cur.fetchone()[0] or 0)
    if version >= DB_VERSION:
        return version
    conn.execute("BEGIN")
    try:
        for ver in range(version + 1, DB_VERSION + 1):
            for ddl in MIGRATIONS[ver]:
                conn.execute(ddl)
        conn.execute("PRAGMA user_version=%d" % DB_VERSION)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return DB_VERSION


def open_agent_db(data_dir: Path, uid: str) -> sqlite3.Connection:
    """便捷入口：定位 per-user 库 → 连接 → 迁移。"""
    conn = connect(agent_db_path(data_dir, uid))
    migrate(conn)
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM agent_meta WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row is not None else default


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO agent_meta(key, value, updated_at) VALUES(?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        (key, "" if value is None else str(value), time.time()),
    )
