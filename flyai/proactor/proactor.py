"""主动行为（P3）：概率裁决纯逻辑 + 会话 opt-in/轮询 DAL + 调度 Ticker。

最需要克制的能力：总闸默认关闭、按会话显式 opt-in、静默时段/自冷却/
[静默] 出口多重约束。所有随机与时钟均可注入，测试不 sleep、不赌运气。
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..agentdb import open_agent_db
from . import clock
from .runner import fire

logger = logging.getLogger("flyai.proactor")

# 工作日峰段（本地挂钟 24h 制，含头不含尾）
PEAK_START_HOUR = 9
PEAK_END_HOUR = 21
MISS_BONUS_PERCENT = 10
MISS_BONUS_CAP = 40


# ---------- 纯裁决逻辑 ----------

def cold_multiplier(idle_minutes: float) -> float:
    """冷场乘子：<20 分钟 0.15，>6 小时 1.5，之间 1.0。"""
    if idle_minutes < 20:
        return 0.15
    if idle_minutes > 6 * 60:
        return 1.5
    return 1.0


def is_peak_window(now: float, tzname: str) -> bool:
    """工作日峰段：周一到周五 9:00-21:00。"""
    if not clock.is_workday(now, tzname):
        return False
    hour = clock.local_dt(now, tzname).hour
    return PEAK_START_HOUR <= hour < PEAK_END_HOUR


def speakup_probability(settings: Any, now: float, tzname: str,
                        idle_minutes: float, miss_streak: int) -> float:
    """峰谷基础概率 × 冷场乘子 + 连没中补偿（每次 +10%，封顶 +40）。"""
    base = settings.speakup_peak_percent if is_peak_window(now, tzname) \
        else settings.speakup_valley_percent
    p = float(base) * cold_multiplier(idle_minutes)
    p += min(MISS_BONUS_CAP, max(0, miss_streak) * MISS_BONUS_PERCENT)
    return min(100.0, p)


def roll_hit(percent: float, rng: Optional[Callable[[], float]] = None) -> bool:
    r = rng() if rng is not None else random.random()
    return r * 100.0 < percent


def wake_should_fire(settings: Any, miss_streak: int,
                     rng: Optional[Callable[[], float]] = None) -> bool:
    """基础唤醒掷骰；连续 wake_every_n 次未中保底唤醒一次。"""
    if miss_streak >= int(settings.wake_every_n):
        return True
    return roll_hit(settings.wake_prob_percent, rng)


def in_cooldown(last_at: float, now: float, cooldown_minutes: int) -> bool:
    return last_at > 0 and (now - last_at) < cooldown_minutes * 60.0


# ---------- DAL：proactor_state + polls ----------

class ProactorStore:
    def __init__(self, data_dir, settings: Any,
                 tzname: str = "Asia/Shanghai") -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.tzname = tzname

    def _conn(self, uid: str):
        return open_agent_db(self.data_dir, uid)

    # 会话 opt-in
    def set_enabled(self, uid: str, session_id: str, enabled: bool,
                    now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            conn.execute(
                "INSERT INTO proactor_state(session_id, enabled, wake_miss_streak, "
                "last_wake_at, last_speakup_at, updated_at) VALUES(?, ?, 0, 0, 0, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET enabled=excluded.enabled, "
                "updated_at=excluded.updated_at",
                (session_id, 1 if enabled else 0, now),
            )
        finally:
            conn.close()

    def touch_human(self, uid: str, session_id: str,
                    now: Optional[float] = None) -> None:
        """真人消息到达时刷新活跃时间（仅 opt-in 行存在，未开启的会话空转）。"""
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            conn.execute(
                "UPDATE proactor_state SET last_human_at=?, updated_at=? "
                "WHERE session_id=?", (now, now, session_id),
            )
        finally:
            conn.close()

    def get_state(self, uid: str, session_id: str) -> Optional[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT * FROM proactor_state WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_enabled(self, uid: str) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM proactor_state WHERE enabled=1"
            ).fetchall()]
        finally:
            conn.close()

    def update_state(self, uid: str, session_id: str, **fields) -> None:
        if not fields:
            return
        allowed = {"wake_miss_streak", "last_wake_at",
                   "last_speakup_at", "enabled"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append("%s=?" % k)
                vals.append(v)
        if not sets:
            return
        vals.append(session_id)
        conn = self._conn(uid)
        try:
            conn.execute(
                "UPDATE proactor_state SET %s WHERE session_id=?"
                % ",".join(sets), vals
            )
        finally:
            conn.close()

    # 自建轮询
    def add_poll(self, uid: str, session_id: str, title: str, probability: int,
                 wake_every_n: int, active_hours: float,
                 now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM polls WHERE session_id=?", (session_id,)
            ).fetchone()["n"]
            if count >= int(self.settings.polls_max_per_session):
                raise ValueError(
                    "每会话最多 %d 条轮询"
                    % int(self.settings.polls_max_per_session)
                )
            cur = conn.execute(
                "INSERT INTO polls(session_id, title, probability, wake_every_n, "
                "active_hours, ticks_since_fire, enabled, created_at) "
                "VALUES(?, ?, ?, ?, ?, 0, 1, ?)",
                (session_id, (title or "")[:100],
                 min(100, max(0, int(probability))),
                 max(1, int(wake_every_n)),
                 max(0.5, float(active_hours)), now),
            )
            row = conn.execute("SELECT * FROM polls WHERE id=?",
                               (cur.lastrowid,)).fetchone()
            return dict(row)
        finally:
            conn.close()

    def list_polls(self, uid: str, session_id: Optional[str] = None
                   ) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM polls WHERE session_id=? ORDER BY id",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM polls ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_poll(self, uid: str, poll_id: int, **fields) -> bool:
        allowed = {"title", "probability", "wake_every_n",
                   "active_hours", "enabled", "ticks_since_fire"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append("%s=?" % k)
                vals.append(v)
        if not sets:
            return False
        vals.append(poll_id)
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "UPDATE polls SET %s WHERE id=?" % ",".join(sets), vals
            )
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_poll(self, uid: str, poll_id: int) -> bool:
        conn = self._conn(uid)
        try:
            return conn.execute(
                "DELETE FROM polls WHERE id=?", (poll_id,)
            ).rowcount > 0
        finally:
            conn.close()

    def iter_users(self) -> List[str]:
        if not self.data_dir.exists():
            return []
        return [p.name for p in sorted(self.data_dir.iterdir())
                if p.is_dir() and (p / "agent" / "agent.db").exists()]


# ---------- Ticker ----------

class ProactorTicker:
    """30 分钟节奏的主动行为 ticker（内部按 wake_check_minutes 节流）。"""

    def __init__(self, state, store: ProactorStore,
                 rng: Optional[Callable[[], float]] = None,
                 now_provider: Callable[[], float] = time.time) -> None:
        self.state = state
        self.store = store
        self.rng = rng or random.random
        self.now_provider = now_provider
        self._last_tick = 0.0
        self._fire = fire  # 测试可 monkeypatch
        # P4：可选提示词注册表，None 时用内置默认 brief（行为与 P3 一致）
        self.prompts = None

    def _notice(self, key: str) -> str:
        if self.prompts is None:
            from .prompt_registry import PROMPT_PARTITIONS

            return PROMPT_PARTITIONS[key].default
        return self.prompts.render(key)

    async def initialize(self) -> None:
        self._last_tick = 0.0

    async def tick(self) -> None:
        if not self.store.settings.enabled:
            return
        now = self.now_provider()
        period = int(self.store.settings.wake_check_minutes) * 60
        if now - self._last_tick < period:
            return
        self._last_tick = now
        await self._run_once(now)

    async def _run_once(self, now: float) -> None:
        tzname = self.store.tzname
        for uid in self.store.iter_users():
            try:
                await self._tick_user(uid, now, tzname)
            except Exception:
                logger.exception("主动行为 tick 失败 uid=%s", uid)

    def _scope(self, uid: str):
        if uid in self.state._scopes:
            return self.state._scopes[uid]
        from ..user import UserScope

        return UserScope.build(
            uid, self.state.config, self.state.data_dir,
            self.state.user_store,
        )

    async def _tick_user(self, uid: str, now: float, tzname: str) -> None:
        settings = self.store.settings
        rows = self.store.list_enabled(uid)
        if not rows:
            return
        sc = self._scope(uid)
        for row in rows:
            sid = row["session_id"]
            if sc.store.get(sid) is None:
                # 会话已删除：停用 opt-in，不再扫描
                self.store.set_enabled(uid, sid, False, now=now)
                continue
            last_human = float(row.get("last_human_at") or 0)
            if last_human <= 0:
                continue  # opt-in 后尚无真人消息：不掷骰
            idle_minutes = max(0.0, (now - last_human) / 60.0)
            if idle_minutes > float(settings.active_hours) * 60.0:
                continue  # 冷群不掷骰（活跃门）

            # 1) 基础唤醒
            streak = int(row["wake_miss_streak"] or 0)
            if wake_should_fire(settings, streak, self.rng):
                res = await self._fire(
                    self.state, uid, sid,
                    kind="wake-notice",
                    brief=self._notice("wake_notice"),
                    allow_silent=True, with_history=True,
                )
                if res.get("ok"):
                    self.store.update_state(
                        uid, sid, wake_miss_streak=0, last_wake_at=now
                    )
                    streak = 0
            else:
                streak += 1
                self.store.update_state(uid, sid, wake_miss_streak=streak)

            # 2) 主动搭话：静默时段 + 自冷却双门
            if clock.in_quiet_hours(now, settings.quiet_hours, tzname):
                continue
            if in_cooldown(float(row["last_speakup_at"] or 0), now,
                           int(settings.self_cooldown_minutes)):
                continue
            prob = speakup_probability(
                settings, now, tzname, idle_minutes, streak
            )
            if roll_hit(prob, self.rng):
                res = await self._fire(
                    self.state, uid, sid,
                    kind="speakup-notice",
                    brief=self._notice("speakup_notice"),
                    allow_silent=True, with_history=True,
                )
                if res.get("ok"):
                    self.store.update_state(uid, sid, last_speakup_at=now)

            # 3) 自建轮询：同一 tick 至多一条命中，其余只累加计数
            await self._tick_polls(uid, sid, now, idle_minutes)

    async def _tick_polls(self, uid: str, sid: str, now: float,
                          idle_minutes: float) -> None:
        polls = [p for p in self.store.list_polls(uid, sid) if p["enabled"]]
        if not polls:
            return
        fired = False
        for p in polls:
            ticks = int(p["ticks_since_fire"] or 0) + 1
            due = ticks >= int(p["wake_every_n"]) or roll_hit(
                int(p["probability"]), self.rng
            )
            if due and not fired and idle_minutes <= float(p["active_hours"]) * 60.0:
                res = await self._fire(
                    self.state, uid, sid,
                    kind="poll-notice",
                    brief=(
                        "【自建轮询：%s】按这个关注点检查是否有必要主动开口，"
                        "没有新内容就回复 [静默]。" % (p["title"] or "未命名")
                    ),
                    allow_silent=True, with_history=True,
                )
                if res.get("ok"):
                    fired = True
                    self.store.update_poll(uid, int(p["id"]),
                                           ticks_since_fire=0)
                    continue
            self.store.update_poll(uid, int(p["id"]), ticks_since_fire=ticks)
