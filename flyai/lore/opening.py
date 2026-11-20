"""开局（开场设定）世界书条目的识别与选择。

部分角色卡把不同开局写成独立的「开局*」「序章*」世界书条目，靠条目的
``constant``/``enabled`` 状态决定哪一条常驻注入。新建房间时允许玩家显式
选择，本模块负责识别候选条目并把选择应用到发给模型的角色卡副本上。

只影响 prompt 侧副本：角色库里的原卡、导出、发布都不改动。
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from .models import Character, LoreEntry

# 「开局1」「开局2」「序章」「序章・雨夜」等；允许前导空白
OPENING_TITLE_RE = re.compile(r"^\s*(?:开局|序章)")


def is_opening_title(title: Optional[str]) -> bool:
    return bool(OPENING_TITLE_RE.match(title or ""))


def opening_entries(character: Any) -> List[LoreEntry]:
    """按世界书顺序返回可作为开局的条目（标题命中且内容非空、去重）。"""
    out: List[LoreEntry] = []
    seen = set()
    for entry in getattr(character, "table", None) or []:
        title = (getattr(entry, "title", "") or "").strip()
        if not title or title in seen:
            continue
        if not is_opening_title(title):
            continue
        if not (getattr(entry, "content", "") or "").strip():
            continue
        seen.add(title)
        out.append(entry)
    return out


def opening_titles(character: Any) -> List[str]:
    return [(e.title or "").strip() for e in opening_entries(character)]


def apply_opening_entry(character: Any, title: str) -> Any:
    """返回应用了开局选择的角色卡副本。

    - ``title`` 为空或与任何条目标题都不相等：原样返回（等同「按卡默认」，
      例如会话换绑了角色卡后旧开局标题已失效）；
    - 选中条目 ``pinned=True``；**其它**开局条目整体移除——未选中的开局
      即使只是 unpin 也会被关键字/语义检索触发（keys 为空的条目同样可能
      命中），只有移除才能保证注入内容与玩家选择一致。
    """
    title = (title or "").strip()
    if character is None or not title:
        return character
    if not any((getattr(e, "title", "") or "").strip() == title
               for e in getattr(character, "table", None) or []):
        return character
    clone: Character = Character.from_dict(character.to_dict())
    kept: List[LoreEntry] = []
    for entry in clone.table:
        entry_title = (entry.title or "").strip()
        if entry_title == title:
            entry.pinned = True
            kept.append(entry)
        elif is_opening_title(entry_title):
            continue
        else:
            kept.append(entry)
    clone.table = kept
    return clone
