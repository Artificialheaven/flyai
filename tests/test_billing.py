"""计费存储与辅助：UserStore 额度方法 + billing 纯函数。"""

import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.billing import (
    charge_or_402,
    has_pricing,
    precheck_or_402,
    provider_rate,
    quota_view,
    refund,
    resolve_prices,
    settle_usage,
    usage_cost,
)
from flyai.config import PricePeriod, ProviderConfig, TokenPricing
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


# ---- 按 token 分档计费 ----

def _priced_cfg(periods=None, **prices):
    base = {"cache_hit_input": 0.5, "input": 2.0, "output": 8.0}
    base.update(prices)
    return ProviderConfig(
        id="p1", base_url="u", model="m",
        pricing=TokenPricing(periods=periods or [], **base),
    )


def test_has_pricing():
    plain = ProviderConfig(id="p0", base_url="u", model="m")
    assert has_pricing(SimpleNamespace(cfg=plain)) is False
    assert has_pricing(SimpleNamespace(cfg=_priced_cfg())) is True
    # 兼容直接传配置对象
    assert has_pricing(_priced_cfg()) is True


def test_prices_at_falls_back_to_base():
    cfg = _priced_cfg()
    assert cfg.pricing.prices_at(datetime(2026, 11, 16, 12, 0)) == {
        "cache_hit_input": 0.5, "input": 2.0, "output": 8.0, "period": "",
    }


def test_prices_at_daily_window_and_wrap():
    periods = [
        PricePeriod(name="夜间", cache_hit_input=0.1, input=1.0, output=4.0,
                    daily_start="22:00", daily_end="06:00"),
        PricePeriod(name="白天", cache_hit_input=0.6, input=2.4, output=9.0,
                    daily_start="06:00", daily_end="22:00"),
    ]
    cfg = _priced_cfg(periods)
    night = cfg.pricing.prices_at(datetime(2026, 11, 16, 23, 30))
    assert night["period"] == "夜间" and night["input"] == 1.0
    early = cfg.pricing.prices_at(datetime(2026, 11, 16, 5, 59))
    assert early["period"] == "夜间"
    day = cfg.pricing.prices_at(datetime(2026, 11, 16, 12, 0))
    assert day["period"] == "白天"
    edge = cfg.pricing.prices_at(datetime(2026, 11, 16, 22, 0))
    assert edge["period"] == "夜间"  # 起点含、终点不含


def test_prices_at_date_range_and_first_match():
    periods = [
        PricePeriod(name="活动", cache_hit_input=0, input=0.1, output=0.2,
                    date_start="2026-11-10", date_end="2026-11-20"),
        PricePeriod(name="兜底时段", cache_hit_input=0, input=3.0, output=5.0),
    ]
    cfg = _priced_cfg(periods)
    inside = cfg.pricing.prices_at(datetime(2026, 11, 20, 12, 0))
    assert inside["period"] == "活动"  # 含端点
    outside = cfg.pricing.prices_at(datetime(2026, 11, 21, 12, 0))
    assert outside["period"] == "兜底时段"  # 先命中者优先


def test_prices_validation_clamps_and_rejects_bad_input():
    # 价格被钳到 0；非法 HH:MM 在构造期直接拒绝
    assert PricePeriod(input=-1, cache_hit_input=-2, output=-3).input == 0.0
    with pytest.raises(Exception):
        PricePeriod(daily_start="25:00")
    with pytest.raises(Exception):
        PricePeriod(date_start="2026/11/16")


def test_usage_cost_splits_cached_and_output():
    prices = {"cache_hit_input": 0.5, "input": 2.0, "output": 8.0}
    cost = usage_cost(prices, prompt=1_000_000, cached=200_000,
                      completion=100_000)
    assert cost == pytest.approx(2.5)
    # cached 超过 prompt 时按 prompt 截断
    assert usage_cost(prices, prompt=100, cached=999, completion=0) == \
        pytest.approx(100 * 0.5 / 1e6)


def test_resolve_prices_uses_timezone_local_clock():
    cfg = _priced_cfg([
        PricePeriod(name="夜间", input=1.0, daily_start="22:00",
                    daily_end="06:00"),
    ])
    # 2026-11-16 15:00 UTC = 上海 23:00
    epoch = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc).timestamp()
    got = resolve_prices(SimpleNamespace(cfg=cfg), now=epoch,
                         tzname="Asia/Shanghai")
    assert got["period"] == "夜间"


def test_add_quota_and_add_used(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 2)
    assert store.add_quota(u.id, 5) == pytest.approx(7)
    store.add_used(u.id, 1.25)
    assert quota_view(store, u.id)["used"] == pytest.approx(1.25)
    # 无限额度：加额度保持 -1
    store.set_quota_total(u.id, -1)
    assert store.add_quota(u.id, 5) == -1


def test_precheck_or_402(tmp_path):
    precheck_or_402(None, ADMIN_UID)  # 管理员不访问 store
    store = _store(tmp_path)
    u = _user(store)
    state = SimpleNamespace(user_store=store)
    store.set_quota_total(u.id, 1)
    precheck_or_402(state, u.id)  # 余额 > 0 放行
    store.add_used(u.id, 1)
    with pytest.raises(HTTPException) as ei:
        precheck_or_402(state, u.id)
    assert ei.value.status_code == 402
    assert "CDK" in ei.value.detail
    store.set_quota_total(u.id, -1)
    precheck_or_402(state, u.id)


def test_settle_usage_deducts_and_appends_ledger(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 10)
    state = SimpleNamespace(user_store=store)
    cfg = _priced_cfg()
    ledger = tmp_path / "usage.jsonl"
    now = datetime(2026, 11, 16, 12, 0, tzinfo=None).timestamp()

    rec = settle_usage(
        state, u.id, SimpleNamespace(cfg=cfg),
        {"prompt_tokens": 1000, "cached_tokens": 400,
         "completion_tokens": 500},
        session_id="s1", ledger_path=ledger, messages=1,
        now=now, tzname="Asia/Shanghai",
    )
    assert rec is not None
    # 400*0.5 + 600*2 + 500*8（每 1M）
    assert rec["cost"] == pytest.approx(0.0054)
    assert rec["total_tokens"] == 1500 and rec["cached_tokens"] == 400
    assert quota_view(store, u.id)["used"] == pytest.approx(0.0054)
    from flyai.usage_store import UsageLedger

    line = next(UsageLedger(ledger).records())
    assert line["session_id"] == "s1" and line["messages"] == 1
    assert line["date"] == "2026-11-16"


def test_settle_usage_skips_unpriced_and_admin(tmp_path):
    store = _store(tmp_path)
    u = _user(store)
    store.set_quota_total(u.id, 10)
    state = SimpleNamespace(user_store=store)
    plain = ProviderConfig(id="p0", base_url="u", model="m")
    assert settle_usage(state, u.id, plain,
                        {"prompt_tokens": 10}) is None
    rec = settle_usage(state, ADMIN_UID, _priced_cfg(),
                       {"prompt_tokens": 10 ** 6})
    assert rec is not None and rec["cost"] == pytest.approx(2.0)
    assert store.get_by_id(ADMIN_UID) is None  # 管理员不落 used
