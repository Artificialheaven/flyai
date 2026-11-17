"""CDK 兑换与后台管理接口。

- 用户：POST /api/my/redeem，兑换后直接增加额度（点数）。
- 管理员：生成 / 粘贴导入 / 列表 / 取出 / 停用 / 删除 / 导出。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..billing import quota_view
from ..cdk import CdkError
from ..user import ADMIN_UID
from .routes_auth import _require_admin

router = APIRouter(prefix="/api", tags=["cdk"])


class RedeemPayload(BaseModel):
    code: str = ""


class GeneratePayload(BaseModel):
    count: int = 1
    amount: float = 0
    max_uses: int = 1
    expires_at: str = ""


class ImportPayload(BaseModel):
    codes: str = ""
    amount: float = 0
    max_uses: int = 1
    expires_at: str = ""


def _store(request: Request):
    store = getattr(request.app.state.flyai, "cdk_store", None)
    if store is None:
        raise HTTPException(503, "CDK 功能未初始化")
    return store


@router.post("/my/redeem")
def redeem_cdk(payload: RedeemPayload, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    if sc.uid == ADMIN_UID:
        raise HTTPException(400, "管理员无需兑换 CDK")
    store = _store(request)
    try:
        info = store.check_redeem(sc.uid, payload.code)
    except CdkError as exc:
        raise HTTPException(400, exc.message)
    store.mark_redeemed(info["key"], sc.uid)
    try:
        state.user_store.add_quota(sc.uid, info["amount"])
    except Exception:
        store.unmark_redeemed(info["key"], sc.uid)
        raise HTTPException(500, "兑换失败，请重试")
    return {
        "amount": info["amount"],
        "quota": quota_view(state.user_store, sc.uid),
    }


@router.get("/admin/cdk")
def list_cdks(request: Request, status: str = ""):
    _require_admin(request)
    store = _store(request)
    return {"cdks": store.list(status)}


@router.post("/admin/cdk/generate")
def generate_cdks(payload: GeneratePayload, request: Request):
    _require_admin(request)
    store = _store(request)
    try:
        cds = store.generate(payload.count, payload.amount,
                             payload.max_uses, payload.expires_at)
    except CdkError as exc:
        raise HTTPException(400, exc.message)
    return {"cdks": cds}


@router.post("/admin/cdk/import")
def import_cdks(payload: ImportPayload, request: Request):
    _require_admin(request)
    store = _store(request)
    try:
        result = store.import_codes(payload.codes, payload.amount,
                                    payload.max_uses, payload.expires_at)
    except CdkError as exc:
        raise HTTPException(400, exc.message)
    return result


@router.get("/admin/cdk/extract")
def extract_cdk(request: Request):
    _require_admin(request)
    store = _store(request)
    return {"cdk": store.extract()}


@router.post("/admin/cdk/{code}/disable")
def disable_cdk(code: str, request: Request):
    _require_admin(request)
    store = _store(request)
    try:
        view = store.disable(code)
    except CdkError as exc:
        raise HTTPException(404, exc.message)
    return {"cdk": view}


@router.delete("/admin/cdk/{code}")
def delete_cdk(code: str, request: Request):
    _require_admin(request)
    store = _store(request)
    if not store.remove(code):
        raise HTTPException(404, "CDK 不存在")
    return {"ok": True}


@router.get("/admin/cdk/export")
def export_cdks(request: Request):
    _require_admin(request)
    store = _store(request)
    return {"text": store.export()}