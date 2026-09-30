"""端到端验证：真实 Chromium 逐级浏览本地多页 fixture。

- 首页链接 → 详情页（JS setTimeout 注入内容，证明 JS 被执行）
- target=_blank 链接 → 自动切新标签
- 搜索表单输入回车 → 结果页 → 点进详情
- browser_back / browser_get_text / browser_close

fixture 跑在 127.0.0.1，SSRF 守卫会拦；本脚本仅在测试模块内临时放行
（不改动产品代码、不影响 tests/ 真实套件）。
"""

from __future__ import annotations

import asyncio
import http.server
import os
import socketserver
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import flyai.browser.session as bsess_mod
from flyai.browser.engine import BrowserEngine
from flyai.browser.session import BrowserSession
from types import SimpleNamespace

PORT = 8023

PAGES = {
    "/": """<!doctype html><html><head><title>首页</title></head><body>
      <h1>首页标题</h1>
      <a id="detail-link" href="/detail">进入详情页</a>
      <a href="/popup" target="_blank">打开弹窗页</a>
      <form action="/results" method="get">
        <input id="q" name="q" placeholder="搜索框" />
        <button type="submit">搜索</button>
      </form></body></html>""",
    "/detail": """<!doctype html><html><head><title>详情页</title></head><body>
      <a href="/">返回首页</a>
      <div id="late">加载中…</div>
      <script>setTimeout(function(){
        document.getElementById('late').innerText='异步加载的秘密内容';
      }, 300);</script></body></html>""",
    "/popup": """<!doctype html><html><head><title>弹窗页</title></head>
      <body><h1>弹窗正文</h1></body></html>""",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        body = PAGES.get(path)
        if body is None and path == "/results":
            q = self.path.split("q=", 1)[-1] if "q=" in self.path else ""
            body = ("""<!doctype html><html><head><title>结果页</title></head>
              <body><h1>搜索结果</h1>
              <a href="/detail">匹配的详情（%s）</a></body></html>""" % q)
        if body is None:
            self.send_response(404); self.end_headers(); return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def start_server():
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


async def wait_for(session, needle, tries=10):
    for _ in range(tries):
        snap = await session.snapshot()
        if needle in snap:
            return snap
        await asyncio.sleep(0.3)
    raise AssertionError("等不到内容：" + needle)


async def main():
    settings = SimpleNamespace(
        nav_timeout_ms=10000, idle_seconds=180, max_steps=15,
        max_snapshot_chars=6000,
    )
    engine = BrowserEngine(SimpleNamespace(
        headless=True, executable_path="", proxy="", **settings.__dict__,
    ))
    browser = await engine.browser()
    session = await BrowserSession(settings).attach(browser, None)
    checks = []

    try:
        base = "http://127.0.0.1:%d" % PORT
        snap = await session.open(base + "/")
        checks.append(("首页标题与编号链接", "首页标题" in snap and "[1]" in snap))

        # 找「进入详情页」链接的编号
        import re
        m = re.search(r"\[(\d+)\] 链接「进入详情页」", snap)
        assert m, snap
        await session.click(int(m.group(1)))
        snap = await wait_for(session, "异步加载的秘密内容")
        checks.append(("进入内部页 + JS 异步内容被渲染",
                       "详情页" in snap and "异步加载的秘密内容" in snap))

        text = await session.get_text()
        checks.append(("browser_get_text 取正文",
                       "异步加载的秘密内容" in text and "可交互元素" not in text))

        snap = await session.back()
        checks.append(("browser_back 回首页", "首页标题" in snap))

        # target=_blank
        m = re.search(r"\[(\d+)\] 链接「打开弹窗页」", snap)
        assert m, snap
        await session.click(int(m.group(1)))
        checks.append(("popup 自动切新标签",
                       session._page.url.endswith("/popup")))
        snap = await session.snapshot()
        checks.append(("新标签快照有弹窗正文", "弹窗正文" in snap))

        # 回首页（旧标签仍在）做搜索流程
        pages = session._context.pages
        session._page = pages[0]
        await session._page.bring_to_front()
        snap = await session.snapshot()
        m = re.search(r"\[(\d+)\] 输入框「搜索框」", snap)
        assert m, snap
        snap = await session.type_text(int(m.group(1)), "魔法剑", True)
        checks.append(("搜索提交到结果页",
                       "搜索结果" in snap and "结果页" in snap))
        m = re.search(r"\[(\d+)\] 链接「匹配的详情", snap)
        assert m, snap
        snap = await session.click(int(m.group(1)))
        snap = await wait_for(session, "异步加载的秘密内容")
        checks.append(("结果页点进详情", "详情页" in snap))
    finally:
        await session.close()
        await engine.aclose()

    ok = True
    for name, passed in checks:
        print(("PASS" if passed else "FAIL"), name)
        ok = ok and passed
    if not ok:
        sys.exit(1)
    print("全部 %d 项端到端断言通过" % len(checks))


if __name__ == "__main__":
    # 本地 fixture：临时放行 loopback（仅本脚本进程）
    bsess_mod.is_blocked_host = lambda host: False
    httpd = start_server()
    try:
        asyncio.run(main())
    finally:
        httpd.shutdown()
