"""Bearer 令牌 + 登录限速 + 管理员重置密码（接口级）。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_auth import (
    CreateUserPayload,
    LoginPayload,
    PasswordPayload,
    login,
    logout,
    me,
    reset_user_password,
)
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig
from flyai.user import ADMIN_LEVEL, ADMIN_UID, UserStore


def _state(tmp_path):
    return SimpleNamespace(
        config=AppConfig(),
        user_store=UserStore(tmp_path),
        _scopes={},
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
    )


def _req(state, token=None):
    headers = {"authorization": "Bearer " + token} if token else {}
    return SimpleNamespace(
        headers=headers,
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def _admin_token(state):
    return login(LoginPayload(name="admin", password=""), _req(state))["token"]


# ---- 令牌签发与校验 ----

def test_login_issues_token_and_me_accepts_it(tmp_path):
    state = _state(tmp_path)
    res = login(LoginPayload(name="admin", password=""), _req(state))
    assert res["token"] and len(res["token"]) > 20
    assert me(_req(state, res["token"]))["id"] == ADMIN_UID


def test_request_without_token_rejected(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(HTTPException) as ei:
        me(_req(state))
    assert ei.value.status_code == 401


def test_legacy_x_user_id_header_no_longer_works(tmp_path):
    state = _state(tmp_path)
    req = SimpleNamespace(
        headers={"x-user-id": ADMIN_UID},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )
    with pytest.raises(HTTPException) as ei:
        me(req)
    assert ei.value.status_code == 401


def test_logout_revokes_token(tmp_path):
    state = _state(tmp_path)
    token = _admin_token(state)
    logout(_req(state, token))
    with pytest.raises(HTTPException) as ei:
        me(_req(state, token))
    assert ei.value.status_code == 401


def test_random_token_rejected(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(HTTPException) as ei:
        me(_req(state, "ZGVhZGJlZWY-fake-token"))
    assert ei.value.status_code == 401


# ---- 登录限速 ----

def test_rate_limiter_locks_after_five_failures(tmp_path):
    state = _state(tmp_path)
    for _ in range(5):
        with pytest.raises(HTTPException) as ei:
            login(LoginPayload(name="admin", password="bad"), _req(state))
        assert ei.value.status_code == 401
    # 第 6 次：锁定，429
    with pytest.raises(HTTPException) as ei:
        login(LoginPayload(name="admin", password="bad"), _req(state))
    assert ei.value.status_code == 429
    # 正确密码也被挡
    with pytest.raises(HTTPException) as ei:
        login(LoginPayload(name="admin", password=""), _req(state))
    assert ei.value.status_code == 429


def test_rate_limiter_separate_keys_do_not_share_lock(tmp_path):
    state = _state(tmp_path)
    for _ in range(5):
        with pytest.raises(HTTPException):
            login(LoginPayload(name="admin", password="bad"), _req(state))
    # 另一用户名（IP 相同但 key 不同）不受影响：不存在 → 401 而不是 429
    with pytest.raises(HTTPException) as ei:
        login(LoginPayload(name="other"), _req(state))
    assert ei.value.status_code == 401


def test_nonexistent_user_counts_as_failure(tmp_path):
    state = _state(tmp_path)
    for _ in range(5):
        with pytest.raises(HTTPException) as ei:
            login(LoginPayload(name="ghost", password="x"), _req(state))
    with pytest.raises(HTTPException) as ei:
        login(LoginPayload(name="ghost", password="x"), _req(state))
    assert ei.value.status_code == 429


def test_successful_login_resets_failures(tmp_path):
    state = _state(tmp_path)
    for _ in range(3):
        with pytest.raises(HTTPException):
            login(LoginPayload(name="admin", password="bad"), _req(state))
    res = login(LoginPayload(name="admin", password=""), _req(state))
    assert res["token"]


# ---- LoginRateLimiter 纯逻辑 ----

def test_limiter_record_failure_returns_lock_seconds():
    limiter = LoginRateLimiter()
    for i in range(4):
        locked, wait = limiter.record_failure("ip|user")
        assert locked is False
    locked, wait = limiter.record_failure("ip|user")
    assert locked is True
    assert 290 <= wait <= 300
    assert limiter.locked_seconds("ip|user") > 0
    assert limiter.locked_seconds("ip|other") == 0


# ---- 管理员重置密码 ----

def test_admin_resets_password_and_revokes_sessions(tmp_path):
    state = _state(tmp_path)
    admin = _admin_token(state)
    from flyai.api.routes_auth import create_user as cu

    out = cu(CreateUserPayload(name="小高", password="旧密码"), _req(state, admin))
    uid = out["id"]
    user_token = login(LoginPayload(name="小高", password="旧密码"), _req(state))["token"]
    # 旧密码可用
    assert me(_req(state, user_token))["id"] == uid

    r = reset_user_password(
        uid, PasswordPayload(password="新密码"), _req(state, admin)
    )
    assert r["ok"] is True
    assert r["revoked_sessions"] == 1
    # 旧令牌已失效
    with pytest.raises(HTTPException) as ei:
        me(_req(state, user_token))
    assert ei.value.status_code == 401
    # 旧密码登录失败，新密码成功
    with pytest.raises(HTTPException):
        login(LoginPayload(name="小高", password="旧密码"), _req(state))
    res = login(LoginPayload(name="小高", password="新密码"), _req(state))
    assert res["token"]


def test_password_reset_requires_admin(tmp_path):
    state = _state(tmp_path)
    from flyai.api.routes_auth import create_user as cu

    admin = _admin_token(state)
    out = cu(CreateUserPayload(name="小高"), _req(state, admin))
    other = cu(CreateUserPayload(name="小乙"), _req(state, admin))
    # 用「小乙」的令牌去改「小高」→ 403
    user_tok = login(LoginPayload(name="小乙"), _req(state))["token"]
    with pytest.raises(HTTPException) as ei:
        reset_user_password(
            out["id"], PasswordPayload(password="x"), _req(state, user_tok)
        )
    assert ei.value.status_code == 403


# ---- AuthSessionStore 过期/吊销 ----

def test_expired_token_rejected():
    store = AuthSessionStore(ttl_seconds=-1)  # 签发即过期
    token = store.issue("u", "n", "user", 0)
    assert store.resolve(token) is None


def test_revoke_for_uid():
    store = AuthSessionStore()
    t1 = store.issue("u", "n", "user", 0)
    t2 = store.issue("u", "n", "user", 0)
    store.issue("other", "o", "user", 0)
    assert store.revoke_for_uid("u") == 2
    assert store.resolve(t1) is None and store.resolve(t2) is None


# ---- 令牌磁盘持久化 ----

def test_token_survives_new_store_instance(tmp_path):
    path = tmp_path / "auth_sessions.json"
    s1 = AuthSessionStore(path=path)
    tok = s1.issue("admin", "admin", "admin", 256)
    # 模拟服务重启：新实例绑定同一文件
    s2 = AuthSessionStore(path=path)
    rec = s2.resolve(tok)
    assert rec is not None and rec["uid"] == "admin"


def test_revoke_persisted(tmp_path):
    path = tmp_path / "auth_sessions.json"
    s1 = AuthSessionStore(path=path)
    tok = s1.issue("u", "n", "user", 0)
    assert s1.revoke(tok)
    assert AuthSessionStore(path=path).resolve(tok) is None


def test_expired_token_not_loaded(tmp_path):
    path = tmp_path / "auth_sessions.json"
    s1 = AuthSessionStore(ttl_seconds=-1, path=path)
    tok = s1.issue("u", "n", "user", 0)
    assert AuthSessionStore(path=path).resolve(tok) is None


def test_corrupt_file_tolerated(tmp_path):
    path = tmp_path / "auth_sessions.json"
    path.write_text("{not json", encoding="utf-8")
    store = AuthSessionStore(path=path)
    tok = store.issue("u", "n", "user", 0)
    assert store.resolve(tok) is not None
