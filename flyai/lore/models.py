"""角色卡与世界观的数据模型。

LoreEntry 是「内容表」条目（一个标题 + 一段内容）。
角色卡与世界观各自带一张内容表，AI 通过统一的 query_lore 工具查询。
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
    # ---- 酒馆世界书保真字段 ----
    enabled: bool = True                 # ST disable=false；False 时永不注入
    constant: bool = False               # ST constant：无条件常驻（与 pinned 同义）
    selective: bool = True               # ST selective：按 keys 选择性触发
    secondary_keys: List[str] = field(default_factory=list)
    # and_any（默认）/and_all/not_all/not_any：主键命中后对次键的判定
    selective_logic: str = "and_any"
    scan_depth: Optional[int] = None     # None=same_as_global
    case_sensitive: bool = False
    match_whole_words: bool = False
    # ST effect：命中后保持 N 条消息 / 冷却 N 条消息 / 延迟 N 条消息
    sticky: Optional[int] = None
    cooldown: Optional[int] = None
    delay: Optional[int] = None
    prevent_recursion: bool = False
    exclude_recursion: bool = False
    delay_until_recursion: Optional[int] = None
    # ST 世界书位置：before/after/ANTop/ANBottom/at_depth/EMTop/EMBottom/outlet
    position_type: str = ""

    @property
    def resolved_position_type(self) -> str:
        if self.position_type:
            return self.position_type
        return "at_depth" if self.position == "depth" else "before"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
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
        # 酒馆保真字段只在非默认时落盘，旧数据/旧断言保持原形状。
        if not self.enabled:
            d["enabled"] = False
        if self.constant:
            d["constant"] = True
        if not self.selective:
            d["selective"] = False
        if self.secondary_keys:
            d["secondary_keys"] = list(self.secondary_keys)
        if (self.selective_logic or "and_any") != "and_any":
            d["selective_logic"] = self.selective_logic
        if self.scan_depth is not None:
            d["scan_depth"] = self.scan_depth
        if self.case_sensitive:
            d["case_sensitive"] = True
        if self.match_whole_words:
            d["match_whole_words"] = True
        if self.sticky is not None:
            d["sticky"] = self.sticky
        if self.cooldown is not None:
            d["cooldown"] = self.cooldown
        if self.delay is not None:
            d["delay"] = self.delay
        if self.prevent_recursion:
            d["prevent_recursion"] = True
        if self.exclude_recursion:
            d["exclude_recursion"] = True
        if self.delay_until_recursion is not None:
            d["delay_until_recursion"] = self.delay_until_recursion
        if self.position_type and self.position_type not in ("before", "at_depth"):
            d["position_type"] = self.position_type
        return d

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

        raw_secondary = d.get("secondary_keys") or d.get("keysecondary") or []
        if isinstance(raw_secondary, str):
            raw_secondary = [raw_secondary]
        secondary = [
            str(k).strip() for k in raw_secondary if str(k or "").strip()
        ]
        logic = str(
            d.get("selective_logic")
            or (
                d.get("strategy", {}).get("keys_secondary", {}).get("logic")
                if isinstance(d.get("strategy"), dict) else ""
            )
            or "and_any"
        ).strip().lower()
        if logic not in ("and_any", "and_all", "not_all", "not_any"):
            logic = "and_any"
        raw_scan = d.get("scan_depth")
        if raw_scan in (None, "", "same_as_global"):
            scan_depth = None
        else:
            scan_depth = _int("scan_depth", 0) or None

        def _opt_int(source: Dict[str, Any], *names: str) -> Optional[int]:
            for name in names:
                value = source.get(name)
                if value is None or value == "":
                    continue
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
            return None

        effect = d.get("effect") if isinstance(d.get("effect"), dict) else {}
        recursion = d.get("recursion") if isinstance(d.get("recursion"), dict) else {}
        pos_type = str(d.get("position_type") or "").strip()
        is_depth = pos_type == "at_depth" or (
            not pos_type and d.get("position") == "depth"
        )

        return cls(
            title=d.get("title", ""),
            content=d.get("content", ""),
            pinned=bool(d.get("pinned", False) or d.get("constant", False)),
            keys=keys,
            position="depth" if is_depth else "system",
            depth=_int("depth", 4),
            insert_order=_int("insert_order", 100),
            role=d.get("role") if d.get("role") in ("user", "assistant") else "system",
            probability=max(1, min(100, _int("probability", 100))),
            enabled=bool(d.get("enabled", not bool(d.get("disable", False)))),
            constant=bool(d.get("constant", False) or d.get("pinned", False)),
            selective=bool(d.get("selective", True)),
            secondary_keys=secondary,
            selective_logic=logic,
            scan_depth=scan_depth,
            case_sensitive=bool(d.get("case_sensitive", False)),
            match_whole_words=bool(d.get("match_whole_words", False)),
            sticky=_opt_int(d, "sticky") if d.get("sticky") is not None
            else _opt_int(effect, "sticky"),
            cooldown=_opt_int(d, "cooldown") if d.get("cooldown") is not None
            else _opt_int(effect, "cooldown"),
            delay=_opt_int(d, "delay") if d.get("delay") is not None
            else _opt_int(effect, "delay"),
            prevent_recursion=bool(
                d.get("prevent_recursion")
                or recursion.get("prevent_incoming")
                or d.get("preventRecursion", False)
            ),
            exclude_recursion=bool(
                d.get("exclude_recursion")
                or recursion.get("prevent_outgoing")
                or d.get("excludeRecursion", False)
            ),
            delay_until_recursion=_opt_int(
                d, "delay_until_recursion", "delayUntilRecursion"
            ) if d.get("delay_until_recursion") is not None
            or d.get("delayUntilRecursion") is not None
            else _opt_int(recursion, "delay_until"),
            position_type=pos_type,
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