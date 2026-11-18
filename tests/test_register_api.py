"""邀请码注册接口：成功自动登录、额度、校验、回滚与限速。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_auth import RegisterPayload, register
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig
from flyai.invites import InviteStore
from flyai.user import UserStore


def _state(tmp_path, default_quota=0.0, admin_name="root", require_invite=True):
    cfg = AppConfig()
    cfg.auth.admin.username = admin_name
    cfg.auth.admin.password = "secret"
    cfg.registration.default_quota = default_quota
    cfg.registration.require_invite = require_invite
    return SimpleNamespace(
        config=cfg,
        user_store=UserStore(tmp_path),
        invite_store=InviteStore(tmp_path),
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
    )


def _req(state, host="127.0.0.1"):
    return SimpleNamespace(
        headers={},
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def _payload(name="bob", password="pw", invite_code=""):
    return RegisterPayload(name=name, password=password, invite_code=invite_code)


def test_register_success_auto_login_and_marks_invite(tmp_path):
    state = _state(tmp_path)
    code = state.invite_store.generate("owner")["code"]
    resp = register(_payload(invite_code=code), _req(state))
    assert resp["role"] == "user" and resp["token"]
    assert resp["id"] != "owner"
    rec = state.auth_sessions.resolve(resp["token"])
    assert rec["uid"] == resp["id"]
    u = state.user_store.get_by_id(resp["id"])
    assert u is not None and u.name == "bob" and u.check_password("pw")
    # 注册即自动登录，注册与上次登录时间都已记录
    assert u.created_at and u.last_login_at
    # 默认额度 0
    assert u.quota_total == 0
    invite = state.invite_store.check(code)
    assert invite["used"] == 1
    assert invite["uses"][0]["uid"] == resp["id"]
    assert invite["uses"][0]["name"] == "bob"


@pytest.mark.parametrize("quota", [12, -1])
def test_register_applies_default_quota(tmp_path, quota):
    state = _state(tmp_path, default_quota=quota)
    code = state.invite_store.generate("owner")["code"]
    resp = register(_payload(invite_code=code), _req(state))
    assert state.user_store.get_by_id(resp["id"]).quota_total == quota


def test_register_rejects_bad_inputs(tmp_path):
    state = _state(tmp_path)
    code = state.invite_store.generate("owner")["code"]

    cases = [
        (_payload(name=""), "请输入用户名"),
        (_payload(password=""), "请输入密码"),
        (_payload(name="root"), "该用户名不可用"),
        (_payload(invite_code=""), "请输入邀请码"),
    ]
    for payload, message in cases:
        with pytest.raises(HTTPException) as ei:
            register(payload, _req(state))
        assert ei.value.status_code == 400
        assert ei.value.detail == message


def test_register_rejects_invalid_and_exhausted(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code="NOPE-NOPE-NOPE"), _req(state))
    assert ei.value.detail == "邀请码无效"

    code = state.invite_store.generate("owner", max_uses=1)["code"]
    state.invite_store.mark_used(code, "n1", "alice")
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code=code), _req(state))
    assert ei.value.detail == "邀请码已用尽"


def test_register_rejects_duplicate_name(tmp_path):
    state = _state(tmp_path)
    state.user_store.create("bob", "pw")
    code = state.invite_store.generate("owner")["code"]
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code=code), _req(state))
    assert ei.value.detail == "用户名已存在: bob"
    assert state.invite_store.check(code)["used"] == 0


def test_register_rolls_back_when_mark_used_fails(tmp_path, monkeypatch):
    state = _state(tmp_path)
    code = state.invite_store.generate("owner")["code"]

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(state.invite_store, "mark_used", boom)
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code=code), _req(state))
    assert ei.value.status_code == 500
    assert state.user_store.get_by_name("bob") is None
    # 内存里没有残留使用记录（mark_used 未成功）
    assert state.invite_store.check(code)["used"] == 0


def test_register_rolls_back_when_issue_login_fails(tmp_path, monkeypatch):
    state = _state(tmp_path)
    code = state.invite_store.generate("owner")["code"]

    def boom(*args, **kwargs):
        raise RuntimeError("session store down")

    monkeypatch.setattr(state.auth_sessions, "issue", boom)
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code=code), _req(state))
    assert ei.value.status_code == 500
    assert state.user_store.get_by_name("bob") is None
    assert state.invite_store.check(code)["used"] == 0


def test_register_rate_limited_after_repeated_failures(tmp_path):
    state = _state(tmp_path)
    for _ in range(5):
        with pytest.raises(HTTPException) as ei:
            register(_payload(invite_code="NOPE-NOPE-NOPE"), _req(state))
        assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code="NOPE-NOPE-NOPE"), _req(state))
    assert ei.value.status_code == 429
    assert "注册失败次数过多" in ei.value.detail


def test_register_without_invite_when_disabled(tmp_path):
    # 关闭「注册需要邀请码」后，不传邀请码也不依赖邀请码服务即可注册
    state = _state(tmp_path, require_invite=False)
    state.invite_store = None
    resp = register(_payload(), _req(state))
    assert resp["token"]
    u = state.user_store.get_by_id(resp["id"])
    assert u is not None and u.name == "bob"
    # 传入的邀请码也不会被消费（此时没有邀请码服务）
    resp = register(_payload(name="carol", invite_code="ABCD-EFGH"), _req(state))
    assert state.user_store.get_by_name("carol") is not None


def test_register_requires_invite_store(tmp_path):
    state = _state(tmp_path)
    state.invite_store = None
    with pytest.raises(HTTPException) as ei:
        register(_payload(invite_code="ABCD-EFGH"), _req(state))
    assert ei.value.status_code == 503
