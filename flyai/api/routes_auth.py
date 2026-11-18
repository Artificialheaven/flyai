"""认证接口：随机 Bearer 令牌 + 登录限速。

- 管理员：凭据在 `config/config.json` 的 `auth.admin`，不在用户库内，入口为 ADMIN_UID。
- 普通用户：存在 `data/users.json`，由管理员创建（登录仅验密，不自动注册）。
- 登录成功签发随机 token，之后所有请求带 Authorization: Bearer <token>。
- 用户管理相关接口仅管理员可用。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth_sessions import extract_token
from ..email_codes import EmailCodeError
from ..errors import ProviderError  # noqa: F401  (保持错误统一导出)
from ..mailer import MailError, normalize_email
from ..user import ADMIN_LEVEL, ADMIN_UID

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 注册流程串行化：查重名 → 建用户 → 扣邀请码 → 签发令牌。
# 仅保证单进程内不交叠；多进程部署时 users.json/invites.json 的文件级写入
# 不是事务性的，进程崩溃可能留下部分状态（用户已建、邀请码未计）。
_REGISTER_LOCK = threading.Lock()


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
    if u is None:
        return None
    from ..billing import quota_view

    return {"id": u.id, "name": u.name, "role": "user", "level": int(u.level or 0),
            "quota": quota_view(state.user_store, u.id)}


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


class RegisterPayload(BaseModel):
    name: str
    password: str = ""
    invite_code: str = ""
    # 邮件验证开启时必填；关闭时忽略
    email: str = ""
    email_code: str = ""


class EmailCodePayload(BaseModel):
    email: str
    # register=注册验证；reset=找回密码
    purpose: str = "register"


class ResetPasswordPayload(BaseModel):
    email: str
    code: str = ""
    new_password: str = ""


class CreateUserPayload(BaseModel):
    name: str
    password: str = ""
    level: int = 0
    # 对话次数总额；-1=无限，默认 0（需管理员充值后才能对话）
    quota: float = 0


class UpdateUserPayload(BaseModel):
    level: Optional[int] = None
    quota: Optional[float] = None


class PasswordPayload(BaseModel):
    password: str = ""


def _client_ip(request: Request) -> str:
    # 直连部署取对端地址；置于反代后可按需扩展 X-Forwarded-For
    client = request.client
    return client.host if client is not None else "unknown"


def _email_config(state):
    return getattr(state.config, "email", None)


def _email_enabled(cfg) -> bool:
    return bool(cfg is not None and getattr(cfg, "enabled", False))


def _email_ready(cfg) -> bool:
    """开关开启且服务器/账号/授权码齐全，才认为可以发信。"""
    if not _email_enabled(cfg):
        return False
    return bool(
        str(getattr(cfg, "host", "") or "").strip()
        and str(getattr(cfg, "username", "") or "").strip()
        and str(getattr(cfg, "password", "") or "").strip()
    )


def _invite_required(state) -> bool:
    """注册是否需要邀请码；旧配置没有该字段时保持开启。"""
    reg = getattr(state.config, "registration", None)
    return bool(getattr(reg, "require_invite", True))


@router.get("/register-info")
def register_info(request: Request):
    """注册页能力探测：是否需要邀请码/邮箱验证、是否支持找回密码。"""
    state = request.app.state.flyai
    cfg = _email_config(state)
    return {
        "invite_required": _invite_required(state),
        "email_required": _email_enabled(cfg),
        "password_reset_enabled": _email_ready(cfg),
    }


@router.post("/email-code")
async def send_email_code(payload: EmailCodePayload, request: Request):
    """发送注册/找回密码验证码（公开接口，带冷却与小时限额）。"""
    state = request.app.state.flyai
    cfg = _email_config(state)
    if not _email_enabled(cfg):
        raise HTTPException(400, "邮件验证未开启")
    purpose = (payload.purpose or "").strip().lower()
    if purpose not in ("register", "reset"):
        raise HTTPException(400, "验证码用途不正确")
    try:
        email = normalize_email(payload.email)
    except MailError as exc:
        raise HTTPException(400, str(exc))
    if not _email_ready(cfg):
        # 功能已开启但管理员没配好：明确报错，绝不静默跳过验证
        raise HTTPException(503, "邮件服务未配置，请联系管理员")

    codes = getattr(state, "email_codes", None)
    mailer = getattr(state, "mailer", None)
    if codes is None or mailer is None:
        raise HTTPException(503, "邮件服务未就绪")

    if purpose == "register" and state.user_store.get_by_email(email):
        raise HTTPException(409, "该邮箱已注册")
    if purpose == "reset":
        if state.user_store.get_by_email(email) is None:
            # 防枚举：未知邮箱与已知邮箱返回一致，但不真的发信
            return {"ok": True}
    try:
        code = codes.issue(
            purpose,
            email,
            ip=_client_ip(request),
            ttl=int(getattr(cfg, "code_ttl_seconds", 600) or 600),
            resend_interval=int(
                getattr(cfg, "resend_interval_seconds", 60) or 60
            ),
            hourly_limit=int(getattr(cfg, "hourly_limit", 10) or 10),
        )
    except EmailCodeError as exc:
        raise HTTPException(exc.status, exc.message)
    try:
        await asyncio.to_thread(mailer.send_code, cfg, email, code, purpose)
    except MailError as exc:
        # 发信失败：删掉刚签发的码，允许用户立即重试（不受冷却影响）
        codes.discard(purpose, email)
        logger.warning("发送邮箱验证码失败: %s", type(exc).__name__)
        raise HTTPException(503, "验证码发送失败，请稍后重试")
    return {
        "ok": True,
        "retry_after": int(
            getattr(cfg, "resend_interval_seconds", 60) or 60
        ),
    }


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
    # 管理员不在用户库，登录时间只对普通用户有意义；写盘失败不影响登录
    if uid != ADMIN_UID:
        try:
            state.user_store.touch_login(uid)
        except Exception:
            logger.exception("记录登录时间失败")
    token = state.auth_sessions.issue(uid, name, role, level)
    return {"token": token, "id": uid, "name": name, "role": role, "level": level}


def _undo_register(state, uid: str, code: Optional[str]) -> None:
    """回滚注册的残留状态：先撤回邀请码使用记录，再删用户。"""
    if code:
        store = getattr(state, "invite_store", None)
        if store is not None:
            try:
                store.unmark_used(code, uid)
            except Exception:
                logger.exception("回滚邀请码使用记录失败")
    if uid:
        try:
            state.user_store.remove(uid)
        except Exception:
            logger.exception("回滚注册用户失败")


@router.post("/register")
def register(payload: RegisterPayload, request: Request):
    """注册（邀请码可选；邮件开启时另需邮箱验证码）→ 创建用户 → 自动登录。"""
    state = request.app.state.flyai
    name = (payload.name or "").strip()
    password = payload.password or ""
    invite_code = (payload.invite_code or "").strip()
    invite_required = _invite_required(state)
    if not name:
        raise HTTPException(400, "请输入用户名")
    # 空密码会被 check_password 视为任意密码均可登录，注册时必须拒绝
    if not password:
        raise HTTPException(400, "请输入密码")
    admin = getattr(getattr(state.config, "auth", None), "admin", None)
    admin_name = (getattr(admin, "username", "") or "").strip()
    if admin_name and name == admin_name:
        raise HTTPException(400, "该用户名不可用")
    if invite_required and not invite_code:
        raise HTTPException(400, "请输入邀请码")

    cfg = _email_config(state)
    email_required = _email_enabled(cfg)
    email = ""
    if email_required:
        if not _email_ready(cfg):
            raise HTTPException(503, "邮件服务未配置，请联系管理员")
        try:
            email = normalize_email(payload.email)
        except MailError:
            raise HTTPException(400, "请输入正确的邮箱")
        if not (payload.email_code or "").strip():
            raise HTTPException(400, "请输入邮箱验证码")

    limit_key = "register:" + _client_ip(request) + "|" + name
    wait = state.login_limiter.locked_seconds(limit_key)
    if wait:
        raise HTTPException(429, f"注册失败次数过多，请 {wait} 秒后再试")

    codes = getattr(state, "email_codes", None)
    if email_required and (
        codes is None or not codes.verify("register", email, payload.email_code)
    ):
        # 与登录共用「IP+用户名」失败计数，防止穷举验证码
        state.login_limiter.record_failure(limit_key)
        raise HTTPException(400, "邮箱验证码错误或已过期")

    store = getattr(state, "invite_store", None)
    if invite_required and store is None:
        raise HTTPException(503, "邀请码服务未就绪")

    uid = ""
    marked: Optional[str] = None
    with _REGISTER_LOCK:
        try:
            if state.user_store.get_by_name(name):
                raise HTTPException(400, f"用户名已存在: {name}")
            if email and state.user_store.get_by_email(email):
                # 发码后可能被并发注册占用
                raise HTTPException(400, "该邮箱已被使用")
            if invite_required:
                invite = store.check(invite_code)
                if invite is None:
                    raise HTTPException(400, "邀请码无效")
                if invite["status"] != "usable":
                    raise HTTPException(400, "邀请码已用尽")
            u = state.user_store.create(name, password, level=0, email=email)
            uid = u.id
            reg = getattr(state.config, "registration", None)
            quota = float(getattr(reg, "default_quota", 0) or 0)
            if quota:
                state.user_store.set_quota_total(uid, quota)
            if invite_required:
                store.mark_used(invite["code"], uid, name)
                marked = invite["code"]
            state.login_limiter.record_success(limit_key)
            result = _issue_login(state, uid, name, "user", 0)
            if email and codes is not None:
                codes.discard("register", email)
            return result
        except HTTPException:
            _undo_register(state, uid, marked)
            state.login_limiter.record_failure(limit_key)
            raise
        except ValueError as exc:
            _undo_register(state, uid, marked)
            state.login_limiter.record_failure(limit_key)
            raise HTTPException(400, str(exc))
        except Exception:
            _undo_register(state, uid, marked)
            state.login_limiter.record_failure(limit_key)
            logger.exception("注册失败")
            raise HTTPException(500, "注册失败，请重试")


@router.post("/reset-password")
def reset_password(payload: ResetPasswordPayload, request: Request):
    """邮箱验证码重置密码；成功后吊销该用户全部令牌。"""
    state = request.app.state.flyai
    cfg = _email_config(state)
    if not _email_enabled(cfg):
        raise HTTPException(400, "邮件验证未开启")
    if not _email_ready(cfg):
        raise HTTPException(503, "邮件服务未配置，请联系管理员")
    try:
        email = normalize_email(payload.email)
    except MailError:
        raise HTTPException(400, "邮箱格式不正确")
    new_password = payload.new_password or ""
    if not new_password:
        raise HTTPException(400, "请输入新密码")

    limit_key = "reset-code:" + _client_ip(request) + "|" + email
    wait = state.login_limiter.locked_seconds(limit_key)
    if wait:
        raise HTTPException(429, f"验证码错误次数过多，请 {wait} 秒后再试")

    codes = getattr(state, "email_codes", None)
    u = state.user_store.get_by_email(email)
    # 用户不存在与验证码错误统一文案，避免枚举邮箱
    if u is None or codes is None or not codes.verify(
        "reset", email, payload.code or ""
    ):
        state.login_limiter.record_failure(limit_key)
        raise HTTPException(400, "邮箱验证码错误或已过期")

    u.set_password(new_password)
    state.user_store._save()
    try:
        state.auth_sessions.revoke_for_uid(u.id)
    except Exception:
        logger.exception("重置密码后吊销令牌失败")
    codes.discard("reset", email)
    state.login_limiter.record_success(limit_key)
    return {"ok": True, "name": u.name}


@router.get("/users")
def list_users(request: Request):
    """列出普通用户（仅管理员）。"""
    _require_admin(request)
    state = request.app.state.flyai
    from ..billing import quota_view

    return {"users": [
        {"id": u.id, "name": u.name, "email": u.email,
         "level": int(u.level or 0),
         "created_at": u.created_at, "last_login_at": u.last_login_at,
         "quota": quota_view(state.user_store, u.id)}
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
    # 与管理员同名会永远无法登录（登录优先匹配管理员凭据）
    if (state.config.auth.admin.username or "").strip() == name:
        raise HTTPException(400, "该用户名不可用")
    if state.user_store.get_by_name(name):
        raise HTTPException(400, f"用户名已存在: {name}")
    level = max(0, min(int(payload.level or 0), 255))
    u = state.user_store.create(name, payload.password, level=level)
    quota = float(payload.quota or 0)
    if quota:
        state.user_store.set_quota_total(u.id, quota)
    from ..billing import quota_view

    return {"id": u.id, "name": u.name, "email": u.email, "level": level,
            "created_at": u.created_at, "last_login_at": u.last_login_at,
            "quota": quota_view(state.user_store, u.id)}


@router.patch("/users/{uid}")
def update_user(uid: str, payload: UpdateUserPayload, request: Request):
    """修改用户权限等级（0-255）和/或对话次数总额（-1=无限）。仅管理员。"""
    _require_admin(request)
    state = request.app.state.flyai
    u = state.user_store.get_by_id(uid)
    if u is None:
        raise HTTPException(404, "用户不存在")
    out = {"ok": True}
    if payload.level is not None:
        level = int(payload.level)
        if level < 0 or level > 255:
            raise HTTPException(400, "权限等级必须在 0-255 之间")
        u.level = level
        state.user_store._save()
        out["level"] = level
        # 已缓存的作用域同步刷新
        scope = state._scopes.get(uid)
        if scope is not None:
            scope.level = level
    if payload.quota is not None:
        try:
            total = state.user_store.set_quota_total(uid, float(payload.quota))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        out["quota"] = total
    from ..billing import quota_view

    out["quota_view"] = quota_view(state.user_store, uid)
    return out


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
    # 用户被删除后其邀请码一并清理；失败不影响删除结果
    store = getattr(state, "invite_store", None)
    if store is not None:
        try:
            store.remove_owner(uid)
        except Exception:
            logger.exception("删除用户时清理邀请码失败")
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
