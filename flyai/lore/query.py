"""内容表查询器：角色/世界观内容表的统一检索与系统提示词构建。"""

from __future__ import annotations

from typing import List, Optional

from ..config import AttributeDef, RoleplaySettings
from .attributes import resolve_attribute_defs
from .models import Character, LoreEntry, World

KIND_CHARACTER = "character"
KIND_WORLD = "world"


class LoreQuerier:
    """绑定到单个会话的活动角色/世界观，供 query_lore 工具检索。"""

    def __init__(self, character: Character, world: World):
        self.character = character
        self.world = world

    def list_titles(self, kind: str) -> List[str]:
        table = self._table(kind)
        return [e.title for e in table]

    def search(self, kind: str, keyword: str) -> Optional[List[LoreEntry]]:
        table = self._table(kind)
        kw = (keyword or "").strip().lower()
        if not kw:
            return table
        return [e for e in table if kw in (e.title or "").lower()]

    def _table(self, kind: str) -> List[LoreEntry]:
        if kind == KIND_CHARACTER and self.character:
            return self.character.table
        if kind == KIND_WORLD and self.world:
            return self.world.table
        return []

    def available_kinds(self) -> List[str]:
        kinds = []
        if self.character:
            kinds.append(KIND_CHARACTER)
        if self.world:
            kinds.append(KIND_WORLD)
        return kinds


def build_system_prompt(
    character: Character,
    world: World,
    base: str,
    roleplay: Optional[RoleplaySettings] = None,
) -> str:
    """把角色卡 + 世界观并入系统提示词。无角色/世界则返回 base。"""
    parts: List[str] = []

    if character:
        parts.append(f"你正在扮演角色「{character.name}」,以角色的身份、语气与记忆与用户对话。")
        if character.intro:
            parts.append(f"【角色简介】\n{character.intro}")
        if character.background:
            parts.append(f"【角色经历】\n{character.background}")
        parts.append(
            "角色内容记录表中还有更详细的资料，需要时调用工具 query_lore(kind='character', keyword=…) 查询。"
        )

    if world and world.content:
        parts.append(f"【世界观】\n{world.content}")
        if world.table:
            parts.append(
                "世界设定表的更多细节可通过 query_lore(kind='world', keyword=…) 查询。"
            )

    rp = _requirements_block(character, world, roleplay)
    if rp:
        parts.append(rp)

    attr = _attribute_block(character, roleplay)
    if attr:
        parts.append(attr)

    if not parts:
        return base

    parts.append(base)
    return "\n\n".join(parts)


def build_member_system_prompt(
    name: str,
    memory: str,
    attributes: Optional[dict],
    audience: Optional[List[str]],
    character: Character = None,
    world: World = None,
    base: str = "",
    roleplay: Optional[RoleplaySettings] = None,
) -> str:
    """Beta：为群聊中单个 AI 参与者构建系统提示词。

    在 build_system_prompt 的基础上叠加该 AI 的本窗口记忆、属性与在场名单，
    让每个 AI 在自己的角色卡/副本下、按自己的人物设定发言。
    """
    parts: List[str] = []

    if character:
        parts.append(f"你正在扮演角色「{character.name}」,以角色的身份、语气与记忆对话。")
        if character.intro:
            parts.append(f"【角色简介】\n{character.intro}")
        if character.background:
            parts.append(f"【角色经历】\n{character.background}")
        parts.append(
            "角色内容记录表中还有更详细的资料，需要时调用工具 query_lore(kind='character', keyword=…) 查询。"
        )

    if world and world.content:
        parts.append(f"【世界观】\n{world.content}")

    if audience:
        names = "、".join(audience)
        parts.append(
            f"【在场】你正与{names}同处一个聊天窗口并轮流发言。"
            "回复要符合你的角色，无需替他人说话，也不要重复别人说过的话。"
        )

    if memory:
        parts.append(f"【你在这个聊天里的记忆】\n{memory}")

    if attributes:
        try:
            import json

            lines = "；".join(
                f"{k}={v}" for k, v in attributes.items() if not str(k).startswith("_")
            )
        except Exception:
            lines = ""
        if lines:
            parts.append(f"【你的属性】\n{lines}")

    if not parts:
        return base
    if base:
        parts.append(base)
    return "\n\n".join(parts)


def _requirements_block(
    character: Optional[Character],
    world: Optional[World],
    roleplay: Optional[RoleplaySettings],
) -> Optional[str]:
    """组装「对白格式要求 + 工具使用要求」。

    角色卡/世界观各自可覆盖内置默认（prompt_format / prompt_tools）：
    - 对白格式：取角色卡的 prompt_format，其次内置 format_instruction（需 format_enabled）。
    - 工具使用：取角色卡 → 世界观 → 内置 tool_instruction（内置需 format_enabled）。
    """
    blocks: List[str] = []

    fmt = None
    if character and character.prompt_format and character.prompt_format.strip():
        fmt = character.prompt_format.strip()
    elif roleplay and roleplay.format_enabled and roleplay.format_instruction:
        fmt = roleplay.format_instruction.strip()
    if fmt:
        blocks.append(f"【对白格式要求】\n{fmt}")

    tools = None
    if character and character.prompt_tools and character.prompt_tools.strip():
        tools = character.prompt_tools.strip()
    elif world and world.prompt_tools and world.prompt_tools.strip():
        tools = world.prompt_tools.strip()
    elif roleplay and roleplay.format_enabled and roleplay.tool_instruction:
        tools = roleplay.tool_instruction.strip()
    if tools:
        blocks.append(f"【工具使用要求】\n{tools}")

    if not blocks:
        return None
    return "\n\n".join(blocks)


def _attribute_block(character: Optional[Character], roleplay: Optional[RoleplaySettings]) -> Optional[str]:
    """组装属性跟踪的系统提示片段，按当前角色解析出的属性定义描述。"""
    if roleplay is None or not roleplay.attributes.enabled:
        return None
    defs: List[AttributeDef] = resolve_attribute_defs(character, roleplay)
    if not defs:
        return None
    labels = "、".join(f"{d.label or d.key}({d.key})" for d in defs) or "（未配置）"
    if roleplay.attributes.ai_provided:
        return (
            f"当前角色带有可随剧情变化的属性（包括：{labels}）。"
            "在每回合对白结束后，根据本回合剧情发展调用工具 update_attributes 提交各属性的增减量（如好感度、金钱等变化），"
            "没有明显变化时可以不调用。不要直接把属性改成绝对新值，只提交本次增减。"
        )
    return (
        f"当前角色带有属性（包括：{labels}），如剧情需要，可在合适时机用工具 update_attributes 调整。"
    )