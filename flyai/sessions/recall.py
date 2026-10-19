"""撤回一整轮：用户消息 + 到下一条 user（或另一墓碑）之前的全部回复。

撤回后在原位插一条 role=TOMBSTONE_ROLE 的墓碑消息（保留原 user id），
载荷仅用于前端"重新编辑"回填；模型视图由 agent loop / provider 适配器 /
messages_to_script 三处过滤，墓碑永不发给模型。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..schemas import Message

TOMBSTONE_ROLE = "tombstone"


def is_tombstone(m: Message) -> bool:
    return bool(getattr(m, "tombstone", None))


def turn_span(messages: List[Message], mid: str) -> Tuple[int, int]:
    """与 versions.locate_turn 同边界，但终点遇 user 或墓碑都停。

    mid 不存在、锚点不是活的 user 消息 → ValueError。
    """
    idx = -1
    for i, m in enumerate(messages):
        if m.id == mid:
            idx = i
            break
    if idx < 0:
        raise ValueError(f"找不到消息: {mid}")
    target = messages[idx]
    if target.role != "user" or is_tombstone(target):
        raise ValueError(f"锚点不是可撤回的用户消息: {mid}")
    end = len(messages)
    for j in range(idx + 1, len(messages)):
        m = messages[j]
        if m.role == "user" or is_tombstone(m):
            end = j
            break
    return idx, end


def recall_turn(
    messages: List[Message],
    mid: str,
    edits: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """就地撤回 mid 所在轮，返回墓碑载荷（同时作为接口响应体）。

    edits 传 thread.edits 时 pop 该 anchor 的全部编辑/重生成版本
    （非活动版本里也存着原文，不 pop 等于撤回不彻底）。
    旧形态 session.messages 传 edits=None 即可复用。
    """
    i, end = turn_span(messages, mid)
    user = messages[i]
    payload: Dict[str, Any] = {
        "content": user.content,
        "images": [im.to_dict() for im in (user.images or [])],
        "name": user.name,
    }
    stone = Message(role=TOMBSTONE_ROLE, id=user.id, tombstone=dict(payload))
    messages[i:end] = [stone]
    if edits is not None:
        edits.pop(user.id, None)
    return payload


def find_target(session: Any, mid: str) -> Tuple[Optional[Any], Optional[List[Message]]]:
    """在所有 thread.messages 中定位 mid；找不到再兜底旧形态 session.messages。

    返回 (thread|None, messages_list|None)。
    """
    for t in getattr(session, "threads", None) or []:
        if any(m.id == mid for m in t.messages):
            return t, t.messages
    legacy = getattr(session, "messages", None)
    if legacy and any(m.id == mid for m in legacy):
        return None, legacy
    return None, None
