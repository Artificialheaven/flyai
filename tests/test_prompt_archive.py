"""真实提示词归档：按锚点用户消息保存每次 provider 请求。"""

import json

from flyai.prompt_archive import append_call, delete_session, load_turn
from flyai.schemas import Message


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
    raw = json.loads((tmp_path / "admin" / "prompts" / "s1" / "u1.json").read_text("utf-8"))
    assert raw["calls"][0]["messages"][0]["content"] == "a"
