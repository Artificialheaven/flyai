"""酒馆（SillyTavern）世界书 v2 解析为 FlyAI World。

酒馆条目（entry）到 LoreEntry 的映射：

- comment/name        → title
- content             → content
- constant 且未 disable → pinned（常驻）
- position=4（@depth）→ position="depth"，depth/order/role 一并迁移；
  其余位置（角色定义前/后、范例前后）统一为 "system" 常驻块
- key/keysecondary    → 触发词 keys（selectiveLogic=0 AND ANY 时
  次级关键词也作为触发词；NOT 逻辑不迁移）
- useProbability/probability → 每轮注入概率
- disable=True 的条目不丢弃：作为非常驻、无触发词的普通条目导入，
  用户可在编辑器里手动常驻/开启
- 空内容条目跳过（常见于分隔/分组占位）
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..tavern import lenient_json_loads
from .models import LoreEntry, World

_KEY_SPLIT_RE = re.compile(r"[\s,，、;；/]+")
_ROLE_MAP = {0: "system", 1: "user", 2: "assistant"}


class WorldbookError(ValueError):
    """世界书格式无法识别。"""


def iter_entries(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """entries 可能是 id→entry 字典（世界书 v2）或列表。"""
    book = data.get("entries") if isinstance(data, dict) else None
    if isinstance(book, dict):
        return [e for e in book.values() if isinstance(e, dict)]
    if isinstance(book, list):
        return [e for e in book if isinstance(e, dict)]
    raise WorldbookError("文件中没有 entries 字段，不是酒馆世界书")


def _split_keys(raw: Any) -> List[str]:
    """酒馆 key 数组（元素自身可能是逗号分隔多词）→ 去重触发词列表。"""
    out: List[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return out
    for item in raw:
        for part in _KEY_SPLIT_RE.split(str(item or "")):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _entry_keys(e: Dict[str, Any]) -> List[str]:
    """世界书导出用 key（单数）；CC 角色卡内嵌条目用 keys，两者兼容。"""
    keys = _split_keys(e.get("key"))
    if not keys:
        keys = _split_keys(e.get("keys"))
    return keys


def _entry_secondary(e: Dict[str, Any]) -> List[str]:
    keys = _split_keys(e.get("keysecondary"))
    if not keys:
        keys = _split_keys(e.get("key_secondary"))
    return keys


def _entry_title(e: Dict[str, Any]) -> str:
    title = (e.get("comment") or e.get("name") or "").strip()
    if title:
        return title
    keys = e.get("key") or e.get("keys") or []
    if keys and isinstance(keys[0], str):
        return keys[0].strip()
    return "(无标题)"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_entry(e: Dict[str, Any]) -> LoreEntry:
    """单条酒馆 entry → LoreEntry（调用方负责空内容过滤）。"""
    keys = _entry_keys(e)
    # selectiveLogic: 0=AND ANY（主词 + 任一次级词）时把次级词也当触发词；
    # 1=AND ALL / 2=NOT ANY / 3=NOT ALL 的语义本项目没有，忽略次级词
    if e.get("selective") and _as_int(e.get("selectiveLogic"), 0) == 0:
        for k in _entry_secondary(e):
            if k not in keys:
                keys.append(k)

    constant = bool(e.get("constant"))
    # 世界书用 disable=True；CC 卡内嵌条目用 enabled=False
    disabled = bool(e.get("disable")) or (e.get("enabled") is False)
    st_position = _as_int(e.get("position"), 0)

    use_prob = e.get("useProbability")
    probability = _as_int(e.get("probability"), 100) if use_prob else 100

    return LoreEntry(
        title=_entry_title(e),
        content=e.get("content") or "",
        pinned=constant and not disabled,
        keys=keys,
        position="depth" if st_position == 4 else "system",
        depth=max(0, _as_int(e.get("depth"), 4)),
        insert_order=_as_int(e.get("order"), 100),
        role=_ROLE_MAP.get(_as_int(e.get("role"), 0), "system"),
        probability=max(1, min(100, probability)),
    )


def worldbook_from_dict(data: Dict[str, Any], name: str = "") -> Tuple[World, Dict[str, int]]:
    """酒馆世界书 JSON → 未保存的 World。返回 (world, 统计)。

    统计：{"entries": 导入条目数, "disabled": 禁用但保留数,
    "depth": 深度常驻条数, "skipped": 空内容跳过数}
    """
    raws = iter_entries(data)
    table: List[LoreEntry] = []
    stats = {"entries": 0, "disabled": 0, "depth": 0, "skipped": 0}
    for raw in raws:
        content = (raw.get("content") or "").strip()
        if not content:
            stats["skipped"] += 1
            continue
        entry = parse_entry(raw)
        if raw.get("disable"):
            stats["disabled"] += 1
        if entry.pinned and entry.position == "depth":
            stats["depth"] += 1
        table.append(entry)
    stats["entries"] = len(table)

    w = World(
        name=(name or (data.get("name") or "")).strip() or "导入的世界书",
        content=(data.get("description") or "").strip(),
        table=table,
    )
    return w, stats


def worldbook_from_text(text: str, name: str = "") -> Tuple[World, Dict[str, int]]:
    """文本入口：严格 JSON 失败时回退容错解析。"""
    try:
        import json

        data = json.loads(text, strict=False)
    except (ValueError, TypeError):
        data = lenient_json_loads(text)
    if not isinstance(data, dict):
        raise WorldbookError("世界书 JSON 顶层不是对象")
    return worldbook_from_dict(data, name=name)
