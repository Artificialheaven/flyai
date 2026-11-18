"""爱发电自动发卡：webhook 通知 → query-order 权威校验 → 生成绑定 CDK。

交付三条路同时提供：
- 站内：订单备注填 FlyAI 用户名 → CDK 直接绑定该账号，设置 → 数据里一键兑换；
- 邮件：账号有注册邮箱且 SMTP 可用时，把卡密发到邮箱；
- 公开领取：已登录用户输入爱发电订单号自行领取（备注没填/填错时的兜底）。

webhook 没有签名，只有 URL 里的 token 做身份校验；订单是否真实已支付一律
以 query-order（status == 2）为准，且累计销售额超过上限的订单只记录待补发。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..afdian import AfdianError, compute_credits, pick_paid_order, query_orders
from ..cdk import CdkError
from ..user import ADMIN_UID
from .routes_auth import _require_admin

router = APIRouter(tags=["afdian"])

_BODY_LIMIT = 64 * 1024

# 领取接口失败冷却：订单号可被枚举，连续失败 5 次/分钟即 429
_CLAIM_LOCK = threading.Lock()
_CLAIM_FAILS: Dict[str, List[float]] = {}
_CLAIM_WINDOW = 60.0
_CLAIM_MAX_FAILS = 5


class ClaimPayload(BaseModel):
    out_trade_no: str = ""


def _no_store(payload: Dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code,
                        headers={"Cache-Control": "no-store"})


def _amount(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def _claim_allowed(uid: str) -> bool:
    now = time.time()
    with _CLAIM_LOCK:
        times = [t for t in _CLAIM_FAILS.get(uid, []) if now - t < _CLAIM_WINDOW]
        _CLAIM_FAILS[uid] = times
        return len(times) < _CLAIM_MAX_FAILS


def _claim_fail(uid: str) -> None:
    with _CLAIM_LOCK:
        _CLAIM_FAILS.setdefault(uid, []).append(time.time())


def _store(state) -> Any:
    store = getattr(state, "afdian_orders", None)
    if store is None:
        raise HTTPException(503, "爱发电功能未初始化")
    return store


def _extract_order_no(data: Any) -> str:
    """webhook 体：{"data": {"order": {...}}}，兼容 order 直接挂在 data/顶层。"""
    if not isinstance(data, dict):
        return ""
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = data
    order = payload.get("order")
    if not isinstance(order, dict):
        order = payload
    return str(order.get("out_trade_no") or "").strip()


def _match_uid(state, remark: Any) -> str:
    """订单备注里填的 FlyAI 用户名 → uid；精确匹配，找不到返回空。"""
    name = str(remark or "").strip()
    if not name:
        return ""
    user = state.user_store.get_by_name(name)
    return user.id if user is not None else ""


async def _maybe_send_mail(state, key: str, uid: str, code: str, credits: float) -> None:
    """把卡密发到绑定账号的注册邮箱；失败只记录，不影响发卡结果。"""
    if not uid:
        return
    user = state.user_store.get_by_id(uid)
    email = (getattr(user, "email", "") or "").strip() if user is not None else ""
    email_cfg = getattr(state.config, "email", None)
    mailer = getattr(state, "mailer", None)
    if (not email or email_cfg is None or mailer is None
            or not getattr(email_cfg, "enabled", False)):
        return
    subject = "FlyAI 兑换码"
    text = (
        "感谢你的支持！\n\n"
        "兑换码：%s\n"
        "额度：%s 点\n"
        "订单号：%s\n\n"
        "兑换方式：打开 FlyAI → 设置 → 数据 → 兑换码，粘贴后兑换。\n"
        "该兑换码已绑定你的账号，无法转给他人使用。\n"
        "—— FlyAI"
    ) % (code, credits, key)
    try:
        await asyncio.to_thread(mailer.send, email_cfg, email, subject, text)
        _store(state).mark(key, email_sent=True, email_error="")
    except Exception as exc:  # noqa: BLE001 - 邮件失败不影响发卡
        _store(state).mark(key, email_sent=False,
                           email_error=_text(getattr(exc, "message", exc)))


async def _process_order(state, key: str) -> Dict[str, Any]:
    """校验一笔已登记的订单并按需发卡；webhook / 后台校验 / 补发共用。

    网络查询在锁外完成，避免 async 场景下长时间持有线程锁；进入锁后重新
    读取记录，「上限判断 → 发卡 → 落库」整体原子，重复回调不会重复发卡。
    """
    cfg = state.config.afdian
    store = _store(state)
    transport = getattr(state, "afdian_transport", None)
    now = time.time()
    try:
        items = await query_orders(cfg, key, transport=transport)
    except AfdianError as exc:
        store.mark(key, "verify_failed", reason=exc.message, verified_at=now)
        return {"state": "verify_failed", "reason": exc.message}
    order = pick_paid_order(items, key)
    if order is None:
        store.mark(key, "verify_failed", reason="订单未支付或不存在", verified_at=now)
        return {"state": "verify_failed", "reason": "订单未支付或不存在"}

    credits = compute_credits(cfg, order)
    total = _amount(order.get("total_amount"))
    remark = _text(order.get("remark"), 500)
    bound = _match_uid(state, remark)
    info = {
        "afdian_user_id": _text(order.get("user_id")),
        "plan_id": _text(order.get("plan_id")),
        "plan_title": _text(order.get("plan_title")) or _text(order.get("plan_name")),
        "total_amount": total,
        "remark": remark,
        "order_status": _text(order.get("status"), 20),
        "credits": credits,
        "bound_uid": bound,
        "verified_at": now,
    }

    with store.lock:
        rec = store.get(key)
        if rec is None:
            return {"state": "invalid", "reason": "订单未登记"}
        if rec.get("state") == "issued":
            return {"state": "issued", "code": rec.get("cdk_code") or ""}
        if credits <= 0:
            store.mark(key, "invalid", reason="计算发放点数为 0", **info)
            return {"state": "invalid", "reason": "计算发放点数为 0"}
        cap = float(getattr(cfg, "max_total_amount", -1) or 0)
        if cap >= 0 and store.cumulative_paid(exclude_key=key) + total > cap:
            store.mark(key, "pending_cap", reason="超过累计销售额上限", **info)
            return {"state": "pending_cap", "reason": "超过累计销售额上限"}
        try:
            views = state.cdk_store.generate(1, credits, max_uses=1, bound_uid=bound)
        except Exception as exc:  # noqa: BLE001 - 落盘失败留给重试
            store.mark(key, "verify_failed", reason=_text(exc), **info)
            return {"state": "verify_failed", "reason": _text(exc)}
        code = str(views[0].get("code") or "")
        store.mark(key, "issued", cdk_code=code, issued_at=time.time(),
                   reason="", **info)

    await _maybe_send_mail(state, key, bound, code, credits)
    return {"state": "issued", "code": code}


def _public_order(rec: Dict[str, Any], state) -> Dict[str, Any]:
    code = str(rec.get("cdk_code") or "")
    uid = str(rec.get("bound_uid") or "")
    return {
        "out_trade_no": rec.get("out_trade_no") or "",
        "plan_title": rec.get("plan_title") or "",
        "amount": _amount(rec.get("total_amount")),
        "credits": _amount(rec.get("credits")),
        "code": code,
        "redeemed": bool(uid and state.cdk_store.is_redeemed(code, uid)),
        "issued_at": rec.get("issued_at") or 0,
    }


# ---- 公开接口 ----

@router.post("/api/app/afdian/webhook")
async def afdian_webhook(request: Request):
    """爱发电订单通知：成功时必须回 {"ec":200,"em":""}，否则对方会重试。"""
    state = request.app.state.flyai
    cfg = getattr(state.config, "afdian", None)
    expected = str(getattr(cfg, "webhook_token", "") or "")
    if cfg is None or not getattr(cfg, "enabled", False) or not expected:
        raise HTTPException(404, "Not Found")
    token = str(request.query_params.get("token") or "")
    if not token or not hmac.compare_digest(token.encode("utf-8"),
                                            expected.encode("utf-8")):
        raise HTTPException(401, "invalid token")

    chunks: List[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _BODY_LIMIT:
            raise HTTPException(413, "payload too large")
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks).decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "invalid json")

    key = _extract_order_no(data)
    if not key:
        raise HTTPException(400, "missing out_trade_no")

    store = _store(state)
    store.begin(key, data)
    result = await _process_order(state, key)
    if result["state"] == "issued":
        return JSONResponse({"ec": 200, "em": ""})
    if result["state"] == "pending_cap":
        # 已入库待补发，不需要爱发电重试
        return JSONResponse({"ec": 200, "em": ""})
    return JSONResponse({"ec": 500, "em": str(result.get("reason") or "verify failed")})


@router.get("/api/app/afdian")
def afdian_public_info(request: Request):
    """登录页/设置页展示「去爱发电购买」入口；未启用时返回 enabled=false。"""
    state = request.app.state.flyai
    cfg = getattr(state.config, "afdian", None)
    payload = {
        "enabled": bool(getattr(cfg, "enabled", False)),
        "purchase_url": str(getattr(cfg, "purchase_url", "") or ""),
        "purchase_label": str(getattr(cfg, "purchase_label", "") or ""),
    }
    return _no_store(payload)


# ---- 用户接口 ----

@router.get("/api/my/afdian/orders")
def my_afdian_orders(request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    store = getattr(state, "afdian_orders", None)
    if store is None:
        return {"orders": []}
    orders = [
        _public_order(rec, state)
        for rec in store.list()
        if rec.get("state") == "issued" and rec.get("bound_uid") == sc.uid
    ]
    return {"orders": orders}


@router.post("/api/my/afdian/claim")
def claim_afdian_order(payload: ClaimPayload, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    if sc.uid == ADMIN_UID:
        raise HTTPException(400, "管理员无需领取")
    key = str(payload.out_trade_no or "").strip()
    if not key:
        raise HTTPException(400, "请输入爱发电订单号")
    if not _claim_allowed(sc.uid):
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    store = _store(state)
    rec = store.get(key)
    if rec is None:
        _claim_fail(sc.uid)
        raise HTTPException(404, "订单不存在或尚未同步")
    state_name = rec.get("state")
    if state_name == "pending_cap":
        raise HTTPException(409, "订单超过销售额上限，请联系管理员补发")
    if state_name != "issued":
        _claim_fail(sc.uid)
        raise HTTPException(409, "订单尚未完成发放，请稍后再试")
    owner = str(rec.get("bound_uid") or "")
    if owner and owner != sc.uid:
        raise HTTPException(409, "该订单已被其他账号领取")
    code = str(rec.get("cdk_code") or "")
    try:
        state.cdk_store.bind(code, sc.uid)
    except CdkError as exc:
        _claim_fail(sc.uid)
        raise HTTPException(409, exc.message)
    if not owner:
        store.mark(key, bound_uid=sc.uid)
    return {
        "out_trade_no": key,
        "code": code,
        "amount": _amount(rec.get("total_amount")),
        "credits": _amount(rec.get("credits")),
    }


# ---- 管理接口 ----

@router.get("/api/admin/afdian")
def admin_afdian(request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    store = _store(state)
    cfg = state.config.afdian
    cap = float(getattr(cfg, "max_total_amount", -1) or 0)
    orders = [{k: v for k, v in rec.items() if k != "raw"} for rec in store.list()]
    return {"summary": store.summary(cap), "orders": orders}


@router.post("/api/admin/afdian/orders/{out_trade_no}/verify")
async def admin_verify_order(out_trade_no: str, request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    store = _store(state)
    key = (out_trade_no or "").strip()
    if store.get(key) is None:
        raise HTTPException(404, "订单不存在")
    result = await _process_order(state, key)
    return {"result": result, "order": store.get(key)}


@router.post("/api/admin/afdian/orders/{out_trade_no}/reissue")
async def admin_reissue_order(out_trade_no: str, request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    store = _store(state)
    key = (out_trade_no or "").strip()
    rec = store.get(key)
    if rec is None:
        raise HTTPException(404, "订单不存在")
    if rec.get("state") == "issued":
        return {"result": {"state": "issued", "code": rec.get("cdk_code") or ""},
                "order": rec}
    result = await _process_order(state, key)
    return {"result": result, "order": store.get(key)}
