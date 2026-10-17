"""台账：角色/物品/地点/任务/伏笔的当前状态与变迁史。

文件：character-memory/{char_id}.ledger.json
状态：
  character/item/location：active（只有 active；删除即不使用）
  task：open → done | cancelled
  foreshadow：planted → resolved
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List

KINDS = ("character", "item", "location", "task", "foreshadow")
# 各类的活跃状态（查询「未完成」时用）
ACTIVE_STATUS = {
    "character": ("active",),
    "item": ("active",),
    "location": ("active",),
    "task": ("open",),
    "foreshadow": ("planted",),
}
# 统一终结动作：resolve / cancel（与抽取提示词一致），落为各类的终态
_TERMINAL_STATUS = {
    "task": {"resolve": "done", "cancel": "cancelled"},
    "foreshadow": {"resolve": "resolved", "cancel": "cancelled"},
}


@dataclass
class LedgerItem:
    kind: str
    name: str
    state: str = ""
    id: str = ""
    first_seen: str = ""
    updated_at: str = ""
    status: str = ""
    history: List[Dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = (self.name or "").strip()
        if self.kind not in KINDS:
            self.kind = "item"
        today = date.today().isoformat()
        if not self.id:
            self.id = "l_" + secrets.token_hex(5)
        if not self.first_seen:
            self.first_seen = today
        if not self.updated_at:
            self.updated_at = today
        if not self.status:
            self.status = (
                "open" if self.kind == "task"
                else "planted" if self.kind == "foreshadow"
                else "active"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "state": self.state,
            "first_seen": self.first_seen,
            "updated_at": self.updated_at,
            "status": self.status,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LedgerItem":
        return cls(
            id=str(d.get("id") or ""),
            kind=str(d.get("kind") or "item"),
            name=str(d.get("name") or ""),
            state=str(d.get("state") or ""),
            first_seen=str(d.get("first_seen") or ""),
            updated_at=str(d.get("updated_at") or ""),
            status=str(d.get("status") or ""),
            history=list(d.get("history") or []),
        )


def _empty_ledger() -> Dict[str, List[LedgerItem]]:
    return {k: [] for k in KINDS}


def load_ledger(path) -> Dict[str, List[LedgerItem]]:
    """读台账；不存在/损坏返回空台账。"""
    try:
        if not path.exists():
            return _empty_ledger()
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_ledger()
    out = _empty_ledger()
    raw_items = []
    if isinstance(data, dict):
        for k in KINDS:
            for d in data.get(k) or []:
                if isinstance(d, dict):
                    raw_items.append(LedgerItem.from_dict(d))
    for item in raw_items:
        out[item.kind].append(item)
    return out


def save_ledger(path, ledger: Dict[str, List[LedgerItem]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: [it.to_dict() for it in ledger.get(k) or []] for k in KINDS}
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_ops(ledger: Dict[str, List[LedgerItem]],
              ops: List[Dict[str, Any]]) -> Dict[str, int]:
    """把抽取产出的台账 ops 应用到台账，返回 {added, updated, closed} 计数。

    op: {"kind", "name", "state", "op": upsert|resolve|cancel}
    按 (kind, name) 匹配既有条目。
    """
    report = {"added": 0, "updated": 0, "closed": 0}
    today = date.today().isoformat()
    for op in ops or []:
        kind = str(op.get("kind") or "item")
        if kind not in KINDS:
            kind = "item"
        name = str(op.get("name") or "").strip()
        if not name:
            continue
        action = str(op.get("op") or "upsert").strip()
        existing = next(
            (it for it in ledger[kind] if it.name == name), None
        )
        terminal = _TERMINAL_STATUS.get(kind, {})
        if action in terminal:
            if existing is not None:
                existing.history.append(
                    {"state": existing.state, "at": existing.updated_at}
                )
                existing.status = terminal[action]
                existing.updated_at = today
                if op.get("state"):
                    existing.state = str(op.get("state"))
                report["closed"] += 1
            else:
                # 无对应条目的终态操作：直接以终态建一条，避免悬空
                item = LedgerItem(
                    kind=kind, name=name,
                    state=str(op.get("state") or ""),
                    status=terminal[action],
                )
                ledger[kind].append(item)
                report["added"] += 1
            continue
        # upsert
        state_text = str(op.get("state") or "")
        if existing is None:
            ledger[kind].append(
                LedgerItem(kind=kind, name=name, state=state_text)
            )
            report["added"] += 1
        else:
            if existing.state != state_text:
                existing.history.append(
                    {"state": existing.state, "at": existing.updated_at}
                )
            existing.state = state_text
            existing.updated_at = today
            # 曾被关闭的条目重新出现 → 复活
            if kind in ("task", "foreshadow"):
                existing.status = ACTIVE_STATUS[kind][0]
            else:
                existing.status = "active"
            report["updated"] += 1
    return report


def match_items(ledger: Dict[str, List[LedgerItem]],
                terms: List[str], limit: int = 8) -> List[LedgerItem]:
    """按关键词（OR 命中，小写包含）在全部条目中找匹配。"""
    low_terms = [t.lower() for t in terms if t]
    out: List[LedgerItem] = []
    for kind in KINDS:
        for it in ledger[kind]:
            name = it.name.lower()
            if any(t in name or t in (it.state or "").lower()
                   for t in low_terms):
                out.append(it)
                if len(out) >= limit:
                    return out
    return out


def open_summary(ledger: Dict[str, List[LedgerItem]],
                 limit_per_kind: int = 3) -> List[LedgerItem]:
    """每类返回最近的活跃条目若干（无 topic 时用）。"""
    out: List[LedgerItem] = []
    for kind in ("task", "foreshadow", "item"):
        active = [
            it for it in (ledger.get(kind) or [])
            if it.status in ACTIVE_STATUS[kind]
        ]
        out.extend(active[-limit_per_kind:])
    return out


KIND_LABELS = {
    "character": "人物", "item": "物品", "location": "地点",
    "task": "任务", "foreshadow": "伏笔",
}
STATUS_LABELS = {
    # 物品/人物/地点只有 active 一种态，标签冗余，留空只显示类别名
    "active": "", "open": "进行中", "planted": "已埋下",
    "done": "已完成", "cancelled": "已取消", "resolved": "已回收",
}

REMINDER_LIMIT_PER_KIND = 2
REMINDER_STATE_LIMIT = 120
REMINDER_BUDGET = 600


def render_ledger_reminder(ledger: Dict[str, List[LedgerItem]],
                           limit_per_kind: int = REMINDER_LIMIT_PER_KIND,
                           state_limit: int = REMINDER_STATE_LIMIT,
                           budget: int = REMINDER_BUDGET) -> str:
    """每轮自动注入的未终结状态概览（任务/伏笔/物品各最近 N 条）。

    与 query_chat_memory 的台账块不同：这里无需 AI 主动调用，也不依赖本轮
    话题，只放"仍在进行中"的状态，防止模型把进行中的任务说成已完成。
    无活跃条目返回空串。
    """
    items = open_summary(ledger, limit_per_kind=limit_per_kind)
    if not items:
        return ""
    lines: List[str] = []
    used = 0
    for it in items:
        kind_label = KIND_LABELS.get(it.kind, it.kind)
        st_label = STATUS_LABELS.get(it.status, it.status)
        head = kind_label + ("·" + st_label if st_label else "")
        state = (it.state or "").strip()
        if len(state) > state_limit:
            state = state[:state_limit] + "…"
        line = "- [{0}] {1}{2}".format(
            head, it.name, ("：" + state) if state else ""
        )
        if used + len(line) > budget:
            continue  # 超预算跳过本条，后面的短条目仍可能放得下
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return (
        "【剧情状态】以下是仍在进行中的任务、已埋下未回收的伏笔与重要物品的"
        "当前状态，请自然沿用；不要把它们说成已完成、已取消或已回收：\n"
        + "\n".join(lines)
    )
