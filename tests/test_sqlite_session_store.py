"""SQLite 会话存储：读写保真、增量更新、搜索、列表、旧 JSON 迁移。"""

import json
import time
from pathlib import Path

import pytest

from flyai.schemas import Message
from flyai.sessions.models import Session, Thread
from flyai.sessions.json_store import session_from_dict
from flyai.sessions.sqlite_store import SqliteSessionStore


def _session_with_thread() -> Session:
    s = Session(id="s1", title="测试会话", provider_id="p1",
                character_id="c1", world_id="w1")
    s.created_at = "2026-01-01T00:00:00"
    s.updated_at = "2026-01-01T00:00:00"
    s.threads = [Thread(id="t1", kind="group", title="测试窗口")]
    t = s.threads[0]
    t.messages = [
        Message(id="m1", role="user", name="阿明", content="今天天气不错，我们去公园散步"),
        Message(id="m2", role="assistant", name="小美", content="好呀，顺便买点水果"),
    ]
    s.messages = []
    return s


def test_roundtrip_and_incremental(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "sessions")
    s = _session_with_thread()
    store.save(s, touch=False)
    got = store.get("s1")
    assert got is not None
    assert got.title == "测试会话" and got.provider_id == "p1"
    assert len(got.threads) == 1
    assert [m.content for m in got.threads[0].messages] == [
        "今天天气不错，我们去公园散步", "好呀，顺便买点水果"]
    # 再存一次不应改变行数/哈希
    before = [tuple(r) for r in store.db.query("SELECT rowid, hash FROM messages ORDER BY rowid")]
    store.save(got, touch=False)
    after = [tuple(r) for r in store.db.query("SELECT rowid, hash FROM messages ORDER BY rowid")]
    assert before == after
    # 追加一条只新增一行
    got.threads[0].messages.append(
        Message(id="m3", role="assistant", name="小美", content="水果店在街角"))
    store.save(got, touch=False)
    rows = [tuple(r) for r in store.db.query("SELECT id, seq FROM messages WHERE thread_id='t1' ORDER BY seq")]
    assert rows == [("m1", 0), ("m2", 1), ("m3", 2)]


def test_list_summary(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "sessions")
    s = _session_with_thread()
    store.save(s, touch=False)
    out = store.list()
    assert len(out) == 1
    item = out[0]
    assert item.id == "s1" and item.message_count == 2
    assert item.messages == [] and item.title == "测试会话"


def test_search(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "sessions")
    store.save(_session_with_thread(), touch=False)
    # 3 字以上走 FTS
    hit = store.message_search("天气不错")
    assert "s1" in hit and hit["s1"][0] == 1
    assert "公园" in hit["s1"][1]
    # 2 字中文回退 LIKE
    hit2 = store.message_search("水果")
    assert "s1" in hit2
    # 名字命中
    assert "s1" in store.message_search("阿明")
    # 无命中
    assert store.message_search("不存在的词") == {}
    # 删除后不可检索
    s = store.get("s1")
    s.threads[0].messages.pop()
    store.save(s, touch=False)
    assert store.message_search("水果") == {}


def test_delete_and_stat(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "sessions")
    store.save(_session_with_thread(), touch=False)
    st = store.stat("s1")
    assert st and st["size"] > 0 and st["mtime_ns"] > 0
    store.delete("s1")
    assert store.get("s1") is None
    assert store.stat("s1") is None
    assert store.list() == []


def test_legacy_json_import(tmp_path: Path):
    from flyai.sessions.json_store import session_to_dict

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    s = _session_with_thread()
    (sessions / "s1.json").write_text(
        json.dumps(session_to_dict(s), ensure_ascii=False, indent=2), encoding="utf-8")
    store = SqliteSessionStore(sessions)
    got = store.get("s1")
    assert got is not None and len(got.threads[0].messages) == 2
    # 原文件已改名备份，二次打开不重复导入
    assert not (sessions / "s1.json").exists()
    assert list(sessions.glob("s1.json.bak-*"))
    again = SqliteSessionStore(sessions)
    assert again.get("s1") is not None


def test_top_level_messages_searchable(tmp_path: Path):
    store = SqliteSessionStore(tmp_path / "sessions")
    s = Session(id="s2", title="旧会话", provider_id="p")
    s.messages = [Message(id="m9", role="user", content="旧版顶层消息")]
    store.save(s, touch=False)
    assert "s2" in store.message_search("顶层消息")
    # 读取时按 JSON store 同样的规则 bootstrap 成房间窗口，消息不丢
    got = store.get("s2")
    texts = [m.content for t in got.threads for m in t.messages] + [
        m.content for m in got.messages]
    assert "旧版顶层消息" in texts
