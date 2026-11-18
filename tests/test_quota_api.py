"""额度管理接口：创建带额度、PATCH 充值、列表 /me /my/quota 可见。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api.routes_auth import (
    CreateUserPayload,
    LoginPayload,
    UpdateUserPayload,
    create_user,
    list_users,
    login,
    me,
    update_user,
)
from flyai.api.routes_config import get_my_quota
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig
from flyai.user import ADMIN_UID, UserStore


def _state(tmp_path):
    return SimpleNamespace(
        config=AppConfig(),
        user_store=UserStore(tmp_path),
        _scopes={},
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
    )


def _req(state, uid=ADMIN_UID, level=0):
    role = "admin" if uid == ADMIN_UID else "user"
    lv = 256 if uid == ADMIN_UID else level
    token = state.auth_sessions.issue(uid, uid, role, lv)
    return SimpleNamespace(
        headers={"authorization": "Bearer " + token},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def _create(state, name="小高", quota=0):
    return create_user(
        CreateUserPayload(name=name, password="pw", level=0, quota=quota),
        _req(state),
    )


def test_create_user_with_quota(tmp_path):
    state = _state(tmp_path)
    out = _create(state, quota=12.5)
    q = out["quota"]
    assert q["total"] == pytest.approx(12.5)
    assert q["remaining"] == pytest.approx(12.5) and q["used"] == 0


def test_list_users_includes_quota(tmp_path):
    state = _state(tmp_path)
    out = _create(state, quota=3)
    state.user_store.consume(out["id"], 1)
    users = list_users(_req(state))["users"]
    row = next(u for u in users if u["id"] == out["id"])
    assert row["quota"]["total"] == pytest.approx(3)
    assert row["quota"]["remaining"] == pytest.approx(2)


def test_list_users_includes_timestamps(tmp_path):
    state = _state(tmp_path)
    out = _create(state)
    row = next(u for u in list_users(_req(state))["users"] if u["id"] == out["id"])
    assert row["created_at"]
    # 管理员代建、尚未登录过
    assert row["last_login_at"] == ""

    login(LoginPayload(name="小高", password="pw"), _req(state))
    row = next(u for u in list_users(_req(state))["users"] if u["id"] == out["id"])
    assert row["last_login_at"]
    # 落盘后重新加载仍保留
    reloaded = UserStore(tmp_path).get_by_id(out["id"])
    assert reloaded is not None and reloaded.last_login_at == row["last_login_at"]


def test_patch_quota_to_up_and_unlimited(tmp_path):
    state = _state(tmp_path)
    out = _create(state, quota=2)
    res = update_user(out["id"], UpdateUserPayload(quota=10), _req(state))
    assert res["quota_view"]["remaining"] == pytest.approx(10)

    res = update_user(out["id"], UpdateUserPayload(quota=-1), _req(state))
    assert res["quota_view"]["total"] == -1
    assert res["quota_view"]["remaining"] is None

    with pytest.raises(HTTPException) as ei:
        update_user(out["id"], UpdateUserPayload(quota=-5), _req(state))
    assert ei.value.status_code == 400


def test_patch_level_still_works_with_optional_quota(tmp_path):
    state = _state(tmp_path)
    out = _create(state)
    res = update_user(out["id"], UpdateUserPayload(level=99), _req(state))
    assert res["level"] == 99
    assert "quota" not in res  # 只传 level 时不改额度


def test_me_includes_quota_for_regular_user(tmp_path):
    state = _state(tmp_path)
    out = _create(state, quota=5)
    info = me(_req(state, out["id"]))
    assert info["quota"]["remaining"] == pytest.approx(5)


def test_my_quota_endpoint(tmp_path):
    state = _state(tmp_path)
    out = _create(state, quota=1)
    assert get_my_quota(_req(state, out["id"]))["remaining"] == pytest.approx(1)
    # 管理员：unlimited
    assert get_my_quota(_req(state))["unlimited"] is True


def test_patch_quota_forbidden_for_user(tmp_path):
    state = _state(tmp_path)
    out = _create(state)
    with pytest.raises(HTTPException) as ei:
        update_user(out["id"], UpdateUserPayload(quota=99),
                    _req(state, out["id"]))
    assert ei.value.status_code == 403
