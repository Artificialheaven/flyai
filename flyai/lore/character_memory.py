"""角色级全局记忆簿（跨会话/跨房间持久）。

归属严格按 (用户 uid, 角色 id)：文件位于该用户目录下
character-memory/<character_id>.json，不同用户、不同角色互不相见。
房间通过 RoomMember.memory_mode ("rw"/"ro") 挂载，空=不挂载。

存储已升级为结构化记忆簿（memory.entries.EntryBook，version=2），
但 load/append/save 的字符串行形态保持兼容：
  load -> ["- [日期] 文本", ...]（仅有效条目）
  append/ save 接受同样的行；旧字符串文件惰性升级。
台账文件：character-memory/<character_id>.ledger.json
"""

from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from ..ids import safe_child
from ..memory.entries import (
    EntryBook,
    MemoryEntry,
    load_book,
    save_book,
)
from ..memory.ledger import LedgerItem, load_ledger, save_ledger


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


class CharacterMemoryStore:
    def __init__(self, base_dir) -> None:
        self._dir = base_dir / "character-memory"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, char_id: str):
        return safe_child(self._dir, f"{char_id}.json")

    def _ledger_path(self, char_id: str):
        return safe_child(self._dir, f"{char_id}.ledger.json")

    def exists(self, char_id: str) -> bool:
        return self._path(char_id).exists()

    # ---- 结构化读写（新通道）----
    def load_book(self, char_id: str) -> EntryBook:
        return load_book(self._path(char_id))

    def save_book(self, char_id: str, book: EntryBook) -> None:
        save_book(self._path(char_id), book)

    def load_ledger(self, char_id: str) -> Dict[str, List[LedgerItem]]:
        return load_ledger(self._ledger_path(char_id))

    def save_ledger(
        self, char_id: str, ledger: Dict[str, List[LedgerItem]]
    ) -> None:
        save_ledger(self._ledger_path(char_id), ledger)

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
        """追加一条记忆，原子写回，返回最新条目（行形态）。

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
