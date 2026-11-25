"""真实提示词归档：按锚点用户消息保存每次 provider 请求。"""

import json
import sqlite3

from flyai.prompt_archive import append_call, delete_session, load_turn
from flyai.schemas import Message


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT k, doc FROM turns").fetchall()
    finally:
        conn.close()


def test_append_and_load_roundtrip(tmp_path):
    msgs = [
        Message(role="system", content="系统"),
        Message(role="user", content="你好"),
    ]
    append_call(
        tmp_path, "admin", "s1", "u1", "main", msgs,
        [{"name": "web_search", "description": "搜索"}],
        {"max_tokens": 512},
    )
    append_call(
        tmp_path, "admin", "s1", "u1", "main",
        [Message(role="assistant", content="回复")], [], {},
    )
    data = load_turn(tmp_path, "admin", "s1", "u1")
    assert data is not None
    assert data["anchor_id"] == "u1"
    assert len(data["calls"]) == 2
    first = data["calls"][0]
    assert first["kind"] == "main"
    assert [m["role"] for m in first["messages"]] == ["system", "user"]
    assert first["messages"][0]["content"] == "系统"
    assert first["tools"][0]["name"] == "web_search"
    assert first["params"]["max_tokens"] == 512


def test_long_content_truncated(tmp_path):
    append_call(
        tmp_path, "admin", "s1", "u2", "main",
        [Message(role="user", content="x" * 250_000)], [], {},
    )
    data = load_turn(tmp_path, "admin", "s1", "u2")
    content = data["calls"][0]["messages"][0]["content"]
    assert len(content) < 250_000
    assert "已截断" in content


def test_delete_session(tmp_path):
    append_call(tmp_path, "admin", "s1", "u1", "main", [Message(role="user", content="a")], [], {})
    delete_session(tmp_path, "admin", "s1")
    assert load_turn(tmp_path, "admin", "s1", "u1") is None


def test_other_user_isolated(tmp_path):
    append_call(tmp_path, "admin", "s1", "u1", "main", [Message(role="user", content="a")], [], {})
    assert load_turn(tmp_path, "other", "s1", "u1") is None
    db = tmp_path / "admin" / "prompts.db"
    assert db.is_file()
    rows = _rows(db)
    assert len(rows) == 1
    doc = json.loads(rows[0][1])
    assert doc["calls"][0]["messages"][0]["content"] == "a"


def test_legacy_json_migrated_once(tmp_path):
    import flyai.prompt_archive as pa

    legacy = tmp_path / "admin" / "prompts" / "s1" / "u1.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({
        "session_id": "s1", "message_id": "u1",
        "calls": [{"kind": "main", "messages": [], "tools": [], "params": {}}],
    }), "utf-8")
    broken = tmp_path / "admin" / "prompts" / "s2" / "u9.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json", "utf-8")

    data = load_turn(tmp_path, "admin", "s1", "u1")
    assert data is not None and data["message_id"] == "u1"
    assert not legacy.exists()
    assert list(legacy.parent.glob("u1.json.bak-*"))
    # 坏 JSON 跳过且不删原文件
    assert load_turn(tmp_path, "admin", "s2", "u9") is None
    assert broken.exists()
    assert len(_rows(tmp_path / "admin" / "prompts.db")) == 1

    # 模拟重启：清掉进程内缓存与表内容，已备份的旧文件不应被再次导入
    pa._TABLES.clear()
    pa._MIGRATED.clear()
    pa._table(tmp_path / "admin" / "prompts.db").clear()
    assert load_turn(tmp_path, "admin", "s1", "u1") is None

