"""邮箱验证码存储：生成/冷却/限额/尝试次数/过期。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

import flyai.email_codes as ec
from flyai.email_codes import EmailCodeError, EmailCodeStore


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ec, "time", SimpleNamespace(time=c))
    return c


def test_issue_and_verify(clock):
    store = EmailCodeStore()
    code = store.issue("register", "A@B.com")
    assert len(code) == 6 and code.isdigit()
    # 邮箱归一化：大小写不敏感
    assert store.verify("register", "a@b.com", code) is True
    assert store.verify("register", "a@b.com", "000000") is False


def test_wrong_code_then_correct_keeps_usable(clock):
    store = EmailCodeStore()
    code = store.issue("register", "a@b.com")
    wrong = "000000" if code != "000000" else "111111"
    assert store.verify("register", "a@b.com", wrong) is False
    assert store.verify("register", "a@b.com", code) is True


def test_resend_cooldown(clock):
    store = EmailCodeStore()
    store.issue("register", "a@b.com", resend_interval=60)
    with pytest.raises(EmailCodeError) as ei:
        store.issue("register", "a@b.com", resend_interval=60)
    assert ei.value.status == 429 and ei.value.retry_after > 0
    clock.now += 61
    code2 = store.issue("register", "a@b.com", resend_interval=60)
    assert len(code2) == 6


def test_new_code_replaces_old(clock):
    store = EmailCodeStore()
    old = store.issue("register", "a@b.com", resend_interval=0)
    clock.now += 1
    new = store.issue("register", "a@b.com", resend_interval=0)
    assert store.verify("register", "a@b.com", old) is False
    assert store.verify("register", "a@b.com", new) is True


def test_hourly_limit_per_email(clock):
    store = EmailCodeStore()
    for i in range(2):
        store.issue("register", "a@b.com", resend_interval=0, hourly_limit=2)
        clock.now += 10
    with pytest.raises(EmailCodeError) as ei:
        store.issue("register", "a@b.com", resend_interval=0, hourly_limit=2)
    assert ei.value.status == 429
    # 另一个邮箱不受影响
    assert store.issue("register", "c@d.com", resend_interval=0, hourly_limit=2)


def test_ip_hourly_limit(clock):
    store = EmailCodeStore()
    for email in ("a@b.com", "c@d.com"):
        store.issue("register", email, ip="1.2.3.4", resend_interval=0,
                    ip_hourly_limit=2)
        clock.now += 1
    with pytest.raises(EmailCodeError) as ei:
        store.issue("register", "e@f.com", ip="1.2.3.4",
                    resend_interval=0, ip_hourly_limit=2)
    assert "请求过于频繁" in ei.value.message
    # 其它 IP 不受影响
    assert store.issue("register", "e@f.com", ip="5.6.7.8",
                       resend_interval=0, ip_hourly_limit=2)


def test_max_attempts_invalidates(clock):
    store = EmailCodeStore()
    code = store.issue("register", "a@b.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(ec.MAX_ATTEMPTS):
        assert store.verify("register", "a@b.com", wrong) is False
    # 作废后即使正确码也不再通过
    assert store.verify("register", "a@b.com", code) is False


def test_expiry(clock):
    store = EmailCodeStore()
    code = store.issue("register", "a@b.com", ttl=60)
    clock.now += 61
    assert store.verify("register", "a@b.com", code) is False


def test_discard(clock):
    store = EmailCodeStore()
    code = store.issue("register", "a@b.com")
    store.discard("register", "a@b.com")
    assert store.verify("register", "a@b.com", code) is False


def test_purposes_isolated(clock):
    store = EmailCodeStore()
    code = store.issue("register", "a@b.com")
    assert store.verify("reset", "a@b.com", code) is False
    assert store.verify("register", "a@b.com", code) is True


def test_invalid_purpose(clock):
    store = EmailCodeStore()
    with pytest.raises(EmailCodeError):
        store.issue("login", "a@b.com")
