"""断线可续的聊天运行（ChatRun）。

生成过程与 HTTP 连接解耦：agent loop 在后台 asyncio 任务中执行，产出的事件
（thinking/content/tool 等）全量缓存在 ChatRun 内并带递增序号。客户端刷新、
网络抖动后，凭 run_id + 最后收到的序号重新订阅，服务端先补发错过的事件、
再实时跟随，直到终态。

运行在终态后保留 RUN_TTL 秒（默认 600），在此窗口内重连仍可拿到完整事件；
服务进程重启则运行丢失（内存态）。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from ..sse import sse_event

logger = logging.getLogger("flyai.chat")

# 终态后保留时长 / 进程内最多保留运行数
RUN_TTL = 600
RUN_MAX = 64

class ChatRun:
    """一次聊天回答的后台运行与事件缓存。"""

    def __init__(self, run_id: str, uid: str, session_id: str, thread_id: str = ""):
        self.id = run_id
        self.uid = uid
        self.session_id = session_id
        self.thread_id = thread_id or ""
        self.status = "running"  # running | done | error | cancelled
        self.events: List[Tuple[str, dict]] = []
        self.cond = asyncio.Condition()
        self.task: Optional[asyncio.Task] = None
        self.created = time.time()
        self.finished = 0.0

    async def emit(self, event: str, data: dict) -> None:
        """发布一个事件并通知所有订阅者。"""
        async with self.cond:
            self.events.append((event, data))
            self.cond.notify_all()

    async def finish(self, status: str) -> None:
        async with self.cond:
            self.status = status
            self.finished = time.time()
            self.cond.notify_all()

    @staticmethod
    def _is_terminal(event: str, data: dict) -> bool:
        if event == "done":
            # 群聊 fan-out 每个 AI 后各有一个 done（terminal=False），
            # 只有最后一个才真正结束运行
            return bool((data or {}).get("terminal", True))
        if event == "error":
            return bool((data or {}).get("fatal", True))
        return False

    async def stream(self, after: int = 0) -> AsyncIterator[str]:
        """SSE 帧生成器：先补发序号 after 之后的缓存事件，再实时跟随至终态。"""
        idx = max(0, int(after))
        while True:
            async with self.cond:
                if idx >= len(self.events) and self.status == "running":
                    await self.cond.wait()
                # 锁内取快照，锁外逐帧发送（发送不应持锁）
                pending = self.events[idx:]
                idx = len(self.events)
                status = self.status

            terminal_seen = False
            base_seq = idx - len(pending) + 1
            for i, (ev, data) in enumerate(pending):
                yield sse_event(ev, data, event_id=base_seq + i)
                if self._is_terminal(ev, data):
                    terminal_seen = True
            if terminal_seen:
                return
            # 非运行态且事件已发完（如取消但无终态帧）
            if status != "running" and not pending:
                return


class ChatRunManager:
    """进程内全部 ChatRun 的注册表。"""

    def __init__(self, ttl: int = RUN_TTL, max_runs: int = RUN_MAX):
        self._runs: Dict[str, ChatRun] = {}
        self._ttl = int(ttl)
        self._max = int(max_runs)
        # 会话级写锁：sid -> run_id。同一房间任意窗口的运行期间独占，
        # 防止两个并发 /chat 读-改-写互相覆盖。仅在单 uvicorn worker 下有效。
        self._session_locks: Dict[str, str] = {}

    def session_locked(self, session_id: str) -> Optional[str]:
        """会话是否被某运行独占；返回持有 run_id（未持有 None）。"""
        return self._session_locks.get(session_id)

    def lock_session(self, session_id: str, run: ChatRun) -> None:
        """独占会话。调用前须在同一无 await 片段内先查 session_locked。"""
        self._session_locks[session_id] = run.id

    def unlock_session(self, session_id: str, run: ChatRun) -> None:
        # 只释放自己的锁：运行结束与取消竞态时避免误放后来者的锁
        if self._session_locks.get(session_id) == run.id:
            self._session_locks.pop(session_id, None)

    def _prune(self) -> None:
        now = time.time()
        for rid in [rid for rid, r in self._runs.items()
                    if r.status != "running" and now - r.finished >= self._ttl]:
            self._runs.pop(rid, None)
        # 插入新运行前即需腾出位置（create 先 prune 再 put）
        if len(self._runs) >= self._max:
            finished = sorted(
                (r.finished, rid) for rid, r in self._runs.items()
                if r.status != "running"
            )
            for _ts, rid in finished[: len(self._runs) - self._max + 1]:
                self._runs.pop(rid, None)

    def create(self, uid: str, session_id: str, thread_id: str = "") -> ChatRun:
        self._prune()
        run = ChatRun(secrets.token_hex(8), uid, session_id, thread_id or "")
        self._runs[run.id] = run
        return run

    def get(self, run_id: str, uid: str) -> Optional[ChatRun]:
        """按 id 取运行；不存在或不属于该用户返回 None。"""
        run = self._runs.get(run_id)
        if run is None or run.uid != uid:
            return None
        return run

    def active_for(
        self, uid: str, session_id: str, thread_id: str = ""
    ) -> Optional[ChatRun]:
        """该用户在同一会话/窗口是否已有运行中的回答。"""
        for run in self._runs.values():
            if (
                run.uid == uid
                and run.status == "running"
                and run.session_id == session_id
                and run.thread_id == (thread_id or "")
            ):
                return run
        return None

    def active_in_session(self, uid: str, session_id: str) -> Optional[ChatRun]:
        """该用户在同一会话（任意窗口）是否有运行中的回答。"""
        for run in self._runs.values():
            if (
                run.uid == uid
                and run.status == "running"
                and run.session_id == session_id
            ):
                return run
        return None

    def try_lock_for(self, uid: str, session_id: str, run: ChatRun) -> bool:
        """会话独占的原子 check-and-set：同 uid 同会话已有【其它】运行 → False；
        否则置锁并返回 True。run 已由 create() 注册，扫描时要排除它自己。
        检查与置位必须在同一段同步代码内完成。
        """
        for other in self._runs.values():
            if (other is not run and other.uid == uid
                    and other.status == "running"
                    and other.session_id == session_id):
                return False
        self._session_locks[session_id] = run.id
        return True

    async def aclose(self) -> None:
        """关闭全部运行中的任务（服务退出时）。"""
        self._session_locks.clear()
        tasks = [r.task for r in self._runs.values() if r.task is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()
