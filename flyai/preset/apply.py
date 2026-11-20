"""把生效中的预设渲染成本轮请求素材（采样参数 + 三类 prompt 块）。"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..tavern import render_tavern_macros
from .models import Preset

logger = logging.getLogger(__name__)


def load_active_preset(sc) -> Optional[Preset]:
    """读取该用户当前启用的预设；未启用/已删除/损坏一律返回 None（等于不使用）。"""
    store = getattr(sc, "presets", None)
    us = getattr(sc, "user_settings", None)
    if store is None or us is None:
        return None
    pid = getattr(us, "active_preset_id", "") or ""
    if not pid:
        return None
    try:
        return store.get(pid)
    except Exception:
        logger.exception("启用预设读取失败")
        return None


def active_preset_regex_scripts(sc) -> List[Dict[str, Any]]:
    """启用预设附带的原始正则脚本（已清洗）；是否生效由调用侧开关决定。"""
    preset = load_active_preset(sc)
    if preset is None:
        return []
    return list(getattr(preset, "regex_scripts", None) or [])


@dataclass
class RenderedPreset:
    main_text: str = ""                 # 追加到系统提示词末尾
    tail_text: str = ""                 # 历史之后的 system（与 PHI 同位）
    depth_blocks: List[Dict[str, Any]] = field(default_factory=list)
    prefill: str = ""                   # assistant 强制前缀
    params: Dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 0


def _macros(text: str, ctx: Dict[str, Any], rng: random.Random) -> str:
    return render_tavern_macros(
        text,
        char_name=ctx.get("char_name") or "",
        user_name=ctx.get("user_name") or "",
        group_names=ctx.get("group_names") or [],
        last_message=ctx.get("last_message") or "",
        rng=rng,
    )


def render_preset(
    preset: Optional[Preset],
    *,
    char_name: str = "",
    user_name: str = "",
    group_names: Optional[List[str]] = None,
    last_message: str = "",
    rng: Optional[random.Random] = None,
) -> RenderedPreset:
    """渲染预设；preset 为 None 时返回全空结果。"""
    out = RenderedPreset()
    if preset is None:
        return out
    rng = rng or random
    ctx = {"char_name": char_name, "user_name": user_name,
           "group_names": group_names or [], "last_message": last_message}

    main_parts: List[str] = []
    tail_parts: List[str] = []
    depth: List[Dict[str, Any]] = []
    for i, b in enumerate(preset.blocks):
        if not b.enabled or not (b.content or "").strip():
            continue
        text = _macros(b.content, ctx, rng).strip()
        if not text:
            continue
        if b.position == "main":
            main_parts.append((b.insert_order, i, text))
        elif b.position == "tail":
            tail_parts.append((b.insert_order, i, text))
        elif b.position == "depth":
            depth.append({
                "role": b.role,
                "content": text,
                "depth": b.depth,
                "order": b.insert_order,
            })

    main_parts.sort(key=lambda x: (x[0], x[1]))
    tail_parts.sort(key=lambda x: (x[0], x[1]))
    out.main_text = "\n\n".join(t for _o, _i, t in main_parts)
    out.tail_text = "\n\n".join(t for _o, _i, t in tail_parts)
    out.depth_blocks = depth
    out.prefill = _macros(preset.assistant_prefill or "", ctx, rng).strip()
    out.params = dict(preset.params or {})
    out.max_tokens = int(preset.max_tokens or 0)
    return out
