"""用量流水接口：按本地日期聚合近 N 天的 token / 花费 / 回复数。

数据源为 data/<uid>/usage.jsonl，混合两类行：
- token 结算行（无 kind）：对话 token 计费；
- metering 行（kind ∈ search/fetch/tts/asr）：按次/字符/秒计费。
两类行都计入每日花费；另按 kind 汇总出项目消耗（categories）。
旧数据没有流水，不做回溯补算，页面从功能上线后的第一轮对话起展示。
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Dict, List

from fastapi import APIRouter, Request

from ..proactor.clock import local_date_str, local_dt

router = APIRouter(prefix="/api", tags=["usage"])


def _tzname(state) -> str:
    try:
        return state.config.scheduler.timezone or "Asia/Shanghai"
    except AttributeError:
        return "Asia/Shanghai"


def _empty_day(date: str) -> Dict:
    return {
        "date": date,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "messages": 0,
    }


# 项目消耗的固定展示顺序；token 行没有 kind，归为 chat
CATEGORY_ORDER = ("chat", "search", "fetch", "tts", "asr")
_DEFAULT_UNITS = {
    "chat": "token",
    "search": "次",
    "fetch": "次",
    "tts": "字符",
    "asr": "秒",
}


def _empty_category(kind: str) -> Dict:
    return {
        "kind": kind,
        "cost": 0.0,
        "quantity": 0,
        "unit": _DEFAULT_UNITS.get(kind, ""),
        "calls": 0,
        "engines": [],
    }


@router.get("/my/usage")
def my_usage(request: Request, days: int = 7):
    """当前用户近 `days` 天（含今天）的用量汇总。"""
    state = request.app.state.flyai
    sc = state.user(request)
    try:
        days = max(1, min(90, int(days)))
    except (TypeError, ValueError):
        days = 7
    tzname = _tzname(state)
    now = time.time()
    today = local_dt(now, tzname).date()
    dates: List[str] = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(days - 1, -1, -1)
    ]
    buckets = {d: _empty_day(d) for d in dates}
    categories: Dict[str, Dict] = {}
    ledger = None
    if getattr(sc, "settings_path", None):
        ledger = sc.settings_path.parent / "usage.jsonl"
    if ledger is not None and ledger.exists():
        try:
            lines = ledger.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            bucket = buckets.get(str(rec.get("date") or ""))
            if bucket is None:
                continue
            prompt_tokens = int(rec.get("prompt_tokens") or 0)
            cached_tokens = int(rec.get("cached_tokens") or 0)
            completion_tokens = int(rec.get("completion_tokens") or 0)
            total_tokens = int(rec.get("total_tokens") or 0)
            messages = int(rec.get("messages") or 0)
            cost = float(rec.get("cost") or 0)
            bucket["prompt_tokens"] += prompt_tokens
            bucket["cached_tokens"] += cached_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["total_tokens"] += total_tokens
            bucket["cost"] += cost
            bucket["messages"] += messages

            kind = str(rec.get("kind") or "chat").strip() or "chat"
            cat = categories.get(kind)
            if cat is None:
                cat = _empty_category(kind)
                categories[kind] = cat
            cat["cost"] += cost
            if kind == "chat":
                cat["quantity"] += total_tokens
                cat["calls"] += messages
            else:
                cat["quantity"] += int(float(rec.get("quantity") or 0))
                cat["calls"] += 1
                unit = str(rec.get("unit") or "").strip()
                if unit:
                    cat["unit"] = unit
                engine = str(rec.get("engine") or "").strip()
                if engine and engine not in cat["engines"]:
                    cat["engines"].append(engine)
    out = [buckets[d] for d in dates]
    for b in out:
        b["cost"] = round(b["cost"], 6)
    totals = _empty_day("")
    totals["date"] = ""
    for b in out:
        for key in ("prompt_tokens", "cached_tokens", "completion_tokens",
                    "total_tokens", "cost", "messages"):
            totals[key] += b[key]
    totals["cost"] = round(totals["cost"], 6)
    ordered_kinds = [k for k in CATEGORY_ORDER if k in categories]
    ordered_kinds += [k for k in categories if k not in CATEGORY_ORDER]
    cat_out = []
    for kind in ordered_kinds:
        c = categories[kind]
        c["cost"] = round(c["cost"], 6)
        cat_out.append(c)
    # 今天（用于页面右侧「今日」摘要）由前端取最后一项即可
    return {"days": out, "totals": totals, "today": dates[-1],
            "categories": cat_out}