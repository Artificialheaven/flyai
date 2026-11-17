"""邀请码接口。

- 用户：查看/重新生成自己的邀请码（旧码重新生成后立即作废）。
- 管理员：自定义自己的邀请码与人数、查看全部邀请码、删除任意邀请码。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..invites import InviteError
from ..user import ADMIN_UID
from .routes_auth import _require_admin

router = APIRouter(prefix="/api", tags=["invite"])

DEFAULT_MAX_USES = 5


class AdminInvitePayload(BaseModel):
    # 留空=随机生成；填了则用自定义码
    code: str = ""
    max_uses: int = DEFAULT_MAX_USES


def _store(request: Request):
    store = getattr(request.app.state.flyai, "invite_store", None)
    if store is None:
        raise HTTPException(503, "邀请码服务未就绪")
    return store


def _owner_name(state, owner: str) -> str:
    if owner == ADMIN_UID:
        return state.config.auth.admin.username or ADMIN_UID
    u = state.user_store.get_by_id(owner)
    return u.name if u is not None else ""


@router.get("/my/invite")
def get_my_invite(request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    store = _store(request)
    return {"invite": store.get_for_owner(sc.uid)}


@router.post("/my/invite/regenerate")
def regenerate_my_invite(request: Request):
    """重新生成邀请码：默认 5 人，旧码立即作废。"""
    state = request.app.state.flyai
    sc = state.user(request)
    store = _store(request)
    try:
        invite = store.generate(sc.uid, DEFAULT_MAX_USES)
    except InviteError as exc:
        raise HTTPException(400, exc.message)
    return {"invite": invite}


@router.put("/admin/invites/mine")
def set_admin_invite(payload: AdminInvitePayload, request: Request):
    """管理员设置自己的邀请码：空码=随机生成，自定义码可同时改人数。"""
    _require_admin(request)
    store = _store(request)
    try:
        if (payload.code or "").strip():
            invite = store.set_custom(ADMIN_UID, payload.code, payload.max_uses)
        else:
            invite = store.generate(ADMIN_UID, payload.max_uses)
    except InviteError as exc:
        raise HTTPException(400, exc.message)
    return {"invite": invite}


@router.get("/admin/invites")
def list_invites(request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    store = _store(request)
    invites = []
    for view in store.list_all():
        owner = view.get("owner") or ""
        item = dict(view)
        item["owner_name"] = _owner_name(state, owner)
        item["owner_missing"] = owner != ADMIN_UID and not item["owner_name"]
        invites.append(item)
    return {"invites": invites}


@router.delete("/admin/invites/{code}")
def delete_invite(code: str, request: Request):
    _require_admin(request)
    store = _store(request)
    if not store.remove(code):
        raise HTTPException(404, "邀请码不存在")
    return {"ok": True}
