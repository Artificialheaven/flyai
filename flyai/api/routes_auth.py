"""认证接口（本地工具级，无会话 cookie，前端带 X-User-Id 头）。

- 管理员：凭据在 `config/config.json` 的 `auth.admin`，不在用户库内，入口为 ADMIN_UID。
- 普通用户：存在 `data/users.json`，由管理员创建（登录仅验密，不自动注册）。
- 用户管理相关接口仅管理员可用。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..errors import ProviderError  # noqa: F401  (保持错误统一导出)
from ..user import ADMIN_LEVEL, ADMIN_UID

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _admin(request: Request):
    """返回当前用户；未登录返回 None。"""
    state = request.app.state.flyai
    uid = (request.headers.get("x-user-id") or "").strip()
    if uid == ADMIN_UID:
        return {"id": ADMIN_UID, "name": state.config.auth.admin.username,
                "role": "admin", "level": ADMIN_LEVEL}
    u = state.user_store.get_by_id(uid) if uid else None
    return ({"id": u.id, "name": u.name, "role": "user", "level": int(u.level or 0)}
            if u else None)


def _require_admin(request: Request):
    """要求当前用户是管理员（即 X-User-Id 为管理员标识），否则 403。"""
    cur = _admin(request)
    if cur is None:
        raise HTTPException(401, "未登录或用户不存在")
    if cur["id"] != ADMIN_UID:
        raise HTTPException(403, "需要管理员权限")
    return cur


class LoginPayload(BaseModel):
    name: str
    password: str = ""


class CreateUserPayload(BaseModel):
    name: str
    password: str = ""
    level: int = 0


class UpdateUserPayload(BaseModel):
    level: int


@router.post("/login")
def login(payload: LoginPayload, request: Request):
    """登录：先匹配管理员（config.auth.admin），再匹配普通用户（仅验密）。"""
    state = request.app.state.flyai
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "请输入用户名")

    admin = state.config.auth.admin
    if name == admin.username:
        # 管理员：精确比对 config 中的密码
        if payload.password != admin.password:
            raise HTTPException(401, "密码错误")
        return {"id": ADMIN_UID, "name": admin.username, "role": "admin",
                "level": ADMIN_LEVEL}

    u = state.user_store.get_by_name(name)
    if u is None:
        raise HTTPException(401, "用户不存在（需管理员创建）")
    if not u.check_password(payload.password):
        raise HTTPException(401, "密码错误")
    return {"id": u.id, "name": u.name, "role": "user", "level": int(u.level or 0)}


@router.get("/names")
def list_names(request: Request):
    """登录页选用户用的公开用户名清单（普通用户；管理员不含在内）。"""
    state = request.app.state.flyai
    return {"names": [u.name for u in state.user_store.list()]}


@router.get("/users")
def list_users(request: Request):
    """列出普通用户（仅管理员）。"""
    _require_admin(request)
    state = request.app.state.flyai
    return {"users": [
        {"id": u.id, "name": u.name, "level": int(u.level or 0)}
        for u in state.user_store.list()
    ]}


@router.post("/users")
def create_user(payload: CreateUserPayload, request: Request):
    """创建普通用户（仅管理员）。"""
    _require_admin(request)
    state = request.app.state.flyai
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "请输入用户名")
    if state.user_store.get_by_name(name):
        raise HTTPException(400, f"用户名已存在: {name}")
    level = max(0, min(int(payload.level or 0), 255))
    u = state.user_store.create(name, payload.password, level=level)
    return {"id": u.id, "name": u.name, "level": level}


@router.patch("/users/{uid}")
def update_user(uid: str, payload: UpdateUserPayload, request: Request):
    """修改用户权限等级（仅管理员，0-255）。"""
    _require_admin(request)
    state = request.app.state.flyai
    u = state.user_store.get_by_id(uid)
    if u is None:
        raise HTTPException(404, "用户不存在")
    level = int(payload.level)
    if level < 0 or level > 255:
        raise HTTPException(400, "权限等级必须在 0-255 之间")
    u.level = level
    state.user_store._save()
    # 已缓存的作用域同步刷新
    scope = state._scopes.get(uid)
    if scope is not None:
        scope.level = level
    return {"ok": True, "level": level}


@router.delete("/users/{uid}")
def delete_user(uid: str, request: Request):
    """删除普通用户（仅管理员）。"""
    _require_admin(request)
    state = request.app.state.flyai
    if not state.user_store.remove(uid):
        raise HTTPException(400, "删除失败（用户不存在）")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    """校验当前 X-User-Id 是否有效并返回信息。"""
    cur = _admin(request)
    if cur is None:
        raise HTTPException(401, "未登录或用户不存在")
    return cur


@router.post("/logout")
def logout():
    return {"ok": True}