"""用户权限等级：创建带等级、PATCH 修改、登录 / /me 返回等级。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_auth import (
    CreateUserPayload,
    LoginPayload,
    UpdateUserPayload,
    create_user,
    login,
    me,
    update_user,
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


def _req(state, uid=ADMIN_UID):
    token = state.auth_sessions.issue(uid, uid, "admin" if uid == ADMIN_UID else "user",
                                     ADMIN_LEVEL if uid == ADMIN_UID else 0)
    return SimpleNamespace(
        headers={"authorization": "Bearer " + token},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def test_create_user_with_level(tmp_path):
    state = _state(tmp_path)
    out = create_user(CreateUserPayload(name="小高", password="pw", level=42),
                      _req(state))
    assert out["level"] == 42
    uid = out["id"]
    assert state.user_store.get_by_id(uid).level == 42
    # 越界值被夹到 255
    out2 = create_user(CreateUserPayload(name="顶格", level=999), _req(state))
    assert out2["level"] == 255


def test_login_and_me_return_level(tmp_path):
    state = _state(tmp_path)
    out = create_user(CreateUserPayload(name="小高", password="pw", level=7),
                      _req(state))
    res = login(LoginPayload(name="小高", password="pw"), _req(state))
    assert res["level"] == 7 and res["role"] == "user"
    assert me(_req(state, out["id"]))["level"] == 7
    # 管理员登录返回 256
    admin = login(LoginPayload(name="admin", password=""), _req(state))
    assert admin["level"] == ADMIN_LEVEL and admin["id"] == ADMIN_UID


def test_patch_level_updates_store_and_scope(tmp_path):
    state = _state(tmp_path)
    out = create_user(CreateUserPayload(name="小高", level=1), _req(state))
    uid = out["id"]
    # 模拟已缓存的用户作用域
    state._scopes[uid] = SimpleNamespace(level=1)

    res = update_user(uid, UpdateUserPayload(level=200), _req(state))
    assert res["level"] == 200
    assert state.user_store.get_by_id(uid).level == 200
    assert state._scopes[uid].level == 200
    # 落盘可重读
    assert UserStore(tmp_path).get_by_id(uid).level == 200


def test_patch_level_out_of_range(tmp_path):
    state = _state(tmp_path)
    out = create_user(CreateUserPayload(name="小高"), _req(state))
    with pytest.raises(HTTPException) as ei:
        update_user(out["id"], UpdateUserPayload(level=256), _req(state))
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        update_user(out["id"], UpdateUserPayload(level=-1), _req(state))
    assert ei.value.status_code == 400


def test_patch_level_permissions(tmp_path):
    state = _state(tmp_path)
    out = create_user(CreateUserPayload(name="小高"), _req(state))
    # 普通用户不能改等级
    with pytest.raises(HTTPException) as ei:
        update_user(out["id"], UpdateUserPayload(level=5), _req(state, out["id"]))
    assert ei.value.status_code == 403
    # 不存在的用户 404
    with pytest.raises(HTTPException) as ei:
        update_user("ghost", UpdateUserPayload(level=5), _req(state))
    assert ei.value.status_code == 404


def test_create_user_forbidden_for_user(tmp_path):
    state = _state(tmp_path)
    out = create_user(CreateUserPayload(name="小高"), _req(state))
    with pytest.raises(HTTPException) as ei:
        create_user(CreateUserPayload(name="另一个"), _req(state, out["id"]))
    assert ei.value.status_code == 403
