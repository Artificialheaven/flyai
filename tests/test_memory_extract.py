"""结构化批量抽取：条目冲突消解 + 节奏触发（P4）。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.character_memory import CharacterMemoryStore
from flyai.memory.entries import EntryBook, MemoryEntry
from flyai.memory.extract import (
    apply_entry_ops,
    maybe_structured_extract,
)
from flyai.providers.base import StreamEventType
from flyai.schemas import Message


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_apply_entry_ops_add():
    book = EntryBook()
    report = apply_entry_ops(book, [
        {"text": "用户是杂货铺老板娘", "op": "add",
         "entities": ["用户"], "importance": 0.8},
    ])
    assert report == {"added": 1, "updated": 0}
    assert book.entries[0].source == "extract"
    assert book.entries[0].importance == 0.8


def test_apply_entry_ops_update_matches_entity():
    book = EntryBook(entries=[
        MemoryEntry(text="银钥匙在用户手中", entities=["银钥匙"]),
    ])
    report = apply_entry_ops(book, [
        {"text": "银钥匙转交给了老板娘保管", "op": "update",
         "entities": ["银钥匙"]},
    ])
    assert report["updated"] == 1 and report["added"] == 0
    assert book.entries[0].text == "银钥匙转交给了老板娘保管"
    assert len(book.entries[0].history) == 1
    assert "用户手中" in book.entries[0].history[0]["text"]


def test_apply_entry_ops_strong_similarity_inplace():
    book = EntryBook(entries=[
        MemoryEntry(text="用户特别怕水这件事"),
    ])
    # 无 op 标注但高度相似 → 视为更新
    report = apply_entry_ops(book, [
        {"text": "用户特别怕水这件事情"},
    ])
    assert report["updated"] == 1


def test_apply_entry_ops_medium_similarity_supersedes():
    book = EntryBook(entries=[
        MemoryEntry(text="银钥匙被放在了吧台抽屉的最里面那一层"),
    ])
    report = apply_entry_ops(book, [
        {"text": "银钥匙其实已经被老板娘收进了保险柜"},
    ])
    # 既不是强相似也没有实体交集 → 新增（旧条目继续保留）
    assert report["added"] == 1
    assert len(book.entries) == 2


def test_apply_entry_ops_update_without_match_becomes_add():
    book = EntryBook()
    report = apply_entry_ops(book, [
        {"text": "全新事实", "op": "update"},
    ])
    assert report["added"] == 1


def test_apply_entry_ops_ignores_empty():
    book = EntryBook()
    report = apply_entry_ops(book, [{"text": "  ", "op": "add"}])
    assert report["added"] == 0 and book.entries == []


class _JsonProvider:
    """无工具 provider：把预置 JSON 作为正文流式返回。"""

    def __init__(self, payload):
        self._text = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    async def stream_chat(self, messages, tool_specs=None):
        self.calls += 1
        yield SimpleNamespace(
            type=StreamEventType.CONTENT_DELTA, text=self._text
        )


def _turns(n):
    msgs = []
    for i in range(n):
        msgs.append(Message(role="user", content="第{0}轮问题".format(i)))
        msgs.append(Message(role="assistant", content="第{0}轮回答".format(i)))
    return msgs


def test_maybe_extract_runs_at_cadence_and_marks(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    payload = {
        "entries": [{"text": "用户怕水", "op": "add",
                     "entities": ["用户"]}],
        "ledger": [{"kind": "item", "name": "银钥匙",
                    "state": "用户手中", "op": "upsert"}],
    }
    provider = _JsonProvider(payload)

    # 3 轮、节奏 5 → 不触发
    r1 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", session_id="sid-1",
        history=_turns(3), provider=provider, every=5,
    ))
    assert r1 is None and provider.calls == 0

    # 5 轮 → 触发
    r2 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", session_id="sid-1",
        history=_turns(5), provider=provider, every=5,
    ))
    assert r2 is not None
    assert r2["entries"]["added"] == 1
    assert r2["ledger"]["added"] == 1
    assert provider.calls == 1

    book = store.load_book("TA")
    assert book.extract_markers["sid-1"] == 5
    assert any(e.text == "用户怕水" for e in book.entries)
    assert store.load_ledger("TA")["item"][0].name == "银钥匙"

    # 再发同样 5 轮（marker 已记 5，未见新增）→ 不重复触发
    r3 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", session_id="sid-1",
        history=_turns(5), provider=provider, every=5,
    ))
    assert r3 is None and provider.calls == 1


def test_maybe_extract_increments_after_marker(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    provider = _JsonProvider({"entries": [], "ledger": []})
    # 预置 marker=3
    book = store.load_book("TA")
    book.extract_markers["sid-1"] = 3
    store.save_book("TA", book)

    # 总共 5 轮：新增 2 轮，未达节奏 3 → 不触发
    r1 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", session_id="sid-1",
        history=_turns(5), provider=provider, every=3,
    ))
    assert r1 is None

    # 总共 6 轮：新增 3 轮 → 触发
    r2 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", session_id="sid-1",
        history=_turns(6), provider=provider, every=3,
    ))
    assert r2 is not None
    assert store.load_book("TA").extract_markers["sid-1"] == 6
