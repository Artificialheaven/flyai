"""计费存储与辅助：UserStore 额度方法 + billing 纯函数。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.billing import (
    charge_or_402,
    provider_rate,
    quota_view,
    refund,
)
from flyai.config import ProviderConfig
from flyai.user import ADMIN_UID, UserStore


def _store(tmp_path):
    return UserStore(tmp_path)


def _user(store, name="u1"):
    return store.create(name, "", level=0)


def test_consume_deducts_and_refunds(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 3)

    assert store.consume(u.id, 1) is True
    assert store.consume(u.id, 1.5) is True
    view = quota_view(store, u.id)
    assert view["remaining"] == pytest.approx(0.5)
    assert view["used"] == pytest.approx(2.5)

    store.refund(u.id, 1)
    assert quota_view(store, u.id)["remaining"] == pytest.approx(1.5)


def test_consume_insufficient_returns_false_and_keeps_balance(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 1)
    assert store.consume(u.id, 2) is False
    assert quota_view(store, u.id)["remaining"] == pytest.approx(1)


def test_unlimited_quota(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, -1)
    assert store.consume(u.id, 999) is True
    view = quota_view(store, u.id)
    assert view["total"] == -1 and view["remaining"] is None


def test_refund_never_negative(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.refund(u.id, 5)
    assert store.get_by_id(u.id).quota_used == 0


def test_quota_persists_roundtrip(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 10)
    store.consume(u.id, 2.5)

    reloaded = _store(tmp_path)
    view = quota_view(reloaded, u.id)
    assert view["total"] == pytest.approx(10)
    assert view["used"] == pytest.approx(2.5)
    assert view["remaining"] == pytest.approx(7.5)


def test_legacy_user_without_quota_fields(tmp_path):
    # 旧 users.json 没有额度字段：默认额度 0
    import json

    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "users.json").write_text(json.dumps({
        "users": [{"id": "old1", "name": "旧用户", "password_hash": "x",
                   "created_at": "2026-01-01T00:00:00", "level": 0}]
    }), encoding="utf-8")
    store = _store(tmp_path)
    view = quota_view(store, "old1")
    assert view["remaining"] == 0 and view["used"] == 0


def test_set_quota_rejects_negative(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    with pytest.raises(ValueError):
        store.set_quota_total(u.id, -2)


def test_provider_rate_defaults():
    class NoCfg:
        pass

    assert provider_rate(NoCfg()) == 1.0

    class Cfg:
        cfg = ProviderConfig(id="x", base_url="u", model="m", rate=0.5)

    assert provider_rate(Cfg()) == 0.5

    class Bad:
        cfg = SimpleNamespace(rate="not-a-number")

    assert provider_rate(Bad()) == 1.0


def test_charge_or_402_admin_and_insufficient(tmp_path):
    # 管理员：没有 user_store 也放行
    charge_or_402(None, ADMIN_UID, 1)

    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 0.5)
    state = SimpleNamespace(user_store=store)
    with pytest.raises(HTTPException) as ei:
        charge_or_402(state, u.id, 1)
    assert ei.value.status_code == 402
    assert quota_view(store, u.id)["remaining"] == pytest.approx(0.5)


def test_refund_admin_noop():
    refund(None, ADMIN_UID, 1)  # 不访问 store
    assert quota_view(None, ADMIN_UID) is None
