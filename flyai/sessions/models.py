"""会话数据结构。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..schemas import Message


@dataclass
class Session:
    id: str = ""
    title: str = "新会话"
    provider_id: str = ""
    character_id: str = ""
    world_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    messages: List[Message] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    # 会话专属副本（R7）：序列化的 Character/World dict，优先于 character_id/world_id 引用
    character_override: Optional[Dict[str, Any]] = None
    world_override: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def set_title(self, title: str) -> None:
        self.title = title or self.title