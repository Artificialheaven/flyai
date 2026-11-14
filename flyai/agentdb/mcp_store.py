"""MCP server 登记 DAL（P4）：表 mcp_servers。

server 配置集中存 admin 的 agent.db；args/env 以 JSON 文本落库，
tools_json 是最近一次连接测试的工具 schema 缓存——连接失败也不清空，
UI 标注「缓存可能过期」即可（技术书 §8.1 状态机 new|ready|failed）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import open_agent_db

STATUS_NEW = "new"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


class McpServerStore:
    def __init__(self, data_dir) -> None:
        self.data_dir = Path(data_dir)

    def _conn(self, uid: str) -> sqlite3.Connection:
        return open_agent_db(self.data_dir, uid)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        try:
            item["args"] = json.loads(item.get("args") or "[]")
        except ValueError:
            item["args"] = []
        try:
            item["env"] = json.loads(item.get("env") or "{}")
        except ValueError:
            item["env"] = {}
        try:
            item["tools"] = json.loads(item.get("tools_json") or "[]")
        except ValueError:
            item["tools"] = []
        item.pop("tools_json", None)
        return item

    def list(self, uid: str) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            rows = conn.execute(
                "SELECT * FROM mcp_servers ORDER BY id ASC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def list_enabled(self, uid: str) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            rows = conn.execute(
                "SELECT * FROM mcp_servers WHERE enabled=1 ORDER BY id ASC"
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get(self, uid: str, server_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT * FROM mcp_servers WHERE server_id=?", (server_id,)
            ).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def create(self, uid: str, server_id: str, command: str,
               args: Optional[List[str]] = None,
               env: Optional[Dict[str, str]] = None,
               enabled: bool = True,
               now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            try:
                conn.execute(
                    "INSERT INTO mcp_servers(server_id, command, args, env, "
                    "enabled, status, tools_json, last_error, created_at, "
                    "updated_at) VALUES(?, ?, ?, ?, ?, ?, '', '', ?, ?)",
                    (server_id, command,
                     json.dumps(args or [], ensure_ascii=False),
                     json.dumps(env or {}, ensure_ascii=False),
                     1 if enabled else 0, STATUS_NEW, now, now),
                )
            except sqlite3.IntegrityError:
                raise ValueError("server_id 已存在: %s" % server_id)
            row = conn.execute(
                "SELECT * FROM mcp_servers WHERE server_id=?", (server_id,)
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def update(self, uid: str, server_id: str, command: Optional[str] = None,
               args: Optional[List[str]] = None,
               env: Optional[Dict[str, str]] = None,
               enabled: Optional[bool] = None) -> Dict[str, Any]:
        """改连接参数会把状态打回 new（工具缓存保留，待重新测试）。"""
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT * FROM mcp_servers WHERE server_id=?", (server_id,)
            ).fetchone()
            if row is None:
                raise KeyError(server_id)
            new_command = row["command"] if command is None else command
            new_args = row["args"] if args is None else json.dumps(
                args, ensure_ascii=False)
            new_env = row["env"] if env is None else json.dumps(
                env, ensure_ascii=False)
            new_enabled = row["enabled"] if enabled is None else (
                1 if enabled else 0)
            connection_changed = (
                command is not None or args is not None or env is not None
            )
            new_status = STATUS_NEW if connection_changed else row["status"]
            conn.execute(
                "UPDATE mcp_servers SET command=?, args=?, env=?, enabled=?, "
                "status=?, updated_at=? WHERE server_id=?",
                (new_command, new_args, new_env, new_enabled, new_status,
                 time.time(), server_id),
            )
            row = conn.execute(
                "SELECT * FROM mcp_servers WHERE server_id=?", (server_id,)
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def set_enabled(self, uid: str, server_id: str,
                    enabled: bool) -> Dict[str, Any]:
        return self.update(uid, server_id, enabled=enabled)

    def update_connection(self, uid: str, server_id: str, status: str,
                          tools: Optional[List[Dict[str, Any]]] = None,
                          last_error: str = "") -> None:
        """连接测试结果落库；失败时 tools=None 表示保留旧缓存。"""
        conn = self._conn(uid)
        try:
            if tools is None:
                conn.execute(
                    "UPDATE mcp_servers SET status=?, last_error=?, "
                    "updated_at=? WHERE server_id=?",
                    (status, last_error[:1000], time.time(), server_id),
                )
            else:
                conn.execute(
                    "UPDATE mcp_servers SET status=?, tools_json=?, "
                    "last_error=?, updated_at=? WHERE server_id=?",
                    (status,
                     json.dumps(tools, ensure_ascii=False)[:200000],
                     last_error[:1000], time.time(), server_id),
                )
        finally:
            conn.close()

    def delete(self, uid: str, server_id: str) -> bool:
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "DELETE FROM mcp_servers WHERE server_id=?", (server_id,)
            )
            return cur.rowcount > 0
        finally:
            conn.close()
