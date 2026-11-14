"""承诺与到期提醒（P3 §7.5）：commitments DAL + ReminderTicker。

提醒不是普通主动搭话，它有独立的两条处置策略：
- 会话未开启主动行为（proactor opt-in 关闭）：跳过且**不置 reminded**，
  下次扫描继续尝试，直到用户打开开关或承诺被关闭/取消；
- 会话已删除：承诺置 cancelled 并标记 reminded，彻底退出扫描。
锁竞争同样不置 reminded，下周期重试，保证提醒不丢。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..agentdb import open_agent_db
from .runner import fire

logger = logging.getLogger("flyai.reminders")

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_CANCELLED = "cancelled"


class CommitmentStore:
    def __init__(self, data_dir) -> None:
        self.data_dir = Path(data_dir)

    def _conn(self, uid: str):
        return open_agent_db(self.data_dir, uid)

    def add(self, uid: str, session_id: str, content: str, due_at: float,
            owner_name: str = "", now: Optional[float] = None) -> Dict[str, Any]:
        text = (content or "").strip()
        if not text:
            raise ValueError("承诺内容为空")
        try:
            due = float(due_at)
        except (TypeError, ValueError):
            raise ValueError("due_at 必须是 epoch 秒")
        if due <= 0:
            raise ValueError("due_at 非法")
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "INSERT INTO commitments(session_id, owner_name, content, due_at, "
                "status, reminded, created_at, closed_at) "
                "VALUES(?, ?, ?, ?, 'open', 0, ?, 0)",
                (session_id, (owner_name or "")[:100], text[:500], due, now),
            )
            return dict(conn.execute(
                "SELECT * FROM commitments WHERE id=?", (cur.lastrowid,)
            ).fetchone())
        finally:
            conn.close()

    def list(self, uid: str, session_id: Optional[str] = None,
             status: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM commitments WHERE 1=1"
        args: List[Any] = []
        if session_id:
            sql += " AND session_id=?"
            args.append(session_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY due_at ASC, id DESC"
        conn = self._conn(uid)
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def due(self, uid: str, now: float) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM commitments WHERE status='open' AND reminded=0 "
                "AND due_at<=? ORDER BY due_at", (now,),
            ).fetchall()]
        finally:
            conn.close()

    def mark_reminded(self, uid: str, commitment_id: int) -> None:
        conn = self._conn(uid)
        try:
            conn.execute(
                "UPDATE commitments SET reminded=1 WHERE id=?", (commitment_id,)
            )
        finally:
            conn.close()

    def close(self, uid: str, commitment_id: int,
              now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            return conn.execute(
                "UPDATE commitments SET status='closed', closed_at=?, reminded=1 "
                "WHERE id=?", (now, commitment_id),
            ).rowcount > 0
        finally:
            conn.close()

    def cancel(self, uid: str, commitment_id: int) -> bool:
        conn = self._conn(uid)
        try:
            return conn.execute(
                "UPDATE commitments SET status='cancelled', reminded=1 WHERE id=?",
                (commitment_id,),
            ).rowcount > 0
        finally:
            conn.close()

    def iter_users(self) -> List[str]:
        if not self.data_dir.exists():
            return []
        return [p.name for p in sorted(self.data_dir.iterdir())
                if p.is_dir() and (p / "agent" / "agent.db").exists()]


class ReminderTicker:
    """60 秒节奏扫描到期承诺（内部节流），复用 runner 产出后台 ChatRun。"""

    def __init__(self, state, commitment_store: CommitmentStore,
                 proactor_store, check_seconds: int = 60,
                 now_provider: Callable[[], float] = time.time) -> None:
        self.state = state
        self.store = commitment_store
        self.proactor_store = proactor_store
        self.check_seconds = max(10, int(check_seconds))
        self.now_provider = now_provider
        self._last_tick = 0.0
        self._fire = fire  # 测试可 monkeypatch

    async def initialize(self) -> None:
        self._last_tick = 0.0

    async def tick(self) -> None:
        now = self.now_provider()
        if now - self._last_tick < self.check_seconds:
            return
        self._last_tick = now
        for uid in self.store.iter_users():
            try:
                await self._tick_user(uid, now)
            except Exception:
                logger.exception("承诺提醒 tick 失败 uid=%s", uid)

    def _scope(self, uid: str):
        if uid in self.state._scopes:
            return self.state._scopes[uid]
        from ..user import UserScope

        return UserScope.build(
            uid, self.state.config, self.state.data_dir,
            self.state.user_store,
        )

    async def _tick_user(self, uid: str, now: float) -> None:
        items = self.store.due(uid, now)
        if not items:
            return
        sc = self._scope(uid)
        for item in items:
            cid = int(item["id"])
            sid = item["session_id"]
            if sc.store.get(sid) is None:
                # 策略二：会话已删除，取消承诺并退出扫描
                self.store.cancel(uid, cid)
                continue
            pro_state = self.proactor_store.get_state(uid, sid)
            if not pro_state or not pro_state.get("enabled"):
                # 策略一：主动行为未开启，不置 reminded，下周期继续等
                continue
            owner = ("（%s 记下的）" % item["owner_name"]) if item["owner_name"] else ""
            brief = (
                "【承诺提醒】%s你有一件到点该做的事：%s。"
                "现在用贴合人设的方式主动提醒对方这件事。"
                % (owner, item["content"])
            )
            res = await self._fire(
                self.state, uid, sid, kind="reminder",
                brief=brief, allow_silent=False,
                with_history=False, max_tokens=500,
            )
            if not res.get("ok"):
                # locked 或其它失败：不置 reminded，下周期重试
                continue
            self.store.mark_reminded(uid, cid)
