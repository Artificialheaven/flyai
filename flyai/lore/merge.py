"""压缩 patch 的合并逻辑：内容表按标题合并、角色/世界字段合并。

替代旧 gen.py / resolve.py 中四处重复的「整表替换」实现。
核心保证：AI 漏列的条目不丢失、pinned 标记保留、永不删除旧条目。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Union

from .models import LoreEntry

_WRAP_QUOTES = "『』「」“”\"' "


def normalized_title_key(title: Any) -> str:
    """标题归一化：去包裹引号、去全部空白、casefold。

    使「魔 法 剑」==「魔法剑」、大小写不敏感；不做模糊/同义匹配，
    AI 改了标题措辞会被视为新条目（宁重复不丢数据）。
    """
    s = str(title or "").strip().strip(_WRAP_QUOTES)
    s = re.sub(r"\s+", "", s)
    return s.casefold()


def _as_entry(item: Union[LoreEntry, Dict[str, Any]]) -> LoreEntry:
    if isinstance(item, LoreEntry):
        return LoreEntry.from_dict(item.to_dict())
    if isinstance(item, dict):
        return LoreEntry.from_dict(item)
    return LoreEntry()


def merge_tables(
    base_entries: List[Union[LoreEntry, Dict[str, Any]]],
    patch_entries: List[Union[LoreEntry, Dict[str, Any]]],
) -> List[LoreEntry]:
    """把 patch 条目按标题合并进 base 表。

    - 同标题：patch content 非空才覆盖（空 content 不覆盖旧文）；
      pinned 只增不减（AI 输出的 pinned 恒为 False）。
    - 新标题：追加到末尾。
    - 永不删除；title/content 皆空的 patch 条目丢弃。
    """
    work = [_as_entry(e) for e in (base_entries or [])]
    index: Dict[str, int] = {}
    for i, e in enumerate(work):
        key = normalized_title_key(e.title)
        if key not in index:
            index[key] = i

    for raw in patch_entries or []:
        p = _as_entry(raw)
        title = (p.title or "").strip()
        content = p.content or ""
        if not title and not content.strip():
            continue
        key = normalized_title_key(title)
        if key in index:
            i = index[key]
            if content.strip():
                work[i].content = content
            work[i].pinned = bool(work[i].pinned) or bool(p.pinned)
        else:
            work.append(LoreEntry(title=title, content=content, pinned=bool(p.pinned)))
            index[key] = len(work) - 1
    return work


def merge_character_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """把 AI 归纳出的角色字段合并进现有卡 dict。

    intro/background：patch 值 truthy 时 strip 覆盖（沿用旧语义）；
    table：非空 list 时按标题合并；mes_example 不由压缩产生，保持 base。
    """
    merged = dict(base or {})
    patch = patch or {}
    for f in ("intro", "background"):
        if patch.get(f):
            merged[f] = str(patch[f]).strip()
    table = patch.get("table")
    if isinstance(table, list) and table:
        merged["table"] = [e.to_dict() for e in merge_tables(merged.get("table", []), table)]
    return merged


def merge_world_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """世界设定 patch：content truthy 时覆盖，table 非空时按标题合并。"""
    merged = dict(base or {})
    patch = patch or {}
    if patch.get("content"):
        merged["content"] = str(patch["content"]).strip()
    table = patch.get("table")
    if isinstance(table, list) and table:
        merged["table"] = [e.to_dict() for e in merge_tables(merged.get("table", []), table)]
    return merged
