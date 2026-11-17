"""用量流水接口：近 N 天聚合、项目消耗拆分、窗口外数据忽略、时区日期边界。"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_usage import my_usage
from flyai.proactor.clock import local_dt

TZ = "Asia/Shanghai"


def _sc(tmp_path, uid="u1"):
    settings = tmp_path / "data" / uid / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(uid=uid, settings_path=settings)


def _state(sc):
    return SimpleNamespace(
        config=SimpleNamespace(
            scheduler=SimpleNamespace(timezone=TZ),
        ),
        user=lambda request: sc,
    )


def _request(state):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )


def _record(date, **kw):
    rec = {
        "ts": 0,
        "date": date,
        "session_id": "s1",
        "provider_id": "p1",
        "model": "m",
        "prompt_tokens": 100,
        "cached_tokens": 20,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cost": 0.001,
        "messages": 1,
    }
    rec.update(kw)
    return rec


def _metering(date, kind, quantity, unit, cost, engine=None):
    rec = {
        "ts": 0,
        "date": date,
        "kind": kind,
        "quantity": quantity,
        "unit": unit,
        "cost": cost,
        "engine": engine or "",
    }
    return rec


def _write_ledger(sc, records):
    ledger = sc.settings_path.parent / "usage.jsonl"
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _today():
    return local_dt(time.time(), TZ).date()


def test_aggregates_last_seven_days(tmp_path):
    sc = _sc(tmp_path)
    today = _today()
    yesterday = today - timedelta(days=1)
    _write_ledger(sc, [
        _record(today.isoformat()),
        _record(today.isoformat(), prompt_tokens=300, cached_tokens=0,
                completion_tokens=200, total_tokens=500, cost=0.004,
                messages=2),
        _record(yesterday.isoformat(), prompt_tokens=10, cached_tokens=5,
                completion_tokens=1, total_tokens=11, cost=0.0005),
        # 窗口外（第 8 天）
        _record((today - timedelta(days=7)).isoformat(), cost=9.9),
    ])

    result = my_usage(_request(_state(sc)), days=7)
    days = result["days"]
    assert len(days) == 7
    assert days[-1]["date"] == today.isoformat()
    assert days[-2]["date"] == yesterday.isoformat()
    assert result["today"] == today.isoformat()

    newest = days[-1]
    assert newest["prompt_tokens"] == 400
    assert newest["cached_tokens"] == 20
    assert newest["completion_tokens"] == 250
    assert newest["total_tokens"] == 650
    assert newest["cost"] == 0.005
    assert newest["messages"] == 3

    prev = days[-2]
    assert prev["prompt_tokens"] == 10 and prev["cost"] == 0.0005

    totals = result["totals"]
    assert totals["prompt_tokens"] == 410
    assert totals["total_tokens"] == 661
    assert totals["cost"] == 0.0055
    assert totals["messages"] == 4


def test_categories_split_by_project(tmp_path):
    """混合 ledger：无 kind 的 token 行归 chat，metering 行按 kind 各自成桶。"""
    sc = _sc(tmp_path)
    today = _today().isoformat()
    _write_ledger(sc, [
        _record(today),
        _record(today, prompt_tokens=50, cached_tokens=0, completion_tokens=0,
                total_tokens=50, cost=0.002, messages=2),
        _metering(today, "search", 10, "次", 0.01, engine="bing"),
        _metering(today, "search", 5, "次", 0.005, engine="bing"),
        _metering(today, "fetch", 3, "次", 0.002),
        _metering(today, "tts", 1200, "字符", 0.02, engine="volc"),
        _metering(today, "asr", 30, "秒", 0.004),
    ])

    result = my_usage(_request(_state(sc)), days=1)
    assert [c["kind"] for c in result["categories"]] == [
        "chat", "search", "fetch", "tts", "asr",
    ]
    cats = {c["kind"]: c for c in result["categories"]}

    chat = cats["chat"]
    assert chat["cost"] == 0.003
    assert chat["quantity"] == 200  # token 总数
    assert chat["unit"] == "token"
    assert chat["calls"] == 3  # 消息条数
    assert chat["engines"] == []

    search = cats["search"]
    assert search["cost"] == 0.015
    assert search["quantity"] == 15
    assert search["unit"] == "次"
    assert search["calls"] == 2
    assert search["engines"] == ["bing"]  # 同引擎去重

    assert cats["fetch"]["quantity"] == 3 and cats["fetch"]["calls"] == 1
    assert cats["fetch"]["engines"] == []
    assert cats["tts"]["quantity"] == 1200 and cats["tts"]["calls"] == 1
    assert cats["tts"]["unit"] == "字符"
    assert cats["asr"]["quantity"] == 30 and cats["asr"]["calls"] == 1
    assert cats["asr"]["unit"] == "秒"

    # 项目 cost 之和与当日/窗口总额一致
    total_cost = round(sum(c["cost"] for c in result["categories"]), 6)
    assert result["days"][0]["cost"] == total_cost
    assert result["totals"]["cost"] == total_cost


def test_missing_ledger_returns_zero_filled_days(tmp_path):
    sc = _sc(tmp_path)
    result = my_usage(_request(_state(sc)), days=3)
    assert len(result["days"]) == 3
    assert result["days"][-1]["date"] == _today().isoformat()
    assert all(
        b["total_tokens"] == 0 and b["cost"] == 0 and b["messages"] == 0
        for b in result["days"]
    )
    assert result["categories"] == []


def test_invalid_lines_skipped(tmp_path):
    sc = _sc(tmp_path)
    today = _today().isoformat()
    ledger = sc.settings_path.parent / "usage.jsonl"
    ledger.write_text(
        "not-json\n" + json.dumps(_record(today)) + "\n",
        encoding="utf-8",
    )
    result = my_usage(_request(_state(sc)), days=1)
    assert result["days"][0]["prompt_tokens"] == 100


def test_days_clamped(tmp_path):
    sc = _sc(tmp_path)
    req = _request(_state(sc))
    assert len(my_usage(req, days=0)["days"]) == 1
    assert len(my_usage(req, days="abc")["days"]) == 7
    assert len(my_usage(req, days=999)["days"]) == 90


def test_today_uses_configured_timezone(tmp_path, monkeypatch):
    import flyai.api.routes_usage as routes_usage

    # 2026-11-16 16:30 UTC = 上海 2026-11-17 00:30
    epoch = datetime(2026, 11, 16, 16, 30, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(routes_usage.time, "time", lambda: epoch)
    sc = _sc(tmp_path)
    result = my_usage(_request(_state(sc)), days=1)
    assert result["today"] == "2026-11-17"
    assert result["days"][0]["date"] == "2026-11-17"


def test_state_without_settings_path(tmp_path):
    sc = SimpleNamespace(uid="u1")
    result = my_usage(_request(_state(sc)), days=2)
    assert len(result["days"]) == 2
    assert result["totals"]["cost"] == 0