"""市场条目的数据模型。

市场是跨用户公共区：用户把自己的角色卡 / 世界观 / 历史对话快照发布上来，
其他用户浏览后复制一份到自己的库。条目只存快照，与发布者的原对象不再联动
（「更新内容」是重新拍一次快照）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict

KINDS = ("character", "world", "session")


@dataclass
class MarketEntry:
    id: str = ""              # 市场条目 id（uuid hex，与源对象 id 不同）
    kind: str = ""            # "character" | "world" | "session"
    source_id: str = ""       # 发布者原始对象 id（供「更新内容」重新快照）
    title: str = ""
    intro: str = ""
    cover: str = ""           # 封面图 URL，默认取卡/世界 avatar
    author_uid: str = ""
    author_name: str = ""     # 发布时快照，发布者改名不影响
    min_level: int = 0        # 等级门槛：低于此等级的用户完全看不到
    revision: int = 1         # 每次「更新内容」+1
    installs: int = 0
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source_id": self.source_id,
            "title": self.title,
            "intro": self.intro,
            "cover": self.cover,
            "author_uid": self.author_uid,
            "author_name": self.author_name,
            "min_level": self.min_level,
            "revision": self.revision,
            "installs": self.installs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MarketEntry":
        return cls(
            id=d.get("id", ""),
            kind=d.get("kind", ""),
            source_id=d.get("source_id", ""),
            title=d.get("title", ""),
            intro=d.get("intro", "") or "",
            cover=d.get("cover", "") or "",
            author_uid=d.get("author_uid", "") or "",
            author_name=d.get("author_name", "") or "",
            min_level=int(d.get("min_level", 0) or 0),
            revision=int(d.get("revision", 1) or 1),
            installs=int(d.get("installs", 0) or 0),
            created_at=d.get("created_at", "") or "",
            updated_at=d.get("updated_at", "") or "",
        )
