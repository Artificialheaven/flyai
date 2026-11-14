"""定时作业（P3 §7.4）：scheduled_jobs DAL、严格未来递推、作业 Ticker。

语义锁定：
- mode=once 时刻已过即失效（创建时拒绝、扫描到的残留自动停用），不补跑；
- daily/weekly 严格未来：候选时刻 <= now 就顺延到明天/下周同一时刻；
- 宕机重启不补跑错过的轮次，只认 next_run_at 与当前时刻的比较；
- 触发失败（非锁竞争）置 next_run_at = now+300 重试；
- 作业触发不带历史、不带工具，仅人设 + brief + 时间，max_tokens=500。
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..agentdb import open_agent_db
from . import clock
from .runner import fire

logger = logging.getLogger("flyai.scheduler_jobs")

MODE_ONCE = "once"
MODE_DAILY = "daily"
MODE_WEEKLY = "weekly"
VALID_MODES = (MODE_ONCE, MODE_DAILY, MODE_WEEKLY)
RETRY_SECONDS = 300


# ---------- 纯递推 ----------

def compute_next_run(mode: str, hhmm: str, weekday: int,
                     after_epoch: float, tzname: str) -> Optional[float]:
    """返回 after_epoch 之后（严格大于）的下一次触发 epoch；once 过期返回 None。"""
    hm = clock.parse_hhmm(hhmm)
    if hm is None or mode not in VALID_MODES:
        return None
    hour, minute = hm
    zone = clock.get_zone(tzname)
    local = clock.local_dt(after_epoch, tzname)
    cand = local.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if mode == MODE_ONCE:
        if cand <= local:
            return None
    elif mode == MODE_DAILY:
        if cand <= local:
            cand += timedelta(days=1)
    else:  # weekly：weekday 0=周一 … 6=周日
        target = int(weekday) % 7
        cand += timedelta(days=(target - cand.weekday()) % 7)
        if cand <= local:
            cand += timedelta(days=7)
    return cand.replace(tzinfo=zone).timestamp()


# ---------- DAL ----------

class JobStore:
    def __init__(self, data_dir, tzname: str = "Asia/Shanghai") -> None:
        self.data_dir = Path(data_dir)
        self.tzname = tzname

    def _conn(self, uid: str):
        return open_agent_db(self.data_dir, uid)

    def create(self, uid: str, session_id: str, mode: str, hhmm: str,
               brief: str, weekday: int = -1,
               now: Optional[float] = None) -> Dict[str, Any]:
        if mode not in VALID_MODES:
            raise ValueError("mode 只能是 once/daily/weekly")
        if clock.parse_hhmm(hhmm) is None:
            raise ValueError("时间格式应为 HH:MM")
        text = (brief or "").strip()
        if not text:
            raise ValueError("brief 为空")
        now = time.time() if now is None else now
        if mode == MODE_WEEKLY and not (0 <= int(weekday) <= 6):
            raise ValueError("weekly 作业需要 weekday 0-6")
        nxt = compute_next_run(mode, hhmm, int(weekday), now, self.tzname)
        if mode == MODE_ONCE and nxt is None:
            raise ValueError("一次性作业的时刻已经过去")
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "INSERT INTO scheduled_jobs(session_id, mode, hhmm, weekday, "
                "brief, enabled, next_run_at, last_run_at, last_status, created_at) "
                "VALUES(?, ?, ?, ?, ?, 1, ?, 0, '', ?)",
                (session_id, mode, hhmm, int(weekday), text[:500], nxt, now),
            )
            return dict(conn.execute(
                "SELECT * FROM scheduled_jobs WHERE id=?", (cur.lastrowid,)
            ).fetchone())
        finally:
            conn.close()

    def list(self, uid: str, session_id: Optional[str] = None
             ) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM scheduled_jobs WHERE session_id=? ORDER BY id",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scheduled_jobs ORDER BY next_run_at"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete(self, uid: str, job_id: int) -> bool:
        conn = self._conn(uid)
        try:
            return conn.execute(
                "DELETE FROM scheduled_jobs WHERE id=?", (job_id,)
            ).rowcount > 0
        finally:
            conn.close()

    def set_enabled(self, uid: str, job_id: int, enabled: bool) -> bool:
        conn = self._conn(uid)
        try:
            return conn.execute(
                "UPDATE scheduled_jobs SET enabled=? WHERE id=?",
                (1 if enabled else 0, job_id),
            ).rowcount > 0
        finally:
            conn.close()

    def due(self, uid: str, now: float) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM scheduled_jobs WHERE enabled=1 AND next_run_at<=? "
                "ORDER BY next_run_at", (now,),
            ).fetchall()]
        finally:
            conn.close()

    def mark_result(self, uid: str, job_id: int, job: Dict[str, Any],
                    ok: bool, now: float) -> None:
        """成功：once 停用、周期作业严格未来递推；失败：5 分钟后重试。"""
        conn = self._conn(uid)
        try:
            if not ok:
                conn.execute(
                    "UPDATE scheduled_jobs SET last_run_at=?, last_status='error', "
                    "next_run_at=? WHERE id=?",
                    (now, now + RETRY_SECONDS, job_id),
                )
                return
            if job["mode"] == MODE_ONCE:
                conn.execute(
                    "UPDATE scheduled_jobs SET enabled=0, last_run_at=?, "
                    "last_status='done' WHERE id=?", (now, job_id),
                )
                return
            nxt = compute_next_run(
                job["mode"], job["hhmm"], int(job["weekday"] or -1),
                now, self.tzname,
            )
            if nxt is None:
                # 理论上周期作业不会走到；兜底停用避免热轮询
                conn.execute(
                    "UPDATE scheduled_jobs SET enabled=0, last_run_at=?, "
                    "last_status='expired' WHERE id=?", (now, job_id),
                )
                return
            conn.execute(
                "UPDATE scheduled_jobs SET last_run_at=?, last_status='done', "
                "next_run_at=? WHERE id=?", (now, nxt, job_id),
            )
        finally:
            conn.close()

    def iter_users(self) -> List[str]:
        if not self.data_dir.exists():
            return []
        return [p.name for p in sorted(self.data_dir.iterdir())
                if p.is_dir() and (p / "agent" / "agent.db").exists()]


# ---------- Ticker ----------

class JobTicker:
    """每个调度周期扫描到期作业；锁竞争跳过（留在 due，下周期再试）。"""

    def __init__(self, state, store: JobStore,
                 now_provider: Callable[[], float] = time.time) -> None:
        self.state = state
        self.store = store
        self.now_provider = now_provider
        self._fire = fire  # 测试可 monkeypatch

    async def initialize(self) -> None:
        return None

    async def tick(self) -> None:
        now = self.now_provider()
        for uid in self.store.iter_users():
            try:
                await self._tick_user(uid, now)
            except Exception:
                logger.exception("定时作业 tick 失败 uid=%s", uid)

    def _scope(self, uid: str):
        if uid in self.state._scopes:
            return self.state._scopes[uid]
        from ..user import UserScope

        return UserScope.build(
            uid, self.state.config, self.state.data_dir,
            self.state.user_store,
        )

    async def _tick_user(self, uid: str, now: float) -> None:
        jobs = self.store.due(uid, now)
        if not jobs:
            return
        sc = self._scope(uid)
        for job in jobs:
            if sc.store.get(job["session_id"]) is None:
                # 会话已删除：停用作业，不补跑
                self.store.set_enabled(uid, int(job["id"]), False)
                continue
            stamp = clock.local_dt(now, self.store.tzname).strftime(
                "%Y-%m-%d %H:%M"
            )
            brief = "【定时任务·%s】%s" % (stamp, job["brief"])
            res = await self._fire(
                self.state, uid, job["session_id"],
                kind="scheduled-job", brief=brief,
                allow_silent=False, with_history=False, max_tokens=500,
            )
            if not res.get("ok"):
                if res.get("reason") == "locked":
                    continue  # 交互占用：保持 due，下周期重试
                self.store.mark_result(uid, int(job["id"]), job, False, now)
                continue
            self.store.mark_result(uid, int(job["id"]), job, True, now)
