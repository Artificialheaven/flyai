"""按量计费：计费公式 + UsageMeter 扣额/流水/权限。

1 点 = 1 元；admin 不扣费也不写流水；quota_total=-1 仍累计 used。
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.metering import (
    UsageMeter,
    asr_cost,
    billable_chars,
    make_usage_meter,
    per_success_cost,
    tts_cost,
)
from flyai.user import ADMIN_UID, UserStore


# ---- 纯函数 ----

def test_billable_chars_counts_cjk_as_two():
    assert billable_chars("") == 0
    assert billable_chars("abc") == 3
    assert billable_chars("你好") == 4
    # 全角标点、全角字母同样算 2
    assert billable_chars("「，！Ａ") == 8
    assert billable_chars("你好abc") == 7


def test_per_success_cost_is_price_per_thousand():
    assert per_success_cost(5) == pytest.approx(0.005)
    assert per_success_cost(0) == 0
    assert per_success_cost(None) == 0
    assert per_success_cost(-3) == 0


def test_tts_cost_per_ten_thousand_chars():
    assert tts_cost(0, 3) == 0
    assert tts_cost(10000, 3) == pytest.approx(3)
    assert tts_cost(5000, 3) == pytest.approx(1.5)
    assert tts_cost(-1, 3) == 0  # 负字符按 0


def test_asr_cost_rounds_up_to_seconds():
    assert asr_cost(0, 1) == 0
    assert asr_cost(1000, 1) == pytest.approx(0.000278)   # 1 秒（保留 6 位）
    assert asr_cost(1001, 1) == pytest.approx(0.000556)   # 向上取整到 2 秒
    assert asr_cost(3600000, 1) == pytest.approx(1.0)
    assert asr_cost(3600000, 2) == pytest.approx(2.0)


# ---- UsageMeter ----

def _meter(tmp_path, uid="u1", admin=False):
    store = UserStore(tmp_path / "users")
    user = store.create("u1", "", level=0)
    ledger = tmp_path / "data" / uid / "usage.jsonl"
    meter = UsageMeter(store, user.id, ledger, "Asia/Shanghai", is_admin=admin)
    return user, store, meter


def _ledger_rows(path: Path):
    from flyai.usage_store import UsageLedger

    return list(UsageLedger(path).records())


def test_charge_deducts_and_writes_ledger(tmp_path):
    user, store, meter = _meter(tmp_path)
    store.set_quota_total(user.id, 10)
    assert meter.available() is True

    charged = meter.charge("search", 1, "次", 0.005, "deepseek",
                           session_id="sid1")
    assert charged == pytest.approx(0.005)
    assert store.get_by_id(user.id).quota_used == pytest.approx(0.005)

    rows = _ledger_rows(meter.ledger_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "search"
    assert row["quantity"] == 1
    assert row["unit"] == "次"
    assert row["cost"] == pytest.approx(0.005)
    assert row["engine"] == "deepseek"
    assert row["session_id"] == "sid1"
    assert row["messages"] == 0
    assert row["date"] and row["ts"] > 0


def test_admin_never_charged_and_no_ledger(tmp_path):
    user, store, meter = _meter(tmp_path, admin=True)
    store.set_quota_total(user.id, 1)
    assert meter.charge("tts", 100, "字符", 0.03, "deepseek") == 0
    assert store.get_by_id(user.id).quota_used == 0
    assert not meter.ledger_path.exists()


def test_admin_uid_detected_by_default(tmp_path):
    store = UserStore(tmp_path / "users")
    meter = UsageMeter(store, ADMIN_UID, None)
    assert meter.is_admin is True


def test_unlimited_quota_accumulates_used(tmp_path):
    user, store, meter = _meter(tmp_path)
    store.set_quota_total(user.id, -1)
    assert meter.available() is True
    meter.charge("asr", 3, "秒", 0.001)
    assert store.get_by_id(user.id).quota_used == pytest.approx(0.001)


def test_available_false_when_exhausted(tmp_path):
    user, store, meter = _meter(tmp_path)
    store.set_quota_total(user.id, 0.004)
    meter.charge("fetch", 1, "次", 0.004)
    assert meter.available() is False


def test_unknown_user_treated_as_available(tmp_path):
    store = UserStore(tmp_path / "users")
    meter = UsageMeter(store, "ghost", None)
    assert meter.available() is True


def test_zero_or_negative_cost_is_noop(tmp_path):
    user, store, meter = _meter(tmp_path)
    store.set_quota_total(user.id, 10)
    assert meter.charge("search", 1, "次", 0) == 0
    assert meter.charge("search", 1, "次", -1) == 0
    assert store.get_by_id(user.id).quota_used == 0
    assert not meter.ledger_path.exists()


def test_make_usage_meter_derives_paths_and_admin(tmp_path):
    store = UserStore(tmp_path / "users")
    user = store.create("u1", "", level=0)
    cfg = SimpleNamespace(scheduler=SimpleNamespace(timezone="UTC"))
    state = SimpleNamespace(user_store=store, config=cfg)
    sc = SimpleNamespace(uid=user.id,
                         settings_path=tmp_path / "data" / user.id / "settings.json")
    meter = make_usage_meter(state, sc)
    assert meter.uid == user.id
    assert meter.tzname == "UTC"
    assert meter.ledger_path == tmp_path / "data" / user.id / "usage.jsonl"
    assert meter.is_admin is False

    admin_sc = SimpleNamespace(uid=ADMIN_UID, settings_path=None)
    admin_meter = make_usage_meter(state, admin_sc)
    assert admin_meter.is_admin is True
    assert admin_meter.ledger_path is None