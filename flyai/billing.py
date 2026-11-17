"""普通用户计费辅助：模型倍率/分档价格、扣费拦截、额度视图、用量流水。

规则：
- 未配置 pricing 的模型沿用「每轮扣 1×rate」（群聊按发言 AI 逐个累计）；
- 配置了 pricing 的模型按实际 token 结算（缓存命中/未命中/输出分价，
  可被时段覆盖），开轮前只要求余额 > 0；
- 管理员（ADMIN_UID）不计费；quota_total = -1 表示无限。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from .proactor.clock import local_date_str, local_dt
from .user import ADMIN_UID


def provider_rate(provider: Any) -> float:
    """取提供商的计费倍率，缺省 1.0（兼容 provider 包装与配置对象）。"""
    cfg = getattr(provider, "cfg", provider)
    rate = getattr(cfg, "rate", 1.0)
    try:
        return max(0.0, float(rate))
    except (TypeError, ValueError):
        return 1.0


def charge_or_402(state, uid: str, cost: float) -> None:
    """扣费；管理员与无限额度直接放行，余额不足抛 402。

    入参传 state 而非 user_store：管理员路径必须在属性访问之前返回，
    兼容测试里没有 user_store 的简易 state。
    """
    if uid == ADMIN_UID:
        return
    if not state.user_store.consume(uid, cost):
        raise HTTPException(402, "对话次数已用完，请联系管理员充值")


def refund(state, uid: str, cost: float) -> None:
    """管理员无额度记录；其余用户退回未实际发生的费用。"""
    if uid == ADMIN_UID:
        return
    state.user_store.refund(uid, cost)


def has_pricing(provider: Any) -> bool:
    """该提供商是否按 token 计费。"""
    cfg = getattr(provider, "cfg", provider)
    return getattr(cfg, "pricing", None) is not None


def resolve_prices(provider: Any, now: Optional[float] = None,
                   tzname: str = "Asia/Shanghai") -> Optional[Dict[str, Any]]:
    """解析当前时刻生效的价格（每 1M token）；未配置 pricing 返回 None。"""
    cfg = getattr(provider, "cfg", provider)
    pricing = getattr(cfg, "pricing", None)
    if pricing is None:
        return None
    local = local_dt(time.time() if now is None else now, tzname)
    try:
        return pricing.prices_at(local)
    except AttributeError:
        # 兼容 dict 形态（测试/外部传入）
        base = pricing if isinstance(pricing, dict) else {}
        return {
            "cache_hit_input": float(base.get("cache_hit_input") or 0),
            "input": float(base.get("input") or 0),
            "output": float(base.get("output") or 0),
            "period": "",
        }


def usage_cost(prices: Dict[str, Any], prompt: int = 0, cached: int = 0,
               completion: int = 0) -> float:
    """按实际 token 算点数：命中输入/未命中输入/输出分别计价。"""
    prompt = max(0, int(prompt or 0))
    cached = max(0, min(int(cached or 0), prompt))
    completion = max(0, int(completion or 0))
    miss = prompt - cached
    cost = (
        cached * float(prices.get("cache_hit_input") or 0)
        + miss * float(prices.get("input") or 0)
        + completion * float(prices.get("output") or 0)
    ) / 1_000_000.0
    return round(max(0.0, cost), 6)


def precheck_or_402(state, uid: str) -> None:
    """按 token 计费模型的开轮预检：余额 > 0 才放行（管理员/无限额度直接过）。"""
    if uid == ADMIN_UID:
        return
    u = state.user_store.get_by_id(uid)
    if u is None:
        return
    total = float(u.quota_total or 0)
    if total == -1:
        return
    if max(0.0, total - float(u.quota_used or 0)) <= 1e-9:
        raise HTTPException(402, "额度已用完，请兑换 CDK 或联系管理员充值")


def append_ledger(path: Path, record: Dict[str, Any]) -> None:
    """追加一行用量流水（JSONL）；写失败不影响主流程。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def settle_usage(state, uid: str, provider: Any, usage: Optional[Dict[str, Any]],
                 session_id: str = "", ledger_path: Optional[Path] = None,
                 messages: int = 1, now: Optional[float] = None,
                 tzname: str = "Asia/Shanghai") -> Optional[Dict[str, Any]]:
    """一轮 token 计费的结算：算价 → 累加 quota_used → 写流水。

    未配置 pricing 或没有任何用量时返回 None（由旧 rate 计费路径负责）。
    """
    prices = resolve_prices(provider, now, tzname)
    if prices is None or not usage:
        return None
    p = int(usage.get("prompt_tokens") or 0)
    c = int(usage.get("completion_tokens") or 0)
    hit = int(usage.get("cached_tokens") or 0)
    if not (p or c or hit):
        return None
    cost = usage_cost(prices, p, hit, c)
    if uid != ADMIN_UID and cost > 0:
        state.user_store.add_used(uid, cost)
    stamp = time.time() if now is None else float(now)
    cfg = getattr(provider, "cfg", provider)
    record = {
        "ts": round(stamp, 3),
        "date": local_date_str(stamp, tzname),
        "session_id": session_id or "",
        "provider_id": getattr(cfg, "id", "") or "",
        "model": getattr(cfg, "model", "") or "",
        "prompt_tokens": p,
        "cached_tokens": hit,
        "completion_tokens": c,
        "total_tokens": p + c,
        "cost": cost,
        "messages": max(0, int(messages or 0)),
        "period": prices.get("period") or "",
    }
    if ledger_path is not None:
        append_ledger(ledger_path, record)
    return record


def quota_view(user_store, uid: str) -> Optional[Dict[str, Any]]:
    """普通用户额度视图；管理员/用户不存在返回 None。"""
    if uid == ADMIN_UID:
        return None
    u = user_store.get_by_id(uid)
    if u is None:
        return None
    total = float(u.quota_total or 0)
    used = float(u.quota_used or 0)
    if total == -1:
        return {"total": -1, "used": used, "remaining": None}
    return {"total": total, "used": used,
            "remaining": max(0.0, total - used)}
