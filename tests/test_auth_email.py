"""注册邮件验证：能力探测、发码、校验、失败回滚与关闭时兼容。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_auth import (
    EmailCodePayload,
    RegisterPayload,
    register,
    register_info,
    send_email_code,
)
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig
from flyai.email_codes import EmailCodeStore
from flyai.invites import InviteStore
from flyai.mailer import MailError
from flyai.user import UserStore

EMAIL = "bob@example.com"


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send_code(self, cfg, to, code, purpose):
        if self.fail:
            raise MailError("smtp down")
        self.sent.append((to, code, purpose))

    def send_test(self, cfg, to):
        if self.fail:
            raise MailError("smtp down")
        self.sent.append((to, "", "test"))


def _state(tmp_path, enabled=True, configured=True):
    cfg = AppConfig()
    cfg.auth.admin.username = "root"
    cfg.auth.admin.password = "secret"
    cfg.email.enabled = enabled
    if configured:
        cfg.email.host = "smtp.qq.com"
        cfg.email.username = "bot@qq.com"
        cfg.email.password = "auth-code"
    return SimpleNamespace(
        config=cfg,
        user_store=UserStore(tmp_path),
        invite_store=InviteStore(tmp_path),
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
        email_codes=EmailCodeStore(),
        mailer=FakeMailer(),
    )


def _req(state, host="127.0.0.1"):
    return SimpleNamespace(
        headers={},
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def _send_code(state, email=EMAIL, purpose="register"):
    return asyncio.run(
        send_email_code(EmailCodePayload(email=email, purpose=purpose), _req(state))
    )


def _register(state, email=EMAIL, code="", invite="", name="bob", password="pw"):
    return register(
        RegisterPayload(
            name=name,
            password=password,
            invite_code=invite,
            email=email,
            email_code=code,
        ),
        _req(state),
    )


def test_register_info_flags(tmp_path):
    state = _state(tmp_path, enabled=False, configured=False)
    assert register_info(_req(state)) == {
        "invite_required": True,
        "email_required": False,
        "password_reset_enabled": False,
    }
    state = _state(tmp_path / "a")
    assert register_info(_req(state)) == {
        "invite_required": True,
        "email_required": True,
        "password_reset_enabled": True,
    }
    state = _state(tmp_path / "b", configured=False)
    assert register_info(_req(state)) == {
        "invite_required": True,
        "email_required": True,
        "password_reset_enabled": False,
    }
    # 管理员关闭邀请码后注册页不再要求邀请码
    state = _state(tmp_path / "c", enabled=False, configured=False)
    state.config.registration.require_invite = False
    assert register_info(_req(state)) == {
        "invite_required": False,
        "email_required": False,
        "password_reset_enabled": False,
    }


def test_send_code_success(tmp_path):
    state = _state(tmp_path)
    out = _send_code(state)
    assert out["ok"] is True and out["retry_after"] == 60
    assert len(state.mailer.sent) == 1
    to, code, purpose = state.mailer.sent[0]
    assert to == EMAIL and purpose == "register" and len(code) == 6
    assert state.email_codes.verify("register", EMAIL, code) is True


def test_send_code_disabled_and_unconfigured(tmp_path):
    state = _state(tmp_path, enabled=False)
    with pytest.raises(HTTPException) as ei:
        _send_code(state)
    assert ei.value.status_code == 400
    assert state.mailer.sent == []

    state = _state(tmp_path / "b", configured=False)
    with pytest.raises(HTTPException) as ei:
        _send_code(state)
    assert ei.value.status_code == 503


@pytest.mark.parametrize("email", ["", "not-an-email", "a@b"])
def test_send_code_invalid_email(tmp_path, email):
    state = _state(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _send_code(state, email=email)
    assert ei.value.status_code == 400


def test_send_code_bad_purpose(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _send_code(state, purpose="login")
    assert ei.value.status_code == 400


def test_send_code_register_existing_email(tmp_path):
    state = _state(tmp_path)
    state.user_store.create("taken", "pw", email=EMAIL)
    with pytest.raises(HTTPException) as ei:
        _send_code(state)
    assert ei.value.status_code == 409


def test_send_code_failure_discards_code(tmp_path):
    state = _state(tmp_path)
    state.mailer.fail = True
    with pytest.raises(HTTPException) as ei:
        _send_code(state)
    assert ei.value.status_code == 503
    # 发信失败后码被删除，可立即重试（不受冷却影响）
    state.mailer.fail = False
    _send_code(state)


def test_register_requires_email_and_code(tmp_path):
    state = _state(tmp_path)
    invite = state.invite_store.generate("owner")["code"]
    with pytest.raises(HTTPException) as ei:
        _register(state, invite=invite, code="")
    assert ei.value.status_code == 400 and "邮箱验证码" in ei.value.detail

    with pytest.raises(HTTPException) as ei:
        _register(state, email="bad", invite=invite, code="123456")
    assert ei.value.status_code == 400 and "邮箱" in ei.value.detail


def test_register_wrong_code_no_user_no_invite_consumed(tmp_path):
    state = _state(tmp_path)
    invite = state.invite_store.generate("owner")["code"]
    _send_code(state)
    with pytest.raises(HTTPException) as ei:
        _register(state, code="000000", invite=invite)
    assert ei.value.status_code == 400
    assert "验证码" in ei.value.detail
    assert state.user_store.get_by_name("bob") is None
    assert state.invite_store.check(invite)["used"] == 0


def test_register_with_valid_code_success(tmp_path):
    state = _state(tmp_path)
    invite = state.invite_store.generate("owner")["code"]
    _send_code(state)
    code = state.mailer.sent[0][1]
    resp = _register(state, code=code, invite=invite)
    assert resp["token"] and resp["role"] == "user"
    u = state.user_store.get_by_name("bob")
    assert u is not None and u.email == EMAIL
    assert state.invite_store.check(invite)["used"] == 1
    # 验证码一次性消费
    assert state.email_codes.verify("register", EMAIL, code) is False


def test_register_duplicate_email_rejected(tmp_path):
    state = _state(tmp_path)
    state.user_store.create("alice", "pw", email=EMAIL)
    invite = state.invite_store.generate("owner")["code"]
    # 发码阶段就会 409；直接造一个码模拟并发场景
    code = state.email_codes.issue("register", EMAIL)
    with pytest.raises(HTTPException) as ei:
        _register(state, code=code, invite=invite)
    assert ei.value.status_code == 400
    assert "邮箱" in ei.value.detail
    assert state.invite_store.check(invite)["used"] == 0


def test_register_unconfigured_email_blocks(tmp_path):
    state = _state(tmp_path, configured=False)
    invite = state.invite_store.generate("owner")["code"]
    with pytest.raises(HTTPException) as ei:
        _register(state, invite=invite, code="123456")
    assert ei.value.status_code == 503


def test_register_email_disabled_keeps_old_flow(tmp_path):
    state = _state(tmp_path, enabled=False, configured=False)
    invite = state.invite_store.generate("owner")["code"]
    resp = _register(state, email="", code="", invite=invite)
    assert resp["token"]
    assert state.user_store.get_by_name("bob").email == ""
