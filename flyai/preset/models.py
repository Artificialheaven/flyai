"""模型预设数据模型。

预设两部分：
- params：采样参数（temperature/top_p/top_k/…），按白名单透传到请求体，
  优先级低于窗口/用户级 max_tokens，高于模型配置 params。
- blocks：prompt 块（酒馆 prompt manager 概念），三类位置：
  main  = 并入系统提示词末尾；
  depth = 按 @depth 插到聊天历史深处（agent loop 注入）；
  tail  = 历史之后、生成之前的末位 system（类似 PHI）。
另有 assistant_prefill：assistant 角色的强制前缀（jailbreak 标记）。
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..st.regex_scripts import sanitize_regex_scripts

# 允许透传给 OpenAI 兼容 / Anthropic 提供商的采样参数
SAMPLING_KEYS = (
    "temperature", "top_p", "top_k", "top_a", "min_p",
    "frequency_penalty", "presence_penalty", "repetition_penalty",
    "seed", "reasoning_effort",
)
POSITIONS = ("main", "depth", "tail")
ROLES = ("system", "user", "assistant")


def now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class PresetBlock:
    name: str = ""
    content: str = ""
    enabled: bool = True
    role: str = "system"        # depth 块的消息角色
    position: str = "main"      # main | depth | tail
    depth: int = 4
    insert_order: int = 100
    identifier: str = ""        # 酒馆内建标记 id（main/nsfw/jailbreak…）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "content": self.content,
            "enabled": bool(self.enabled),
            "role": self.role if self.role in ROLES else "system",
            "position": self.position if self.position in POSITIONS else "main",
            "depth": max(0, int(self.depth or 0)),
            "insert_order": int(self.insert_order if self.insert_order is not None else 100),
            "identifier": self.identifier,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PresetBlock":
        def _i(key, default):
            try:
                return int(d.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            name=str(d.get("name") or ""),
            content=str(d.get("content") or ""),
            enabled=bool(d.get("enabled", True)),
            role=d.get("role") if d.get("role") in ROLES else "system",
            position=d.get("position") if d.get("position") in POSITIONS else "main",
            depth=max(0, _i("depth", 4)),
            insert_order=_i("insert_order", 100),
            identifier=str(d.get("identifier") or ""),
        )


@dataclass
class Preset:
    id: str = ""
    name: str = ""
    note: str = ""
    source: str = "tavern"           # tavern | flyai
    params: Dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 0              # 回复 token 上限；0=不覆盖
    assistant_prefill: str = ""
    blocks: List[PresetBlock] = field(default_factory=list)
    # 预设绑定正则（酒馆 Regex Preset Binder 写入 prompts 的 regexes-bindings）
    regex_scripts: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex
        if not self.created_at:
            self.created_at = now_stamp()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "note": self.note,
            "source": self.source if self.source in ("tavern", "flyai") else "flyai",
            "params": dict(self.params or {}),
            "max_tokens": int(self.max_tokens or 0),
            "assistant_prefill": self.assistant_prefill or "",
            "blocks": [b.to_dict() for b in self.blocks],
            "regex_scripts": copy.deepcopy(list(self.regex_scripts or [])),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Preset":
        return cls(
            id=str(d.get("id") or ""),
            name=str(d.get("name") or ""),
            note=str(d.get("note") or ""),
            source=str(d.get("source") or "flyai"),
            params=sanitize_params(d.get("params") or {}),
            max_tokens=max(0, int(d.get("max_tokens") or 0)),
            assistant_prefill=str(d.get("assistant_prefill") or ""),
            blocks=[PresetBlock.from_dict(b) for b in (d.get("blocks") or [])
                    if isinstance(b, dict)],
            regex_scripts=sanitize_regex_scripts(d.get("regex_scripts")),
            created_at=str(d.get("created_at") or ""),
            updated_at=str(d.get("updated_at") or ""),
        )


def _coerce_num(v: Any):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def sanitize_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    """只保留白名单采样参数与合法类型；seed=-1（随机）剔除。"""
    out: Dict[str, Any] = {}
    for k in SAMPLING_KEYS:
        if k not in raw or raw[k] is None:
            continue
        if k == "reasoning_effort":
            v = str(raw[k]).strip().lower()
            if v in ("low", "medium", "high"):
                out[k] = v
            continue
        n = _coerce_num(raw[k])
        if n is None:
            continue
        if k == "seed" and int(n) < 0:
            continue
        if k == "temperature":
            n = min(2.0, max(0.0, float(n)))
        elif k in ("top_p", "min_p", "top_a"):
            n = min(1.0, max(0.0, float(n)))
        elif k in ("frequency_penalty", "presence_penalty"):
            n = min(2.0, max(-2.0, float(n)))
        out[k] = n
    return out
