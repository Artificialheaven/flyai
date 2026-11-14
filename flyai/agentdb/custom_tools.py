"""custom_tools 表的数据访问层（同步 API；调用方在 asyncio.to_thread 内使用）。

每个 uid 一个 agent.db，因此行级不需要 uid 列——拿到哪个库就是哪个用户的。
作用域两级：'user'（该用户全部会话）与 'session:<sid>'（单会话），
会话级同名工具覆盖用户级。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from . import open_agent_db

SCOPE_USER = "user"


def session_scope(sid: str) -> str:
    return "session:" + sid


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    d["owner_only"] = bool(d.get("owner_only"))
    return d


def upsert_tool(
    conn: sqlite3.Connection,
    *,
    scope: str,
    name: str,
    title: str,
    description: str,
    params_schema: str,
    code: str,
    owner_only: bool,
    created_by: str,
) -> int:
    """插入或同名覆盖（覆盖保留 id/created_at/enabled），返回行 id。"""
    now = time.time()
    conn.execute(
        "INSERT INTO custom_tools(scope, name, title, description, params_schema, "
        "code, enabled, owner_only, created_by, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?) "
        "ON CONFLICT(scope, name) DO UPDATE SET "
        "title=excluded.title, description=excluded.description, "
        "params_schema=excluded.params_schema, code=excluded.code, "
        "owner_only=excluded.owner_only, updated_at=excluded.updated_at",
        (scope, name, title, description, params_schema, code,
         1 if owner_only else 0, created_by, now, now),
    )
    row = conn.execute(
        "SELECT id FROM custom_tools WHERE scope=? AND name=?", (scope, name)
    ).fetchone()
    return int(row["id"])


def list_all(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM custom_tools ORDER BY scope, id"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_for_scope(conn: sqlite3.Connection, scope: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM custom_tools WHERE scope=?", (scope,)
    ).fetchone()
    return int(row["n"])


def get_by_id(conn: sqlite3.Connection, tool_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM custom_tools WHERE id=?", (tool_id,)
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def set_enabled(conn: sqlite3.Connection, tool_id: int, enabled: bool) -> bool:
    cur = conn.execute(
        "UPDATE custom_tools SET enabled=?, updated_at=? WHERE id=?",
        (1 if enabled else 0, time.time(), tool_id),
    )
    return cur.rowcount > 0


def delete_by_id(conn: sqlite3.Connection, tool_id: int) -> bool:
    cur = conn.execute("DELETE FROM custom_tools WHERE id=?", (tool_id,))
    return cur.rowcount > 0


def list_enabled_for_session(
    conn: sqlite3.Connection, sid: str
) -> List[Dict[str, Any]]:
    """当前会话可见的启用行：user 作用域 + 本会话作用域，同名会话级覆盖用户级。"""
    rows = conn.execute(
        "SELECT * FROM custom_tools WHERE enabled=1 AND scope IN (?, ?)",
        (SCOPE_USER, session_scope(sid)),
    ).fetchall()
    by_name: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = _row_to_dict(r)
        # user 先落，session 后到即覆盖（按 scope 排序已满足顺序，显式判断更稳）
        if d["name"] not in by_name or d["scope"] != SCOPE_USER:
            by_name[d["name"]] = d
    return list(by_name.values())


def open_for(data_dir, uid: str) -> sqlite3.Connection:
    """便捷入口：定位/迁移该用户的 agent.db。"""
    return open_agent_db(data_dir, uid)
