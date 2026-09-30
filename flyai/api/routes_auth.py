"""认证接口：随机 Bearer 令牌 + 登录限速。

- 管理员：凭据在 `config/config.json` 的 `auth.admin`，不在用户库内，入口为 ADMIN_UID。
- 普通用户：存在 `data/users.json`，由管理员创建（登录仅验密，不自动注册）。
- 登录成功签发随机 token，之后所有请求带 Authorization: Bearer <token>。
- 用户管理相关接口仅管理员可用。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth_sessions import extract_token
from ..errors import ProviderError  # noqa: F401  (保持错误统一导出)
from ..user import ADMIN_LEVEL, ADMIN_UID

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _identity(request: Request):
    """按 Bearer 令牌返回当前用户；无令牌/已过期/用户已删除返回 None。"""
    state = request.app.state.flyai
    rec = state.auth_sessions.resolve(extract_token(request))
    if rec is None:
        return None
    uid = rec.get("uid")
    if uid == ADMIN_UID:
        return {"id": ADMIN_UID, "name": state.config.auth.admin.username,
                "role": "admin", "level": ADMIN_LEVEL}
    u = state.user_store.get_by_id(uid)
    return ({"id": u.id, "name": u.name, "role": "user", "level": int(u.level or 0)}
            if u else None)


def _require_admin(request: Request):
    """要求当前用户是管理员，否则 401（未登录）/403（普通用户）。"""
    cur = _identity(request)
    if cur is None:
        raise HTTPException(401, "未登录或登录已失效")
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


class PasswordPayload(BaseModel):
    password: str = ""


def _client_ip(request: Request) -> str:
    # 直连部署取对端地址；置于反代后可按需扩展 X-Forwarded-For
    client = request.client
    return client.host if client is not None else "unknown"


@router.post("/login")
def login(payload: LoginPayload, request: Request):
    """登录：限速（IP+用户名）→ 匹配管理员/普通用户 → 签发随机令牌。"""
    state = request.app.state.flyai
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "请输入用户名")

    limit_key = _client_ip(request) + "|" + name
    wait = state.login_limiter.locked_seconds(limit_key)
    if wait:
        raise HTTPException(429, f"登录失败次数过多，请 {wait} 秒后再试")

    admin = state.config.auth.admin
    if name == admin.username:
        if payload.password != admin.password:
            _record_login_fail(state, limit_key)
        else:
            state.login_limiter.record_success(limit_key)
            return _issue_login(state, ADMIN_UID, admin.username, "admin", ADMIN_LEVEL)

    u = state.user_store.get_by_name(name)
    if u is None:
        # 用户不存在同样记一次失败，避免用限速侧信道区分用户是否存在
        _record_login_fail(state, limit_key)
    elif not u.check_password(payload.password):
        _record_login_fail(state, limit_key)
    else:
        state.login_limiter.record_success(limit_key)
        return _issue_login(state, u.id, u.name, "user", int(u.level or 0))


def _record_login_fail(state, limit_key: str) -> None:
    """记一次失败，本次请求始终 401；锁定从下一次请求开始生效。"""
    state.login_limiter.record_failure(limit_key)
    raise HTTPException(401, "用户名或密码错误")


def _issue_login(state, uid: str, name: str, role: str, level: int) -> dict:
    token = state.auth_sessions.issue(uid, name, role, level)
    return {"token": token, "id": uid, "name": name, "role": role, "level": level}


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


@router.patch("/users/{uid}/password")
def reset_user_password(uid: str, payload: PasswordPayload, request: Request):
    """管理员重置普通用户密码；重置后该用户现有令牌全部吊销，需重新登录。"""
    _require_admin(request)
    state = request.app.state.flyai
    u = state.user_store.get_by_id(uid)
    if u is None:
        raise HTTPException(404, "用户不存在")
    u.set_password(payload.password or "")
    state.user_store._save()
    revoked = state.auth_sessions.revoke_for_uid(uid)
    return {"ok": True, "revoked_sessions": revoked}


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
    """校验当前令牌是否有效并返回信息。"""
    cur = _identity(request)
    if cur is None:
        raise HTTPException(401, "未登录或登录已失效")
    return cur


@router.post("/logout")
def logout(request: Request):
    """吊销当前令牌，使其立即失效。"""
    state = request.app.state.flyai
    state.auth_sessions.revoke(extract_token(request))
    return {"ok": True}
