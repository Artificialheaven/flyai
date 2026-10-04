"""共享 Playwright 浏览器引擎：进程内懒启动一次，所有会话共用。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger("flyai.browser")

_INSTALL_HINT = (
    "未安装 Playwright，AI 浏览器无法使用。请先安装：\n"
    "  pip install -r requirements-browser.txt\n"
    "  playwright install chromium\n"
    "Ubuntu 还需：sudo playwright install-deps chromium"
)


class BrowserEngine:
    """持有一个启动的 Chromium；浏览器只启动一次，会话以隐身上下文隔离。"""

    def __init__(self, settings: Any):
        self.settings = settings
        self._browser: Optional[Any] = None
        # 懒创建：Python 3.9 在无运行中的事件循环时构造 Lock 会报错
        self._lock: Optional[asyncio.Lock] = None

    async def browser(self) -> Any:
        if self._browser is None:
            if self._lock is None:
                self._lock = asyncio.Lock()
            async with self._lock:
                # 双检：等锁期间可能已被其他协程启动
                if self._browser is None:
                    self._browser = await self._launch()
        return self._browser

    async def _launch(self) -> Any:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(_INSTALL_HINT) from exc

        proxy = (self.settings.proxy or "").strip() or None
        executable = (self.settings.executable_path or "").strip() or None
        logger.info(
            "启动 Chromium: headless=%s proxy=%s executable=%s",
            bool(self.settings.headless), proxy or "直连", executable or "自动",
        )
        self._pw = await async_playwright().start()
        try:
            browser = await self._pw.chromium.launch(
                headless=bool(self.settings.headless),
                executable_path=executable,
                # 代理留空走直连；per-context proxy 要求 launch 不固定 proxy
            )
        except Exception:
            await self._pw.stop()
            raise
        return browser

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        pw = getattr(self, "_pw", None)
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass
        self._browser = None
        self._pw = None
