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
    4: [
        # P3 心情：每（会话,角色）一行的锚点状态 + 「只宣布一次」状态机
        """
        CREATE TABLE IF NOT EXISTS mood_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            character_id    TEXT NOT NULL,
            mood            REAL NOT NULL DEFAULT 70,
            dims            TEXT NOT NULL DEFAULT '{}',
            updated_at      REAL NOT NULL DEFAULT 0,
            inject_count    INTEGER NOT NULL DEFAULT 0,
            inject_date     TEXT NOT NULL DEFAULT '',
            announce_status TEXT NOT NULL DEFAULT '',  -- ''|pending|injected
            announce_text   TEXT NOT NULL DEFAULT '',
            UNIQUE(session_id, character_id)
        )
        """,
        # P3 心情判定流水（按会话裁剪保留 N 条）
        """
        CREATE TABLE IF NOT EXISTS mood_timeline (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            character_id TEXT NOT NULL,
            delta        REAL NOT NULL DEFAULT 0,
            dims         TEXT NOT NULL DEFAULT '{}',
            reason       TEXT NOT NULL DEFAULT '',
            confidence   INTEGER NOT NULL DEFAULT 0,
            source       TEXT NOT NULL DEFAULT 'human',
            created_at   REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_mood_tl ON mood_timeline(session_id, id)",
        # P3 第一人称自我记忆（全局视角，不绑角色卡）
        """
        CREATE TABLE IF NOT EXISTS self_memories (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            statement      TEXT NOT NULL,
            tags           TEXT NOT NULL DEFAULT '',
            importance     REAL NOT NULL DEFAULT 0.5,
            superseded     INTEGER NOT NULL DEFAULT 0,
            supersedes_id  INTEGER NOT NULL DEFAULT 0,
            created_at     REAL NOT NULL,
            last_accessed  REAL NOT NULL DEFAULT 0,
            access_count   INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_self_mem_active ON self_memories(superseded, id)",
        # P3 自我记忆每日写配额（本地自然日）
        """
        CREATE TABLE IF NOT EXISTS self_memory_state (
            date          TEXT PRIMARY KEY,
            created_count INTEGER NOT NULL DEFAULT 0
        )
        """,
        # P3 回忆链审计流水（永不拼进模型上下文）
        """
        CREATE TABLE IF NOT EXISTS self_memory_traces (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            kind       TEXT NOT NULL,  -- recall|revise|supersede
            memory_id  INTEGER NOT NULL DEFAULT 0,
            detail     TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )
        """,
        # P3 约定/待办与到期提醒（与剧情台账职责分离）
        """
        CREATE TABLE IF NOT EXISTS commitments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            owner_name TEXT NOT NULL DEFAULT '',
            content    TEXT NOT NULL,
            due_at     REAL NOT NULL DEFAULT 0,
            status     TEXT NOT NULL DEFAULT 'open',  -- open|closed|cancelled
            reminded   INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            closed_at  REAL NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_commit_due ON commitments(status, reminded, due_at)",
        # P3 定时作业：once/daily/weekly，严格未来语义
        """
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            mode         TEXT NOT NULL,  -- once|daily|weekly
            hhmm         TEXT NOT NULL,
            weekday      INTEGER NOT NULL DEFAULT -1,
            brief        TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            next_run_at  REAL NOT NULL,
            last_run_at  REAL NOT NULL DEFAULT 0,
            last_status  TEXT NOT NULL DEFAULT '',
            created_at   REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_jobs_due ON scheduled_jobs(enabled, next_run_at)",
        # P3 会话内自建轮询（每会话上限由配置约束）
        """
        CREATE TABLE IF NOT EXISTS polls (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id         TEXT NOT NULL,
            title              TEXT NOT NULL DEFAULT '',
            probability        INTEGER NOT NULL DEFAULT 10,
            wake_every_n       INTEGER NOT NULL DEFAULT 20,
            active_hours       REAL NOT NULL DEFAULT 12,
            ticks_since_fire   INTEGER NOT NULL DEFAULT 0,
            enabled            INTEGER NOT NULL DEFAULT 1,
            created_at         REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_polls_sess ON polls(session_id, enabled)",
        # P3 主动行为状态：会话 opt-in 开关 + 保底计数/冷却（重启不丢）
        """
        CREATE TABLE IF NOT EXISTS proactor_state (
            session_id        TEXT PRIMARY KEY,
            enabled           INTEGER NOT NULL DEFAULT 0,
            wake_miss_streak  INTEGER NOT NULL DEFAULT 0,
            last_wake_at      REAL NOT NULL DEFAULT 0,
            last_speakup_at   REAL NOT NULL DEFAULT 0,
            -- 最后一条真人消息时间：活跃门依据（消息模型本身无时间戳）
            last_human_at     REAL NOT NULL DEFAULT 0,
            updated_at        REAL NOT NULL
        )
        """,
    ],
    5: [
        # P4 MCP stdio server 登记：工具 schema 缓存落库，停机仍可查看
        """
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id   TEXT NOT NULL UNIQUE,
            command     TEXT NOT NULL,
            args        TEXT NOT NULL DEFAULT '[]',
            env         TEXT NOT NULL DEFAULT '{}',
            enabled     INTEGER NOT NULL DEFAULT 1,
            -- new|ready|failed：连接测试后更新；failed 不删除缓存
            status      TEXT NOT NULL DEFAULT 'new',
            tools_json  TEXT NOT NULL DEFAULT '[]',
            last_error  TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            updated_at  REAL NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_mcp_enabled ON mcp_servers(enabled)",
        # P4 监测进程：detached 子进程登记，重启 reconcile 的权威依据
        """
        CREATE TABLE IF NOT EXISTS watchers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            script_path TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'watcher',
            pid         INTEGER NOT NULL DEFAULT 0,
            -- running|stopped：刻意不做崩溃自动重启，死透即 stopped
            status      TEXT NOT NULL DEFAULT 'running',
            log_path    TEXT NOT NULL DEFAULT '',
            created_at  REAL NOT NULL,
            stopped_at  REAL NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_watchers_status ON watchers(status, session_id)",
        # P4 提示词注册表覆盖：只存被改过的分区，默认文本在代码里
        """
        CREATE TABLE IF NOT EXISTS prompt_overrides (
            key        TEXT PRIMARY KEY,
            text       TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """,
    ],
}

DB_VERSION = max(MIGRATIONS)
