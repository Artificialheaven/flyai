"""普通用户计费辅助：模型倍率读取、扣费拦截、额度视图。

规则见 plan：每轮对话扣 1×当前模型倍率；群聊按发言 AI 逐个累计；
管理员（ADMIN_UID）不计费；quota_total = -1 表示无限。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from .user import ADMIN_UID


def provider_rate(provider: Any) -> float:
    """取提供商的计费倍率，缺省 1.0。"""
    rate = getattr(getattr(provider, "cfg", None), "rate", 1.0)
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
