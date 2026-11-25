"""角色级全局记忆簿（跨会话/跨房间持久）。

归属严格按 (用户 uid, 角色 id)：SQLite 库位于
data/{uid}/memory.db，memory / ledger 两张文档表，不同用户互不相见。
房间通过 RoomMember.memory_mode ("rw"/"ro") 挂载，空=不挂载。

存储为结构化记忆簿（memory.entries.EntryBook，version=2），
但 load/append/save 的字符串行形态保持兼容：
  load -> ["- [日期] 文本", ...]（仅有效条目）
  append/ save 接受同样的行；旧字符串内容惰性升级。
台账：同库 ledger 表（旧布局 character-memory/<id>.ledger.json 自动导入）。
"""

from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

from ..ids import check_safe_name
from ..memory.entries import (
    EntryBook,
    MemoryEntry,
    book_from_dict,
    book_to_dict,
)
from ..memory.ledger import (
    LedgerItem,
    ledger_from_dict,
    ledger_to_dict,
)
from ..sqlite_store import JsonTable, SqliteDB, migrate_legacy_dir


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


class CharacterMemoryStore:
    def __init__(self, base_dir) -> None:
        self._base = Path(base_dir)
        self._dir = self._base / "character-memory"  # 旧布局（迁移来源）
        self._db = SqliteDB(self._base / "memory.db")
        self._books = JsonTable(self._db, "memory")
        self._ledgers = JsonTable(self._db, "ledger")
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        """旧 JSON -> memory.db；<id>.json 进 memory，<id>.ledger.json 进 ledger。

        坏 JSON 由 migrate_legacy_dir 跳过且不改名，下次启动重试，不会崩溃；
        目录只读导致改名失败时也不影响启动（重复导入幂等）。
        """
        if not self._dir.exists():
            return
        try:
            migrate_legacy_dir(self._dir, self._import_legacy)
        except OSError:
            pass

    def _import_legacy(self, stem: str, data: dict) -> None:
        if stem.endswith(".ledger"):
            self._ledgers.put(stem[: -len(".ledger")], data)
        else:
            self._books.put(stem, data)

    @staticmethod
    def _check(char_id: str) -> str:
        # 空 id 是历史匿名公共簿（旧布局 ".json"），保留读写能力；
        # 其余 id 一律走安全名校验（穿越/分隔符在写入与读取前收口）。
        if char_id == "":
            return ""
        return check_safe_name(char_id)

    def ids(self) -> List[str]:
        """所有有记忆簿的角色 id（台账-only / 匿名公共簿不算，与旧目录语义一致）。"""
        return sorted(k for k in self._books.keys() if k)

    def exists(self, char_id: str) -> bool:
        return self._books.exists(self._check(char_id))

    # ---- 结构化读写（新通道）----
    def load_book(self, char_id: str) -> EntryBook:
        doc = self._books.get(self._check(char_id))
        return book_from_dict(doc)

    def save_book(self, char_id: str, book: EntryBook) -> None:
        self._books.put(self._check(char_id), book_to_dict(book))

    def load_ledger(self, char_id: str) -> Dict[str, List[LedgerItem]]:
        doc = self._ledgers.get(self._check(char_id))
        return ledger_from_dict(doc)

    def save_ledger(
        self, char_id: str, ledger: Dict[str, List[LedgerItem]]
    ) -> None:
        self._ledgers.put(self._check(char_id), ledger_to_dict(ledger))

    # ---- 兼容字符串行通道 ----
    def load(self, char_id: str) -> List[str]:
        book = self.load_book(char_id)
        return [e.legacy_line for e in book.entries if not e.superseded]

    def append(
        self,
        char_id: str,
        text: str,
        stamp: Optional[str] = None,
    ) -> List[str]:
        """追加一条记忆，写回库，返回最新条目（行形态）。

        与现有有效条目高度相似（≥0.9，difflib）时视为重复，不再追加。
        """
        book = self.load_book(char_id)
        for existing in book.entries:
            if existing.superseded:
                continue
            if _similar(existing.text, text) >= 0.9:
                return [
                    e.legacy_line for e in book.entries if not e.superseded
                ]
        created = stamp or date.today().isoformat()
        book.entries.append(
            MemoryEntry(text=text, source="ai", created_at=created)
        )
        self.save_book(char_id, book)
        return self.load(char_id)

    def save(self, char_id: str, entries: List[str]) -> None:
        """以字符串行整体覆盖（保留抽取进度标记）。"""
        book = self.load_book(char_id)
        book.entries = [MemoryEntry.from_legacy(line) for line in entries]
        self.save_book(char_id, book)
