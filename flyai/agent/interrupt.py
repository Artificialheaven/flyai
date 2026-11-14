"""安全点打断（P2）：长跑进行中的会话消息不抢会话锁，进会话级队列。

run_agent_loop 只在每轮请求模型之前（上一轮工具链全部完成之后）drain，
因此工具执行中途 push 绝不会切断半截调用。纯内存态：进程重启后未消费的
插队项丢失，但 loop_sessions 会被 reconcile 为 interrupted，语义一致。
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List


class InterruptHub:
    def __init__(self, max_per_session: int = 32) -> None:
        self._q: Dict[str, Deque[dict]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._max = max_per_session

    def push(self, session_id: str, item: Dict[str, Any]) -> bool:
        """插队一条打断/临时指示。队列满时丢弃最旧项，返回是否发生挤出。"""
        if not session_id:
            return False
        dropped = False
        with self._lock:
            q = self._q[session_id]
            if len(q) >= self._max:
                q.popleft()
                dropped = True
            q.append(dict(item))
        return dropped

    def drain(self, session_id: str) -> List[dict]:
        """排空并返回该会话全部待注入项（只在安全点调用）。"""
        with self._lock:
            q = self._q.pop(session_id, None)
        return list(q) if q else []

    def pending(self, session_id: str) -> int:
        with self._lock:
            return len(self._q.get(session_id, ()))

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._q.pop(session_id, None)
