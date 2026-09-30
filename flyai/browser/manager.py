"""浏览器会话管理：按「用户:聊天会话」键复用 BrowserSession，空闲 GC。"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .engine import BrowserEngine
from .session import BrowserSession

logger = logging.getLogger("flyai.browser")


class BrowserSessionManager:
    """持有共享引擎与全部活跃会话；会话键由 API 层注入（uid:session_id）。"""

    def __init__(self, state: Any):
        self.settings = state.config.browser
        self.engine = BrowserEngine(self.settings)
        self._sessions: Dict[str, BrowserSession] = {}

    async def get(self, key: str) -> BrowserSession:
        """取已有会话；不存在则启动浏览器并新建一个隐身上下文会话。"""
        await self.gc()
        session = self._sessions.get(key)
        if session is not None:
            return session
        browser = await self.engine.browser()
        proxy = (self.settings.proxy or "").strip() or None
        session = await BrowserSession(self.settings).attach(browser, proxy)
        self._sessions[key] = session
        logger.info("新建浏览器会话 key=%s（活跃 %d）", key, len(self._sessions))
        return session

    async def drop(self, key: str) -> bool:
        session = self._sessions.pop(key, None)
        if session is None:
            return False
        await session.close()
        return True

    async def gc(self) -> int:
        """关闭空闲超时的会话，返回回收数。"""
        dead = [k for k, s in self._sessions.items() if s.is_idle()]
        for k in dead:
            session = self._sessions.pop(k, None)
            if session is not None:
                await session.close()
        if dead:
            logger.info("回收空闲浏览器会话 %d 个", len(dead))
        return len(dead)

    async def aclose(self) -> None:
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        await self.engine.aclose()
