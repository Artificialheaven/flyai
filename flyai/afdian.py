"""爱发电 Open API：订单查询（webhook 的权威校验来源）。

爱发电的 webhook 没有签名，只能证明「有人访问了回调地址」，不能证明订单
真实存在或已支付。因此每次通知都再用 query-order 查一遍，status == 2 才
视为已支付；webhook 只是触发时机。

签名公式：md5(token + "params" + params + "ts" + ts + "user_id" + user_id)。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

import httpx


class AfdianError(Exception):
    """爱发电接口调用失败（附带可展示的中文原因）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def afdian_sign(token: str, params: str, ts: int, user_id: str) -> str:
    raw = token + "params" + params + "ts" + str(ts) + "user_id" + user_id
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _client(transport: Optional[Any] = None) -> httpx.AsyncClient:
    """每次调用新建客户端；测试可注入 MockTransport。"""
    kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(10.0, connect=5.0)}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


async def query_orders(
    cfg: Any,
    out_trade_no: str = "",
    transport: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """按订单号查询爱发电订单列表；任何异常都转成 AfdianError。"""
    user_id = str(getattr(cfg, "user_id", "") or "").strip()
    token = str(getattr(cfg, "token", "") or "").strip()
    if not user_id or not token:
        raise AfdianError("爱发电 user_id 或 Token 未配置")
    params = json.dumps(
        {"out_trade_no": out_trade_no}, ensure_ascii=False, separators=(",", ":")
    )
    ts = int(time.time())
    body = {
        "user_id": user_id,
        "params": params,
        "ts": ts,
        "sign": afdian_sign(token, params, ts, user_id),
    }
    api_base = str(getattr(cfg, "api_base", "") or "https://afdian.com").rstrip("/")
    try:
        async with _client(transport) as client:
            resp = await client.post(api_base + "/api/open/query-order", json=body)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AfdianError("爱发电接口不可用：" + str(exc)) from exc
    if not isinstance(data, dict):
        raise AfdianError("爱发电接口返回格式异常")
    try:
        ec = int(data.get("ec"))
    except (TypeError, ValueError):
        ec = -1
    if ec != 200:
        raise AfdianError(str(data.get("em") or "爱发电接口返回 ec=" + str(ec)))
    payload = data.get("data") or {}
    items = None
    if isinstance(payload, dict):
        items = payload.get("list")
    elif isinstance(payload, list):
        items = payload
    return [item for item in (items or []) if isinstance(item, dict)]


def pick_paid_order(items: Any, out_trade_no: str) -> Optional[Dict[str, Any]]:
    """在查询结果里找同一订单号且 status == 2（已支付）的订单。"""
    for item in items or []:
        if str(item.get("out_trade_no") or "") != out_trade_no:
            continue
        if str(item.get("status") or "") == "2":
            return item
    return None


def compute_credits(cfg: Any, order: Dict[str, Any]) -> float:
    """按方案映射计算发放点数：基数（方案金额，0 = 实付金额）× 倍率。"""
    plan_id = str(order.get("plan_id") or "").strip()
    plan = None
    for item in getattr(cfg, "plans", None) or []:
        if str(getattr(item, "plan_id", "") or "").strip() == plan_id:
            plan = item
            break
    if plan is not None:
        base = float(getattr(plan, "amount", 0) or 0)
        ratio = float(getattr(plan, "ratio", 1) or 0)
    else:
        base = float(getattr(cfg, "default_amount", 0) or 0)
        ratio = float(getattr(cfg, "default_ratio", 1) or 0)
    if base <= 0:
        try:
            base = float(order.get("total_amount") or 0)
        except (TypeError, ValueError):
            base = 0.0
    return round(max(0.0, base) * max(0.0, ratio), 2)
