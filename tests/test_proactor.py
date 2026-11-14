# -*- coding: utf-8 -*-
"""P3 心情系统 / 自我记忆 / 主动行为：纯逻辑、DAL、Ticker、LIVE_CTX、工具、
接线函数与 HTTP 路由测试。所有随机/时钟均可注入，测试不 sleep、不赌运气。"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.runs import ChatRunManager
from flyai.agentdb.self_memory import QuotaExceeded, SelfMemoryStore
from flyai.agentdb import open_agent_db
from flyai.config import (
    AppConfig,
    MoodSettings,
    ProactorSettings,
    RoleplaySettings,
    SelfMemorySettings,
)
from flyai.feature_prefs import allowed_tool_names
from flyai.lore.models import Character
from flyai.proactor import clock, integrate, live_ctx, mood as mood_mod
from flyai.proactor.proactor import (
    ProactorStore,
    ProactorTicker,
    cold_multiplier,
    in_cooldown,
    is_peak_window,
    roll_hit,
    speakup_probability,
    wake_should_fire,
)
from flyai.proactor.reminders import CommitmentStore, ReminderTicker
from flyai.proactor.scheduler_jobs import (
    JobStore,
    JobTicker,
    RETRY_SECONDS,
    compute_next_run,
)
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.tools.base import ToolContext, ToolResult
from flyai.tools.proactor_tools import (
    CloseCommitmentTool,
    RecallSelfTool,
    RememberCommitmentTool,
    RememberSelfTool,
)
from flyai.tools.registry import ToolRegistry

ZONE = clock.get_zone("Asia/Shanghai")


def _epoch(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ZONE).timestamp()


# 找一个周五 10:00（工作日峰段）作为确定性锚点
def _friday_ten():
    cur = datetime(2026, 1, 1, 10, 0)
    while cur.weekday() != 4:
        cur += timedelta(days=1)
    return cur.replace(tzinfo=ZONE).timestamp()


NOW = _friday_ten()
SUNDAY_NOON = NOW + 2 * 86400 + 2 * 3600  # 周日 12:00，低谷段


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _ms(**kw):
    return MoodSettings(**kw)


def _ss(**kw):
    return SelfMemorySettings(**kw)


def _ps(**kw):
    kw.setdefault("enabled", True)
    kw.setdefault("wake_check_minutes", 1)
    return ProactorSettings(**kw)


class _FakeStore:
    def __init__(self, sessions):
        self._sessions = sessions

    def get(self, sid):
        return self._sessions.get(sid)


def _ticker_state(tmp_path, sessions):
    sc = SimpleNamespace(
        uid="admin", store=_FakeStore(sessions),
        lore=SimpleNamespace(get_character=lambda cid: None),
    )
    return SimpleNamespace(
        _scopes={"admin": sc}, config=AppConfig(),
        data_dir=tmp_path,
        user_store=SimpleNamespace(get_by_id=lambda uid: None),
    )


async def _ok_fire(state, uid, sid, **kw):
    _ok_fire.calls.append(kw)
    return {"ok": True}


def _install_fire(ticker):
    _ok_fire.calls = []
    ticker._fire = _ok_fire
    return _ok_fire.calls


# ============ 1. clock：显式时区与 TZ 无关 ============

def test_get_zone_fallback_and_fixed_offset():
    # 无 tzdata 环境也走固定偏移：epoch 0 在上海是 1970-01-01 08:00
    z = clock.get_zone("Asia/Shanghai")
    dt = datetime(1970, 1, 1, tzinfo=z)
    assert dt.timestamp() == -8 * 3600
    # 未登记时区退回 UTC，绝不读机器本地时区
    unknown = clock.get_zone("Antarctica/South_Pole")
    assert datetime(1970, 1, 1, tzinfo=unknown).timestamp() == 0


def test_parse_due_at_variants():
    assert clock.parse_due_at(1234567890, NOW, "Asia/Shanghai") == 1234567890.0
    assert clock.parse_due_at("1234567890", NOW, "Asia/Shanghai") == 1234567890.0
    assert clock.parse_due_at(0, NOW, "Asia/Shanghai") is None
    assert clock.parse_due_at("胡说", NOW, "Asia/Shanghai") is None
    got = clock.parse_due_at("2099-01-02 09:30", NOW, "Asia/Shanghai")
    assert clock.local_dt(got, "Asia/Shanghai").strftime("%Y-%m-%d %H:%M") == \
        "2099-01-02 09:30"
    # 裸 HH:MM：今天该时刻已过则滚到明天
    local = clock.local_dt(NOW, "Asia/Shanghai")
    past_hm = "%02d:%02d" % ((local.hour - 1) % 24, local.minute)
    got2 = clock.parse_due_at(past_hm, NOW, "Asia/Shanghai")
    assert got2 > NOW
    assert clock.local_dt(got2, "Asia/Shanghai").strftime("%H:%M") == past_hm


def test_quiet_hours_regular_and_cross_midnight():
    at_ten = _epoch(2026, 11, 13, 10)
    assert clock.in_quiet_hours(at_ten, "09:00-11:00", "Asia/Shanghai")
    assert not clock.in_quiet_hours(at_ten, "11:00-18:00", "Asia/Shanghai")
    at_two = _epoch(2026, 11, 13, 2)
    assert clock.in_quiet_hours(at_two, "23:00-07:00", "Asia/Shanghai")
    at_noon = _epoch(2026, 11, 13, 12)
    assert not clock.in_quiet_hours(at_noon, "23:00-07:00", "Asia/Shanghai")
    # 非法配置不拦人
    assert not clock.in_quiet_hours(at_ten, "garbage", "Asia/Shanghai")


def test_format_local_and_workday():
    text = clock.format_local(NOW, "Asia/Shanghai")
    assert len(text) == 16 and text[4] == "-"
    assert clock.is_workday(NOW, "Asia/Shanghai") is True
    assert clock.is_workday(SUNDAY_NOON, "Asia/Shanghai") is False


# ============ 2. 心情：纯数学 ============

def test_decay_half_life_anchors():
    # 主心情 3 小时半衰期向基线回归：恰好一个半衰期，差距减半
    assert mood_mod.decay(90, NOW, NOW + 3 * 3600, mood_mod.MOOD_HALF_LIFE, 70) == \
        pytest.approx(80.0)
    # 七绪 90 分钟半衰期向 0
    assert mood_mod.decay(10, NOW, NOW + 90 * 60, mood_mod.DIM_HALF_LIFE, 0.0) == \
        pytest.approx(5.0)


def test_clamp_delta_source_caps():
    assert mood_mod.clamp_delta(50, "human") == 10
    assert mood_mod.clamp_delta(-50, "human") == -10
    assert mood_mod.clamp_delta(50, "talk") == 5
    assert mood_mod.clamp_delta(50, "work") == 3
    assert mood_mod.clamp_delta(50, "greet") == 5
    # 未登记来源按最严档 ±3
    assert mood_mod.clamp_delta(50, "mystery") == 3


def test_decayed_dims_erases_below_half_and_unknown():
    out = mood_mod.decayed_dims({"开心": 0.4, "难过": 10, "外星绪": 8}, NOW, NOW)
    assert "开心" not in out and "外星绪" not in out
    # 3 个半衰期（270 分钟）后 10 → 1.25，仍保留
    later = mood_mod.decayed_dims({"难过": 10}, NOW, NOW + 270 * 60)
    assert later.get("难过") == pytest.approx(1.25)
    # 5 个半衰期（450 分钟）后 → 0.3125，绝对值不足 0.5 被抹除
    gone = mood_mod.decayed_dims({"难过": 10}, NOW, NOW + 450 * 60)
    assert "难过" not in gone


def test_gate_announce_all_conditions():
    s = _ms(delta_threshold=5, min_confidence=60, daily_cap=6)
    assert mood_mod.gate_announce(delta=8, confidence=80, reason="r",
                                  today_count=0, settings=s)
    assert not mood_mod.gate_announce(delta=4, confidence=80, reason="r",
                                      today_count=0, settings=s)
    assert not mood_mod.gate_announce(delta=8, confidence=59, reason="r",
                                      today_count=0, settings=s)
    assert not mood_mod.gate_announce(delta=8, confidence=80, reason=" ",
                                      today_count=0, settings=s)
    assert not mood_mod.gate_announce(delta=8, confidence=80, reason="r",
                                      today_count=6, settings=s)


def test_parse_judgement_normalizes_garbage():
    j = mood_mod.parse_judgement('{"delta": "x", "dims": {"开心": 3, "坏维度": 9},'
                                 ' "confidence": 150, "reason": "r"}')
    assert j["delta"] == 0.0 and j["confidence"] == 100
    assert set(j["dims"].keys()) == {"开心"}
    assert mood_mod.parse_judgement("not json")["delta"] == 0.0


def test_judge_mood_swallows_provider_failure():
    class Boom:
        async def stream_chat(self, *a, **k):
            raise RuntimeError("network down")
            yield  # pragma: no cover

    out = _run(mood_mod.judge_mood(Boom(), "对话", {"mood": 70, "dims": {}}))
    assert out["delta"] == 0.0 and out["confidence"] == 0


# ============ 3. 心情：DAL 状态机 ============

def test_mood_record_view_decay_and_timeline(tmp_path):
    store = mood_mod.MoodStore(tmp_path, _ms(), "Asia/Shanghai")
    r = store.record("admin", "s1", "c1", delta=8, dims={"开心": 6},
                     reason="被夸了", confidence=80, source="human", now=NOW)
    assert r["announce"] == "pending" and r["mood"] == 78
    view = store.view("admin", "s1", "c1", now=NOW)
    assert view["mood"] == 78 and view["dims"]["开心"] == 6
    # 读时衰减：3 小时后心情向 70 回归一半 → 74
    later = store.view("admin", "s1", "c1", now=NOW + 3 * 3600)
    assert later["mood"] == pytest.approx(74.0)
    tl = store.timeline("admin", "s1", "c1")
    assert len(tl) == 1 and tl[0]["reason"] == "被夸了"


def test_mood_announce_consume_state_machine(tmp_path):
    store = mood_mod.MoodStore(tmp_path, _ms(daily_cap=1), "Asia/Shanghai")
    store.record("admin", "s1", "c1", delta=8, dims={}, reason="r",
                 confidence=80, source="human", now=NOW)
    assert store.view("admin", "s1", "c1", NOW)["announce"] == "pending"
    # ChatRun 成功后才消费：pending → injected
    assert store.consume_pending("admin", "s1", "c1", NOW) is True
    assert store.view("admin", "s1", "c1", NOW)["announce"] == "injected"
    # 无 pending 可消费 → False（失败轮不调用消费，pending 保留即由此语义保证）
    assert store.consume_pending("admin", "s1", "c1", NOW) is False
    # 当日宣布达上限：新记录裁决失败，UPDATE 把 announce 清空（不再 pending）
    store.record("admin", "s1", "c1", delta=8, dims={}, reason="r2",
                 confidence=80, source="human", now=NOW + 60)
    assert store.view("admin", "s1", "c1", NOW + 60)["announce"] == ""
    # 计数已落，后续整日都不会再宣布
    assert store.consume_pending("admin", "s1", "c1", NOW + 120) is False


def test_mood_clear_wipes_state_and_timeline(tmp_path):
    store = mood_mod.MoodStore(tmp_path, _ms(), "Asia/Shanghai")
    store.record("admin", "s1", "c1", delta=8, dims={"开心": 6}, reason="r",
                 confidence=80, source="human", now=NOW)
    store.clear("admin", "s1", "c1")
    view = store.view("admin", "s1", "c1", NOW)
    assert view["row_id"] == 0 and view["mood"] == 70
    assert store.timeline("admin", "s1", "c1") == []


# ============ 4. 自我记忆 ============

def test_self_memory_quota_bills_create_not_update(tmp_path):
    store = SelfMemoryStore(tmp_path, _ss(daily_quota=2), "Asia/Shanghai")
    _r1, created1 = store.put("admin", "我喜欢拿铁", ["咖啡"], 0.6, now=NOW)
    assert created1 and store.today_used("admin", NOW) == 1
    _r2, created2 = store.put("admin", "我常去城北的店", [], 0.4, now=NOW)
    assert created2 and store.today_used("admin", NOW) == 2
    with pytest.raises(QuotaExceeded):
        store.put("admin", "第三条新事实", [], 0.5, now=NOW)
    # 同事实合并更新不占配额
    _r3, created3 = store.put("admin", "我喜欢拿铁", ["咖啡", "日常"], 0.8, now=NOW)
    assert created3 is False and store.today_used("admin", NOW) == 2


def test_self_memory_recall_ranking_and_traces(tmp_path):
    store = SelfMemoryStore(tmp_path, _ss(recall_topk=5), "Asia/Shanghai")
    store.put("admin", "我最喜欢的咖啡是拿铁", ["咖啡"], 0.9, now=NOW)
    store.put("admin", "我周末习惯去爬山", ["运动"], 0.5,
              now=NOW - 30 * 86400)
    hits = store.recall("admin", "拿铁咖啡", now=NOW)
    assert hits and "拿铁" in hits[0]["statement"]
    # 返回的是 UPDATE 前的行快照，计数需重新查库确认
    fresh = next(r for r in store.list_all("admin")
                 if "拿铁" in r["statement"])
    assert fresh["access_count"] == 1
    traces = store.traces("admin")
    assert any(t["kind"] == "recall" for t in traces)
    # 审计流水绝不进模型上下文：渲染块只含陈述
    block = store.render_block("admin", "拿铁", now=NOW)
    assert "recall" not in block and "拿铁" in block


def test_self_memory_render_block_respects_budget(tmp_path):
    store = SelfMemoryStore(tmp_path, _ss(recall_budget=60), "Asia/Shanghai")
    for i in range(5):
        store.put("admin", "第%d条很长的事实陈述内容内容内容内容内容" % i,
                  [], 0.5, now=NOW)
    block = store.render_block("admin", "事实陈述内容", now=NOW)
    assert block.startswith("【我的自我记忆】")
    assert len(block) <= 60 + 40  # 预算 + 标题与最长一行余量


def test_self_memory_supersede_and_delete(tmp_path):
    store = SelfMemoryStore(tmp_path, _ss(), "Asia/Shanghai")
    old, _ = store.put("admin", "我住在旧房子", [], 0.5, now=NOW)
    new = store.supersede("admin", old["id"], "我搬进了新家", now=NOW)
    active = store.list_all("admin")
    assert len(active) == 1 and active[0]["id"] == new["id"]
    both = store.list_all("admin", include_superseded=True)
    assert len(both) == 2
    traces = store.traces("admin")
    assert any(t["kind"] == "supersede" for t in traces)
    assert store.delete("admin", new["id"]) is True
    assert store.delete("admin", 9999) is False
    with pytest.raises(ValueError):
        store.supersede("admin", 9999, "不存在的旧事实")


# ============ 5. 主动行为：纯裁决 ============

def test_cold_multiplier_tiers():
    assert cold_multiplier(10) == 0.15
    assert cold_multiplier(30) == 1.0
    assert cold_multiplier(400) == 1.5


def test_peak_window_weekday_and_weekend():
    assert is_peak_window(NOW, "Asia/Shanghai") is True       # 周五 10:00
    assert is_peak_window(SUNDAY_NOON, "Asia/Shanghai") is False
    friday_eight = _epoch(2026, 11, 13, 8)
    assert is_peak_window(friday_eight, "Asia/Shanghai") is False


def test_speakup_probability_peak_valley_bonus_cap():
    s = _ps(speakup_peak_percent=20, speakup_valley_percent=60)
    # 峰段、30 分钟冷场（乘子 1.0）、无连丢 → 20%
    assert speakup_probability(s, NOW, "Asia/Shanghai", 30, 0) == 20.0
    # 连丢 3 次 +30%
    assert speakup_probability(s, NOW, "Asia/Shanghai", 30, 3) == 50.0
    # 低谷 × 冷场 1.5 = 90%，补偿封顶 +40 → 总封顶 100
    p = speakup_probability(s, SUNDAY_NOON, "Asia/Shanghai", 400, 5)
    assert p == 100.0


def test_roll_hit_and_wake_guarantee():
    assert roll_hit(10, rng=lambda: 0.05) is True
    assert roll_hit(10, rng=lambda: 0.5) is False
    s = _ps(wake_prob_percent=10, wake_every_n=3)
    # 未到保底次数，大 rng 不命中，streak +1
    assert wake_should_fire(s, 0, rng=lambda: 0.99) is False
    # 连续未中达阈值：无论 rng 保底唤醒
    assert wake_should_fire(s, 3, rng=lambda: 0.99) is True


def test_cooldown_helper():
    assert in_cooldown(NOW - 60, NOW, 20) is True
    assert in_cooldown(NOW - 21 * 60, NOW, 20) is False
    assert in_cooldown(0, NOW, 20) is False


# ============ 6. ProactorStore + ProactorTicker ============

def test_proactor_store_optin_touch_and_state_allowlist(tmp_path):
    store = ProactorStore(tmp_path, _ps(), "Asia/Shanghai")
    # 未 opt-in 时 touch 是空转
    store.touch_human("admin", "s1", now=NOW)
    assert store.get_state("admin", "s1") is None
    store.set_enabled("admin", "s1", True, now=NOW)
    store.touch_human("admin", "s1", now=NOW + 60)
    row = store.get_state("admin", "s1")
    assert row["enabled"] == 1 and row["last_human_at"] == NOW + 60
    store.update_state("admin", "s1", wake_miss_streak=4, evil_field=1)
    assert store.get_state("admin", "s1")["wake_miss_streak"] == 4
    assert len(store.list_enabled("admin")) == 1
    store.set_enabled("admin", "s1", False, now=NOW)
    assert store.list_enabled("admin") == []


def test_poll_crud_clamps_and_cap(tmp_path):
    s = _ps(polls_max_per_session=2)
    store = ProactorStore(tmp_path, s, "Asia/Shanghai")
    p1 = store.add_poll("admin", "s1", "查邮件", 150, 0, 0.1, now=NOW)
    assert p1["probability"] == 100 and p1["wake_every_n"] == 1 \
        and p1["active_hours"] == 0.5
    store.add_poll("admin", "s1", "看天气", 10, 20, 12, now=NOW)
    with pytest.raises(ValueError):
        store.add_poll("admin", "s1", "超限", 10, 20, 12, now=NOW)
    assert store.update_poll("admin", p1["id"], enabled=0) is True
    assert store.update_poll("admin", 9999, enabled=0) is False
    assert len(store.list_polls("admin", "s1")) == 2
    assert store.delete_poll("admin", p1["id"]) is True


def test_ticker_disabled_and_throttle(tmp_path):
    settings = _ps(enabled=False)
    store = ProactorStore(tmp_path, settings, "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = ProactorTicker(state, store, rng=lambda: 0.0,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    _run(ticker.initialize())
    _run(ticker.tick())
    assert calls == []
    # 启用后节流：连续两个 tick 只跑一次
    settings.enabled = True
    store.set_enabled("admin", "s1", True, now=NOW)
    store.touch_human("admin", "s1", now=NOW)
    _run(ticker.tick())  # 执行
    n1 = len(calls)
    _run(ticker.tick())  # 被 wake_check_minutes 节流
    assert len(calls) == n1


def test_ticker_skips_without_human_and_beyond_active_gate(tmp_path):
    store = ProactorStore(tmp_path, _ps(active_hours=12), "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object(), "s2": object()})
    ticker = ProactorTicker(state, store, rng=lambda: 0.0,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    # s1：opt-in 但从无真人消息 → 不掷骰
    store.set_enabled("admin", "s1", True, now=NOW - 3600)
    # s2：真人消息在 13 小时前，超过 12 小时活跃门
    store.set_enabled("admin", "s2", True, now=NOW - 13 * 3600)
    store.touch_human("admin", "s2", now=NOW - 13 * 3600)
    _run(ticker.tick())
    assert calls == []
    assert store.get_state("admin", "s1")["wake_miss_streak"] == 0


def test_ticker_session_gone_disables_optin(tmp_path):
    store = ProactorStore(tmp_path, _ps(), "Asia/Shanghai")
    state = _ticker_state(tmp_path, {})  # 会话已不在
    ticker = ProactorTicker(state, store, now_provider=lambda: NOW)
    _install_fire(ticker)
    store.set_enabled("admin", "ghost", True, now=NOW)
    store.touch_human("admin", "ghost", now=NOW)
    _run(ticker.tick())
    assert store.list_enabled("admin") == []
    assert store.get_state("admin", "ghost")["enabled"] == 0


def test_ticker_wake_guarantee_resets_streak(tmp_path):
    store = ProactorStore(tmp_path,
                          _ps(wake_prob_percent=10, wake_every_n=3,
                              quiet_hours="01:00-08:00",
                              self_cooldown_minutes=20),
                          "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = ProactorTicker(state, store, rng=lambda: 0.99,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    store.set_enabled("admin", "s1", True, now=NOW)
    store.touch_human("admin", "s1", now=NOW)
    store.update_state("admin", "s1", wake_miss_streak=3)
    _run(ticker.tick())
    kinds = [c["kind"] for c in calls]
    assert "wake-notice" in kinds
    # 保底唤醒成功后 streak 归零
    assert store.get_state("admin", "s1")["wake_miss_streak"] == 0


def test_ticker_quiet_hours_blocks_speakup_not_wake(tmp_path):
    store = ProactorStore(tmp_path,
                          _ps(wake_prob_percent=100, wake_every_n=99,
                              quiet_hours="09:00-11:00"),
                          "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = ProactorTicker(state, store, rng=lambda: 0.0,
                            now_provider=lambda: NOW)  # NOW=周五 10:00
    calls = _install_fire(ticker)
    store.set_enabled("admin", "s1", True, now=NOW)
    store.touch_human("admin", "s1", now=NOW)
    _run(ticker.tick())
    kinds = [c["kind"] for c in calls]
    assert "speakup-notice" not in kinds
    # rng=0 时唤醒骰命中（静默时段只挡搭话，不挡唤醒）
    assert "wake-notice" in kinds


def test_ticker_speakup_cooldown_blocks(tmp_path):
    store = ProactorStore(tmp_path,
                          _ps(wake_prob_percent=0, wake_every_n=99,
                              self_cooldown_minutes=20,
                              quiet_hours="01:00-08:00"),
                          "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = ProactorTicker(state, store, rng=lambda: 0.0,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    store.set_enabled("admin", "s1", True, now=NOW)
    store.touch_human("admin", "s1", now=NOW)
    store.update_state("admin", "s1", last_speakup_at=NOW - 60)
    _run(ticker.tick())
    assert "speakup-notice" not in [c["kind"] for c in calls]


def test_ticker_polls_at_most_one_fires_per_tick(tmp_path):
    store = ProactorStore(tmp_path, _ps(wake_prob_percent=0,
                                        wake_every_n=99,
                                        quiet_hours="01:00-08:00"),
                          "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = ProactorTicker(state, store, rng=lambda: 0.99,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    store.set_enabled("admin", "s1", True, now=NOW)
    store.touch_human("admin", "s1", now=NOW)
    # wake_every_n=1：两条轮询同 tick 都到期，但只允许一条开口
    store.add_poll("admin", "s1", "甲", 0, 1, 12, now=NOW)
    store.add_poll("admin", "s1", "乙", 0, 1, 12, now=NOW)
    _run(ticker.tick())
    poll_calls = [c for c in calls if c["kind"] == "poll-notice"]
    assert len(poll_calls) == 1
    rows = store.list_polls("admin", "s1")
    fired = [p for p in rows if p["ticks_since_fire"] == 0]
    assert len(fired) == 1


# ============ 7. 定时作业 ============

def test_compute_next_run_strict_future_semantics():
    # once：时刻已过 → None（不补跑）
    assert compute_next_run("once", "08:00", -1, NOW, "Asia/Shanghai") is None
    once_future = compute_next_run("once", "23:59", -1, NOW, "Asia/Shanghai")
    assert once_future is not None and once_future > NOW
    # daily：今天 09:00 已过 → 明天 09:00
    nxt = compute_next_run("daily", "09:00", -1, NOW, "Asia/Shanghai")
    assert nxt > NOW
    assert clock.local_dt(nxt, "Asia/Shanghai").strftime("%H:%M") == "09:00"
    assert clock.local_dt(nxt, "Asia/Shanghai").date() == \
        (clock.local_dt(NOW, "Asia/Shanghai") + timedelta(days=1)).date()
    # weekly：周一（weekday=0）相对周五 → +3 天
    mon = compute_next_run("weekly", "09:00", 0, NOW, "Asia/Shanghai")
    assert clock.local_dt(mon, "Asia/Shanghai").weekday() == 0
    assert mon > NOW


def test_job_store_create_validation(tmp_path):
    jobs = JobStore(tmp_path, "Asia/Shanghai")
    with pytest.raises(ValueError):
        jobs.create("admin", "s1", "bad", "09:00", "b", now=NOW)
    with pytest.raises(ValueError):
        jobs.create("admin", "s1", "once", "9点", "b", now=NOW)
    with pytest.raises(ValueError):
        jobs.create("admin", "s1", "once", "08:00", "  ", now=NOW)
    with pytest.raises(ValueError):
        jobs.create("admin", "s1", "weekly", "09:00", "b", weekday=9, now=NOW)
    row = jobs.create("admin", "s1", "daily", "09:00", "晨报", now=NOW)
    assert row["enabled"] == 1 and row["next_run_at"] > NOW


def test_job_mark_result_done_retry_and_no_backfill(tmp_path):
    jobs = JobStore(tmp_path, "Asia/Shanghai")
    once = jobs.create("admin", "s1", "once", "23:59", "单次", now=NOW)
    jobs.mark_result("admin", once["id"], once, True, NOW)
    got = jobs.list("admin")[0]
    assert got["enabled"] == 0 and got["last_status"] == "done"
    # 失败：5 分钟后重试
    daily = jobs.create("admin", "s1", "daily", "09:00", "日报", now=NOW)
    jobs.mark_result("admin", daily["id"], daily, False, NOW)
    got2 = next(j for j in jobs.list("admin") if j["id"] == daily["id"])
    assert got2["last_status"] == "error"
    assert abs(got2["next_run_at"] - (NOW + RETRY_SECONDS)) < 1
    # 宕机后扫描到残留到期周期作业：成功递推必须严格未来，不补跑
    jobs.mark_result("admin", daily["id"], got2, True, NOW + 600)
    got3 = next(j for j in jobs.list("admin") if j["id"] == daily["id"])
    assert got3["next_run_at"] > NOW + 600


def test_job_ticker_locked_leaves_due_and_gone_disables(tmp_path):
    jobs = JobStore(tmp_path, "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = JobTicker(state, jobs, now_provider=lambda: NOW)
    j1 = jobs.create("admin", "s1", "daily", "09:00", "甲", now=NOW - 7200)
    j2 = jobs.create("admin", "s1", "daily", "10:00", "乙", now=NOW - 7200)
    ghost = jobs.create("admin", "gone", "daily", "10:00", "丙", now=NOW - 7200)

    async def fake_fire(state, uid, sid, **kw):
        if kw["brief"].endswith("甲"):
            return {"ok": False, "reason": "locked"}
        return {"ok": True}

    ticker._fire = fake_fire
    _run(ticker.tick())
    rows = {j["id"]: j for j in jobs.list("admin")}
    # 锁竞争：保持 due，状态未改
    assert rows[j1["id"]]["last_status"] == ""
    assert jobs.due("admin", NOW + 1) and \
        any(j["id"] == j1["id"] for j in jobs.due("admin", NOW + 1))
    # 成功的作业已递推到未来，不再 due
    assert rows[j2["id"]]["last_status"] == "done"
    # 会话删除：停用而非补跑
    assert rows[ghost["id"]]["enabled"] == 0


def test_job_ticker_failure_marks_retry(tmp_path):
    jobs = JobStore(tmp_path, "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = JobTicker(state, jobs, now_provider=lambda: NOW)
    j1 = jobs.create("admin", "s1", "daily", "09:00", "甲", now=NOW - 7200)

    async def boom(state, uid, sid, **kw):
        return {"ok": False, "reason": "no_provider"}

    ticker._fire = boom
    _run(ticker.tick())
    row = next(j for j in jobs.list("admin") if j["id"] == j1["id"])
    assert row["last_status"] == "error"
    assert abs(row["next_run_at"] - (NOW + RETRY_SECONDS)) < 1


# ============ 8. 承诺与提醒 ============

def test_commitment_lifecycle(tmp_path):
    cs = CommitmentStore(tmp_path)
    with pytest.raises(ValueError):
        cs.add("admin", "s1", "  ", NOW + 100, now=NOW)
    c = cs.add("admin", "s1", "交报告", NOW - 10,
               owner_name="小林", now=NOW - 100)
    assert cs.due("admin", NOW)[0]["id"] == c["id"]
    assert cs.due("admin", NOW - 50) == []
    assert cs.close("admin", c["id"], now=NOW + 200) is True
    assert cs.list("admin", status="closed")[0]["status"] == "closed"
    c2 = cs.add("admin", "s1", "另一件", NOW + 100, now=NOW)
    assert cs.cancel("admin", c2["id"]) is True
    assert cs.list("admin", status="cancelled")[0]["reminded"] == 1
    assert cs.cancel("admin", 9999) is False


def test_reminder_ticker_two_strategies(tmp_path):
    cs = CommitmentStore(tmp_path)
    ps = ProactorStore(tmp_path, _ps(), "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ticker = ReminderTicker(state, cs, ps, check_seconds=10,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    # 承诺 A：会话存在但未 opt-in → 不提醒、不置 reminded
    a = cs.add("admin", "s1", "未开关的承诺", NOW - 10, now=NOW - 100)
    # 承诺 B：会话已删除 → 取消并退出扫描
    b = cs.add("admin", "gone", "幽灵承诺", NOW - 10, now=NOW - 100)
    _run(ticker.tick())
    assert calls == []
    assert cs.due("admin", NOW + 1) and \
        any(x["id"] == a["id"] for x in cs.due("admin", NOW + 1))
    ghost = next(x for x in cs.list("admin") if x["id"] == b["id"])
    assert ghost["status"] == "cancelled" and ghost["reminded"] == 1


def test_reminder_ticker_fire_success_and_retry(tmp_path):
    cs = CommitmentStore(tmp_path)
    ps = ProactorStore(tmp_path, _ps(), "Asia/Shanghai")
    state = _ticker_state(tmp_path, {"s1": object()})
    ps.set_enabled("admin", "s1", True, now=NOW)
    ticker = ReminderTicker(state, cs, ps, check_seconds=10,
                            now_provider=lambda: NOW)
    calls = _install_fire(ticker)
    c_ok = cs.add("admin", "s1", "会成功的提醒", NOW - 10, now=NOW - 100)
    _run(ticker.tick())
    assert len(calls) == 1 and calls[0]["kind"] == "reminder"
    assert cs.due("admin", NOW + 1) == []  # 已置 reminded
    # fire 失败：不置 reminded，下周期重试
    c_bad = cs.add("admin", "s1", "会失败的提醒", NOW - 10, now=NOW - 100)

    async def fail_fire(state, uid, sid, **kw):
        return {"ok": False, "reason": "locked"}

    ticker._fire = fail_fire
    ticker._last_tick = 0
    _run(ticker.tick())
    assert any(x["id"] == c_bad["id"] for x in cs.due("admin", NOW + 1))
    assert c_ok["id"] not in [x["id"] for x in cs.due("admin", NOW + 1)]


# ============ 9. LIVE_CTX ============

def test_build_live_block_sections_and_limit(tmp_path):
    block = live_ctx.build_live_block(
        now=NOW, tzname="Asia/Shanghai",
        mood_view={"mood": 72.6, "dims": {"开心": 3.2}},
        self_memory_block="【我的自我记忆】\n- 我喜欢拿铁",
        commitments=[
            {"id": 1, "due_at": NOW - 10, "content": "过期事项"},
            {"id": 2, "due_at": NOW + 100, "content": "未来事项"},
        ] + [{"id": i, "due_at": NOW + 100, "content": "x"}
             for i in range(20)],
        announce_text="（内心独白·只宣布一次）今天很开心",
    )
    assert "【现在时刻】" in block and "Asia/Shanghai" in block
    assert "内心独白·只宣布一次" in block
    assert "【当前心情】73/100" in block and "开心+3" in block
    assert "我喜欢拿铁" in block
    assert "【未结约定】" in block and "（已到点）" in block
    assert block.count("\n- #") <= 10  # 约定上限 10 条


def test_render_for_turn_gates_and_degrades(tmp_path):
    config = AppConfig()
    config.mood.enabled = True
    config.proactor.enabled = True
    mood_store = mood_mod.MoodStore(tmp_path, config.mood, "Asia/Shanghai")
    sm_store = SelfMemoryStore(tmp_path, config.self_memory, "Asia/Shanghai")
    c_store = CommitmentStore(tmp_path)
    mood_store.record("admin", "s1", "c1", delta=8, dims={"开心": 5},
                      reason="好事", confidence=80, source="human", now=NOW)
    sm_store.put("admin", "我最爱喝拿铁咖啡", ["咖啡"], 0.8, now=NOW)
    c_store.add("admin", "s1", "今晚交稿", NOW + 3600, now=NOW)
    state = SimpleNamespace(
        config=config, mood_store=mood_store,
        self_memory_store=sm_store, commitment_store=c_store,
    )
    sc = SimpleNamespace(uid="admin")
    block = _run(live_ctx.render_for_turn(
        state, sc, session_id="s1", character_id="c1",
        query="帮我点杯拿铁", now=NOW))
    assert "【当前心情】" in block
    assert "内心独白·只宣布一次" in block
    assert "拿铁" in block and "【未结约定】" in block
    assert live_ctx.had_pending_announce(block)
    # 空 query 不出自我记忆段；无 character_id 不出心情段
    block2 = _run(live_ctx.render_for_turn(
        state, sc, session_id="s1", character_id="", query="", now=NOW))
    assert "【当前心情】" not in block2 and "我的自我记忆" not in block2
    assert "【现在时刻】" in block2  # 时钟行始终保留


def test_had_pending_announce_marker():
    assert live_ctx.had_pending_announce("xxx（内心独白·只宣布一次）yyy")
    assert not live_ctx.had_pending_announce("普通上下文")
    assert not live_ctx.had_pending_announce("")


# ============ 10. 工具层 ============

def test_remember_and_recall_self_tools(tmp_path):
    store = SelfMemoryStore(tmp_path, _ss(daily_quota=1), "Asia/Shanghai")
    ctx = ToolContext(uid="admin", session_id="s1",
                      services={"self_memory_store": store})
    res = _run(RememberSelfTool().run(
        {"statement": "我喜欢雨天", "tags": ["天气"], "importance": 0.7}, ctx))
    assert res.ok and "#1" in res.output
    # 配额耗尽 → ok=False 而不是抛异常
    res2 = _run(RememberSelfTool().run({"statement": "第二条"}, ctx))
    assert not res2.ok and "配额" in res2.output
    # 服务缺失
    res3 = _run(RememberSelfTool().run({"statement": "x"}, ToolContext()))
    assert not res3.ok
    recall = _run(RecallSelfTool().run({"keyword": "雨天"}, ctx))
    assert recall.ok and "雨天" in recall.output
    assert not _run(RecallSelfTool().run({"keyword": " "}, ctx)).ok


def test_commitment_tools(tmp_path):
    cs = CommitmentStore(tmp_path)
    ctx = ToolContext(uid="admin", session_id="s1", services={
        "commitment_store": cs, "tzname": "Asia/Shanghai",
        "member": SimpleNamespace(name="小林"),
    })
    res = _run(RememberCommitmentTool().run(
        {"content": "交周报", "due_at": NOW + 3600}, ctx))
    assert res.ok and "#1" in res.output
    bad = _run(RememberCommitmentTool().run(
        {"content": "x", "due_at": "看不懂"}, ctx))
    assert not bad.ok
    cid = cs.list("admin")[0]["id"]
    ok = _run(CloseCommitmentTool().run({"commitment_id": cid}, ctx))
    assert ok.ok
    # 关闭是幂等 UPDATE：同 id 再关仍 ok；不存在的 id 才失败
    again = _run(CloseCommitmentTool().run({"commitment_id": cid}, ctx))
    assert again.ok
    missing = _run(CloseCommitmentTool().run({"commitment_id": 9999}, ctx))
    assert not missing.ok
    # cancel 路径 + 缺服务
    c2 = cs.add("admin", "s1", "再一件", NOW + 3600, now=NOW)
    cancel = _run(CloseCommitmentTool().run(
        {"commitment_id": c2["id"], "cancel": True}, ctx))
    assert cancel.ok
    assert not _run(CloseCommitmentTool().run({"commitment_id": 1},
                                              ToolContext())).ok


def test_feature_prefs_proactor_categories(tmp_path):
    cfg = AppConfig()
    reg = ToolRegistry()
    for cls in (RememberSelfTool, RecallSelfTool,
                RememberCommitmentTool, CloseCommitmentTool):
        reg.register(cls())
    # 默认：self_memory 全局开、proactor 关
    names = allowed_tool_names(reg, cfg, None)
    assert "remember_self" in names and "recall_self" in names
    assert "remember_commitment" not in names
    cfg.proactor.enabled = True
    names2 = allowed_tool_names(reg, cfg, None)
    assert {"remember_commitment", "close_commitment"} <= names2
    cfg.self_memory.enabled = False
    names3 = allowed_tool_names(reg, cfg, None)
    assert "remember_self" not in names3


# ============ 11. 接线函数 integrate ============

def test_transcript_from_history_filters():
    history = [
        Message(role="user", content="你好"),
        Message(role="assistant", content="甲发言", name="甲", actor_id="m1"),
        Message(role="assistant", content="乙发言", name="乙", actor_id="m2"),
        Message(role="user", content="隐藏消息", hidden=True),
        Message(role="system", content="系统噪声"),
        Message(role="assistant", content="甲又说", name="甲", actor_id="m1"),
    ]
    text = integrate.transcript_from_history(history)
    assert "我: 你好" in text and "甲: 甲发言" in text and "乙: 乙发言" in text
    assert "隐藏消息" not in text and "系统噪声" not in text
    # actor 过滤 + head 截断
    only_m1 = integrate.transcript_from_history(history, head=1, actor_id="m1")
    assert "乙发言" not in only_m1 and "甲又说" in only_m1
    # 尾部字符上限
    capped = integrate.transcript_from_history(history, limit_chars=5)
    assert len(capped) <= 5


def test_extract_self_after_turn_writes_store(tmp_path, monkeypatch):
    store = SelfMemoryStore(tmp_path, _ss(), "Asia/Shanghai")
    state = SimpleNamespace(
        config=SimpleNamespace(self_memory=SelfMemorySettings(enabled=True)),
        self_memory_store=store,
        get_provider=lambda: object(),
    )

    async def fake_complete(provider, messages, system_prompt=""):
        assert "自我记忆" in system_prompt
        return '[{"statement": "我答应小林周末看电影", "tags": ["承诺"],' \
               ' "importance": 0.9}]'

    monkeypatch.setattr(integrate, "complete", fake_complete)
    _run(integrate.extract_self_after_turn(
        state, SimpleNamespace(uid="admin"), "我: 周末一起看电影吧"))
    rows = store.list_all("admin")
    assert len(rows) == 1 and "看电影" in rows[0]["statement"]
    # 关闭开关后直接返回
    state.config.self_memory.enabled = False
    _run(integrate.extract_self_after_turn(
        state, SimpleNamespace(uid="admin"), "对话"))
    assert len(store.list_all("admin")) == 1


def test_handle_post_turn_mood_consumes_then_records(tmp_path, monkeypatch):
    settings = _ms(enabled=True)
    store = mood_mod.MoodStore(tmp_path, settings, "Asia/Shanghai")
    store.record("admin", "s1", "c1", delta=8, dims={}, reason="旧好事",
                 confidence=80, source="human", now=NOW - 60)
    assert store.view("admin", "s1", "c1", NOW)["announce"] == "pending"

    scripted = [
        # 第一次：零增量 → 只消费旧 pending，不落新记录
        {"delta": 0, "dims": {}, "reason": "", "confidence": 0},
        # 第二次：低于宣布门槛的小波动 → 落记录但不产生新 pending
        {"delta": -2, "dims": {}, "reason": "被批评", "confidence": 75},
    ]

    async def fake_judge(provider, transcript, current):
        return scripted.pop(0)

    monkeypatch.setattr(mood_mod, "judge_mood", fake_judge)
    provider = SimpleNamespace(cfg=SimpleNamespace(id="p", model="m"))
    state = SimpleNamespace(
        config=SimpleNamespace(mood=settings),
        mood_store=store,
        get_provider=lambda pid=None: provider,
        _judge_providers={},
    )
    _run(integrate.handle_post_turn_mood(
        state, SimpleNamespace(uid="admin"), session_id="s1",
        character_id="c1", transcript="对话", provider=provider,
        had_pending=True, now=NOW))
    # 旧 pending 已消费为 injected；零增量不写时间线
    assert store.view("admin", "s1", "c1", NOW)["announce"] == "injected"
    assert len(store.timeline("admin", "s1", "c1")) == 1
    # 第二轮：小波动落锚，裁决未达门槛，announce 被记录覆写为空
    _run(integrate.handle_post_turn_mood(
        state, SimpleNamespace(uid="admin"), session_id="s1",
        character_id="c1", transcript="对话", provider=provider,
        had_pending=False, now=NOW + 60))
    assert store.view("admin", "s1", "c1", NOW + 60)["announce"] == ""
    tl = store.timeline("admin", "s1", "c1")
    assert any(t["reason"] == "被批评" for t in tl)


def test_resolve_judge_provider_routing():
    fallback = SimpleNamespace(cfg=SimpleNamespace(id="main", model="m1"))
    named = SimpleNamespace(cfg=SimpleNamespace(id="judge", model="m2"))
    state = SimpleNamespace(
        get_provider=lambda pid: named if pid == "j1" else None,
        _judge_providers={},
    )
    cfg_empty = SimpleNamespace(judge_provider_id="", judge_model="")
    assert integrate.resolve_judge_provider(state, fallback, cfg_empty) is fallback
    cfg_named = SimpleNamespace(judge_provider_id="j1", judge_model="")
    assert integrate.resolve_judge_provider(state, fallback, cfg_named) is named
    # 指定 provider 不存在 → 回落 fallback
    cfg_miss = SimpleNamespace(judge_provider_id="nope", judge_model="")
    assert integrate.resolve_judge_provider(state, fallback, cfg_miss) is fallback


# ============ 12. HTTP 路由 ============

def _http_env(tmp_path):
    from flyai.api import routes_proactor as rp

    mood_store = mood_mod.MoodStore(tmp_path, MoodSettings(enabled=True),
                                    "Asia/Shanghai")
    sm_store = SelfMemoryStore(tmp_path, SelfMemorySettings(enabled=True),
                               "Asia/Shanghai")
    pro_store = ProactorStore(tmp_path, ProactorSettings(enabled=True),
                              "Asia/Shanghai")
    job_store = JobStore(tmp_path, "Asia/Shanghai")
    c_store = CommitmentStore(tmp_path)
    cfg = SimpleNamespace(
        mood=MoodSettings(enabled=True),
        self_memory=SelfMemorySettings(enabled=True),
        proactor=ProactorSettings(enabled=True),
        scheduler=SimpleNamespace(timezone="Asia/Shanghai"),
    )
    admin_sc = SimpleNamespace(uid="admin",
                               store=_FakeStore({"s1": object()}))
    user_sc = SimpleNamespace(uid="u1",
                              store=_FakeStore({"s1": object()}))
    state = SimpleNamespace(
        config=cfg, mood_store=mood_store, self_memory_store=sm_store,
        proactor_store=pro_store, job_store=job_store,
        commitment_store=c_store, data_dir=tmp_path,
        user_store=SimpleNamespace(get_by_id=lambda uid: None),
        _scopes={"admin": admin_sc, "u1": user_sc},
    )
    state.user = lambda request: state._scopes[request.uid]

    def req(uid):
        return SimpleNamespace(
            uid=uid, app=SimpleNamespace(state=SimpleNamespace(flyai=state)))

    return rp, state, req


def test_http_proactive_enable_requires_feature(tmp_path):
    rp, state, req = _http_env(tmp_path)
    state.config.proactor.enabled = False
    with pytest.raises(Exception) as ei:
        _run(rp.proactive_enable(
            rp.OptInBody(session_id="s1", enabled=True), req("admin")))
    assert ei.value.status_code == 400
    state.config.proactor.enabled = True
    out = _run(rp.proactive_enable(
        rp.OptInBody(session_id="s1", enabled=True), req("admin")))
    assert out["enabled"] is True
    row = state.proactor_store.get_state("admin", "s1")
    assert row["enabled"] == 1 and row["last_human_at"] > 0
    # 普通用户代管他人 → 403
    with pytest.raises(Exception) as ei:
        _run(rp.proactive_enable(
            rp.OptInBody(session_id="s1", enabled=True, uid="admin"),
            req("u1")))
    assert ei.value.status_code == 403
    # 会话不存在 → 404
    with pytest.raises(Exception) as ei:
        _run(rp.proactive_enable(
            rp.OptInBody(session_id="nope", enabled=True), req("admin")))
    assert ei.value.status_code == 404


def test_http_jobs_crud_and_validation(tmp_path):
    rp, state, req = _http_env(tmp_path)
    body = rp.JobBody(session_id="s1", mode="daily", hhmm="09:00",
                      brief="晨报", weekday=-1)
    out = _run(rp.job_add(body, req("admin")))
    jid = out["item"]["id"]
    assert out["item"]["next_run_at"] > NOW  # add 返回原始行
    listed = _run(rp.job_list(req("admin")))
    assert len(listed["items"]) == 1 and listed["timezone"] == "Asia/Shanghai"
    # 本地挂钟字符串只在 list 视图补算
    assert listed["items"][0]["next_run_local"]
    assert _run(rp.job_toggle(jid, rp.ToggleBody(enabled=False),
                              req("admin")))["ok"]
    assert _run(rp.job_delete(jid, req("admin")))["ok"]
    with pytest.raises(Exception) as ei:
        _run(rp.job_delete(jid, req("admin")))
    assert ei.value.status_code == 404
    # 非法作业 → 400
    bad = rp.JobBody(session_id="s1", mode="once", hhmm="00:01",
                     brief="过期", weekday=-1)
    with pytest.raises(Exception) as ei:
        _run(rp.job_add(bad, req("admin")))
    assert ei.value.status_code == 400


def test_http_polls_and_commitments(tmp_path):
    rp, state, req = _http_env(tmp_path)
    poll = _run(rp.poll_add(rp.PollBody(
        session_id="s1", title="查邮件", probability=150,
        wake_every_n=20, active_hours=12), req("admin")))
    assert poll["item"]["probability"] == 100  # 服务端钳制
    pid = poll["item"]["id"]
    assert _run(rp.poll_toggle(pid, rp.ToggleBody(enabled=False),
                               req("admin")))["ok"]
    assert len(_run(rp.poll_list(req("admin"), "s1"))["items"]) == 1
    assert _run(rp.poll_delete(pid, req("admin")))["ok"]
    # 承诺登记 + 关闭/取消
    c = _run(rp.commitment_add(rp.CommitmentBody(
        session_id="s1", content="交报告", due_at=NOW + 3600), req("admin")))
    cid = c["item"]["id"]
    assert _run(rp.commitment_close(
        cid, rp.IdBody(), req("admin")))["ok"]
    c2 = _run(rp.commitment_add(rp.CommitmentBody(
        session_id="s1", content="另一件", due_at=NOW + 7200), req("admin")))
    assert _run(rp.commitment_cancel(
        c2["item"]["id"], rp.IdBody(), req("admin")))["ok"]
    items = _run(rp.commitment_list(req("admin")))["items"]
    assert {x["status"] for x in items} == {"closed", "cancelled"}
    # 无法解析的 due_at → 400
    with pytest.raises(Exception) as ei:
        _run(rp.commitment_add(rp.CommitmentBody(
            session_id="s1", content="x", due_at="瞎写"), req("admin")))
    assert ei.value.status_code == 400


def test_http_self_memory_and_mood(tmp_path):
    rp, state, req = _http_env(tmp_path)
    out = _run(rp.self_memory_add(rp.SelfMemoryBody(
        statement="我喜欢下雨天", tags=["天气"], importance=2), req("admin")))
    assert out["created"] is True and out["item"]["importance"] == 1.0
    mid = out["item"]["id"]
    listed = _run(rp.self_memory_list(req("admin")))
    assert len(listed["items"]) == 1
    traces = _run(rp.self_memory_traces(req("admin")))
    assert "items" in traces
    assert _run(rp.self_memory_delete(mid, req("admin")))["ok"]
    with pytest.raises(Exception) as ei:
        _run(rp.self_memory_delete(mid, req("admin")))
    assert ei.value.status_code == 404
    # 心情：无记录时视图回基线，时间线空，clear 需功能开启
    view = _run(rp.mood_view("s1", "c1", req("admin")))
    assert view["mood"] == 70 and view["row_id"] == 0
    tl = _run(rp.mood_timeline("s1", "c1", req("admin")))
    assert tl["items"] == []
    state.mood_store.record("admin", "s1", "c1", delta=8, dims={},
                            reason="r", confidence=80, source="human", now=NOW)
    assert _run(rp.mood_clear(rp.MoodClearBody(
        session_id="s1", character_id="c1"), req("admin")))["ok"]
    assert _run(rp.mood_timeline("s1", "c1", req("admin")))["items"] == []
    # 功能关闭后 clear → 400
    state.config.mood.enabled = False
    with pytest.raises(Exception) as ei:
        _run(rp.mood_clear(rp.MoodClearBody(
            session_id="s1", character_id="c1"), req("admin")))
    assert ei.value.status_code == 400


# ============ 13. 端到端：定时作业到点产出会话消息 ============

class _StaticProvider:
    cfg = SimpleNamespace(id="p1", model="m1")

    async def stream_chat(self, messages, tool_specs, **kwargs):
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "早安，新的一天开始了")


def test_e2e_scheduled_job_produces_session_message(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s = Session(title="定时会话")
        user = RoomMember(name="你", is_user=True)
        ai = RoomMember(name="小艾", character_id="c1")
        s.members.extend([user, ai])
        s.threads.append(Thread(kind="dm", title="小艾",
                                member_ids=[ai.id]))
        store.save(s)
        char = Character(id="c1", name="小艾")
        lore = SimpleNamespace(
            get_character=lambda cid: char if cid == "c1" else None)
        provider = _StaticProvider()
        state = SimpleNamespace(
            run_manager=ChatRunManager(), data_dir=tmp_path,
            user_store=SimpleNamespace(get_by_id=lambda uid: None),
            config=SimpleNamespace(
                roleplay=RoleplaySettings(format_example="")),
        )
        state.get_provider = lambda pid=None: provider
        sc = SimpleNamespace(uid="admin", store=store, lore=lore)
        state._scopes = {"admin": sc}

        jobs = JobStore(tmp_path, "Asia/Shanghai")
        job = jobs.create("admin", s.id, "once", "23:59", "道早安", now=NOW)
        # 人工置为到期，模拟到点
        conn = open_agent_db(tmp_path, "admin")
        conn.execute("UPDATE scheduled_jobs SET next_run_at=? WHERE id=?",
                     (NOW - 5, job["id"]))
        conn.close()

        ticker = JobTicker(state, jobs, now_provider=lambda: NOW)
        await ticker.tick()
        # fire 异步驱动：等后台 ChatRun 跑完
        runs = list(state.run_manager._runs.values())
        assert runs and runs[0].task is not None
        await runs[0].task

        reloaded = store.get(s.id)
        thread = reloaded.threads[0]
        assistant_msgs = [m for m in thread.messages if m.role == "assistant"]
        assert len(assistant_msgs) == 1
        assert "早安" in assistant_msgs[-1].content
        # 触发通知是 hidden user 消息
        notice = [m for m in thread.messages
                  if m.role == "user" and getattr(m, "hidden", False)]
        assert len(notice) == 1 and "定时任务" in notice[0].content
        # 一次性作业已收尾
        row = next(j for j in jobs.list("admin") if j["id"] == job["id"])
        assert row["enabled"] == 0 and row["last_status"] == "done"

    _run(body())


def test_e2e_silent_turn_leaves_no_history(tmp_path):
    async def body():
        from flyai.proactor import runner

        store = JsonSessionStore(tmp_path / "s")
        s = Session(title="静默会话")
        user = RoomMember(name="你", is_user=True)
        ai = RoomMember(name="小艾", character_id="c1")
        s.members.extend([user, ai])
        s.threads.append(Thread(kind="dm", title="小艾",
                                member_ids=[ai.id]))
        store.save(s)
        char = Character(id="c1", name="小艾")
        lore = SimpleNamespace(
            get_character=lambda cid: char if cid == "c1" else None)

        class SilentProvider:
            cfg = SimpleNamespace(id="p1", model="m1")

            async def stream_chat(self, messages, tool_specs, **kwargs):
                yield StreamEvent(StreamEventType.CONTENT_DELTA,
                                  runner.SILENT_MARKER + " 没什么好说的")

        state = SimpleNamespace(
            run_manager=ChatRunManager(), data_dir=tmp_path,
            user_store=SimpleNamespace(get_by_id=lambda uid: None),
            config=SimpleNamespace(
                roleplay=RoleplaySettings(format_example="")),
        )
        state.get_provider = lambda pid=None: SilentProvider()
        sc = SimpleNamespace(uid="admin", store=store, lore=lore)
        state._scopes = {"admin": sc}

        res = await runner.fire(
            state, "admin", s.id, kind="wake-notice", brief="【系统唤醒】test",
            allow_silent=True, with_history=True)
        assert res["ok"]
        run = state.run_manager.get(res["run_id"], "admin")
        await run.task

        reloaded = store.get(s.id)
        thread = reloaded.threads[0]
        assert not [m for m in thread.messages if m.role == "assistant"]
        # 隐藏通知也被撤下
        assert not [m for m in thread.messages
                    if m.role == "user" and getattr(m, "hidden", False)]

    _run(body())
