"""找回密码：发码防枚举、重置凭码、改密后吊销旧令牌。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_auth import (
    EmailCodePayload,
    ResetPasswordPayload,
    reset_password,
    send_email_code,
)
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig
from flyai.email_codes import EmailCodeStore
from flyai.user import UserStore

EMAIL = "bob@example.com"


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send_code(self, cfg, to, code, purpose):
        self.sent.append((to, code, purpose))

    def send_test(self, cfg, to):
        self.sent.append((to, "", "test"))


def _state(tmp_path):
    cfg = AppConfig()
    cfg.email.enabled = True
    cfg.email.host = "smtp.qq.com"
    cfg.email.username = "bot@qq.com"
    cfg.email.password = "auth-code"
    state = SimpleNamespace(
        config=cfg,
        user_store=UserStore(tmp_path),
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
        email_codes=EmailCodeStore(),
        mailer=FakeMailer(),
    )
    state.user = state.user_store.create("bob", "oldpw", email=EMAIL)
    return state


def _req(state, host="127.0.0.1"):
    return SimpleNamespace(
        headers={},
        client=SimpleNamespace(host=host),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def _send(state, email=EMAIL, host="127.0.0.1"):
    return asyncio.run(
        send_email_code(
            EmailCodePayload(email=email, purpose="reset"), _req(state, host)
        )
    )


def _reset(state, email=EMAIL, code="", new_password="newpw", host="127.0.0.1"):
    return reset_password(
        ResetPasswordPayload(email=email, code=code, new_password=new_password),
        _req(state, host),
    )


def test_unknown_email_returns_ok_without_sending(tmp_path):
    state = _state(tmp_path)
    out = _send(state, email="nobody@example.com")
    assert out == {"ok": True}
    assert state.mailer.sent == []


def test_known_email_sends_code(tmp_path):
    state = _state(tmp_path)
    out = _send(state)
    assert out["ok"] is True
    assert len(state.mailer.sent) == 1
    to, code, purpose = state.mailer.sent[0]
    assert (to, purpose) == (EMAIL, "reset")
    assert state.email_codes.verify("reset", EMAIL, code) is True


def test_reset_success_changes_password_and_revokes(tmp_path):
    state = _state(tmp_path)
    old_token = state.auth_sessions.issue(state.user.id, "bob", "user", 0)
    _send(state)
    code = state.mailer.sent[0][1]

    out = _reset(state, code=code)
    assert out == {"ok": True, "name": "bob"}

    u = state.user_store.get_by_name("bob")
    assert u.check_password("newpw") is True
    assert u.check_password("oldpw") is False
    # 旧令牌已失效
    assert state.auth_sessions.resolve(old_token) is None
    # 码已消费
    assert state.email_codes.verify("reset", EMAIL, code) is False

    # 落盘的也是新密码
    assert UserStore(tmp_path).get_by_name("bob").check_password("newpw") is True


def test_wrong_code_and_unknown_email_uniform_error(tmp_path):
    state = _state(tmp_path)
    _send(state)
    real_code = state.mailer.sent[0][1]
    wrong = "000000" if real_code != "000000" else "111111"

    for email in (EMAIL, "nobody@example.com"):
        with pytest.raises(HTTPException) as ei:
            _reset(state, email=email, code=wrong)
        assert ei.value.status_code == 400
        assert ei.value.detail == "邮箱验证码错误或已过期"
    # 密码未被改动
    assert state.user_store.get_by_name("bob").check_password("oldpw") is True


def test_requires_new_password(tmp_path):
    state = _state(tmp_path)
    _send(state)
    code = state.mailer.sent[0][1]
    with pytest.raises(HTTPException) as ei:
        _reset(state, code=code, new_password="")
    assert ei.value.status_code == 400


def test_repeated_failures_lock(tmp_path):
    state = _state(tmp_path)
    _send(state)
    real_code = state.mailer.sent[0][1]
    wrong = "000000" if real_code != "000000" else "111111"

    for _ in range(5):
        with pytest.raises(HTTPException) as ei:
            _reset(state, code=wrong)
        assert ei.value.status_code == 400
    # 第 6 次直接锁定，即使码正确
    with pytest.raises(HTTPException) as ei:
        _reset(state, code=real_code)
    assert ei.value.status_code == 429
    assert state.user_store.get_by_name("bob").check_password("oldpw") is True


def test_reset_disabled_or_unconfigured(tmp_path):
    state = _state(tmp_path)
    state.config.email.enabled = False
    with pytest.raises(HTTPException) as ei:
        _reset(state, code="123456")
    assert ei.value.status_code == 400

    state.config.email.enabled = True
    state.config.email.password = ""
    with pytest.raises(HTTPException) as ei:
        _reset(state, code="123456")
    assert ei.value.status_code == 503
