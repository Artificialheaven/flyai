"""酒馆（SillyTavern）OpenAI 预设 → FlyAI Preset。

预设 JSON 的关键字段：
- 顶层采样参数（temperature/top_p/top_k/top_a/min_p/惩罚/seed/…）；
- openai_max_tokens（回复上限，缺省回退 genamt）；
- prompts：prompt manager 块列表，每块有 enabled / injection_position
  （0=in-chat@depth，1=主提示词，null=替换内建标记）/ injection_depth /
  injection_order / role / identifier / content；
- assistant_prefill：assistant 强制前缀。

null 位置的内建标记：main/nsfw/enhanceDefinitions 视为主提示词块；
jailbreak 且 role=assistant → assistant_prefill；其余有内容的标记落到
tail（历史后 system，类似 PHI）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple

from ..tavern import lenient_json_loads
from .models import Preset, PresetBlock, sanitize_params

_ROLE_MAP = {0: "system", 1: "user", 2: "assistant"}
# 视为「主提示词」位置的内建标记
_MAIN_MARKERS = {"main", "nsfw", "enhanceDefinitions"}

ENVELOPE_TYPE = "flyai-preset"
ENVELOPE_VERSION = 1


class PresetImportError(ValueError):
    """预设格式无法识别。"""


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _block_from_prompt(raw: Dict[str, Any]) -> Tuple[PresetBlock, str]:
    """酒馆 prompt 项 → (PresetBlock, kind)；kind 为 main/depth/tail/prefill/skip。"""
    pos = raw.get("injection_position")
    st_role = _as_int(raw.get("role"), 0)
    role = _ROLE_MAP.get(st_role, "system")
    order = _as_int(raw.get("injection_order"), 100)
    identifier = str(raw.get("identifier") or "")

    b = PresetBlock(
        name=str(raw.get("name") or identifier or "未命名块"),
        content=raw.get("content") or "",
        enabled=bool(raw.get("enabled", True)),
        role=role,
        depth=max(0, _as_int(raw.get("injection_depth"), 4)),
        insert_order=order,
        identifier=identifier,
    )
    if pos == 0:
        b.position = "depth"
        return b, "depth"
    if pos == 1:
        b.position = "main"
        return b, "main"
    # injection_position=null：替换内建标记
    if identifier == "jailbreak":
        if role == "assistant":
            b.position = "tail"  # 实际走 assistant_prefill 槽
            return b, "prefill"
        b.position = "tail"
        return b, "tail"
    if identifier in _MAIN_MARKERS:
        b.position = "main"
        return b, "main"
    b.position = "tail"
    return b, "tail"


def preset_from_dict(data: Dict[str, Any], name: str = "") -> Tuple[Preset, Dict[str, int]]:
    if not isinstance(data, dict):
        raise PresetImportError("顶层不是 JSON 对象")
    # FlyAI 导出信封：直接还原
    if data.get("type") == ENVELOPE_TYPE:
        inner = data.get("data")
        if not isinstance(inner, dict):
            raise PresetImportError("信封缺少 data")
        p = Preset.from_dict(inner)
        if name:
            p.name = name
        p.id = ""  # 导入即新 id
        return p, {"blocks": len(p.blocks), "enabled": sum(1 for b in p.blocks if b.enabled)}

    params = sanitize_params(data)
    max_tokens = _as_int(data.get("openai_max_tokens"), 0)
    if max_tokens <= 0:
        max_tokens = _as_int(data.get("genamt"), 0)
    max_tokens = max(0, max_tokens)

    blocks = []
    stats = {"blocks": 0, "enabled": 0, "main": 0, "depth": 0, "tail": 0,
             "skipped": 0, "prefill": 0}
    prefill = ""
    prompts = data.get("prompts")
    if not isinstance(prompts, list):
        raise PresetImportError("缺少 prompts 列表，不是酒馆 OpenAI 预设")
    for raw in prompts:
        if not isinstance(raw, dict):
            continue
        if not (raw.get("content") or "").strip():
            stats["skipped"] += 1
            continue
        block, kind = _block_from_prompt(raw)
        if kind == "prefill":
            if not prefill:
                prefill = block.content
                stats["prefill"] = 1
            # jailbreak 前缀块不再重复进 tail
            continue
        blocks.append(block)
        stats["blocks"] += 1
        if block.enabled:
            stats["enabled"] += 1
            stats[kind] += 1
    # 顶层 assistant_prefill（jailbreak 块优先）
    if not prefill and (data.get("assistant_prefill") or "").strip():
        prefill = str(data["assistant_prefill"]).strip()
        stats["prefill"] = 1

    p = Preset(
        name=(name or str(data.get("name") or "")).strip() or "导入的预设",
        note=str(data.get("description") or ""),
        source="tavern",
        params=params,
        max_tokens=max_tokens,
        assistant_prefill=prefill,
        blocks=blocks,
    )
    return p, stats


def preset_from_text(text: str, name: str = "") -> Tuple[Preset, Dict[str, int]]:
    try:
        data = json.loads(text, strict=False)
    except (TypeError, ValueError):
        data = lenient_json_loads(text)
    return preset_from_dict(data, name=name)


def preset_to_envelope(p: Preset) -> Dict[str, Any]:
    return {"type": ENVELOPE_TYPE, "version": ENVELOPE_VERSION,
            "name": p.name, "data": p.to_dict()}
