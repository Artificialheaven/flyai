"""角色属性跟踪服务（好感度、金钱等）。

每个会话维护一份属性字典（session.attributes），初次使用时按配置里的默认值初始化。
AI 通过 update_attributes 工具在每回合结束后提交增减值，本类负责校验、应用并做上下限钳制。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config import AttributeDef
from ..sessions.models import Session


def resolve_attribute_defs(character, roleplay) -> List[AttributeDef]:
    """解析某个会话实际使用的属性定义集。

    优先取角色卡自带的 attributes；角色卡未定义（兼容旧卡）时回退到全局
    roleplay.attributes.list（需角色扮演全局开启）。
    """
    raw = getattr(character, "attributes", None) if character else None
    if raw:
        defs: List[AttributeDef] = []
        for a in raw:
            if isinstance(a, AttributeDef):
                defs.append(a)
            elif isinstance(a, dict) and a.get("key"):
                try:
                    defs.append(AttributeDef(
                        key=str(a["key"]),
                        label=str(a.get("label", "") or ""),
                        initial=float(a.get("initial", 0) or 0),
                        min=_num(a.get("min")),
                        max=_num(a.get("max")),
                    ))
                except (TypeError, ValueError):
                    continue  # 非法定义跳过，避免拖垮会话
        return defs
    if roleplay and roleplay.attributes.enabled:
        return list(roleplay.attributes.list)
    return []


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class AttributeManager:
    def __init__(self, session: Session, defs: List[AttributeDef]):
        self.session = session
        self.defs = {d.key: d for d in defs}
        self._ensure_initial()

    def _ensure_initial(self) -> None:
        if not self.session.attributes:
            self.session.attributes = {k: d.initial for k, d in self.defs.items()}

    def get_all(self) -> Dict[str, Any]:
        self._ensure_initial()
        return dict(self.session.attributes)

    def describe(self) -> str:
        """返回「label: value」的可读描述，供工具结果与提示词展示。"""
        parts = []
        for k, v in self.get_all().items():
            d = self.defs.get(k)
            name = d.label if d and d.label else k
            parts.append(f"{name}({k}): {v}")
        return "、".join(parts)

    def _clamp(self, key: str, value: float) -> float:
        d = self.defs.get(key)
        if d is None:
            return value
        if d.min is not None:
            value = max(float(d.min), value)
        if d.max is not None:
            value = min(float(d.max), value)
        return value

    def apply_changes(self, changes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """应用一批增减量 [{key,delta}]，返回更新后的全量属性。"""
        self._ensure_initial()
        for ch in changes or []:
            key = str(ch.get("key", ""))
            if key not in self.defs:
                continue  # 未知属性忽略，避免脚本注入
            try:
                delta = float(ch.get("delta", 0))
            except (TypeError, ValueError):
                continue
            current = float(self.session.attributes.get(key, self.defs[key].initial))
            self.session.attributes[key] = self._clamp(key, current + delta)
        return self.get_all()