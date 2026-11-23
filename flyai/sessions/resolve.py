"""会话有效角色/世界观解析与副本（override）管理。

会话绑定 character_id/world_id 指向角色库；R7 起引入「会话专属副本」：
把一份 Character/World 深拷贝进 session.character_override / world_override，
对话、压缩、导出、属性生成都优先用 override，主卡不受影响。
未建副本（override 为空）时回退到角色库引用（兼容旧会话）。
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from ..lore.diff import content_signature
from ..lore.merge import merge_character_patch, merge_world_patch
from ..lore.models import Character, World
from ..lore.opening import apply_opening_entry
from .models import RoomMember, Session

# 通过属性的 module+name 判断，避免循环导入

# 酒馆助手脚本可达十几 MB，只应在角色库里存一份；会话副本落盘/下发时剥离，
# 读取时再从库主卡补回。否则单个会话 JSON 能到 30MB+，多请求并发直接 OOM。
_HEAVY_EXT_KEYS = ("tavern_helper", "TavernHelper")


def strip_heavy_extensions(data: Optional[dict]) -> Optional[dict]:
    """返回去掉 tavern_helper 的角色 dict 副本（不改原对象）。"""
    if not isinstance(data, dict):
        return data
    extensions = data.get("extensions")
    if not isinstance(extensions, dict):
        return data
    if not any(key in extensions for key in _HEAVY_EXT_KEYS):
        return data
    trimmed = {k: v for k, v in extensions.items() if k not in _HEAVY_EXT_KEYS}
    out = dict(data)
    out["extensions"] = trimmed
    return out


def _restore_heavy_extensions(char: Character, card_id: Optional[str], lore) -> None:
    """会话副本剥离过的酒馆助手脚本，从库主卡补回（共享只读引用，不再深拷贝）。"""
    if lore is None or not card_id:
        return
    root = lore.get_character(card_id)
    if root is None or not isinstance(root.extensions, dict):
        return
    missing = [
        key for key in _HEAVY_EXT_KEYS
        if key not in (char.extensions or {}) and key in root.extensions
    ]
    if not missing:
        return
    extensions = dict(char.extensions or {})
    for key in missing:
        extensions[key] = root.extensions[key]
    char.extensions = extensions


def effective_character(session: Session, lore) -> Optional[Character]:
    """当前会话实际使用的角色：override 优先，否则库引用。"""
    if session.character_override:
        char = Character.from_dict(session.character_override)
        card_id = session.character_id
        if not card_id:
            # 旧会话顶层可能没同步，退到唯一 AI 座位的绑定卡
            for member in session.members or []:
                if not member.is_user and member.character_id:
                    card_id = member.character_id
                    break
        _restore_heavy_extensions(char, card_id, lore)
        return char
    if session.character_id:
        return lore.get_character(session.character_id)
    return None


def opening_character(session: Session, character: Optional[Character]) -> Optional[Character]:
    """按会话选定的开局条目调整常驻注入；无选择/标题不存在时原样返回。

    只用于构建发给模型的角色卡副本（聊天/主动发言），不用于导出、发布、
    MVU 载体判定等场景——那些地方应看到作者的原始卡。
    """
    title = getattr(session, "opening_entry", "") or ""
    if character is None or not title:
        return character
    return apply_opening_entry(character, title)


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
    set_character_override(session, copy)
    return copy


def fork_world(session: Session, lore) -> World:
    base = effective_world(session, lore)
    if base is None:
        raise ValueError("当前会话没有绑定世界观，无法派生副本")
    copy = World.from_dict(base.to_dict())
    session.world_override = copy.to_dict()
    return copy


def set_character_override(session: Session, char: Character) -> None:
    session.character_override = strip_heavy_extensions(char.to_dict())


def set_world_override(session: Session, world: World) -> None:
    session.world_override = world.to_dict()


def clear_character_override(session: Session) -> None:
    session.character_override = None


def clear_world_override(session: Session) -> None:
    session.world_override = None


# ---- beta 多 AI：按参与者解析有效角色 ----

def member_character(member, lore) -> Optional[Character]:
    """某个参与者（AI 座位）的有效角色：override 优先，否则库引用。

    对用户座位同样适用：该座位的 character_id 即「用户扮演的自设（persona）」。
    """
    if member is None:
        return None
    if member.override:
        char = Character.from_dict(member.override)
        _restore_heavy_extensions(char, member.character_id, lore)
        return char
    if member.character_id:
        return lore.get_character(member.character_id)
    return None


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).casefold()


def _patch_name_matches(member: RoomMember, cur, patch_name: str) -> bool:
    """压缩补丁回带的角色名是否与座位一致。补丁省略 name 时放行（不阻断旧行为）。"""
    pn = _norm_name(patch_name)
    if not pn:
        return True
    expected = []
    if cur is not None and getattr(cur, "name", ""):
        expected.append(_norm_name(cur.name))
    if member.name:
        expected.append(_norm_name(member.name))
    expected = [x for x in expected if x]
    if not expected:
        return True
    for ex in expected:
        if pn == ex or pn in ex or ex in pn:
            return True
        if SequenceMatcher(None, pn, ex).ratio() >= 0.6:
            return True
    return False


def plan_room_compression(session: Session, data: dict, lore=None) -> dict:
    """只计算房间压缩结果，不赋值。返回 {"members": {mid: dict}, "world": dict|None}。"""
    members_out = {}
    for item in data.get("members", []) or []:
        if not isinstance(item, dict):
            continue
        m = session.member(item.get("member_id"))
        patch = item.get("character")
        if m is None or not isinstance(patch, dict):
            continue
        cur = member_character(m, lore) if lore is not None else None
        # 防张冠李戴：AI 可能把 A 角色的资料写到 B 的 member_id 下。
        # 要求补丁回带角色名，与该座位的角色名/座位名明显不符则拒绝此补丁
        if not _patch_name_matches(m, cur, str(patch.get("name") or "")):
            logger.warning(
                "群聊压缩：成员 %s 的补丁名字 %r 与座位角色不符，已跳过",
                m.id, patch.get("name"),
            )
            continue
        if cur is not None:
            base = cur.to_dict()
        elif m.override:
            base = dict(m.override)
        else:
            base = {"name": m.name}
        members_out[m.id] = merge_character_patch(base, patch)

    world_out = None
    wpatch = data.get("world")
    if isinstance(wpatch, dict):
        cur_w = effective_world(session, lore) if lore is not None else None
        base = cur_w.to_dict() if cur_w is not None else (session.world_override or {})
        world_out = merge_world_patch(base, wpatch)
        # 从 {} 新建的世界没有 id：补一个，避免后续经 World 往返时每次重生 uuid
        if not world_out.get("id"):
            import uuid

            world_out["id"] = uuid.uuid4().hex

    return {"members": members_out, "world": world_out}


def apply_room_compression(session: Session, data: dict, lore=None) -> dict:
    """把房间压缩结果写入成员副本（member.override）与会话世界副本。

    这样房间内 AI 下一轮就能感知压缩后的设定（而非写到成员不读的
    session.character_override）。返回 {"members": {mid: dict}, "world": dict|None}。
    """
    planned = plan_room_compression(session, data, lore)
    for mid, merged in planned["members"].items():
        m = session.member(mid)
        if m is not None:
            m.override = merged
    if planned["world"] is not None:
        session.world_override = planned["world"]
    return planned


def room_base_signatures(session: Session, lore) -> Dict[str, Any]:
    """各 AI 座位当前有效角色与当前世界的签名，供压缩 apply 时做 409 校验。

    已有会话副本时直接签原始 override dict；经 Character/World 对象往返会因
    id 为空时重新生成 uuid 而让签名每次都变。
    """
    members: Dict[str, str] = {}
    for m in session.members:
        if m.is_user:
            continue
        if m.override:
            members[m.id] = content_signature(m.override)
        else:
            cur = member_character(m, lore)
            members[m.id] = content_signature(
                cur.to_dict() if cur is not None else None
            )
    if session.world_override:
        world = content_signature(session.world_override)
    else:
        cur_w = effective_world(session, lore)
        world = content_signature(cur_w.to_dict() if cur_w is not None else None)
    return {"world": world, "members": members}


def user_seat(session: Session) -> Optional[RoomMember]:
    """返回房间里的用户座位（第一个 is_user 成员）；无则 None。"""
    for m in session.members:
        if m.is_user:
            return m
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