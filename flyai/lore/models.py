"""角色卡与世界观的数据模型。

LoreEntry 是「内容表」条目（一个标题 + 一段内容）。
角色卡与世界观各自带一张内容表，AI 通过统一的 query_lore 工具查询。
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class LoreEntry:
    title: str = ""
    content: str = ""
    pinned: bool = False     # 常驻/钉选：True 时无条件注入系统提示词（类似世界书 constant）
    # 触发词（世界书 selective/keys）：用户消息命中任一词（含模糊匹配）时
    # 该条目自动注入系统提示词；为空时以标题作为触发词
    keys: List[str] = field(default_factory=list)
    # 常驻条目注入位置（酒馆世界书 position）：
    # "system"=并入系统提示词常驻块；"depth"=按 at-depth 插到聊天历史深处
    position: str = "system"
    depth: int = 4           # position="depth" 时距历史末尾的消息数
    insert_order: int = 100  # 同一深度多条时的排序（小的在前）
    role: str = "system"     # depth 注入时的消息角色：system/user/assistant
    probability: int = 100   # 触发概率 0-100（酒馆 useProbability/probability）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "pinned": self.pinned,
            "keys": list(self.keys or []),
            "position": self.position if self.position == "depth" else "system",
            "depth": int(self.depth or 4),
            "insert_order": int(self.insert_order or 100),
            "role": self.role if self.role in ("user", "assistant") else "system",
            "probability": max(1, min(100, int(self.probability or 100))),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LoreEntry":
        raw_keys = d.get("keys") or []
        if isinstance(raw_keys, str):
            raw_keys = [raw_keys]
        keys = [str(k).strip() for k in raw_keys if str(k or "").strip()]

        def _int(key: str, default: int) -> int:
            try:
                return int(d.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            title=d.get("title", ""),
            content=d.get("content", ""),
            pinned=bool(d.get("pinned", False)),
            keys=keys,
            position="depth" if d.get("position") == "depth" else "system",
            depth=_int("depth", 4),
            insert_order=_int("insert_order", 100),
            role=d.get("role") if d.get("role") in ("user", "assistant") else "system",
            probability=max(1, min(100, _int("probability", 100))),
        )


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
    mes_example: str = ""     # 对话范例：<START> 分隔的 3~5 轮范例（可用 {{char}}/{{user}}）
    table: List[LoreEntry] = field(default_factory=list)
    attributes: List[Dict[str, Any]] = field(default_factory=list)
    prompt_format: str = ""   # 对白格式要求（覆盖内置默认）
    prompt_tools: str = ""    # 工具使用要求（覆盖内置默认）
    system_prompt: str = ""   # 系统级提示词（高优先级自定义系统指令，空则不注入）
    post_history_instructions: str = ""  # PHI：每轮注入在聊天历史之后、生成之前的末位 system 指令（空则不注入）
    first_mes: str = ""       # 开场白：用该角色（单角色普通房间）新建会话时自动发送的首条 AI 消息（可用 {{char}}/{{user}}）
    parent_id: str = ""       # 空=根卡（主角色卡）；非空=该根卡下的副本
    alias: str = ""           # 副本在列表中显示的别名（空则回退 name）
    voice: str = ""           # 绑定音色的 speaker ID（空=用全局默认音色）
    author: str = ""          # 创建者 uid（服务端注入）；对外以 author:id 唯一标识
    public: bool = False      # 是否已发布为全体用户共享（admin 操作）
    # ---- 酒馆卡保真字段（原样保留；服务端不执行其中的 JS）----
    regex_scripts: List[Dict[str, Any]] = field(default_factory=list)  # ST regex_scripts
    extensions: Dict[str, Any] = field(default_factory=dict)  # depth_prompt/world/talkativeness/fav/tavern_helper 等
    alternate_greetings: List[str] = field(default_factory=list)
    assets: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    @property
    def display_name(self) -> str:
        """副本列表/展示用名称：别名优先，否则角色名。"""
        return self.alias or self.name

    @property
    def ref(self) -> str:
        """Author+ID 复合标识。"""
        return f"{self.author}:{self.id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "avatar": self.avatar,
            "intro": self.intro,
            "background": self.background,
            "mes_example": self.mes_example,
            "table": [e.to_dict() for e in self.table],
            "attributes": list(self.attributes),
            "prompt_format": self.prompt_format,
            "prompt_tools": self.prompt_tools,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "first_mes": self.first_mes,
            "parent_id": self.parent_id,
            "alias": self.alias,
            "voice": self.voice,
            "author": self.author,
            "public": self.public,
            "regex_scripts": copy.deepcopy(list(self.regex_scripts or [])),
            "extensions": copy.deepcopy(dict(self.extensions or {})),
            "alternate_greetings": [str(g) for g in (self.alternate_greetings or [])],
            "assets": copy.deepcopy(list(self.assets or [])),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Character":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            avatar=d.get("avatar", ""),
            intro=d.get("intro", ""),
            background=d.get("background", ""),
            mes_example=d.get("mes_example", "") or "",
            table=[LoreEntry.from_dict(e) for e in d.get("table", [])],
            attributes=list(d.get("attributes", []) or []),
            prompt_format=d.get("prompt_format", ""),
            prompt_tools=d.get("prompt_tools", ""),
            system_prompt=d.get("system_prompt", "") or "",
            post_history_instructions=d.get("post_history_instructions", "") or "",
            first_mes=d.get("first_mes", "") or "",
            parent_id=d.get("parent_id", "") or "",
            alias=d.get("alias", "") or "",
            voice=d.get("voice", "") or "",
            author=d.get("author", "") or "",
            public=bool(d.get("public", False)),
            regex_scripts=[
                dict(s) for s in (d.get("regex_scripts") or [])
                if isinstance(s, dict)
            ],
            extensions=dict(d.get("extensions") or {})
            if isinstance(d.get("extensions"), dict) else {},
            alternate_greetings=[
                str(g) for g in (d.get("alternate_greetings") or [])
                if str(g or "").strip()
            ],
            assets=[
                dict(a) for a in (d.get("assets") or [])
                if isinstance(a, dict)
            ],
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