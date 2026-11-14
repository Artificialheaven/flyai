"""P0 agent.db：建库路径、WAL、user_version 迁移幂等、meta UPSERT。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai import agentdb
from flyai.agentdb import (
    DB_VERSION,
    agent_db_path,
    get_meta,
    migrate,
    open_agent_db,
    set_meta,
)


def test_db_path_layout(tmp_path):
    p = agent_db_path(tmp_path, "u1")
    assert p == tmp_path / "u1" / "agent" / "agent.db"


def test_open_creates_and_migrates(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    try:
        assert (tmp_path / "u1" / "agent" / "agent.db").exists()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION
    finally:
        conn.close()


def test_migrate_idempotent(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    try:
        assert migrate(conn) == DB_VERSION  # 再次迁移不报错、不跳版本
        assert conn.execute("PRAGMA user_version").fetchone()[0] == DB_VERSION
    finally:
        conn.close()


def test_meta_upsert(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    try:
        assert get_meta(conn, "missing") is None
        assert get_meta(conn, "missing", "def") == "def"
        set_meta(conn, "k1", "v1")
        assert get_meta(conn, "k1") == "v1"
        set_meta(conn, "k1", "v2")  # 覆盖而非新增
        assert get_meta(conn, "k1") == "v2"
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_meta WHERE key='k1'"
        ).fetchone()
        assert rows["n"] == 1
        set_meta(conn, "k2", 123)  # 非字符串值落盘前转字符串
        assert get_meta(conn, "k2") == "123"
    finally:
        conn.close()


def test_reopen_keeps_data(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    set_meta(conn, "persist", "yes")
    conn.close()
    conn2 = open_agent_db(tmp_path, "u1")
    try:
        assert get_meta(conn2, "persist") == "yes"
    finally:
        conn2.close()
    assert DB_VERSION >= 1
