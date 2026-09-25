"""角色卡与世界观的数据模型。

LoreEntry 是「内容表」条目（一个标题 + 一段内容）。
角色卡与世界观各自带一张内容表，AI 通过统一的 query_lore 工具查询。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class LoreEntry:
    title: str = ""
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "content": self.content}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LoreEntry":
        return cls(title=d.get("title", ""), content=d.get("content", ""))


@dataclass
class Character:
    """角色卡：头像、简介、经历、内容表、属性数值、以及对白/工具要求。

    attributes: 本角色自定义的属性定义（键/名称/初值/上下限）原始字典列表；
    留空则使用全局 roleplay.attributes 默认（兼容旧角色卡）。
    """

    id: str = ""
    name: str = ""
    avatar: str = ""          # 头像资源路径（/uploads/xxx.png）或空
    intro: str = ""           # 角色简介
    background: str = ""      # 角色经历
    table: List[LoreEntry] = field(default_factory=list)
    attributes: List[Dict[str, Any]] = field(default_factory=list)
    prompt_format: str = ""   # 对白格式要求（覆盖内置默认）
    prompt_tools: str = ""    # 工具使用要求（覆盖内置默认）

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "avatar": self.avatar,
            "intro": self.intro,
            "background": self.background,
            "table": [e.to_dict() for e in self.table],
            "attributes": list(self.attributes),
            "prompt_format": self.prompt_format,
            "prompt_tools": self.prompt_tools,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Character":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            avatar=d.get("avatar", ""),
            intro=d.get("intro", ""),
            background=d.get("background", ""),
            table=[LoreEntry.from_dict(e) for e in d.get("table", [])],
            attributes=list(d.get("attributes", []) or []),
            prompt_format=d.get("prompt_format", ""),
            prompt_tools=d.get("prompt_tools", ""),
        )


@dataclass
class World:
    """世界观：名称、总体内容、内容表、以及格式/工具要求。"""

    id: str = ""
    name: str = ""
    avatar: str = ""
    content: str = ""         # 世界观整体描述
    table: List[LoreEntry] = field(default_factory=list)
    prompt_format: str = ""   # 对白格式要求（覆盖内置默认）
    prompt_tools: str = ""    # 工具使用要求（覆盖内置默认）

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "avatar": self.avatar,
            "content": self.content,
            "table": [e.to_dict() for e in self.table],
            "prompt_format": self.prompt_format,
            "prompt_tools": self.prompt_tools,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "World":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            avatar=d.get("avatar", ""),
            content=d.get("content", ""),
            table=[LoreEntry.from_dict(e) for e in d.get("table", [])],
            prompt_format=d.get("prompt_format", ""),
            prompt_tools=d.get("prompt_tools", ""),
        )