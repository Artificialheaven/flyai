"""后台调度服务（P0 骨架）。

P0 职责：按配置开关启动、争取领导锁、按 tick_seconds 周期驱动已注册的
ticker。P3 起在这里挂载主动行为/定时任务/提醒检查。单进程 uvicorn 下
领导锁天然命中；多 worker 部署时未拿到锁的实例只待命、不 tick，避免
重复调度。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, List, Optional

from .leadlock import LeadLock

logger = logging.getLogger("flyai.scheduler")


class SchedulerService:
    def __init__(self, state: Any) -> None:
        self._state = state
        self._task: Optional[asyncio.Task] = None
        self._stop: Optional[asyncio.Event] = None
        self._tickers: List[Any] = []
        self._leader = False

    def add_ticker(self, ticker: Any) -> None:
        """注册周期 ticker（需实现 async initialize()/tick()）。"""
        self._tickers.append(ticker)

    @property
    def is_leader(self) -> bool:
        return self._leader

    async def start(self) -> None:
        cfg = self._state.config.scheduler
        if not cfg.enabled:
            logger.info("调度器未启用（scheduler.enabled=false），待命")
            return
        data_dir = getattr(self._state, "data_dir", None)
        if data_dir is None:
            logger.warning("data_dir 未就绪，调度器不启动")
            return
        if not LeadLock.try_acquire(Path(data_dir) / ".scheduler.lock"):
            logger.warning("未拿到调度领导锁，本实例仅待命（多 worker 属正常现象）")
            return
        self._leader = True
        for ticker in self._tickers:
            try:
                await ticker.initialize()
            except Exception:
                logger.exception("ticker 初始化失败: %r", ticker)
        # Event 在运行中的循环内惰性创建，避免 3.9 构造期绑定循环
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())
        logger.info(
            "调度器已启动: tick=%ds tz=%s tickers=%d",
            cfg.tick_seconds,
            cfg.timezone,
            len(self._tickers),
        )

    async def _run(self) -> None:
        cfg = self._state.config.scheduler
        while self._stop is not None and not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=cfg.tick_seconds
                )
            except asyncio.TimeoutError:
                pass
            if self._stop is not None and self._stop.is_set():
                break
            await self._safe_tick()

    async def _safe_tick(self) -> None:
        for ticker in self._tickers:
            try:
                await ticker.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 单个 ticker 异常不影响其他 ticker 与后续周期
                logger.exception("ticker 执行失败: %r", ticker)

    async def restart(self) -> None:
        """配置热更新后按新配置重启（释放旧锁/任务后重新争取）。"""
        await self.aclose()
        await self.start()

    async def aclose(self) -> None:
        if self._task is not None and self._stop is not None:
            self._stop.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("调度任务退出异常")
            self._task = None
            self._stop = None
        if self._leader:
            LeadLock.release()
            self._leader = False
