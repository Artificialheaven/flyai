"""SMTP 发信：协议选择、认证映射、邮件头安全与文案。"""

import os
import smtplib
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import flyai.mailer as mailer_mod
from flyai.config import EmailSettings
from flyai.mailer import (
    MailError,
    Mailer,
    build_code_mail,
    normalize_email,
    sanitize_header,
)


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.ehlo_calls = 0
        self.starttls_calls = []
        self.login_args = None
        self.msg = None
        self.quit_called = False
        type(self).instances.append(self)

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self, context=None):
        self.starttls_calls.append(context)

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, msg):
        self.msg = msg

    def quit(self):
        self.quit_called = True


class FakeSMTPSSL(FakeSMTP):
    instances = []


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTPSSL.instances = []
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP, FakeSMTPSSL


def _cfg(**kw):
    base = dict(
        enabled=True,
        host="smtp.qq.com",
        port=465,
        encryption="ssl",
        username="bot@qq.com",
        password="auth-code",
        from_name="FlyAI",
        from_address="",
    )
    base.update(kw)
    return EmailSettings(**base)


@pytest.mark.parametrize("raw,expected", [
    (" A@B.com ", "a@b.com"),
    ("user.name+tag@example.co.uk", "user.name+tag@example.co.uk"),
])
def test_normalize_email_ok(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "no-at", "a@b", "a b@c.com", "@b.com"])
def test_normalize_email_invalid(raw):
    with pytest.raises(MailError):
        normalize_email(raw)


def test_sanitize_header():
    assert sanitize_header("Hi\r\nBcc: evil@x.com") == "Hi Bcc: evil@x.com"


def test_build_code_mail_content():
    subject, text = build_code_mail(_cfg(), "123456", "register", 600)
    assert subject == "FlyAI 注册验证码"
    assert "123456" in text and "10 分钟" in text
    subject2, _ = build_code_mail(_cfg(), "123456", "reset", 600)
    assert subject2 == "FlyAI 密码重置验证码"


def test_send_via_ssl(fake_smtp):
    smtp_cls, ssl_cls = fake_smtp
    Mailer().send(_cfg(), "to@example.com", "主题", "正文")
    assert smtp_cls.instances == []
    assert len(ssl_cls.instances) == 1
    conn = ssl_cls.instances[0]
    assert (conn.host, conn.port) == ("smtp.qq.com", 465)
    assert conn.login_args == ("bot@qq.com", "auth-code")
    assert conn.msg["To"] == "to@example.com"
    assert conn.msg["Subject"] == "主题"
    assert "正文" in conn.msg.get_content()
    assert conn.quit_called is True


def test_send_via_starttls(fake_smtp):
    smtp_cls, ssl_cls = fake_smtp
    Mailer().send(
        _cfg(encryption="starttls", port=587),
        "to@example.com",
        "主题",
        "正文",
    )
    assert ssl_cls.instances == []
    conn = smtp_cls.instances[0]
    assert (conn.host, conn.port) == ("smtp.qq.com", 587)
    assert conn.ehlo_calls == 2
    assert len(conn.starttls_calls) == 1
    assert conn.login_args == ("bot@qq.com", "auth-code")


def test_send_plain(fake_smtp):
    smtp_cls, _ = fake_smtp
    Mailer().send(_cfg(encryption="none", port=25), "to@example.com", "s", "t")
    conn = smtp_cls.instances[0]
    assert conn.starttls_calls == []
    assert conn.ehlo_calls == 1


def test_auth_error_mapped(fake_smtp, monkeypatch):
    def bad_login(self, username, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad auth")

    monkeypatch.setattr(FakeSMTP, "login", bad_login)
    with pytest.raises(MailError) as ei:
        Mailer().send(_cfg(), "to@example.com", "s", "t")
    assert "认证失败" in str(ei.value) and "授权码" in str(ei.value)


def test_connect_and_timeout_errors_mapped(fake_smtp, monkeypatch):
    def refuse(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, b"nope")

    monkeypatch.setattr(mailer_mod.smtplib, "SMTP_SSL", refuse)
    with pytest.raises(MailError) as ei:
        Mailer().send(_cfg(), "to@example.com", "s", "t")
    assert "无法连接" in str(ei.value)

    def timeout(*args, **kwargs):
        raise OSError("timed out")

    monkeypatch.setattr(mailer_mod.smtplib, "SMTP_SSL", timeout)
    with pytest.raises(MailError) as ei:
        Mailer().send(_cfg(), "to@example.com", "s", "t")
    assert "邮件发送失败" in str(ei.value)


def test_missing_credentials(fake_smtp):
    with pytest.raises(MailError) as ei:
        Mailer().send(_cfg(password=""), "to@example.com", "s", "t")
    assert "未配置" in str(ei.value)


def test_send_code_and_test(fake_smtp):
    smtp_cls, ssl_cls = fake_smtp
    m = Mailer()
    m.send_code(_cfg(), "to@example.com", "654321", "register")
    m.send_test(_cfg(), "to@example.com")
    assert len(ssl_cls.instances) == 2
    first = ssl_cls.instances[0].msg
    assert first["Subject"] == "FlyAI 注册验证码"
    assert "654321" in first.get_content()
    second = ssl_cls.instances[1].msg
    assert second["Subject"] == "FlyAI 邮件服务测试"
    assert "smtp.qq.com:465" in second.get_content()
