"""旧单 AI 会话迁移回归：迁移必须落盘，线程/成员 id 跨 get() 稳定。

曾出现的 bug：迁移只在内存进行，每次 get() 生成新随机 id，前端按
GET 会话返回的线程 id 再加载时 404「窗口不存在」。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Session


def _legacy_session(sid):
    s = Session(id=sid, title="旧会话", character_id="char-1")
    s.messages = [
        Message(role="user", content="你好"),
        Message(role="assistant", content="你好呀"),
    ]
    return s


def test_migration_persisted_with_stable_ids(tmp_path):
    store = JsonSessionStore(base_dir=tmp_path)
    # 直接写出旧形态：顶层 messages，无 members/threads
    s = _legacy_session("abc123")
    legacy = {
        "id": s.id,
        "title": s.title,
        "provider_id": "",
        "character_id": "char-1",
        "world_id": "",
        "created_at": "",
        "updated_at": "",
        "messages": [m.to_dict() for m in s.messages],
        "attributes": {},
        "character_override": None,
        "world_override": None,
    }
    import json

    with open(tmp_path / "abc123.json", "w", encoding="utf-8") as f:
        json.dump(legacy, f, ensure_ascii=False)

    first = store.get("abc123")
    tid = first.threads[0].id
    member_ids = [m.id for m in first.members]
    assert len(first.threads) == 1 and len(first.members) == 2
    # 消息已迁入窗口，顶层清空
    assert len(first.messages) == 0
    assert len(first.threads[0].messages) == 2

    second = store.get("abc123")
    assert second.threads[0].id == tid
    assert [m.id for m in second.members] == member_ids
    # 按首次返回的 id 能直接找到窗口
    assert second.thread(tid) is not None


def test_opening_entry_round_trip_and_legacy_default(tmp_path):
    from flyai.sessions.json_store import session_from_dict, session_to_dict

    s = Session(id="op1", title="开局", opening_entry="开局1")
    data = session_to_dict(s)
    assert data["opening_entry"] == "开局1"
    assert session_from_dict(data).opening_entry == "开局1"

    # 旧会话/存档快照没有该字段：读成空串（按卡默认），行为与改造前一致
    data.pop("opening_entry")
    assert session_from_dict(data).opening_entry == ""

    # 落盘往返
    store = JsonSessionStore(base_dir=tmp_path)
    store.save(s)
    assert store.get("op1").opening_entry == "开局1"
