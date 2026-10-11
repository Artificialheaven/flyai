"""召回日志：内存滚动缓冲（最近 N 条），供记忆管理页查看。"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, List


class RecallLogBuffer:
    def __init__(self, maxlen: int = 200) -> None:
        self._items: Deque[Dict[str, Any]] = deque(maxlen=maxlen)

    def add(self, **fields: Any) -> Dict[str, Any]:
        entry = {"ts": time.time()}
        entry.update(fields)
        self._items.append(entry)
        return entry

    def list(self) -> List[Dict[str, Any]]:
        # 最新在前
        return list(reversed(self._items))
