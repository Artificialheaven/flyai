"""SQLite 迁移：旧 JSON 目录首次打开自动导入、改名备份、幂等、坏文件跳过。

覆盖 lore / character-memory / presets / market 四类库，以及共享角色卡的
只读语义（用户删除/覆盖不作用于共享库）。
"""

import json

from flyai.lore.character_memory import CharacterMemoryStore
from flyai.lore.models import Character, World
from flyai.lore.store import LoreStore
from flyai.market.models import MarketEntry
from flyai.market.store import MarketStore
from flyai.memory.ledger import LedgerItem
from flyai.preset.models import Preset
from flyai.preset.store import PresetStore


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _baks(directory):
    return sorted(directory.glob("*.bak-*")) if directory.exists() else []


# ---- lore ----

def test_lore_imports_legacy_dirs_and_renames(tmp_path):
    base = tmp_path / "u"
    card = Character(id="c1", name="猫娘", intro="旧卡")
    world = World(id="w1", name="旧世界", content="魔法存在")
    _write_json(base / "characters" / "c1.json", card.to_dict())
    _write_json(base / "worlds" / "w1.json", world.to_dict())

    store = LoreStore(base)

    assert (base / "lore.db").exists()
    assert [c.name for c in store.list_characters()] == ["猫娘"]
    assert store.get_character("c1").intro == "旧卡"
    assert store.get_world("w1").content == "魔法存在"
    assert len(store.character_digest("c1")) == 32
    assert len(store.world_digest("w1")) == 32
    # 导入后旧文件改名备份，目录里不再有 *.json
    assert _baks(base / "characters") and _baks(base / "worlds")
    assert list((base / "characters").glob("*.json")) == []
    assert list((base / "worlds").glob("*.json")) == []


def test_lore_reopen_does_not_duplicate_and_skips_bad_json(tmp_path):
    base = tmp_path / "u"
    _write_json(
        base / "characters" / "c1.json", Character(id="c1", name="甲").to_dict()
    )
    bad = base / "characters" / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{不是 json", encoding="utf-8")

    first = LoreStore(base)
    assert [c.name for c in first.list_characters()] == ["甲"]
    # 坏 JSON 跳过且不改名，留在原地等人工处理，不阻塞启动
    assert bad.exists()

    second = LoreStore(base)
    assert len(second.list_characters()) == 1
    assert second.get_character("bad") is None


def test_shared_legacy_is_read_only_source(tmp_path):
    shared = tmp_path / "shared"
    _write_json(
        shared / "characters" / "s1.json",
        Character(
            id="s1", name="共享旧卡", author="admin", public=True
        ).to_dict(),
    )

    user = LoreStore(tmp_path / "u", shared_base=shared)
    assert _baks(shared / "characters")
    assert user.character_source("s1") == "shared"
    assert user.get_character("s1").name == "共享旧卡"
    assert len(user.character_digest("s1")) == 32

    # 用户删除/覆盖只作用自己的库，共享卡仍在
    user.delete_character("s1")
    assert user.character_source("s1") == "shared"
    user.save_character(Character(id="s1", name="我的版本"))
    assert user.get_character("s1").name == "我的版本"

    other = LoreStore(tmp_path / "other", shared_base=shared)
    assert other.get_character("s1").name == "共享旧卡"
    assert other.character_source("s1") == "shared"


def test_shared_readonly_dir_still_opens(tmp_path):
    """共享目录对进程只读时：能导入并读取，改名失败不阻塞启动，重复打开幂等。"""
    shared = tmp_path / "shared"
    _write_json(
        shared / "characters" / "s1.json", Character(id="s1", name="只读共享卡").to_dict()
    )
    legacy_dir = shared / "characters"
    legacy_dir.chmod(0o555)
    try:
        first = LoreStore(tmp_path / "u", shared_base=shared)
        assert first.get_character("s1").name == "只读共享卡"
        # 改名失败，旧文件还在原地
        assert [p.name for p in legacy_dir.glob("*.json")] == ["s1.json"]
        second = LoreStore(tmp_path / "u2", shared_base=shared)
        assert [c.name for c in second.list_characters()] == ["只读共享卡"]
        second.delete_character("s1")  # 用户侧删除不作用于共享
        assert second.character_source("s1") == "shared"
    finally:
        legacy_dir.chmod(0o755)


# ---- character-memory ----

def test_memory_imports_book_and_ledger(tmp_path):
    base = tmp_path / "u"
    mem_dir = base / "character-memory"
    _write_json(
        mem_dir / "TA.json",
        {
            "entries": [
                "- [2026-09-01] 旧事实",
                {"text": "结构化事实", "created_at": "2026-09-02"},
            ],
            "extract_markers": {"sid-1": 3},
        },
    )
    _write_json(
        mem_dir / "TA.ledger.json",
        {
            "item": [LedgerItem(kind="item", name="银钥匙", state="在手里").to_dict()],
            "task": [],
            "character": [],
            "location": [],
            "foreshadow": [],
        },
    )

    store = CharacterMemoryStore(base)
    assert (base / "memory.db").exists()
    assert store.exists("TA")
    assert [e.text for e in store.load_book("TA").entries] == [
        "旧事实", "结构化事实",
    ]
    assert store.load_book("TA").extract_markers == {"sid-1": 3}
    assert store.load_ledger("TA")["item"][0].name == "银钥匙"
    assert store.ids() == ["TA"]
    assert _baks(mem_dir)

    # 重复打开不重复导入
    again = CharacterMemoryStore(base)
    assert len(again.load_book("TA").entries) == 2
    assert again.load("TA") == [
        "- [2026-09-01] 旧事实", "- [2026-09-02] 结构化事实",
    ]


def test_memory_bad_json_skipped(tmp_path):
    base = tmp_path / "u"
    mem_dir = base / "character-memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "bad.json").write_text("{broken", encoding="utf-8")
    store = CharacterMemoryStore(base)
    assert store.load("bad") == []
    assert store.ids() == []
    assert (mem_dir / "bad.json").exists()  # 坏文件保留，不崩溃


# ---- presets ----

def test_presets_import_and_rename(tmp_path):
    base = tmp_path / "u"
    preset = Preset(id="p1", name="旧预设")
    _write_json(base / "presets" / "p1.json", preset.to_dict())

    # 兼容旧调用方式：传 data/{uid}/presets
    store = PresetStore(base / "presets")
    assert (base / "presets.db").exists()
    got = store.get("p1")
    assert got is not None and got.name == "旧预设"
    assert [p.name for p in store.list()] == ["旧预设"]
    assert _baks(base / "presets")

    again = PresetStore(base / "presets")
    assert [p.name for p in again.list()] == ["旧预设"]


# ---- market ----

def test_market_imports_entries_and_payloads(tmp_path):
    base = tmp_path / "market"
    entry = MarketEntry(
        id="e1",
        kind="character",
        source_id="c1",
        title="旧条目",
        author_uid="alice",
        author_name="alice",
        updated_at="2026-09-01T00:00:00",
    )
    _write_json(base / "entries" / "e1.json", entry.to_dict())
    _write_json(
        base / "payloads" / "e1.json",
        {"type": "flyai-character", "data": {"id": "c1", "name": "猫娘"}},
    )
    zip_bytes = b"PK\x03\x04legacy-zip"
    (base / "payloads").mkdir(parents=True, exist_ok=True)
    (base / "payloads" / "e1.zip").write_bytes(zip_bytes)

    store = MarketStore(base)
    assert (base / "market.db").exists()
    assert [e.title for e in store.list()] == ["旧条目"]
    assert store.get("e1").kind == "character"
    assert store.read_payload_json("e1")["data"]["id"] == "c1"
    assert store.read_payload_bytes("e1", "zip") == zip_bytes
    # payload 二进制落在 BLOB 列
    row = store._db.one(
        "SELECT typeof(blob) FROM payloads WHERE k=? AND ext=?", ("e1", "zip")
    )
    assert row[0] == "blob"
    assert _baks(base / "entries") and _baks(base / "payloads")

    again = MarketStore(base)
    assert len(again.list()) == 1


def test_market_bad_entry_json_skipped(tmp_path):
    base = tmp_path / "market"
    (base / "entries").mkdir(parents=True, exist_ok=True)
    (base / "entries" / "bad.json").write_text("{broken", encoding="utf-8")
    store = MarketStore(base)
    assert store.list() == []
    assert (base / "entries" / "bad.json").exists()
