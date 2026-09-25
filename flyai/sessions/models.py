"""会话数据结构。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..schemas import Message


@dataclass
class RoomMember:
    """房间参与者：一个 AI 角色（或用户座位）。每个 AI 在自己每个窗口都有记忆。"""

    id: str = ""
    name: str = ""
    character_id: str = ""
    is_user: bool = False           # 用户座位无角色
    override: Optional[Dict[str, Any]] = None  # R7 会话专属角色副本（to_dict）
    memory: str = ""                # 该 AI 在本房间的记忆簿（各窗口共享）
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "character_id": self.character_id,
            "is_user": self.is_user,
            "override": self.override,
            "memory": self.memory,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RoomMember":
        d = d or {}
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            character_id=d.get("character_id", ""),
            is_user=bool(d.get("is_user", False)),
            override=d.get("override"),
            memory=d.get("memory", "") or "",
            attributes=d.get("attributes", {}) or {},
        )


@dataclass
class Thread:
    """房间内的一个窗口：单 AI 私聊（dm）或群聊（group）。窗口内独立历史=该窗口记忆。"""

    id: str = ""
    kind: str = "dm"                 # dm | group
    title: str = ""
    member_ids: List[str] = field(default_factory=list)
    messages: List[Message] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "member_ids": list(self.member_ids),
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Thread":
        d = d or {}
        return cls(
            id=d.get("id", ""),
            kind=d.get("kind", "dm"),
            title=d.get("title", ""),
            member_ids=list(d.get("member_ids", []) or []),
            messages=[Message.from_dict(m) for m in d.get("messages", [])],
        )


@dataclass
class Session:
    """会话 = 房间。beta：内含多个参与者（members）与多个窗口（threads）。"""

    id: str = ""
    title: str = "新会话"
    provider_id: str = ""
    character_id: str = ""           # 兼容：单 AI 快捷引用
    world_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    messages: List[Message] = field(default_factory=list)  # 兼容字段
    attributes: Dict[str, Any] = field(default_factory=dict)
    # 会话专属副本（R7）：序列化的 Character/World dict，优先于 character_id/world_id 引用
    character_override: Optional[Dict[str, Any]] = None
    world_override: Optional[Dict[str, Any]] = None
    # beta：多 AI 群聊
    members: List[RoomMember] = field(default_factory=list)
    threads: List[Thread] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def set_title(self, title: str) -> None:
        self.title = title or self.title

    def member(self, mid: str) -> Optional[RoomMember]:
        for m in self.members:
            if m.id == mid:
                return m
        return None

    def thread(self, tid: str) -> Optional[Thread]:
        for t in self.threads:
            if t.id == tid:
                return t
        return None