"""联网工具单测：web_search 结果列表、ensearch 重试，与 web_fetch 抓页/安全边界。"""

import asyncio
import os
import re
import sys
from html import unescape

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest

from flyai.config import WebSearchSettings
from flyai.tools import web_search_tool as mod
from flyai.tools.base import ToolContext
from flyai.tools.web_search_tool import WebFetchTool, WebSearchTool


def _settings(**kw):
    # 测试助手显式默认 httpx 路径（大量用 MockTransport 的离线测试）；
    # 产品代码的真实默认值由 test_engine_defaults 单独断言
    kw.setdefault("engine", "http")
    return WebSearchSettings(**kw)


def _ctx(**kw):
    return ToolContext(services=dict(kw))


# ---------- HTML 夹具 ----------

def _bing_html(host_a="example.com", n=3):
    items = []
    for i in range(n):
        items.append(
            '<li class="b_algo"><h2><a href="https://%s/p%d">标题%d</a></h2>'
            '<p>摘要%d&ensp;内容&#0183;x</p></li>' % (host_a, i, i, i)
        )
    return '<ul>' + "".join(items) + "</ul>"


def _page_html(title, text):
    return ("<html><head><title>%s</title></head><body>"
            "<nav>导航菜单</nav><h1>%s</h1><p>%s</p></body></html>"
            % (title, title, text))


_HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


def _html_response(body, status=200):
    return httpx.Response(status, text=body, headers=_HTML_HEADERS)


def _install_transport(monkeypatch, handler):
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *a, **k):
        # 注入 MockTransport 时去掉 proxy，否则 httpx 的代理 mount 会覆盖 transport
        k.pop("proxy", None)
        k["transport"] = httpx.MockTransport(handler)
        orig_init(self, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


# ---------- web_search：结果列表 ----------

def test_search_returns_result_list(monkeypatch):
    def handler(request):
        return httpx.Response(200, text=_bing_html())

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "北京天气"}, _ctx()
    ))
    assert result.ok
    out = result.output
    assert "共 3 条结果" in out
    assert "1. 标题0" in out
    assert "URL: https://example.com/p0" in out
    assert "摘要0 内容·x" in out
    # 明确指引当前 AI 自行 web_fetch
    assert "web_fetch" in out
    # 不抓取正文：输出里不应出现页面正文（导航来自页面，搜索结果页不含它）
    assert "导航菜单" not in out


def test_proxy_uses_www_bing(monkeypatch):
    captured = {"hosts": set()}

    def handler(request):
        captured["hosts"].add(request.url.host)
        return httpx.Response(200, text=_bing_html())

    _install_transport(monkeypatch, handler)
    asyncio.run(WebSearchTool(_settings(proxy="http://127.0.0.1:7897")).run(
        {"query": "x"}, _ctx()
    ))
    assert captured["hosts"] == {"www.bing.com"}


def test_missing_query():
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "  "}, _ctx()
    ))
    assert not result.ok and "参数错误" in result.output


def test_search_does_not_require_provider():
    # 重构后搜索不再依赖 services 注入 provider；无网络时仅因连接失败而报错，
    # 不应出现「提供商上下文」类错误
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "x"}, ToolContext(services={})
    ))
    assert "提供商" not in result.output


def test_no_results(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="<ul></ul>")

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "x"}, _ctx()
    ))
    assert not result.ok and "没有搜索到" in result.output


def test_search_http_error(monkeypatch):
    def handler(request):
        return httpx.Response(500)

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "x"}, _ctx()
    ))
    assert not result.ok and "搜索请求失败" in result.output


def test_results_num_clamped(monkeypatch):
    seen = {}

    def handler(request):
        seen["count"] = request.url.params.get("count")
        return httpx.Response(200, text=_bing_html(n=3))

    _install_transport(monkeypatch, handler)
    asyncio.run(WebSearchTool(_settings(results_num=99)).run(
        {"query": "x"}, _ctx()
    ))
    assert seen["count"] == "10"


# ---------- 低质结果自动 ensearch 重试 ----------

def _bad_html():
    return ('<li class="b_algo"><h2><a href="https://zidian.example/zhan">'
            '战（汉语文字）</a></h2><p>战字解释</p></li>')


def _good_html():
    return ('<li class="b_algo"><h2>'
            '<a href="https://pgr.example/news">战双帕弥什 更新公告</a></h2>'
            '<p>战双帕弥什新版本更新内容公告</p></li>')


def test_auto_ensearch_retry_on_bad_results(monkeypatch):
    calls = []

    def handler(request):
        ensearch = request.url.params.get("ensearch")
        calls.append(ensearch)
        return httpx.Response(200, text=_good_html() if ensearch else _bad_html())

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "战双帕弥什 更新公告"}, _ctx()
    ))
    assert result.ok
    # 第一次普通、第二次带 ensearch=1
    assert calls == [None, "1"]
    # 最终列表采用了重试得到的好结果
    assert "pgr.example/news" in result.output
    assert "战双帕弥什 更新公告" in result.output
    assert "zidian.example" not in result.output


def test_keeps_first_results_when_retry_also_bad(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.params.get("ensearch"))
        return httpx.Response(200, text=_bad_html())

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebSearchTool(_settings()).run(
        {"query": "战双帕弥什 更新公告"}, _ctx()
    ))
    # 重试也差时流程不中断，仍用首搜结果返回列表
    assert result.ok and "zidian.example" in result.output
    assert calls == [None, "1"]


# ---------- web_fetch：成功路径 ----------

def test_fetch_success(monkeypatch):
    def handler(request):
        return _html_response(_page_html("示例页面", "这是正文内容" * 5))

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/page"}, _ctx()
    ))
    assert result.ok
    assert "[网页] 示例页面" in result.output
    assert "URL: https://example.com/page" in result.output
    assert "这是正文内容" in result.output
    # nav 被剔除
    assert "导航菜单" not in result.output


def test_fetch_gbk_encoding(monkeypatch):
    body = _page_html("GBK 页", "这是 GBK 编码的正文内容")

    def handler(request):
        return httpx.Response(
            200,
            content=body.encode("gbk", "ignore"),
            headers={"Content-Type": "text/html; charset=gbk"},
        )

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/"}, _ctx()
    ))
    assert result.ok and "这是 GBK 编码的正文内容" in result.output


def test_fetch_pagination_first_page_hint(monkeypatch):
    def handler(request):
        return _html_response(_page_html("长页面", "正文内容" * 200))

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings(max_page_chars=200)).run(
        {"url": "https://example.com/"}, _ctx()
    ))
    assert result.ok
    assert "内容 1–200 / 共" in result.output
    assert "offset=200 取下一段" in result.output
    assert "已到正文末尾" not in result.output


def test_fetch_pagination_uses_cache_no_refetch(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return _html_response(_page_html("长页面", "正文内容" * 200))

    _install_transport(monkeypatch, handler)
    cache = mod.PageTextCache()
    ctx = _ctx(page_cache=cache)
    tool = WebFetchTool(_settings(max_page_chars=200))

    first = asyncio.run(tool.run({"url": "https://example.com/"}, ctx))
    assert first.ok and calls["n"] == 1
    # 第二页：缓存命中，不重复下载
    second = asyncio.run(tool.run(
        {"url": "https://example.com/", "offset": 200}, ctx
    ))
    assert second.ok and calls["n"] == 1
    assert "内容 201–400 / 共" in second.output
    assert "offset=400 取下一段" in second.output


def test_fetch_pagination_walk_to_end(monkeypatch):
    def handler(request):
        return _html_response(_page_html("长页面", "正文内容" * 200))

    _install_transport(monkeypatch, handler)
    cache = mod.PageTextCache()
    ctx = _ctx(page_cache=cache)
    tool = WebFetchTool(_settings(max_page_chars=200))

    offset = 0
    pages = 0
    out = asyncio.run(tool.run({"url": "https://example.com/"}, ctx))
    while "已到正文末尾" not in out.output:
        m = re.search(r"offset=(\d+) 取下一段", out.output)
        assert m, out.output
        offset = int(m.group(1))
        out = asyncio.run(tool.run(
            {"url": "https://example.com/", "offset": offset}, ctx
        ))
        pages += 1
        assert pages <= 6
    # 最后一段区间以总字数收尾
    total = int(re.search(r"共 (\d+) 字", out.output).group(1))
    assert "内容 %d–%d / 共 %d 字" % (offset + 1, total, total) in out.output
    assert "取下一段" not in out.output


def test_fetch_offset_beyond_total(monkeypatch):
    def handler(request):
        return _html_response(_page_html("短页", "正文"))

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/", "offset": 500},
        _ctx(page_cache=mod.PageTextCache()),
    ))
    assert not result.ok and "超出正文总长度" in result.output


def test_page_cache_expiry():
    c = mod.PageTextCache(ttl=-10)
    c.put("u", "正文")
    assert c.get("u") is None


def test_page_cache_max_entries_evicts_oldest():
    c = mod.PageTextCache(ttl=600, max_entries=2)
    c.put("a", "1")
    c.put("b", "2")
    c.put("c", "3")
    assert c.get("a") is None
    assert c.get("b") == "2"
    assert c.get("c") == "3"


# ---------- web_fetch：参数 / 安全边界 ----------

def test_fetch_missing_url():
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "  "}, _ctx()
    ))
    assert not result.ok and "参数错误" in result.output


def test_fetch_bad_scheme():
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "ftp://example.com/a"}, _ctx()
    ))
    assert not result.ok and "http" in result.output


def test_fetch_relative_url():
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "example.com/page"}, _ctx()
    ))
    assert not result.ok and "http" in result.output


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://localhost/x",
    "http://192.168.1.1/",
    "http://10.0.0.1/",
    "http://[::1]/",
])
def test_fetch_blocks_private_hosts(url):
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": url}, _ctx()
    ))
    assert not result.ok and "内网或本机" in result.output


# ---------- web_fetch：HTTP 异常路径 ----------

def test_fetch_http_error(monkeypatch):
    def handler(request):
        return httpx.Response(500)

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/"}, _ctx()
    ))
    assert not result.ok and "HTTP 500" in result.output


def test_fetch_binary_content_type_rejected(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, content=b"%PDF-1.4 binary",
            headers={"Content-Type": "application/pdf"},
        )

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/a.pdf"}, _ctx()
    ))
    assert not result.ok and "不是可读的文本内容" in result.output


def test_fetch_json_endpoint(monkeypatch):
    payload = {"items": [{"title": "动态内容", "time": "2026-09-28"}], "total": 1}

    def handler(request):
        return httpx.Response(
            200, json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/api/news"}, _ctx()
    ))
    assert result.ok
    assert "[JSON 数据]" in result.output
    assert '"title": "动态内容"' in result.output


def test_fetch_plain_text(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, content="纯文本接口内容".encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/txt"}, _ctx()
    ))
    assert result.ok and "[文本]" in result.output and "纯文本接口内容" in result.output


def test_fetch_rss_feed(monkeypatch):
    feed = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        '<title>站点更新</title><link>https://example.com</link>'
        '<item><title>新版本公告</title><link>https://example.com/n1</link>'
        '<description>更新内容正文</description></item></channel></rss>'
    )

    def handler(request):
        return httpx.Response(
            200, content=feed.encode("utf-8"),
            headers={"Content-Type": "application/rss+xml; charset=utf-8"},
        )

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/feed"}, _ctx()
    ))
    assert result.ok
    assert "[订阅源]" in result.output
    assert "新版本公告" in result.output and "https://example.com/n1" in result.output


def test_fetch_xml_sniffed_as_feed(monkeypatch):
    feed = '<?xml version="1.0"?><feed><title>A</title></feed>'

    def handler(request):
        return httpx.Response(
            200, content=feed.encode("utf-8"),
            headers={"Content-Type": "text/xml"},
        )

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/atom"}, _ctx()
    ))
    assert result.ok and "[订阅源]" in result.output


def test_fetch_sniffs_content_type_when_missing(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b'{"a": 1}')

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/x"}, _ctx()
    ))
    assert result.ok and "[JSON 数据]" in result.output


def test_fetch_keeps_noscript_fallback(monkeypatch):
    body = (
        '<html><body><div id="app"></div>'
        '<noscript>无 JS 降级正文：最新公告内容</noscript></body></html>'
    )

    def handler(request):
        return _html_response(body)

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/"}, _ctx()
    ))
    assert result.ok and "无 JS 降级正文" in result.output


def test_fetch_empty_body(monkeypatch):
    def handler(request):
        return _html_response("<html><body></body></html>")

    _install_transport(monkeypatch, handler)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://example.com/"}, _ctx()
    ))
    assert not result.ok and "没有可提取" in result.output


# ---------- web_fetch：重定向 ----------

def _fake_resolver(monkeypatch, blocked_hosts=()):
    """把 DNS 校验替换成假解析：blocked_hosts 抛 BlockedHost，其余返稳定假 IP。"""
    async def fake_resolve_and_check(hostname):
        h = (hostname or "").strip().lower().rstrip(".")
        if h in blocked_hosts:
            raise mod.BlockedHost("域名 %s 解析到内网或本机地址" % h)
        import ipaddress as _ipa
        return [_ipa.ip_address("203.0.113.%d" % (1 + (abs(hash(h)) % 250)))]

    monkeypatch.setattr(mod, "resolve_and_check", fake_resolve_and_check)


def test_fetch_follows_redirect(monkeypatch):
    def handler(request):
        # pin 之后 URL 主机已是假 IP：按保留下来的 Host 头分发
        if request.headers.get("host") == "start.com":
            return httpx.Response(
                302, headers={"location": "https://final.com/page"}
            )
        return _html_response(_page_html("最终页", "最终正文内容"))

    _install_transport(monkeypatch, handler)
    _fake_resolver(monkeypatch)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://start.com/"}, _ctx()
    ))
    assert result.ok
    assert "URL: https://final.com/page" in result.output
    assert "最终正文内容" in result.output


def test_fetch_redirect_to_private_is_blocked(monkeypatch):
    def handler(request):
        if request.headers.get("host") == "start.com":
            return httpx.Response(
                302, headers={"location": "http://127.0.0.1:8080/"}
            )
        # 若跟随到内网则不应发生
        return httpx.Response(200, text="内网响应")

    _install_transport(monkeypatch, handler)
    _fake_resolver(monkeypatch, blocked_hosts=("127.0.0.1",))
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://start.com/"}, _ctx()
    ))
    assert not result.ok and "内网或本机" in result.output


def test_fetch_too_many_redirects(monkeypatch):
    def handler(request):
        return httpx.Response(
            302, headers={"location": "https://loop.com/next"}
        )

    _install_transport(monkeypatch, handler)
    _fake_resolver(monkeypatch)
    result = asyncio.run(WebFetchTool(_settings()).run(
        {"url": "https://loop.com/"}, _ctx()
    ))
    assert not result.ok and "重定向次数过多" in result.output


# ---------- 纯函数 ----------

def test_quality_score():
    q = "战双帕弥什 更新公告"
    bad = [{"title": "战（汉语文字）", "url": "u", "snippet": "战字解释"}]
    good = [{"title": "战双帕弥什 更新公告", "url": "u",
             "snippet": "战双帕弥什新版本更新内容公告"}]
    assert mod._quality_score(q, bad) < 0.5
    assert mod._quality_score(q, good) >= 0.5
    assert mod._quality_score("", []) == 1.0


def test_parse_bing():
    res = mod._parse_bing(_bing_html())
    assert len(res) == 3
    assert res[0]["title"] == "标题0"
    assert res[0]["url"] == "https://example.com/p0"
    assert "摘要0 内容·x" == res[0]["snippet"]


def test_parse_bing_dedup():
    html = (
        '<li class="b_algo"><h2><a href="https://a.com">A</a></h2></li>'
        '<li class="b_algo"><h2><a href="https://a.com">A2</a></h2></li>'
    )
    assert len(mod._parse_bing(html)) == 1


def test_unwrap_bing_ck_a_url():
    wrapped = (
        "https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;ptn=3&amp;ver=2"
        "&amp;u=a1aHR0cHM6Ly9wbnMua3Vyb2dhbWVzLWFkcy5jb20v&amp;ntb=1"
    )
    # 先过 unescape（与 _parse_bing 内的处理一致）
    assert mod._unwrap_bing_url(unescape(wrapped)) == "https://pns.kurogames-ads.com/"


def test_unwrap_keeps_direct_url():
    assert mod._unwrap_bing_url("https://example.com/a") == "https://example.com/a"
    assert mod._unwrap_bing_url("https://www.bing.com/search?q=x") == (
        "https://www.bing.com/search?q=x"
    )


def test_parse_bing_unwraps_ck_a():
    html = (
        '<li class="b_algo"><h2><a href="https://cn.bing.com/ck/a?'
        'u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9w&amp;ntb=1">标题</a></h2>'
        '<p>摘要</p></li>'
    )
    res = mod._parse_bing(html)
    assert len(res) == 1
    assert res[0]["url"] == "https://example.com/p"


def test_format_results():
    out = mod._format_results("测试", [
        {"title": "T", "url": "https://a", "snippet": "S"},
    ])
    assert "1. T" in out and "URL: https://a" in out and "摘要：S" in out
    assert "web_fetch" in out


def test_html_to_markdown():
    title, body = mod.html_to_markdown(_page_html("T", "正文文字"))
    assert title == "T"
    text = "\n".join(body)
    assert "正文文字" in text
    assert "导航菜单" not in text


def test_detect_encoding_header():
    r = httpx.Response(200, content=b"<html/>",
                       headers={"Content-Type": "text/html; charset=gbk"})
    assert mod._detect_encoding(r) == "gbk"


def test_detect_encoding_meta():
    r = httpx.Response(200, content=b'<meta charset="gb2312">',
                       headers={"Content-Type": "text/html"})
    assert mod._detect_encoding(r) == "gb2312"


@pytest.mark.parametrize("host,blocked", [
    ("localhost", True),
    ("127.0.0.1", True),
    ("10.1.2.3", True),
    ("192.168.0.1", True),
    ("172.16.0.1", True),
    ("169.254.169.254", True),
    ("::1", True),
    ("0.0.0.0", True),
    ("example.com", False),
    ("github.com", False),
])
def test_is_blocked_host(host, blocked):
    assert mod._is_blocked_host(host) is blocked


# ---------- 正文链接保留为 markdown ----------

@pytest.mark.parametrize("href,expected", [
    ("/detail/3", "/detail/3"),
    ("https://wiki.example.com/p", "https://wiki.example.com/p"),
    ("//cdn.example.com/a.png", "//cdn.example.com/a.png"),
    ("/p?a=1&amp;b=2", "/p?a=1&b=2"),
    ("#section", ""),
    ("javascript:alert(1)", ""),
    ("mailto:a@b.com", ""),
    ("ftp://example.com/x", ""),
    ("", ""),
])
def test_clean_href(href, expected):
    assert mod._clean_href(href) == expected


def test_extract_body_links_markdown():
    html = """<html><body>
      <p>列表如下：</p>
      <a href="/detail/%E6%97%B6%E5%B4%8E">时崎狂三·时崎狂三</a>
      <a href="https://x.com/news">外部新闻</a>
      <a href="#top">纯锚点丢弃</a>
      <a href="javascript:x">脚本链接丢弃</a>
    </body></html>"""
    title, lines = mod.html_to_markdown(html)
    text = "\n".join(lines)
    assert "[时崎狂三·时崎狂三](/detail/%E6%97%B6%E5%B4%8E)" in text
    assert "[外部新闻](https://x.com/news)" in text
    # 被丢弃的链接只留文字
    assert "纯锚点丢弃" in text
    assert "](#" not in text
    assert "脚本链接丢弃" in text


def test_extract_image_link_empty_text_emits_href_only():
    html = """<html><body>
      <a href="/files/a.png"><img src="/files/a.png" alt="图" /></a>
    </body></html>"""
    _, lines = mod.html_to_markdown(html)
    text = "\n".join(lines)
    assert text.strip() == "/files/a.png"
    assert "[](" not in text


def test_links_inside_skipped_nav_not_present():
    html = """<html><body>
      <nav><a href="/secret-nav">导航链接</a></nav>
      <a href="/real">正文链接</a>
    </body></html>"""
    _, lines = mod.html_to_markdown(html)
    text = "\n".join(lines)
    assert "/secret-nav" not in text
    assert "[正文链接](/real)" in text


# ---------- 配置字段：engine / search_engine ----------

def test_engine_defaults():
    ws = WebSearchSettings()
    assert ws.engine == "chromium"
    assert ws.search_engine == "bing"


@pytest.mark.parametrize("value", ["http", "chromium"])
def test_engine_accepts_legal(value):
    assert _settings(engine=value).engine == value


@pytest.mark.parametrize("value", ["", "webkit", "CHROMIUM", " chrome"])
def test_engine_falls_back_on_illegal(value):
    assert WebSearchSettings(engine=value).engine == "chromium"


@pytest.mark.parametrize("value", ["bing", "baidu"])
def test_search_engine_accepts_legal(value):
    assert _settings(search_engine=value).search_engine == value


@pytest.mark.parametrize("value", ["", "google", "Bing", " baidu"])
def test_search_engine_falls_back_on_illegal(value):
    assert _settings(search_engine=value).search_engine == "bing"


# ---------- Chromium 引擎分支 ----------

class _FakeSearcher:
    """记录调用参数的假 ChromiumSearcher。"""

    def __init__(self, results=None, raises=None):
        self._results = results if results is not None else []
        # engine_name -> 异常；或单一异常对所有引擎生效
        self._raises = raises or {}
        self.calls = []

    async def search(self, query, engine_name, num):
        self.calls.append((query, engine_name, num))
        exc = self._raises.get(engine_name) if isinstance(self._raises, dict) \
            else self._raises
        if exc is not None:
            raise exc
        return list(self._results)


def _chromium_results(n=3):
    return [
        {"title": "浏览器标题%d" % i, "url": "https://cr.example/p%d" % i,
         "snippet": "浏览器摘要%d" % i}
        for i in range(n)
    ]


def test_chromium_search_returns_results():
    searcher = _FakeSearcher(results=_chromium_results())
    result = asyncio.run(WebSearchTool(_settings(engine="chromium")).run(
        {"query": "测试"}, _ctx(chromium_search=searcher)
    ))
    assert result.ok
    assert "共 3 条结果" in result.output
    assert "1. 浏览器标题0" in result.output
    assert "URL: https://cr.example/p0" in result.output
    assert "浏览器摘要0" in result.output
    # 调用参数：默认 bing，num 取 results_num
    assert searcher.calls == [("测试", "bing", 8)]


def test_chromium_search_passes_engine_and_num():
    searcher = _FakeSearcher(results=_chromium_results(2))
    settings = _settings(engine="chromium", search_engine="baidu", results_num=5)
    asyncio.run(WebSearchTool(settings).run(
        {"query": "x"}, _ctx(chromium_search=searcher)
    ))
    assert searcher.calls == [("x", "baidu", 5)]


def test_chromium_search_num_clamped():
    searcher = _FakeSearcher(results=[])
    # 空列表仍正常返回（共 0 条），num 已按 3..10 夹取
    settings = _settings(engine="chromium", results_num=99)
    result = asyncio.run(WebSearchTool(settings).run(
        {"query": "x"}, _ctx(chromium_search=searcher)
    ))
    assert result.ok and "共 0 条结果" in result.output
    assert searcher.calls[0][2] == 10


def test_chromium_search_missing_service():
    result = asyncio.run(WebSearchTool(_settings(engine="chromium")).run(
        {"query": "x"}, _ctx()
    ))
    assert not result.ok and "Chromium 搜索服务不可用" in result.output


def test_chromium_search_generic_error():
    searcher = _FakeSearcher(raises=RuntimeError("boom"))
    result = asyncio.run(WebSearchTool(_settings(engine="chromium")).run(
        {"query": "x"}, _ctx(chromium_search=searcher)
    ))
    assert not result.ok and "浏览器搜索失败" in result.output
    assert "RuntimeError" in result.output and "boom" in result.output
    assert len(searcher.calls) == 1


def test_http_engine_rejects_baidu():
    result = asyncio.run(WebSearchTool(
        _settings(engine="http", search_engine="baidu")
    ).run({"query": "x"}, _ctx()))
    assert not result.ok
    assert "百度搜索仅支持 Chromium 引擎" in result.output


def test_baidu_captcha_falls_back_to_bing():
    from flyai.browser.search import BaiduCaptcha

    searcher = _FakeSearcher(
        results=_chromium_results(),
        raises={"baidu": BaiduCaptcha("安全验证"), "bing": None},
    )
    settings = _settings(engine="chromium", search_engine="baidu")
    result = asyncio.run(WebSearchTool(settings).run(
        {"query": "测试词"}, _ctx(chromium_search=searcher)
    ))
    assert result.ok
    # 先百度、后 Bing，同一 query/num
    assert searcher.calls == [
        ("测试词", "baidu", 8),
        ("测试词", "bing", 8),
    ]
    # 标题行之后注明回退，且含 Bing 路径的结果
    lines = result.output.splitlines()
    assert lines[0] == "（web_search「测试词」，共 3 条结果）"
    assert lines[1] == "（百度要求安全验证，已自动改用 Bing 搜索）"
    assert "浏览器标题0" in result.output


def test_baidu_captcha_fallback_notice_only_for_baidu():
    # Bing 自己抛 BaiduCaptcha 不应触发回退逻辑（按普通错误处理）
    from flyai.browser.search import BaiduCaptcha

    searcher = _FakeSearcher(raises={"bing": BaiduCaptcha("奇怪")})
    settings = _settings(engine="chromium", search_engine="bing")
    result = asyncio.run(WebSearchTool(settings).run(
        {"query": "x"}, _ctx(chromium_search=searcher)
    ))
    assert not result.ok and "浏览器搜索失败" in result.output
    assert len(searcher.calls) == 1


def test_baidu_captcha_then_bing_also_fails():
    from flyai.browser.search import BaiduCaptcha

    searcher = _FakeSearcher(
        raises={"baidu": BaiduCaptcha("验证"), "bing": RuntimeError("down")}
    )
    settings = _settings(engine="chromium", search_engine="baidu")
    result = asyncio.run(WebSearchTool(settings).run(
        {"query": "x"}, _ctx(chromium_search=searcher)
    ))
    assert not result.ok and "浏览器搜索失败" in result.output
    assert "RuntimeError" in result.output and "down" in result.output
    assert len(searcher.calls) == 2
