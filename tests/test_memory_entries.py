"""结构化条目 v2：旧格式惰性升级、往返、去重（P2）。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.character_memory import CharacterMemoryStore
from flyai.memory.entries import (
    EntryBook,
    MemoryEntry,
    load_book,
    save_book,
)


def test_legacy_line_roundtrip():
    e = MemoryEntry.from_legacy("- [2026-09-01] 用户是猫娘老板娘")
    assert e.text == "用户是猫娘老板娘"
    assert e.created_at == "2026-09-01"
    assert e.legacy_line == "- [2026-09-01] 用户是猫娘老板娘"


def test_load_legacy_file_lazy_upgrade(tmp_path):
    p = tmp_path / "TA.json"
    p.write_text(
        '{"entries": ["- [2026-09-01] 旧事实一", "- [2026-09-02] 旧事实二"]}',
        encoding="utf-8",
    )
    book = load_book(p)
    assert len(book.entries) == 2
    assert book.entries[0].text == "旧事实一"
    # 保存后升级为 v2，字符串形态仍兼容
    save_book(p, book)
    raw = p.read_text(encoding="utf-8")
    assert '"version": 2' in raw
    store = CharacterMemoryStore(tmp_path / "root")
    # 另一个 store 目录里没有该文件，不串数据
    assert store.load("TA") == []


def test_corrupt_book_returns_empty(tmp_path):
    p = tmp_path / "X.json"
    p.write_text("{broken", encoding="utf-8")
    assert load_book(p).entries == []


def test_entry_id_deterministic():
    a = MemoryEntry(text="同一条事实")
    b = MemoryEntry(text="同一条事实")
    assert a.id == b.id and a.id.startswith("m_")


def test_importance_clamped():
    assert MemoryEntry(text="x", importance=2).importance == 1.0
    assert MemoryEntry(text="x", importance=-1).importance == 0.0


def test_store_append_and_load_compatible(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    store.append("TA", "用户特别怕水")
    loaded = store.load("TA")
    assert len(loaded) == 1
    assert "用户特别怕水" in loaded[0]
    assert loaded[0].startswith("- [")


def test_store_append_dedup_similar(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    first = "用户在地下室养了三只猫，名字分别是团子"
    store.append("TA", first)
    # 近乎重复（difflib ≥ 0.9）不追加
    store.append("TA", first + "。")
    assert len(store.load("TA")) == 1
    # 明显不同的事实正常追加
    store.append("TA", "用户特别怕水")
    assert len(store.load("TA")) == 2


def test_store_save_string_lines_preserves_markers(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    store.append("TA", "旧事实")
    book = store.load_book("TA")
    book.extract_markers["sid-1"] = 4
    store.save_book("TA", book)
    store.save("TA", ["- [2026-01-01] 被覆盖的新事实"])
    reloaded = store.load_book("TA")
    assert reloaded.extract_markers["sid-1"] == 4
    assert reloaded.entries[0].text == "被覆盖的新事实"


def test_superseded_hidden_from_active():
    book = EntryBook(entries=[
        MemoryEntry(text="旧说法", superseded=True),
        MemoryEntry(text="新说法"),
    ])
    active = book.active()
    assert len(active) == 1 and active[0].text == "新说法"
