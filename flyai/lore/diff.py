"""压缩提案的 diff：按标题对内容表/角色/世界做前后分类。

只做字符串级 before/after，行级 LCS 对齐放在前端，保持后端简单、payload 小。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .merge import normalized_title_key


def content_signature(d: Optional[Dict[str, Any]]) -> str:
    """对卡 dict 取短签名，用于检测「提案生成后设定是否变化」。空 dict 签名为空串。"""
    if not d:
        return ""
    blob = json.dumps(d, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _entry_dict(e: Any) -> Dict[str, Any]:
    if isinstance(e, dict):
        return {
            "title": str(e.get("title", "") or ""),
            "content": str(e.get("content", "") or ""),
            "pinned": bool(e.get("pinned", False)),
        }
    return {
        "title": getattr(e, "title", "") or "",
        "content": getattr(e, "content", "") or "",
        "pinned": bool(getattr(e, "pinned", False)),
    }


def table_diff(before: List[Any], after: List[Any]) -> Dict[str, Any]:
    """按归一化标题把 after 相对 before 分为 added/changed/unchanged。

    合并语义下不应出现 removed，仍输出 removed: [] 作后端自检。
    """
    before_map: Dict[str, Dict[str, Any]] = {}
    for e in (before or []):
        d = _entry_dict(e)
        key = normalized_title_key(d["title"])
        if key not in before_map:
            before_map[key] = d

    added: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    for e in (after or []):
        d = _entry_dict(e)
        key = normalized_title_key(d["title"])
        old = before_map.get(key)
        if old is None:
            added.append(d)
        elif (old.get("content") or "") != (d.get("content") or ""):
            changed.append(
                {
                    "title": d["title"],
                    "before": old.get("content", ""),
                    "after": d.get("content", ""),
                    "pinned": bool(old.get("pinned")) or bool(d.get("pinned")),
                }
            )
        else:
            unchanged.append(
                {
                    "title": d["title"],
                    "content": d.get("content", ""),
                    "pinned": bool(old.get("pinned")) or bool(d.get("pinned")),
                }
            )
    return {"added": added, "changed": changed, "unchanged": unchanged, "removed": []}


def character_diff(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """角色卡 diff：intro/background 字符串前后 + table_diff。"""
    before = before or {}
    after = after or {}
    return {
        "intro": {"before": before.get("intro", "") or "", "after": after.get("intro", "") or ""},
        "background": {
            "before": before.get("background", "") or "",
            "after": after.get("background", "") or "",
        },
        "table": table_diff(before.get("table", []), after.get("table", [])),
    }


def world_diff(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """世界观 diff：content 字符串前后 + table_diff。"""
    before = before or {}
    after = after or {}
    return {
        "content": {"before": before.get("content", "") or "", "after": after.get("content", "") or ""},
        "table": table_diff(before.get("table", []), after.get("table", [])),
    }
