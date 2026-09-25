"""会话有效角色/世界观解析与副本（override）管理。

会话绑定 character_id/world_id 指向角色库；R7 起引入「会话专属副本」：
把一份 Character/World 深拷贝进 session.character_override / world_override，
对话、压缩、导出、属性生成都优先用 override，主卡不受影响。
未建副本（override 为空）时回退到角色库引用（兼容旧会话）。
"""

from __future__ import annotations

from typing import Optional

from ..lore.models import Character, World
from .models import Session

# 通过属性的 module+name 判断，避免循环导入


def effective_character(session: Session, lore) -> Optional[Character]:
    """当前会话实际使用的角色：override 优先，否则库引用。"""
    if session.character_override:
        return Character.from_dict(session.character_override)
    if session.character_id:
        return lore.get_character(session.character_id)
    return None


def effective_world(session: Session, lore) -> Optional[World]:
    if session.world_override:
        return World.from_dict(session.world_override)
    if session.world_id:
        return lore.get_world(session.world_id)
    return None


def is_character_scoped(session: Session) -> bool:
    return bool(session.character_override)


def is_world_scoped(session: Session) -> bool:
    return bool(session.world_override)


def fork_character(session: Session, lore) -> Character:
    """把当前有效角色复制为会话副本，存入 override，返回副本。"""
    base = effective_character(session, lore)
    if base is None:
        raise ValueError("当前会话没有绑定角色，无法派生副本")
    copy = Character.from_dict(base.to_dict())
    session.character_override = copy.to_dict()
    return copy


def fork_world(session: Session, lore) -> World:
    base = effective_world(session, lore)
    if base is None:
        raise ValueError("当前会话没有绑定世界观，无法派生副本")
    copy = World.from_dict(base.to_dict())
    session.world_override = copy.to_dict()
    return copy


def set_character_override(session: Session, char: Character) -> None:
    session.character_override = char.to_dict()


def set_world_override(session: Session, world: World) -> None:
    session.world_override = world.to_dict()


def clear_character_override(session: Session) -> None:
    session.character_override = None


def clear_world_override(session: Session) -> None:
    session.world_override = None


# ---- beta 多 AI：按参与者解析有效角色 ----

def member_character(member, lore) -> Optional[Character]:
    """某个参与者（AI 座位）的有效角色：override 优先，否则库引用。"""
    if member.override:
        return Character.from_dict(member.override)
    if member.character_id:
        return lore.get_character(member.character_id)
    return None


def primary_thread(session: Session):
    """返回首个私聊线程（无则 None）。"""
    for t in session.threads:
        if t.kind == "dm":
            return t
    return None


def thread_messages(session: Session, tid: Optional[str] = None):
    """取某窗口的历史；未指定或用主私聊/兼容字段兜底。"""
    if tid:
        t = session.thread(tid)
        if t is not None:
            return t.messages
    pt = primary_thread(session)
    if pt is not None and pt.messages:
        return pt.messages
    return session.messages