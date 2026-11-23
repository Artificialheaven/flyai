"""会话落盘去重与条件请求指纹。

覆盖 `docs/会话传输与带宽优化方案.md` L1/B1：
- 单 AI 会话顶层 `character_override` 与成员副本只存一份，读取时重建；
- 群聊多座位保留各自副本；
- `extensions.regex_scripts` 与顶层重复时落盘剥离、读取补回；
- `SessionStore.stat` 提供 ETag 指纹。
"""

import json

from flyai.sessions.json_store import (
    JsonSessionStore,
    session_from_dict,
    session_to_dict,
)
from flyai.sessions.models import RoomMember, Session, Thread


def _member(mid: str, name: str, override=None, is_user: bool = False) -> RoomMember:
    return RoomMember(id=mid, name=name, is_user=is_user, override=override)


def test_single_ai_override_stored_once(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    card = {"name": "小雪", "table": [{"title": "设定", "content": "x"}]}
    s = Session(id="abc", title="单聊")
    s.members = [_member("u1", "你", is_user=True), _member("a1", "小雪", dict(card))]
    s.character_override = dict(card)
    s.threads = [Thread(id="t1", kind="dm", title="小雪", member_ids=["a1"])]
    store.save(s)

    raw = json.loads((tmp_path / "s" / "abc.json").read_text(encoding="utf-8"))
    assert raw["character_override"] is None
    assert raw["members"][1]["override"]["name"] == "小雪"

    loaded = store.get("abc")
    assert loaded.character_override == card
    # 读时重建共享同一对象，避免再次深拷贝整卡
    assert loaded.character_override is loaded.members[1].override


def test_group_keeps_per_seat_overrides():
    members = [
        _member("u1", "你", is_user=True),
        _member("a1", "甲", {"name": "甲"}),
        _member("a2", "乙", {"name": "乙"}),
    ]
    s = Session(id="g1", members=members, character_override={"name": "甲"})
    data = session_to_dict(s)
    # 群聊两个座位不同卡：顶层镜像不能当成员副本的替身
    assert data["character_override"] == {"name": "甲"}
    assert [m["override"]["name"] for m in data["members"][1:]] == ["甲", "乙"]


def test_duplicate_regex_scripts_stripped_and_restored():
    scripts = [{"scriptName": "去广告", "findRegex": "x"}]
    card = {
        "name": "卡",
        "regex_scripts": scripts,
        "extensions": {"regex_scripts": scripts, "world": "w"},
    }
    data = session_to_dict(
        Session(id="r1", members=[_member("a1", "卡", dict(card))])
    )
    stored = data["members"][0]["override"]
    assert stored["regex_scripts"] == scripts
    assert "regex_scripts" not in stored["extensions"]

    # 读取后顶层仍是唯一使用面（导出时由 Character.to_dict 还原扩展字段）
    restored = session_from_dict(data)
    assert restored.members[0].override["regex_scripts"] == scripts


def test_store_stat_fingerprint(tmp_path):
    store = JsonSessionStore(tmp_path / "s")
    s = store.create("t")
    first = store.stat(s.id)
    assert first and first["size"] > 0
    s.title = "t2"
    store.save(s)
    second = store.stat(s.id)
    assert second and second["mtime_ns"] >= first["mtime_ns"]
    assert store.stat("missing") is None
