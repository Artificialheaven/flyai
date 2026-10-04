"""角色级全局记忆簿（跨会话/跨房间持久）。

归属严格按 (用户 uid, 角色 id)：文件位于该用户目录下
character-memory/<character_id>.json，不同用户、不同角色互不相见。
房间通过 RoomMember.memory_mode ("rw"/"ro") 挂载，空=不挂载。
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from datetime import date

from ..ids import safe_child


class CharacterMemoryStore:
    def __init__(self, base_dir) -> None:
        self._dir = base_dir / "character-memory"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, char_id: str):
        return safe_child(self._dir, f"{char_id}.json")

    def exists(self, char_id: str) -> bool:
        return self._path(char_id).exists()

    def load(self, char_id: str) -> List[str]:
        p = self._path(char_id)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return list(data.get("entries") or [])
        except Exception:
            return []

    def append(self, char_id: str, text: str, stamp: Optional[str] = None) -> List[str]:
        """追加一条记忆，原子写回，返回最新条目。"""
        entries = self.load(char_id)
        entry = f"- [{stamp or date.today().isoformat()}] {text}"
        entries.append(entry)
        self.save(char_id, entries)
        return entries

    def save(self, char_id: str, entries: List[str]) -> None:
        p = self._path(char_id)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
