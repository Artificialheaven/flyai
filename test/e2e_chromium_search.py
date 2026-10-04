"""端到端验证：真实无头 Chromium 跑 Bing / 百度搜索（手动执行，需联网，不进 pytest）。

用法：
    python test/e2e_chromium_search.py
    # 走代理：HTTP_PROXY=http://127.0.0.1:7897 python test/e2e_chromium_search.py

覆盖：
- Bing 两条查询：各断言 ≥8 条真实 http(s) 外链
  （产品代码优先走 Bing RSS：httpx 直连 &format=rss，不启动浏览器；
  RSS 失败才回退浏览器渲染）；
- 百度一条查询：
  * 未遇验证码 → 结果链接均已解包（不再有 baidu.com/link?url= 跳转链）；
  * 遇验证码     → 自动回退 Bing（输出含回退说明，结果仍为真实外链）。

整条链路走 WebSearchTool + ChromiumSearcher 的产品代码，与线上路径一致。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import BrowserSettings, WebSearchSettings
from flyai.tools.base import ToolContext
from flyai.tools.web_search_tool import WebSearchTool

BING_QUERIES = ["Python asyncio 教程", "2026 国庆节放假安排"]
BAIDU_QUERY = "哔哩哔哩"
MIN_RESULTS = 8

_URL_RE = re.compile(r"^\s*URL:\s*(\S+)\s*$", re.MULTILINE)


def _urls(output):
    return _URL_RE.findall(output)


async def main():
    proxy = (os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "").strip()
    failures = []

    ws = WebSearchSettings(engine="chromium", search_engine="bing", proxy=proxy)
    browser_cfg = BrowserSettings()
    # 直接构造搜索器；测试结束关掉 Chromium
    from flyai.browser.search import ChromiumSearcher

    searcher = ChromiumSearcher(ws, browser_cfg)
    ctx = ToolContext(services={"chromium_search": searcher})
    try:
        # ---- Bing：两条查询 ----
        for q in BING_QUERIES:
            tool = WebSearchTool(ws)
            result = await tool.run({"query": q}, ctx)
            if not result.ok:
                failures.append("Bing 查询 %r 工具失败: %s" % (q, result.output))
                continue
            urls = _urls(result.output)
            external = [u for u in urls
                        if u.startswith(("http://", "https://"))
                        and "bing.com" not in u]
            print("[Bing] %r -> %d 条（外链 %d）" % (q, len(urls), len(external)))
            for u in urls[:3]:
                print("    ", u)
            if len(external) < MIN_RESULTS:
                failures.append(
                    "Bing 查询 %r 真实外链不足：%d < %d" % (q, len(external), MIN_RESULTS)
                )

        # ---- 百度：一条查询（成功解包 / 验证码回退，二者必居其一）----
        ws_baidu = WebSearchSettings(
            engine="chromium", search_engine="baidu", proxy=proxy
        )
        tool = WebSearchTool(ws_baidu)
        result = await tool.run({"query": BAIDU_QUERY}, ctx)
        if not result.ok:
            failures.append("百度查询工具失败（回退也失败）: %s" % result.output)
        else:
            urls = _urls(result.output)
            fell_back = "已自动改用 Bing" in result.output
            print("[百度] %r -> %d 条，回退=%s" % (BAIDU_QUERY, len(urls), fell_back))
            for u in urls[:3]:
                print("    ", u)
            if fell_back:
                external = [u for u in urls if u.startswith(("http://", "https://"))]
                if len(external) < 3:
                    failures.append(
                        "百度回退 Bing 后结果过少：%d" % len(external)
                    )
            else:
                wrapped = [u for u in urls if "baidu.com/link?url=" in u]
                if wrapped:
                    failures.append(
                        "百度结果存在 %d 条未解包跳转链" % len(wrapped)
                    )
                if len(urls) < 3:
                    failures.append("百度结果过少：%d" % len(urls))
    finally:
        await searcher.aclose()

    if failures:
        print("\nE2E 失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("\nE2E 全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
