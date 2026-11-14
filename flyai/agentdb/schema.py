"""agent.db 的版本化 DDL。

每个版本号对应一组按顺序执行的 DDL；迁移时从 PRAGMA user_version 的当前值
逐版本升到 DB_VERSION。新增表/列只追加新版本，绝不修改历史版本的语句。
"""

from __future__ import annotations

from typing import Dict, List

MIGRATIONS: Dict[int, List[str]] = {
    1: [
        # 通用 KV：各子系统的水位线/游标/状态（P0 仅建表，P1 起投入使用）
        """
        CREATE TABLE IF NOT EXISTS agent_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL
        )
        """,
    ],
    2: [
        # AI 自定义工具（P1）：代码本体只在 python -I 短生子进程里 exec
        """
        CREATE TABLE IF NOT EXISTS custom_tools (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            scope          TEXT NOT NULL,
            name           TEXT NOT NULL,
            title          TEXT NOT NULL DEFAULT '',
            description    TEXT NOT NULL,
            params_schema  TEXT NOT NULL,
            code           TEXT NOT NULL,
            enabled        INTEGER NOT NULL DEFAULT 1,
            owner_only     INTEGER NOT NULL DEFAULT 0,
            created_by     TEXT NOT NULL DEFAULT '',
            created_at     REAL NOT NULL,
            updated_at     REAL NOT NULL,
            UNIQUE(scope, name)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_custom_scope ON custom_tools(scope, enabled)",
    ],
    3: [
        # P2 长跑：AI 申请-审批表
        """
        CREATE TABLE IF NOT EXISTS loop_requests (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            task         TEXT NOT NULL,
            reason       TEXT NOT NULL DEFAULT '',
            requester_uid TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
            decided_by   TEXT NOT NULL DEFAULT '',
            decided_at   REAL NOT NULL DEFAULT 0,
            created_at   REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_loop_req_status ON loop_requests(status, created_at)",
        # P2 长跑：运行状态机（服务重启 reconcile 的权威依据，补纯内存 run 的缺口）
        """
        CREATE TABLE IF NOT EXISTS loop_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            task         TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'running',
            -- running|done|stopped|interrupted|error|idle
            request_id   INTEGER NOT NULL DEFAULT 0,
            rounds       INTEGER NOT NULL DEFAULT 0,
            compacts     INTEGER NOT NULL DEFAULT 0,
            compact_note TEXT NOT NULL DEFAULT '',
            result       TEXT NOT NULL DEFAULT '',
            error        TEXT NOT NULL DEFAULT '',
            created_at   REAL NOT NULL,
            ended_at     REAL NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_loop_sess_status ON loop_sessions(status, session_id)",
    ],
}

DB_VERSION = max(MIGRATIONS)
