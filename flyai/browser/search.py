"""Chromium 搜索引擎：用真实无头浏览器打开 Bing / 百度结果页。

与 AI 浏览器（BrowserSession）共用 BrowserEngine（Chromium 进程懒启动、
全程复用），但每次搜索开一个临时隐身上下文、用完即关，不保留 7 工具那套
浏览会话状态。结果结构 {title,url,snippet} 与 httpx 直连版一致。

百度反爬敏感：可能返回 wappass 验证码页，此时抛 BaiduCaptcha，
由 WebSearchTool 自动回退 Bing；百度的 link?url= 跳转链在这里解包成真实 URL。
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from typing import Any, Dict, List
from urllib.parse import quote

import httpx

from .engine import BrowserEngine

logger = logging.getLogger("flyai.search")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
_NAV_TIMEOUT_DEFAULT = 25000


class BaiduCaptcha(Exception):
    """百度返回安全验证 / 验证码页，调用方应回退 Bing。"""


def _parse_bing_rss(text: str) -> List[Dict[str, str]]:
    """解析必应 RSS（&format=rss）输出：item 的 title/link/description。

    返回结构与浏览器渲染版一致：{title,url,snippet}，按链接去重。
    XML 非法或无条目时返回空列表（由调用方决定回退）。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out: List[Dict[str, str]] = []
    seen = set()
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if not link or link in seen:
            continue
        seen.add(link)
        out.append({"title": title[:120], "url": link, "snippet": desc[:300]})
    return out


class ChromiumSearcher:
    """持有一个 BrowserEngine，按需执行浏览器搜索。"""

    def __init__(self, ws_settings: Any, browser_settings: Any) -> None:
        self._ws = ws_settings
        self._nav_timeout = int(
            getattr(browser_settings, "nav_timeout_ms", _NAV_TIMEOUT_DEFAULT)
        )
        # 搜索恒无头；launch 参数与 BrowserEngine 的读取字段对齐
        self._engine_settings = SimpleNamespace(
            headless=True,
            executable_path=getattr(browser_settings, "executable_path", ""),
            proxy=(ws_settings.proxy or "").strip(),
        )
        self.engine = BrowserEngine(self._engine_settings)
        # 串行化搜索：一次只用一个临时 context，避免并发拉起过多页面。
        # 懒创建：Python 3.9 在无运行中的事件循环时构造 Lock 会报错
        self._lock = None

    async def search(
        self, query: str, engine_name: str, num: int
    ) -> List[Dict[str, str]]:
        if engine_name not in ("bing", "baidu"):
            engine_name = "bing"
        num = max(1, int(num))
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if engine_name == "bing":
                results = await self._bing(query)
            else:
                results = await self._baidu(query)
        if not results:
            raise RuntimeError("搜索未解析到结果")
        return results[:num]

    async def _new_context(self, browser: Any) -> Any:
        """新建隐身上下文：zh-CN / Chrome UA / 搜索代理，并拦截无用媒体资源。"""
        proxy = (self._ws.proxy or "").strip() or None
        context = await browser.new_context(
            locale="zh-CN",
            user_agent=_UA,
            proxy={"server": proxy} if proxy else None,
        )
        await context.route("**/*", self._prune_route)
        return context

    @staticmethod
    async def _prune_route(route: Any) -> None:
        if route.request.resource_type in ("image", "media", "font"):
            await route.abort()
        else:
            await route.continue_()

    # ---- Bing：RSS 优先，失败回退浏览器渲染 ----

    async def _bing(self, query: str) -> List[Dict[str, str]]:
        try:
            return await self._bing_rss(query)
        except Exception as exc:
            logger.info("Bing RSS 不可用，回退浏览器渲染: %r", exc)
        return await self._bing_browser(query)

    async def _bing_rss(self, query: str) -> List[Dict[str, str]]:
        """必应 RSS 数据接口（&format=rss）：httpx 直连干净 XML，无需启动浏览器。"""
        proxy = (self._ws.proxy or "").strip() or None
        host = "www.bing.com" if proxy else "cn.bing.com"
        url = "https://%s/search?q=%s&format=rss" % (host, quote(query))
        async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})
        if resp.status_code != 200:
            raise RuntimeError("Bing RSS HTTP %d" % resp.status_code)
        results = _parse_bing_rss(resp.text)
        if not results:
            raise RuntimeError("Bing RSS 未解析到条目")
        return results

    async def _bing_browser(self, query: str) -> List[Dict[str, str]]:
        """RSS 不可用时的兜底：真实浏览器打开 SERP 并提取。"""
        browser = await self.engine.browser()
        context = await self._new_context(browser)
        try:
            page = await context.new_page()
            page.set_default_timeout(self._nav_timeout)
            # 国内直连 cn.bing.com；配了代理（海外出口）走 www.bing.com
            host = "www.bing.com" if (self._ws.proxy or "").strip() else "cn.bing.com"
            await page.goto(
                "https://%s/search?q=%s&setlang=zh-CN" % (host, quote(query)),
                wait_until="domcontentloaded", timeout=self._nav_timeout,
            )
            try:
                await page.wait_for_selector("li.b_algo", timeout=8000)
                # 容器出现时标题/摘要文本可能尚未注入（Bing 偶发的分步渲染变体），
                # 等首条结果有标题文本再提取，否则会拿到空标题/空摘要
                await page.wait_for_function(
                    """() => {
                      const a = document.querySelector('li.b_algo h2 a');
                      return !!(a && ((a.innerText || a.textContent) || '').trim());
                    }""",
                    timeout=4000,
                )
            except Exception:
                pass
            results = await self._extract_bing(page)
            # 兜底：仍有空标题（文本注入更晚）时稍等再取一次
            if any(not (r.get("title") or "").strip() for r in results):
                await page.wait_for_timeout(800)
                results = await self._extract_bing(page)
            return results
        finally:
            await context.close()

    @staticmethod
    async def _extract_bing(page: Any) -> List[Dict[str, str]]:
        return await page.evaluate(
            """() => {
              const textOf = el => ((el && (el.innerText || el.textContent)) || '');
              const out = [];
              document.querySelectorAll('li.b_algo').forEach(li => {
                const a = li.querySelector('h2 a');
                const p = li.querySelector('.b_caption p, p');
                if (a) out.push({title: textOf(a).trim().slice(0, 120),
                                 url: a.href,
                                 snippet: textOf(p).trim().slice(0, 300)});
              });
              return out;
            }"""
        )

    # ---- 百度 ----

    async def _baidu(self, query: str) -> List[Dict[str, str]]:
        browser = await self.engine.browser()
        context = await self._new_context(browser)
        try:
            page = await context.new_page()
            page.set_default_timeout(self._nav_timeout)
            # 先访问首页拿 BAIDUID cookie，直接搜 /s 更容易触发安全验证
            await page.goto(
                "https://www.baidu.com/",
                wait_until="domcontentloaded",
                timeout=self._nav_timeout,
            )
            url = "https://www.baidu.com/s?wd=%s&ie=utf-8" % quote(query)
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self._nav_timeout
            )
            info = await page.evaluate(
                """() => {
                  if (/wappass|captcha/i.test(location.href))
                    return {captcha: true, results: []};
                  const textOf = el => ((el && (el.innerText || el.textContent)) || '');
                  const out = [];
                  document.querySelectorAll(
                    '#content_left .c-container, #content_left [class*="result"]'
                  ).forEach(d => {
                    if (d.hasAttribute('data-tuiguang')) return;  // 推广条目
                    const a = d.querySelector('h3 a');
                    if (!a) return;
                    const c = d.querySelector(
                      '[class*="content-right"], [class*="abstract"], .c-abstract'
                    );
                    out.push({title: textOf(a).trim().slice(0, 120), url: a.href,
                              snippet: textOf(c).trim().slice(0, 300)});
                  });
                  const head = document.body.innerText.slice(0, 1500);
                  return {captcha: /百度安全验证|请输入验证码/.test(head), results: out};
                }"""
            )
            if info.get("captcha"):
                logger.info("百度返回验证码页: %s", page.url)
                raise BaiduCaptcha(page.url)
            results = self._dedupe(info.get("results") or [])
            await self._unwrap_baidu_links(results)
            return results
        finally:
            await context.close()

    @staticmethod
    def _dedupe(results: List[Dict[str, str]]) -> List[Dict[str, str]]:
        seen = set()
        out: List[Dict[str, str]] = []
        for r in results:
            key = r.get("url") or r.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    async def _unwrap_baidu_links(self, results: List[Dict[str, str]]) -> None:
        """把 www.baidu.com/link?url= 跳转链并发解包成真实 URL；失败保留原链。"""
        pending = [r for r in results if "baidu.com/link" in (r.get("url") or "")]
        if not pending:
            return
        proxy = (self._ws.proxy or "").strip() or None
        async with httpx.AsyncClient(
            follow_redirects=False, proxy=proxy, timeout=15
        ) as client:
            async def one(item: Dict[str, str]) -> None:
                try:
                    req = client.build_request(
                        "GET", item["url"], headers={"User-Agent": _UA}
                    )
                    resp = await client.send(req)
                    location = resp.headers.get("location")
                    if location:
                        item["url"] = str(
                            httpx.URL(item["url"]).join(location)
                        )
                except Exception:
                    pass

            await asyncio.gather(*[one(item) for item in pending[:10]])

    async def aclose(self) -> None:
        await self.engine.aclose()
