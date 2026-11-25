"""结构化记忆条目（v2）+ 旧字符串条目兼容。

文件格式：
  v2: {"version": 2,
       "extract_markers": {"{session_id}": 已见 user 轮数},
       "entries": [MemoryEntry dict, ...]}
  v1/旧版: {"entries": ["- [日期] 文本", ...]} —— 加载时自动包装，惰性升级。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

ENTRY_VERSION = 2


@dataclass
class MemoryEntry:
    """一条跨会话长期记忆。"""

    text: str
    id: str = ""
    source: str = "ai"                 # ai | extract | snapshot
    time_anchor: str = ""
    entities: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0
    superseded: bool = False           # 被更新的事实取代，不参与召回
    history: List[Dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.text = (self.text or "").strip()
        if not self.id:
            self.id = "m_" + hashlib.sha1(
                self.text.encode("utf-8")
            ).hexdigest()[:10]
        if not self.created_at:
            self.created_at = date.today().isoformat()
        try:
            imp = float(self.importance)
        except (TypeError, ValueError):
            imp = 0.5
        self.importance = max(0.0, min(1.0, imp))
        self.access_count = max(0, int(self.access_count or 0))

    @property
    def legacy_line(self) -> str:
        """旧格式条目行（兼容旧 load() 返回与旧文件形态）。"""
        return f"- [{self.created_at}] {self.text}"

    def touch(self) -> None:
        self.last_accessed = date.today().isoformat()
        self.access_count += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "time_anchor": self.time_anchor,
            "entities": list(self.entities),
            "tags": list(self.tags),
            "importance": self.importance,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "superseded": self.superseded,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            text=str(d.get("text") or ""),
            id=str(d.get("id") or ""),
            source=str(d.get("source") or "ai"),
            time_anchor=str(d.get("time_anchor") or ""),
            entities=[str(e) for e in (d.get("entities") or [])],
            tags=[str(t) for t in (d.get("tags") or [])],
            importance=d.get("importance", 0.5),
            created_at=str(d.get("created_at") or ""),
            last_accessed=str(d.get("last_accessed") or ""),
            access_count=int(d.get("access_count") or 0),
            superseded=bool(d.get("superseded")),
            history=list(d.get("history") or []),
        )

    @classmethod
    def from_legacy(cls, line: str) -> "MemoryEntry":
        """旧字符串条目 '- [日期] 文本' → MemoryEntry。"""
        s = (line or "").strip()
        if s.startswith("- "):
            s = s[2:].strip()
        stamp = ""
        if s.startswith("["):
            close = s.find("]")
            if close > 0:
                stamp = s[1:close]
                s = s[close + 1:].strip()
        return cls(
            text=s,
            created_at=stamp or date.today().isoformat(),
            source="ai",
        )


@dataclass
class EntryBook:
    """一个角色的整本记忆簿（条目 + 抽取进度标记）。"""

    entries: List[MemoryEntry] = field(default_factory=list)
    extract_markers: Dict[str, int] = field(default_factory=dict)

    def active(self) -> List[MemoryEntry]:
        return [e for e in self.entries if not e.superseded]


def book_from_dict(data: Any) -> EntryBook:
    """把库里的 JSON 文档还原成记忆簿；损坏/非 dict 返回空簿。"""
    if not isinstance(data, dict):
        return EntryBook()
    entries: List[MemoryEntry] = []
    for item in data.get("entries") or []:
        if isinstance(item, str):
            entries.append(MemoryEntry.from_legacy(item))
        elif isinstance(item, dict):
            try:
                entries.append(MemoryEntry.from_dict(item))
            except Exception:
                continue
    markers = {}
    for sid, n in (data.get("extract_markers") or {}).items():
        try:
            markers[str(sid)] = max(0, int(n))
        except (TypeError, ValueError):
            continue
    return EntryBook(entries=entries, extract_markers=markers)


def book_to_dict(book: EntryBook) -> Dict[str, Any]:
    """序列化为入库文档（v2 结构，与旧文件格式一致）。"""
    return {
        "version": ENTRY_VERSION,
        "extract_markers": dict(book.extract_markers),
        "entries": [e.to_dict() for e in book.entries],
    }


def load_book(path) -> EntryBook:
    """读记忆簿；文件不存在/损坏时返回空簿。旧字符串条目自动包装。"""
    try:
        if not path.exists():
            return EntryBook()
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return EntryBook()
    return book_from_dict(data)


def save_book(path, book: EntryBook) -> None:
    """原子写入记忆簿 v2。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = book_to_dict(book)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
