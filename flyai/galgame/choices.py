"""Galgame 选项等待：阻塞中的 galgame_choice 工具与用户选择的内存态撮合。

按聊天运行 id（run_id）登记 asyncio.Future；前端用户选择后经 API resolve。
仅保存在内存中，服务重启则等待中的运行失效（与 ChatRun 生命周期一致）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Tuple

# 内部兜底：避免用户永不作答导致运行与会话锁无限期挂住
WAIT_TIMEOUT_SECONDS = 600


class ChoiceTimeout(Exception):
    """等待用户选项超时。"""


class GalgameChoiceManager:
    def __init__(self):
        self._futures: Dict[str, Tuple[str, float, asyncio.Future]] = {}

    def register(self, run_id: str, uid: str) -> asyncio.Future:
        """登记一个等待中的选项（同 run 重复登记时取消旧的）。"""
        old = self._futures.get(run_id)
        if old is not None and not old[2].done():
            old[2].cancel()
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._futures[run_id] = (uid, time.time(), fut)
        return fut

    async def wait_for(self, run_id: str, uid: str) -> str:
        """阻塞直到用户选择；返回选中的文本。"""
        fut = self.register(run_id, uid)
        try:
            return await asyncio.wait_for(fut, WAIT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._futures.pop(run_id, None)
            raise ChoiceTimeout("等待用户选择超时（%s 秒）" % WAIT_TIMEOUT_SECONDS)

    def resolve(self, run_id: str, uid: str, text: str) -> bool:
        """用户提交选择。未知 run→KeyError；归属不符→PermissionError；
        已消费→False；成功→True。"""
        entry = self._futures.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        owner, _ts, fut = entry
        if owner != uid:
            raise PermissionError(run_id)
        if fut.done():
            self._futures.pop(run_id, None)
            return False
        fut.set_result(text)
        self._futures.pop(run_id, None)
        return True

    def discard(self, run_id: str) -> None:
        """运行结束/失败时清理未决的等待（不取消已就绪结果）。"""
        entry = self._futures.pop(run_id, None)
        if entry is not None and not entry[2].done():
            entry[2].cancel()
