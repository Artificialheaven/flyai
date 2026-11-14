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
}

DB_VERSION = max(MIGRATIONS)
