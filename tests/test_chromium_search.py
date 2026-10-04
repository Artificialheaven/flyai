"""ChromiumSearcher 单测：Bing RSS 解析与优先/回退编排、上下文构造。

全部离线：httpx 用 MockTransport 返回夹具 XML；浏览器路径用 monkeypatch
替换，不真正启动 Chromium。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest

from flyai.browser import search as smod
from flyai.browser.search import ChromiumSearcher, _parse_bing_rss
from flyai.config import WebSearchSettings
from types import SimpleNamespace


# ---------- 夹具 ----------

RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>必应：测试</title>
<item>
  <title>第一条 &amp; 标题</title>
  <link>https://a.example/p1</link>
  <description>第一条的摘要内容</description>
</item>
<item>
  <title>第二条</title>
  <link>https://b.example/p2</link>
  <description>第二条摘要</description>
</item>
<item>
  <title>重复链接条</title>
  <link>https://a.example/p1</link>
  <description>应被去重</description>
</item>
</channel></rss>"""


def _searcher(**ws_kw):
    ws = WebSearchSettings(**ws_kw)
    br = SimpleNamespace(nav_timeout_ms=20000, executable_path="")
    return ChromiumSearcher(ws, br)


def _install_transport(monkeypatch, handler):
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *a, **k):
        # MockTransport 与 proxy mount 冲突，去掉 proxy
        k.pop("proxy", None)
        k["transport"] = httpx.MockTransport(handler)
        orig_init(self, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _run(coro):
    return asyncio.run(coro)


# ---------- _parse_bing_rss ----------

def test_parse_bing_rss_basic():
    res = _parse_bing_rss(RSS_XML)
    assert len(res) == 2  # 第三条链接重复，去重
    assert res[0] == {
        "title": "第一条 & 标题",
        "url": "https://a.example/p1",
        "snippet": "第一条的摘要内容",
    }
    assert res[1]["url"] == "https://b.example/p2"
    assert res[1]["snippet"] == "第二条摘要"


def test_parse_bing_rss_empty():
    assert _parse_bing_rss("<rss><channel/></rss>") == []


@pytest.mark.parametrize("text", [
    "",
    "<html>not xml feed</html>",
    "<?xml version='1.0'?><rss><channel><item><title>无链接</title></item></channel></rss>",
])
def test_parse_bing_rss_bad_input(text):
    assert _parse_bing_rss(text) == []


def test_parse_bing_rss_malformed_xml():
    assert _parse_bing_rss("<?xml version='1.0'?><rss><item><title>") == []


def test_parse_bing_rss_truncates_long_fields():
    long_t = "题" * 200
    long_d = "摘" * 400
    xml = ("<rss><channel><item><title>%s</title>"
           "<link>https://x.example/</link><description>%s</description>"
           "</item></channel></rss>" % (long_t, long_d))
    res = _parse_bing_rss(xml)
    assert len(res[0]["title"]) == 120
    assert len(res[0]["snippet"]) == 300


# ---------- Bing：RSS 优先，不启动浏览器 ----------

def test_bing_uses_rss_without_browser(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, text=RSS_XML,
            headers={"Content-Type": "application/xml"},
        )

    _install_transport(monkeypatch, handler)
    searcher = _searcher()

    async def boom():
        raise RuntimeError("RSS 成功时不应启动浏览器")

    monkeypatch.setattr(searcher.engine, "browser", boom)
    results = _run(searcher.search("测试", "bing", 10))
    assert len(results) == 2
    assert results[0]["title"] == "第一条 & 标题"


def test_bing_rss_num_slice(monkeypatch):
    def handler(request):
        return httpx.Response(200, text=RSS_XML)

    _install_transport(monkeypatch, handler)
    searcher = _searcher()
    results = _run(searcher.search("测试", "bing", 1))
    assert len(results) == 1


def test_bing_rss_request_url(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text=RSS_XML)

    _install_transport(monkeypatch, handler)
    _run(_searcher().search("原神 7.1", "bing", 8))
    assert "format=rss" in seen["url"]
    assert "cn.bing.com" in seen["url"]


def test_bing_rss_with_proxy_uses_www_host(monkeypatch):
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, text=RSS_XML)

    _install_transport(monkeypatch, handler)
    _run(_searcher(proxy="http://127.0.0.1:7897").search("x", "bing", 8))
    assert seen["host"] == "www.bing.com"


# ---------- Bing：RSS 失败 → 浏览器渲染回退 ----------

def test_bing_falls_back_to_browser(monkeypatch):
    calls = {"rss": 0, "browser": 0}

    async def bad_rss(self, query):
        calls["rss"] += 1
        raise RuntimeError("rss down")

    async def fake_browser(self, query):
        calls["browser"] += 1
        return [{"title": "浏览器结果", "url": "https://x", "snippet": ""}]

    monkeypatch.setattr(ChromiumSearcher, "_bing_rss", bad_rss)
    monkeypatch.setattr(ChromiumSearcher, "_bing_browser", fake_browser)
    results = _run(_searcher().search("q", "bing", 8))
    assert calls == {"rss": 1, "browser": 1}
    assert results[0]["title"] == "浏览器结果"


def test_bing_rss_non_xml_response_falls_back(monkeypatch):
    def handler(request):
        # RSS 端点偶发返回 HTML 页时应触发回退
        return httpx.Response(200, text="<html>bing html page</html>")

    _install_transport(monkeypatch, handler)
    used_browser = {"v": False}

    async def fake_browser(self, query):
        used_browser["v"] = True
        return [{"title": "T", "url": "https://x", "snippet": "S"}]

    monkeypatch.setattr(ChromiumSearcher, "_bing_browser", fake_browser)
    results = _run(_searcher().search("q", "bing", 8))
    assert used_browser["v"]
    assert results[0]["snippet"] == "S"


def test_bing_rss_http_error_falls_back(monkeypatch):
    def handler(request):
        return httpx.Response(500)

    _install_transport(monkeypatch, handler)
    used_browser = {"v": False}

    async def fake_browser(self, query):
        used_browser["v"] = True
        return [{"title": "T", "url": "https://x", "snippet": "S"}]

    monkeypatch.setattr(ChromiumSearcher, "_bing_browser", fake_browser)
    _run(_searcher().search("q", "bing", 8))
    assert used_browser["v"]
