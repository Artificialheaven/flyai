"""AI 浏览器工具组测试。

Playwright 不在测试环境，浏览器真实导航不在这里覆盖；
快照渲染、错误处理、会话管理 GC、工具接线均可用纯 Python 替身测试。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from flyai.app import AppState
from flyai.browser.manager import BrowserSessionManager
from flyai.browser.session import (
    BrowserError,
    BrowserSession,
    normalize_body,
    render_snapshot,
    render_text,
)
from flyai.config import AppConfig, BrowserSettings
from flyai.tools.base import ToolContext
from flyai.tools.browser_tool import (
    BrowserClickTool,
    BrowserOpenTool,
    _with_session,
)


def _settings(**kw):
    base = dict(
        nav_timeout_ms=20000,
        idle_seconds=180,
        max_steps=15,
        max_snapshot_chars=6000,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _elems(n):
    return [
        {"ref": i + 1, "kind": "link", "label": "链接%d" % (i + 1),
         "href": "/p/%d" % (i + 1), "selector": "#l%d" % (i + 1)}
        for i in range(n)
    ]


# ---- 快照渲染 ----

def test_render_snapshot_numbered_elements():
    elements = [
        {"ref": 1, "kind": "link", "label": "新闻", "href": "/news",
         "selector": "#news"},
        {"ref": 2, "kind": "button", "label": "搜索", "href": "",
         "selector": "#btn"},
        {"ref": 3, "kind": "input", "label": "关键词", "href": "",
         "selector": "#q"},
        {"ref": 4, "kind": "textarea", "label": "备注", "href": "",
         "selector": "#t"},
        {"ref": 5, "kind": "select", "label": "排序", "href": "",
         "selector": "#s"},
    ]
    out = render_snapshot("示例站", "https://example.com/", elements,
                          "这是正文", 6000)
    assert "当前页面：示例站" in out
    assert "URL：https://example.com/" in out
    assert "[1] 链接「新闻」 → /news" in out
    assert "[2] 按钮「搜索」" in out
    assert "[3] 输入框「关键词」" in out
    assert "[4] 多行输入框「备注」" in out
    assert "[5] 下拉选择「排序」" in out
    assert "这是正文" in out


def test_render_snapshot_no_elements():
    out = render_snapshot("空", "https://x", [], "正文", 6000)
    assert "（本页没有可交互元素）" in out


def test_render_snapshot_more_than_max_folded():
    out = render_snapshot("多", "https://x", _elems(35), "正文", 6000)
    assert "[30]" in out
    assert "[31]" not in out
    assert "另有 5 个元素未列出" in out


def test_render_snapshot_long_body_truncated():
    body = "正文行。" * 2000
    out = render_snapshot("页", "https://x", _elems(2), body, 1000)
    assert len(out) <= 1000 + 100  # 允许提示语造成少量超出
    assert "正文过长已截断" in out


def test_render_text_truncated():
    out = render_text("页", "https://x", "字" * 5000, 1000)
    assert "正文过长已截断" in out
    assert len(out) < 1100


def test_normalize_body_collapses_blanks_and_spaces():
    raw = "  a   b  \n\n\n\n c \n\n"
    assert normalize_body(raw) == "a b\n\nc"


# ---- BrowserSession 错误路径（无 Playwright）----

def _bare_session(**kw):
    s = BrowserSession(_settings(**kw))
    s._page = object()  # 视为已打开
    return s


@pytest.mark.asyncio
async def test_open_rejects_non_http():
    s = BrowserSession(_settings())
    with pytest.raises(BrowserError):
        await s.open("ftp://example.com/x")


@pytest.mark.asyncio
async def test_open_rejects_localhost():
    s = BrowserSession(_settings())
    with pytest.raises(BrowserError):
        await s.open("http://127.0.0.1:8000/secret")


def test_find_unknown_ref():
    s = _bare_session()
    s.elements = [{"ref": 1, "kind": "button", "label": "x", "href": "",
                   "selector": "#a"}]
    with pytest.raises(BrowserError, match="编号 2"):
        s._find(2)


@pytest.mark.asyncio
async def test_click_step_limit():
    s = _bare_session(max_steps=3)
    s.steps = 3
    s.elements = [{"ref": 1, "kind": "button", "label": "x", "href": "",
                   "selector": "#a"}]
    with pytest.raises(BrowserError, match="步上限"):
        await s.click(1)


@pytest.mark.asyncio
async def test_type_rejects_non_input():
    s = _bare_session()
    s.elements = [{"ref": 1, "kind": "button", "label": "x", "href": "",
                   "selector": "#a"}]
    with pytest.raises(BrowserError, match="不是文本输入框"):
        await s.type_text(1, "hi", False)


@pytest.mark.asyncio
async def test_actions_require_open_session():
    s = BrowserSession(_settings())
    with pytest.raises(BrowserError, match="未打开"):
        await s.snapshot()
    with pytest.raises(BrowserError):
        await s.click(1)


def test_idle_detection():
    s = _bare_session(idle_seconds=180)
    s.last_used = 0
    assert s.is_idle(now=1000.0)
    s.touch()
    assert not s.is_idle()


# ---- 管理器 GC / 复用（替身会话）----

class _FakeSession:
    def __init__(self, idle):
        self._idle = idle
        self.closed = False

    def is_idle(self):
        return self._idle

    async def close(self):
        self.closed = True


def _bare_manager():
    mgr = BrowserSessionManager.__new__(BrowserSessionManager)
    mgr._sessions = {}
    return mgr


@pytest.mark.asyncio
async def test_manager_gc_closes_idle_only():
    mgr = _bare_manager()
    live = _FakeSession(False)
    dead = _FakeSession(True)
    mgr._sessions = {"a": live, "b": dead}
    assert await mgr.gc() == 1
    assert "a" in mgr._sessions
    assert "b" not in mgr._sessions
    assert dead.closed
    assert not live.closed


@pytest.mark.asyncio
async def test_manager_get_reuses_existing():
    mgr = _bare_manager()
    session = _FakeSession(False)
    mgr._sessions["u:1"] = session
    assert await mgr.get("u:1") is session


# ---- 工具接线 ----

class _FakeManager:
    def __init__(self, session=None):
        self.session = session
        self.dropped = None

    async def get(self, key):
        return self.session

    async def drop(self, key):
        self.dropped = key


class _EchoSession:
    async def open(self, url):
        return "已打开 " + url

    async def click(self, target):
        return "点击 %d" % target


@pytest.mark.asyncio
async def test_open_tool_empty_url():
    res = await BrowserOpenTool().run({}, ToolContext())
    assert not res.ok and "url" in res.output


@pytest.mark.asyncio
async def test_click_tool_non_integer_target():
    res = await BrowserClickTool().run({"target": "abc"}, ToolContext())
    assert not res.ok and "整数" in res.output


@pytest.mark.asyncio
async def test_with_session_no_services():
    res = await _with_session(ToolContext(), lambda s: "x")
    assert not res.ok and "不可用" in res.output


@pytest.mark.asyncio
async def test_with_session_success():
    ctx = ToolContext(services={
        "browser_manager": _FakeManager(_EchoSession()),
        "browser_key": "u:1",
    })
    res = await _with_session(ctx, lambda s: s.open("https://example.com"))
    assert res.ok and res.output == "已打开 https://example.com"


@pytest.mark.asyncio
async def test_with_session_maps_browser_error():
    class Boom:
        async def open(self, url):
            raise BrowserError("编号 9 不在")

    ctx = ToolContext(services={
        "browser_manager": _FakeManager(Boom()),
        "browser_key": "u:1",
    })
    res = await _with_session(ctx, lambda s: s.open("https://x"))
    assert not res.ok and "编号 9 不在" in res.output


# ---- 配置与注册 ----

def test_browser_settings_defaults_disabled():
    b = AppConfig().browser
    assert isinstance(b, BrowserSettings)
    assert b.enabled is False
    assert b.max_steps == 15


def test_browser_settings_clamps():
    assert BrowserSettings(max_steps=999).max_steps == 15
    assert BrowserSettings(idle_seconds=1).idle_seconds == 180
    assert BrowserSettings(nav_timeout_ms=1).nav_timeout_ms == 20000


def test_reload_tools_registers_browser_when_enabled():
    cfg = AppConfig()
    cfg.browser.enabled = True
    state = AppState(config=cfg)
    state.reload_tools()
    names = {t.name for t in state.registry.all()}
    assert {"browser_open", "browser_snapshot", "browser_click",
            "browser_type", "browser_back", "browser_get_text",
            "browser_close"} <= names


def test_browser_tools_registered_but_filtered_when_disabled():
    """新设计：browser_* 工具无条件注册（用户可覆盖全局默认），
    是否暴露给模型由每请求的功能偏好过滤决定；管理器仍懒创建。"""
    from flyai.feature_prefs import allowed_tool_names
    from flyai.user import UserSettings

    state = AppState(config=AppConfig())  # browser.enabled 默认 False
    state.reload_tools()
    assert state.browser is None
    names = {t.name for t in state.registry.all()}
    assert "browser_open" in names  # 工具始终注册

    # 默认关闭、用户无覆盖 → 不暴露浏览器工具
    allowed = allowed_tool_names(state.registry, state.config, UserSettings())
    assert "browser_open" not in allowed
    assert "query_lore" in allowed

    # 用户覆盖开启 → 暴露
    us = UserSettings(browser_enabled=True)
    assert "browser_open" in allowed_tool_names(
        state.registry, state.config, us
    )
