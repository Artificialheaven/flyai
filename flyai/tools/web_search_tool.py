"""联网工具：web_search 检索 + web_fetch 抓页面，由当前会话 AI 自主编排。

- web_search(query)：向 Bing 请求结果（国内直连 cn.bing.com；配了代理走
  www.bing.com），解析出标题 / 真实 URL / 摘要，以编号列表返回。
  不抓取正文、不开子线程。
- web_fetch(url)：抓取单个网页，编码探测 + HTML 提纯为 markdown 文本，
  截断到配置的 max_page_chars。

主 AI 自己阅读搜索摘要、判断相关性，决定对哪些 URL（通常 1~3 条）调用
web_fetch，然后基于抓取到的内容作答。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import time
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlsplit

import httpx

from ..config import WebSearchSettings
from ..netguard import is_blocked_host
from .base import Tool, ToolContext, ToolResult

# 兼容旧引用（tests 以 mod._is_blocked_host 调用）
_is_blocked_host = is_blocked_host

logger = logging.getLogger("flyai.websearch")

# 结果质量：top5 标题+摘要对查询字符的覆盖率低于此值 → 判低质，换国际版重试
_QUALITY_MIN = 0.5
# 单次抓取的响应体大小上限（10MB），防止下载超大文件
_MAX_FETCH_BYTES = 10 * 1024 * 1024
_MAX_REDIRECTS = 5
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "联网搜索：当你需要最新信息、自己知识范围外或不确定的事实性内容时调用。"
        "query 传搜索关键词。工具返回编号的结果列表（标题、URL、摘要），"
        "本工具不抓取网页正文。你需要自己阅读摘要判断哪些结果相关，"
        "再对选定的 URL（通常 1~3 条）调用 web_fetch 获取完整正文，"
        "然后基于抓取到的内容作答。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, settings: WebSearchSettings):
        self.settings = settings

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, output="[参数错误] 请提供搜索关键词（query）")

        proxy = (self.settings.proxy or "").strip() or None
        # 国内直连用 cn.bing.com；有代理（海外/本机代理出口）用 www.bing.com
        host = "www.bing.com" if proxy else "cn.bing.com"
        results_num = max(3, min(int(self.settings.results_num or 8), 10))

        started = time.monotonic()
        logger.info(
            "web_search 开始: query=%r host=%s num=%d proxy=%s",
            query, host, results_num, proxy or "直连",
        )

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            proxy=proxy,
        ) as client:
            try:
                results = await _search_bing(client, host, query, results_num)
            except Exception as exc:
                logger.warning("搜索请求失败: %s: %s", type(exc).__name__, exc)
                return ToolResult(
                    ok=False,
                    output=f"[工具错误] 搜索请求失败: {type(exc).__name__}: {exc}",
                )
            score = _quality_score(query, results)
            _log_results("首搜结果", results, score)
            # Bing 会对部分中文资讯类查询返回被过滤的无关结果：
            # 质量不达标时带 ensearch=1（国际版通道，仍可返中文结果）重试一次
            if score < _QUALITY_MIN:
                logger.info("首搜质量 %.2f < %.2f，以 ensearch=1 重试", score, _QUALITY_MIN)
                try:
                    results2 = await _search_bing(
                        client, host, query, results_num, international=True
                    )
                except Exception as exc:
                    logger.warning("ensearch 重试失败: %s: %s", type(exc).__name__, exc)
                    results2 = []
                score2 = _quality_score(query, results2)
                _log_results("ensearch 重试结果", results2, score2)
                if results2 and score2 >= score:
                    results, score = results2, score2

        if not results:
            return ToolResult(
                ok=False, output=f"[工具错误] 没有搜索到与「{query}」相关的结果"
            )
        logger.info("web_search 完成，总耗时 %.1fs", time.monotonic() - started)
        return ToolResult(ok=True, output=_format_results(query, results))


# 抓取后完整正文的缓存时长 / 容量（分页翻页期间不重复下载）
_PAGE_CACHE_TTL = 600
_PAGE_CACHE_MAX = 64


class PageTextCache:
    """final_url -> (完整正文, 抓取时间)。短 TTL，供 web_fetch 分页切片。"""

    def __init__(self, ttl: int = _PAGE_CACHE_TTL, max_entries: int = _PAGE_CACHE_MAX):
        self._ttl = int(ttl)
        self._max = int(max_entries)
        self._data: Dict[str, Tuple[str, float]] = {}

    def get(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if item is None:
            return None
        if time.time() - item[1] > self._ttl:
            self._data.pop(key, None)
            return None
        return item[0]

    def put(self, key: str, text: str) -> None:
        self._prune()
        if key not in self._data and len(self._data) >= self._max:
            # 容量满：淘汰最旧的一条
            oldest = min(self._data.items(), key=lambda kv: kv[1][1])[0]
            self._data.pop(oldest, None)
        self._data[key] = (text, time.time())

    def _prune(self) -> None:
        now = time.time()
        dead = [k for k, v in self._data.items() if now - v[1] > self._ttl]
        for k in dead:
            self._data.pop(k, None)


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "抓取单个网页/接口的内容。通常在 web_search 之后使用："
        "把结果列表中你判断最相关的 URL 传入，一次只传一个 URL；"
        "需要多个页面就多次调用。支持 HTML 网页（整理为 markdown）、"
        "JSON 数据接口、纯文本与 RSS/Atom 订阅源。"
        "**长页面分页读取，不会丢失后半部分**：每次返回约 N 字（默认 6000），"
        "头部标明当前区间与总字数；读完这一段仍需要后面的内容时，"
        "用相同 url 并把 offset 设为下一段起点再次调用（如 offset=6000、"
        "再下一段 offset=12000），直到提示已到末尾。不要对同一 offset 重复抓取。"
        "如果抓回来的页面像空壳（只有导航、几乎没有正文），"
        "说明其内容由 JavaScript 动态加载，本工具不执行 JS，"
        "请改用以下来源再抓：①移动版页面（域名换成 m. 开头，"
        "如 m.example.com/...）；②AMP 页面（链接后加 /amp 或 ?amp=1）；"
        "③站点的 RSS/Atom 订阅（如 /feed、/rss、/atom.xml）；"
        "④站点的数据接口（常见 /api/ 开头，返回 JSON）。"
        "不要抓取与问题无关的链接。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抓取的网页地址（http/https）"},
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "从正文中第几个字符开始读（默认 0=从头；"
                               "翻页按上一次返回的起点递增）",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, settings: WebSearchSettings):
        self.settings = settings

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = str(arguments.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, output="[参数错误] 请提供要抓取的网址（url）")
        try:
            offset = int(arguments.get("offset") or 0)
        except (TypeError, ValueError):
            return ToolResult(ok=False, output="[参数错误] offset 必须是非负整数")
        if offset < 0:
            return ToolResult(ok=False, output="[参数错误] offset 不能为负数")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            return ToolResult(ok=False, output=f"[参数错误] URL 无法解析: {exc}")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ToolResult(
                ok=False, output="[参数错误] url 必须是 http:// 或 https:// 开头的完整网址"
            )
        if _is_blocked_host(parsed.hostname):
            return ToolResult(ok=False, output="[参数错误] 不允许访问内网或本机地址")

        services = ctx.services or {}
        cache = services.get("page_cache")
        page_size = int(self.settings.max_page_chars or 6000)

        # 缓存命中（同一段正文 10 分钟内）：直接分页，不重复下载
        entry = cache.get(url) if cache is not None else None
        if entry is None:
            proxy = (self.settings.proxy or "").strip() or None
            started = time.monotonic()
            logger.info("web_fetch 开始: url=%s proxy=%s", url, proxy or "直连")

            # follow_redirects=False：手动跟随重定向，逐跳检查目标主机（防重定向 SSRF）
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=False,
                proxy=proxy,
            ) as client:
                try:
                    resp, final_url = await _fetch_with_redirect_checks(client, url)
                except Exception as exc:
                    logger.warning("网页抓取失败: %s: %s", type(exc).__name__, exc)
                    return ToolResult(
                        ok=False,
                        output=f"[工具错误] 网页抓取失败: {type(exc).__name__}: {exc}",
                    )

                if resp.status_code >= 400:
                    logger.info("web_fetch %s -> HTTP %d", url, resp.status_code)
                    return ToolResult(
                        ok=False, output=f"[工具错误] 网页返回 HTTP {resp.status_code}"
                    )
                ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if len(resp.content) > _MAX_FETCH_BYTES:
                    return ToolResult(ok=False, output="[工具错误] 内容过大（超过 10MB）")

            try:
                kind, title, full_text, enc = _convert_payload(resp, ctype)
            except ValueError as exc:
                return ToolResult(ok=False, output=f"[工具错误] {exc}")
            if not full_text:
                return ToolResult(ok=False, output="[工具错误] 没有可提取的正文内容")

            # 以最终 URL 缓存完整正文（redirect 之后的地址）
            if cache is not None:
                cache.put(final_url, full_text)
            logger.info(
                "web_fetch 抓取完成: %s -> %s, 全文 %d 字符 (enc=%s, %.1fs)",
                final_url, kind, len(full_text), enc, time.monotonic() - started,
            )
        else:
            # 翻页：从缓存取，kind/title 用默认网页标签即可
            kind, title, final_url, full_text = "html", "", url, entry

        total = len(full_text)
        if offset >= total:
            return ToolResult(
                ok=False,
                output="[参数错误] offset=%d 已超出正文总长度 %d；"
                       "如需重读请用更小的 offset（0 为从头）。" % (offset, total),
            )

        chunk = full_text[offset:offset + page_size]
        end = min(offset + page_size, total)
        tag, label = _KIND_LABELS[kind]
        title_part = label(title)
        head = tag + (" " + title_part if title_part else "")
        header = "%s\nURL: %s\n内容 %d–%d / 共 %d 字\n\n" % (
            head, final_url, offset + 1, end, total,
        )
        output = header + chunk
        if end < total:
            output += (
                "\n\n（还有 %d 字未读：再次调用 web_fetch，url 相同、"
                "offset=%d 取下一段）" % (total - end, end)
            )
        else:
            output += "\n\n（已到正文末尾，共 %d 字）" % total
        logger.info("web_fetch 分页: %s offset=%d 返 %d 字 / 共 %d",
                    final_url, offset, len(chunk), total)
        return ToolResult(ok=True, output=output)


# ---- Bing 搜索 ----

async def _search_bing(
    client: httpx.AsyncClient,
    host: str,
    query: str,
    num: int,
    international: bool = False,
) -> List[Dict[str, str]]:
    params = {"q": query, "setlang": "zh-CN", "count": num}
    # ensearch=1 走国际版通道：可绕开国内市场对部分查询的过滤降级
    if international:
        params["ensearch"] = "1"
    resp = await client.get(
        f"https://{host}/search",
        params=params,
        headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return _parse_bing(resp.text)


def _quality_score(query: str, results: List[Dict[str, str]], top_n: int = 5) -> float:
    """结果与查询的相关度：查询字符集在 top N 标题+摘要中的覆盖率（0~1）。

    Bing 降质返回的无关结果（如查「战双 更新」得到「战」字典页）覆盖率极低。
    """
    wanted = {ch for ch in (query or "") if not ch.isspace()}
    if not wanted:
        return 1.0
    merged = ""
    for item in results[:top_n]:
        merged += item.get("title", "") + item.get("snippet", "")
    hit = sum(1 for ch in wanted if ch in merged)
    return hit / len(wanted)


def _log_results(label: str, results: List[Dict[str, str]], score: float) -> None:
    logger.info("%s（%d 条，质量分 %.2f）：", label, len(results), score)
    for item in results:
        logger.info(
            "  %s | %s | %s",
            item.get("title", ""), item.get("url", ""), item.get("snippet", "")[:120],
        )


def _format_results(query: str, results: List[Dict[str, str]]) -> str:
    lines = [f"（web_search「{query}」，共 {len(results)} 条结果）"]
    for i, item in enumerate(results):
        lines.append("")
        lines.append(f"{i + 1}. {item['title']}")
        lines.append(f"   URL: {item['url']}")
        if item.get("snippet"):
            lines.append(f"   摘要：{item['snippet']}")
    lines.append("")
    lines.append(
        "提示：以上只有标题与摘要，不含正文。需要某条的完整内容时，"
        "调用 web_fetch，参数 url 传该条链接（通常挑选 1~3 条最相关结果）。"
    )
    return "\n".join(lines)


def _parse_bing(html: str) -> List[Dict[str, str]]:
    """解析 b_algo 结果块：h2>a 真实 URL + 首个 <p> 摘要。"""
    blocks = re.split(r'<li class="b_algo"', html)[1:]
    out: List[Dict[str, str]] = []
    seen = set()
    for block in blocks:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                      block, re.S)
        if not m:
            continue
        url = _unwrap_bing_url(unescape(m.group(1)))
        title_html = m.group(2)
        if url in seen:
            continue
        pm = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        snippet = _strip_tags(pm.group(1)) if pm else ""
        title = _strip_tags(title_html)
        if not title:
            continue
        seen.add(url)
        out.append({"title": title.strip(), "url": url, "snippet": snippet.strip()})
    return out


def _unwrap_bing_url(url: str) -> str:
    """Bing 部分结果把真实链接包成 /ck/a?...&u=a1<base64url>，还原出真实 URL。

    已是直链时原样返回。还原失败也原样返回（抓 /ck/a 时 Bing 会 302 过去）。
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    if not parsed.hostname or parsed.hostname not in ("cn.bing.com", "www.bing.com"):
        return url
    if parsed.path != "/ck/a":
        return url
    values = parse_qs(parsed.query).get("u") or []
    token = values[0] if values else ""
    # a1 是 Bing 的编码版本前缀，后面是 URL 的 base64url
    if not token.startswith("a1"):
        return url
    raw = token[2:]
    raw += "=" * (-len(raw) % 4)
    try:
        real = base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8", "ignore")
    except (binascii.Error, ValueError):
        return url
    if real.startswith(("http://", "https://")):
        return real
    return url


# ---- 页面抓取 ----

async def _fetch_with_redirect_checks(
    client: httpx.AsyncClient, url: str
) -> Tuple[httpx.Response, str]:
    """手动跟随重定向，每一跳都检查目标主机，返回最终响应与最终 URL。"""
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlsplit(url)
        if not parsed.hostname or _is_blocked_host(parsed.hostname):
            raise RuntimeError("重定向目标为内网或本机地址，已拒绝")
        resp = await client.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        location = resp.headers.get("location") if resp.status_code in _REDIRECT_CODES else ""
        if not location:
            return resp, url
        url = str(httpx.URL(url).join(location))
        await resp.aclose()
    raise RuntimeError("重定向次数过多（超过 5 次）")


_HTML_TYPES = ("text/html", "application/xhtml+xml", "application/xml", "text/xml")
_FEED_TYPES = ("application/rss+xml", "application/atom+xml")
_KIND_LABELS = {
    "html": ("[网页]", lambda t: t or "(无标题)"),
    "feed": ("[订阅源]", lambda t: t or "(无标题)"),
    "json": ("[JSON 数据]", lambda t: ""),
    "text": ("[文本]", lambda t: ""),
}


def _convert_payload(resp: httpx.Response, ctype: str):
    """按 Content-Type 把响应体转成可读文本。

    返回 (kind, title, text, encoding)。不支持的二进制类型抛 ValueError。
    缺 Content-Type 时按首字节嗅探：< → 标记语言，{/[ → JSON，其余 → 文本。
    """
    content = resp.content
    stripped = content.lstrip()
    if not ctype:
        if stripped[:1] == b"<":
            ctype = "text/html"
        elif stripped[:1] in (b"{", b"["):
            ctype = "application/json"
        else:
            ctype = "text/plain"

    if ctype in _HTML_TYPES or ctype in _FEED_TYPES:
        enc = _detect_encoding(resp)
        raw = content.decode(enc, errors="ignore")
        kind = "feed" if ctype in _FEED_TYPES or _looks_like_feed(raw) else "html"
        title, body = html_to_markdown(raw)
        return kind, title, "\n".join(body).strip(), enc

    if ctype == "application/json":
        enc = _detect_encoding(resp)
        text = content.decode(enc, errors="ignore").strip()
        try:
            # 缩进重排，让 AI 更容易读字段；解析失败则原样返回
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except (ValueError, TypeError):
            pass
        return "json", "", text.strip(), enc

    if ctype == "text/plain":
        enc = _detect_encoding(resp)
        return "text", "", content.decode(enc, errors="ignore").strip(), enc

    raise ValueError(f"该链接不是可读的文本内容（Content-Type: {ctype}）")


def _looks_like_feed(raw_xml: str) -> bool:
    """text/xml 未声明是不是 feed：前 2000 字符出现 RSS/Atom 根标签即按订阅源处理。"""
    head = raw_xml.lstrip()[:2000].lower()
    return "<rss" in head or "<feed" in head or "<channel" in head


def _detect_encoding(resp: httpx.Response) -> str:
    enc = resp.charset_encoding or ""
    if not enc:
        m = re.search(rb'charset=["\']?([a-zA-Z0-9\-_]+)', resp.content[:4096])
        enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        "".encode(enc)
    except LookupError:
        enc = "utf-8"
    return enc


# ---- HTML 提纯 ----

class _TextExtractor(HTMLParser):
    """去 script/style/nav/footer，按块级标签换行，保留标题/列表结构。"""

    # noscript 不跳过：JS 渲染页常把降级内容放在其中，对纯抓取有价值
    SKIP_TAGS = ("script", "style", "iframe", "svg", "form",
                 "header", "footer", "nav", "aside")
    BLOCK_TAGS = ("p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6")

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        # 当前打开的正文链接：[href, 开始时 parts 长度]，供结束时判断是否有链接文字
        self._anchor_stack: List[List[Any]] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "a" and not self.skip_depth:
            href = _clean_href(dict(attrs).get("href"))
            if href:
                self.parts.append("[")
                # 记录 "[" 之后的位置：其后有内容才算有链接文字
                self._anchor_stack.append([href, len(self.parts)])
        if tag in self.BLOCK_TAGS:
            if tag in ("h1", "h2"):
                prefix = "\n\n## "
            elif tag in ("h3", "h4"):
                prefix = "\n\n### "
            elif tag == "li":
                prefix = "\n- "
            else:
                prefix = "\n"
            self.parts.append(prefix)

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._anchor_stack:
            href, started = self._anchor_stack.pop()
            if len(self.parts) > started:
                # 正常 [文字](href)
                self.parts.append("](%s)" % href)
            else:
                # 图片链接等无文字情况：去掉孤立的 "["，直接给地址
                self.parts.pop()
                self.parts.append(href)
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self._in_title:
            self.title += data
        t = re.sub(r"\s+", " ", data)
        if t.strip():
            self.parts.append(t)


_HREF_DROP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "vbscript:")


def _clean_href(href: Optional[str]) -> str:
    """规范化正文链接地址；不可用（空/纯锚点/脚本协议）返回 ""。

    保留相对链接、协议相对（//host/...）与 http(s) 绝对链接，
    让 AI 能从列表页直接拿到详情页真实 URL，不必猜测拼接。
    """
    h = unescape((href or "").strip())
    if not h or h.startswith("#"):
        return ""
    low = h.lower()
    if low.startswith(_HREF_DROP_SCHEMES):
        return ""
    if ":" in h.split("/", 1)[0]:
        # 绝对 URL：只放行 http/https
        scheme = low.split(":", 1)[0]
        if scheme not in ("http", "https"):
            return ""
    return h


def html_to_markdown(html_text: str) -> Tuple[str, List[str]]:
    p = _TextExtractor()
    p.feed(html_text)
    lines = [ln.strip() for ln in "".join(p.parts).splitlines()]
    out: List[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return p.title.strip(), out


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    # 常见 HTML 实体
    text = text.replace("&ensp;", " ").replace("&nbsp;", " ")
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    text = text.replace("&#0183;", "·").replace("&#176;", "°")
    return re.sub(r"\s+", " ", text).strip()
