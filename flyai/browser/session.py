"""浏览器会话：一个隐身上下文 + 一个当前页面，保持状态供逐级操作。

纯函数 render_snapshot / render_text 不依赖 Playwright，可单测；
BrowserSession 持有 Playwright 对象，提供 open/snapshot/click/type/back/get_text。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from ..netguard import BlockedHost, is_blocked_host, resolve_and_check

# snapshot 最多列出的可交互元素数（控 token；其余折叠提示）
_MAX_ELEMENTS = 30


# WebSocketRoute / ServerWebSocketRoute 的收发方法都是同步 fire-and-forget，
# 且对端关闭后再发会抛错：事件回调里统一吞掉，避免 pyee 打出未处理异常
def _ws_send(route: Any, data: Any) -> None:
    try:
        route.send(data)
    except Exception:
        pass


def _ws_close_server(server: Any) -> None:
    try:
        server.close()
    except Exception:
        pass


def _ws_close_client(ws_route: Any) -> None:
    try:
        asyncio.create_task(ws_route.close())
    except Exception:
        pass


class BrowserError(Exception):
    """面向用户的浏览器操作错误（参数/安全/动作失败）。"""


# ---- 纯文本渲染（无 Playwright 依赖）----

def normalize_body(text: str) -> str:
    """折叠每行空白与连续空行。"""
    out: List[str] = []
    blank = False
    for line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def render_snapshot(
    title: str,
    url: str,
    elements: List[Dict[str, Any]],
    body_text: str,
    max_chars: int,
) -> str:
    """把当前页面渲染成 AI 可读快照：页面信息 + 编号的可交互元素 + 正文。"""
    lines = ["当前页面：" + (title or "(无标题)"), "URL：" + url, ""]

    shown = elements[:_MAX_ELEMENTS]
    if shown:
        lines.append("可交互元素（browser_click / browser_type 的 target 传方括号编号）：")
        for e in shown:
            ref = e["ref"]
            label = e["label"] or "(无文字)"
            kind = e["kind"]
            if kind == "link":
                tail = (" → " + e["href"]) if e.get("href") else ""
                lines.append("  [%d] 链接「%s」%s" % (ref, label, tail))
            elif kind == "input":
                lines.append("  [%d] 输入框「%s」" % (ref, label))
            elif kind == "select":
                lines.append("  [%d] 下拉选择「%s」" % (ref, label))
            elif kind == "textarea":
                lines.append("  [%d] 多行输入框「%s」" % (ref, label))
            else:
                lines.append("  [%d] 按钮「%s」" % (ref, label))
        if len(elements) > len(shown):
            lines.append("  …（另有 %d 个元素未列出）" % (len(elements) - len(shown)))
    else:
        lines.append("（本页没有可交互元素）")

    lines.extend(["", "页面正文："])
    header = "\n".join(lines)
    body = normalize_body(body_text)
    budget = int(max_chars) - len(header) - 1
    if budget < 200:
        # 头部本身就超预算（极端小配置）：仍保证给一点正文
        budget = 200
    if len(body) > budget:
        body = body[: max(0, budget - 24)].rstrip() + (
            "\n…（正文过长已截断，可用 browser_get_text 单独取正文）"
        )
    return header + "\n" + body


def render_text(title: str, url: str, body_text: str, max_chars: int) -> str:
    """只取当前页正文（无交互元素列表）。"""
    header = "当前页面：%s\nURL：%s\n\n正文：\n" % (title or "(无标题)", url)
    body = normalize_body(body_text)
    budget = int(max_chars) - len(header)
    if budget < 200:
        budget = 200
    if len(body) > budget:
        body = body[: max(0, budget - 16)].rstrip() + "\n…（正文过长已截断）"
    return header + body


# ---- 页面内枚举 JS ----

_ENUM_JS = r"""
() => {
  const visible = (el) => {
    if (!el || el.disabled) return false;
    const rects = el.getClientRects();
    if (!rects.length) return false;
    const r = rects[0];
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || Number(st.opacity) === 0)
      return false;
    return true;
  };
  const cssPath = (el) => {
    if (el.id) {
      const idSel = '#' + CSS.escape(el.id);
      if (document.querySelectorAll(idSel).length === 1) return idSel;
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 8) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) {
        parts.unshift(part + '#' + CSS.escape(cur.id));
        break;
      }
      const parent = cur.parentElement;
      if (parent) {
        let idx = 1;
        for (const sib of parent.children) {
          if (sib === cur) break;
          if (sib.tagName === cur.tagName) idx++;
        }
        const sameTag = [...parent.children].filter(s => s.tagName === cur.tagName).length;
        if (sameTag > 1) part += ':nth-of-type(' + idx + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ');
  };

  const selector =
    "a[href], button, input, select, textarea, [role='button'], [role='link'], " +
    "[tabindex]:not([tabindex='-1'])";
  const seen = new Set();
  const out = [];
  for (const el of document.querySelectorAll(selector)) {
    if (seen.has(el)) continue;
    seen.add(el);
    if (!visible(el)) continue;
    let kind;
    if (el.tagName === 'A') kind = 'link';
    else if (el.tagName === 'INPUT') kind = 'input';
    else if (el.tagName === 'SELECT') kind = 'select';
    else if (el.tagName === 'TEXTAREA') kind = 'textarea';
    else kind = 'button';

    let label = '';
    if (kind === 'input' || kind === 'textarea' || kind === 'select') {
      label = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
               el.name || el.value || '').toString().trim();
      if (kind === 'select' && el.selectedOptions && el.selectedOptions.length)
        label = label || el.selectedOptions[0].textContent.trim();
      if (kind === 'input' && !label) label = '[' + (el.type || 'text') + ']';
    } else {
      label = (el.getAttribute('aria-label') || el.innerText || el.textContent || '')
        .replace(/\s+/g, ' ').trim();
    }
    if (!label && kind === 'link') label = el.getAttribute('href') || '';
    if (label.length > 80) label = label.slice(0, 80) + '…';
    const href = el.tagName === 'A' ? (el.getAttribute('href') || '') : '';
    out.push({kind: kind, label: label, href: href, selector: cssPath(el)});
  }
  return out;
}
"""

_BODY_JS = "() => document.body ? document.body.innerText : ''"
_TITLE_JS = "() => document.title || ''"


class BrowserSession:
    """单个浏览器会话：隐身上下文隔离 cookie/缓存，逐请求 SSRF 路由拦截。"""

    def __init__(self, settings: Any):
        self.settings = settings
        self._context: Any = None
        self._page: Any = None
        self.elements: List[Dict[str, Any]] = []
        self.steps = 0
        self.last_used = time.time()
        self.opened = False

    # ---- 生命周期 ----

    async def attach(self, browser: Any, proxy: Optional[str]) -> "BrowserSession":
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
            accept_downloads=False,
            proxy={"server": proxy} if proxy else None,
        )
        # 拦截必须在任何导航之前装好
        await context.route("**/*", self._guard_route)
        # HTTP 路由拦不住 WebSocket：单独挂 WS 守卫（握手前可阻断）
        await context.route_web_socket("**/*", self._guard_ws)
        self._context = context
        self._page = await context.new_page()
        timeout = int(self.settings.nav_timeout_ms)
        self._page.set_default_timeout(timeout)
        self._context.set_default_timeout(timeout)
        self._context.set_default_navigation_timeout(timeout)
        return self

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None
        self.opened = False

    def touch(self) -> None:
        self.last_used = time.time()

    def is_idle(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return now - self.last_used > int(self.settings.idle_seconds)

    # ---- 路由级 SSRF + 资源裁剪 ----

    async def _guard_route(self, route: Any) -> None:
        request = route.request
        try:
            host = urlsplit(request.url).hostname or ""
        except ValueError:
            return await route.abort()
        # 图片/媒体/字体对文本理解无用且拖慢渲染
        if request.resource_type in ("image", "media", "font") or is_blocked_host(host):
            return await route.abort()
        # 域名 DNS 解析后逐 IP 校验（nip.io / 非标准字面量归一化均在此收口）
        try:
            await resolve_and_check(host)
        except BlockedHost:
            return await route.abort()
        await route.continue_()

    # ---- WebSocket SSRF 守卫 ----

    async def _guard_ws(self, ws_route: Any) -> None:
        """WS 握手前校验主机；合法连接双向透传消息，非法直接关闭。"""
        try:
            host = urlsplit(ws_route.url).hostname or ""
        except ValueError:
            await ws_route.close()
            return
        try:
            await resolve_and_check(host)
        except BlockedHost:
            await ws_route.close()
            return
        server = ws_route.connect_to_server()
        ws_route.on_message(lambda data: _ws_send(server, data))
        server.on_message(lambda data: _ws_send(ws_route, data))
        ws_route.on_close(lambda code, reason: _ws_close_server(server))
        server.on_close(lambda code, reason: _ws_close_client(ws_route))

    # ---- 动作 ----

    def _require_step_budget(self) -> None:
        if self.steps >= int(self.settings.max_steps):
            raise BrowserError(
                "本浏览器会话已达 %d 步上限。请调用 browser_close 结束，"
                "或 browser_open 重新开一个会话走最短路径。" % self.steps
            )

    async def open(self, url: str) -> str:
        url = (url or "").strip()
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise BrowserError("URL 无法解析: %s" % exc)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise BrowserError("url 必须是 http:// 或 https:// 开头的完整网址")
        try:
            await resolve_and_check(parsed.hostname)
        except BlockedHost as exc:
            raise BrowserError(str(exc))
        self._require_step_budget()
        try:
            await self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(self.settings.nav_timeout_ms),
            )
        except Exception as exc:
            raise BrowserError(
                "打开页面失败: %s: %s" % (type(exc).__name__, exc)
            )
        await self._settle()
        self.steps += 1
        self.opened = True
        self.touch()
        return await self.snapshot()

    async def _settle(self) -> None:
        """domcontentloaded 之后尽量等网络空闲（best-effort，超时不报错）。"""
        try:
            await self._page.wait_for_load_state(
                "networkidle", timeout=min(5000, int(self.settings.nav_timeout_ms))
            )
        except Exception:
            pass

    async def _collect(self) -> List[Dict[str, Any]]:
        raw = await self._page.evaluate(_ENUM_JS)
        elements: List[Dict[str, Any]] = []
        for i, item in enumerate(raw or []):
            elements.append({
                "ref": i + 1,
                "kind": str(item.get("kind") or "button"),
                "label": str(item.get("label") or ""),
                "href": str(item.get("href") or ""),
                "selector": str(item.get("selector") or ""),
            })
        self.elements = elements
        return elements

    async def snapshot(self) -> str:
        if self._page is None:
            raise BrowserError("浏览器会话未打开，请先 browser_open")
        await self._collect()
        title = await self._page.evaluate(_TITLE_JS)
        url = self._page.url
        body = await self._page.evaluate(_BODY_JS)
        self.touch()
        return render_snapshot(
            str(title), str(url), self.elements, str(body or ""),
            int(self.settings.max_snapshot_chars),
        )

    def _find(self, target: int) -> Dict[str, Any]:
        for e in self.elements:
            if e["ref"] == target:
                return e
        raise BrowserError(
            "编号 %d 不在最近一次快照中。请先 browser_snapshot 查看当前页面的有效编号。"
            % target
        )

    async def click(self, target: int) -> str:
        if self._page is None:
            raise BrowserError("浏览器会话未打开，请先 browser_open")
        desc = self._find(target)
        self._require_step_budget()
        before = list(self._context.pages)
        try:
            await self._page.locator(desc["selector"]).first.click(
                timeout=int(self.settings.nav_timeout_ms)
            )
        except Exception as exc:
            raise BrowserError("点击失败: %s: %s" % (type(exc).__name__, exc))

        new_page = await self._adopt_popup(before)
        if new_page is None:
            await self._settle()
        self.steps += 1
        self.touch()
        return await self.snapshot()

    async def _adopt_popup(self, before: List[Any]) -> Optional[Any]:
        """链接 target=_blank 会开新标签；检测到则把当前页切过去。"""
        new_page = None
        for _ in range(10):
            extra = [p for p in self._context.pages
                     if p not in before and p is not self._page]
            if extra:
                new_page = extra[-1]
                break
            await asyncio.sleep(0.1)
        if new_page is None:
            return None
        self._page = new_page
        timeout = int(self.settings.nav_timeout_ms)
        new_page.set_default_timeout(timeout)
        try:
            await new_page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:
            pass
        await self._settle()
        return new_page

    async def type_text(self, target: int, text: str, submit: bool) -> str:
        if self._page is None:
            raise BrowserError("浏览器会话未打开，请先 browser_open")
        desc = self._find(target)
        if desc["kind"] not in ("input", "textarea"):
            raise BrowserError("编号 %d 不是文本输入框" % target)
        self._require_step_budget()
        locator = self._page.locator(desc["selector"]).first
        try:
            await locator.fill("")
            await locator.fill(str(text))
            if submit:
                await locator.press("Enter")
        except Exception as exc:
            raise BrowserError("输入失败: %s: %s" % (type(exc).__name__, exc))
        await self._settle()
        self.steps += 1
        self.touch()
        return await self.snapshot()

    async def back(self) -> str:
        if self._page is None:
            raise BrowserError("浏览器会话未打开，请先 browser_open")
        self._require_step_budget()
        try:
            await self._page.go_back(
                wait_until="domcontentloaded",
                timeout=int(self.settings.nav_timeout_ms),
            )
        except Exception as exc:
            raise BrowserError("后退失败: %s: %s" % (type(exc).__name__, exc))
        await self._settle()
        self.steps += 1
        self.touch()
        return await self.snapshot()

    async def get_text(self) -> str:
        if self._page is None:
            raise BrowserError("浏览器会话未打开，请先 browser_open")
        title = await self._page.evaluate(_TITLE_JS)
        url = self._page.url
        body = await self._page.evaluate(_BODY_JS)
        self.touch()
        return render_text(
            str(title), str(url), str(body or ""),
            int(self.settings.max_snapshot_chars),
        )
