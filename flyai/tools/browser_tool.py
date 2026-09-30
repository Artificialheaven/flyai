"""AI 浏览器工具组：有状态地逐级浏览网页。

browser_open →（每次动作后自动返回新快照）→ browser_click / browser_type
→ browser_back / browser_get_text → browser_close。

会话按「用户:聊天会话」隔离复用（services 注入 browser_manager / browser_key）；
安全边界在 BrowserSession：隐身上下文、逐请求 SSRF 路由拦截、步数上限、空闲回收。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from ..browser.session import BrowserError
from .base import Tool, ToolContext, ToolResult

# 共用工作流说明，附在每个工具描述里
_FLOW = (
    "工作方式：先 browser_open 打开网页，你会收到带编号元素的页面快照；"
    "用 browser_click（target=编号）点链接/按钮进入内部页面，"
    "或 browser_type（target=编号, text=内容）填写搜索框等表单；"
    "每次动作后自动收到新页面快照，据此决定下一步，逐级深入。"
    "走最短路径，动作数有上限。"
)


async def _with_session(
    ctx: ToolContext, fn: Callable[[Any], Any]
) -> ToolResult:
    services = ctx.services or {}
    manager = services.get("browser_manager")
    key = services.get("browser_key")
    if manager is None or not key:
        return ToolResult(ok=False, output="[工具错误] 浏览器服务不可用（未启用或未注入）")
    try:
        session = await manager.get(key)
        output = await fn(session)
        return ToolResult(ok=True, output=output)
    except BrowserError as exc:
        return ToolResult(ok=False, output="[浏览器] " + str(exc))
    except RuntimeError as exc:
        # Playwright 未安装 / 浏览器启动失败（错误内含安装指引）
        return ToolResult(ok=False, output="[浏览器] " + str(exc))
    except Exception as exc:  # noqa: BLE001 - 失败信息回喂 AI 自行纠错
        return ToolResult(
            ok=False,
            output="[浏览器错误] %s: %s" % (type(exc).__name__, exc),
        )


class BrowserOpenTool(Tool):
    name = "browser_open"
    description = (
        "打开浏览器并导航到指定网址，开始一次有状态的网页浏览。"
        "返回当前页面快照（标题、URL、可交互元素编号、正文）。"
        "重复调用会在同一会话里跳转新页面。" + _FLOW
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址（http/https）"},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = str(arguments.get("url") or "").strip()
        if not url:
            return ToolResult(ok=False, output="[参数错误] 请提供网址（url）")
        return await _with_session(ctx, lambda s: s.open(url))


class BrowserSnapshotTool(Tool):
    name = "browser_snapshot"
    description = (
        "重新获取当前页面的快照（可交互元素编号 + 正文）。"
        "通常不需要调用——browser_open/click/type/back 后已自动返回最新快照；"
        "仅在你怀疑页面自行变化、或编号可能失效时使用。" + _FLOW
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await _with_session(ctx, lambda s: s.snapshot())


class BrowserClickTool(Tool):
    name = "browser_click"
    description = (
        "点击当前页面上的链接或按钮：target 传快照中元素的方括号编号。"
        "若是普通链接，会进入内部页面并自动返回新页面快照；"
        "target=_blank 的链接会自动切换到新标签页。"
        "只点与任务相关的元素，不要尝试登录、支付、删除类操作。" + _FLOW
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {
                "type": "integer",
                "minimum": 1,
                "description": "快照中元素的编号（如 3 表示 [3]）",
            },
        },
        "required": ["target"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            target = int(arguments.get("target"))
        except (TypeError, ValueError):
            return ToolResult(ok=False, output="[参数错误] target 必须是快照中的编号（整数）")
        return await _with_session(ctx, lambda s: s.click(target))


class BrowserTypeTool(Tool):
    name = "browser_type"
    description = (
        "向当前页面的输入框/多行文本框填写文字：target 传编号、text 传内容。"
        "submit=true（默认）填完按回车提交，随后自动返回新页面快照；"
        "submit=false 只填写不提交（多字段表单逐个填，最后再提交）。" + _FLOW
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "integer", "minimum": 1, "description": "输入框编号"},
            "text": {"type": "string", "description": "要填入的文字"},
            "submit": {
                "type": "boolean",
                "description": "是否按回车提交（默认 true）",
            },
        },
        "required": ["target", "text"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            target = int(arguments.get("target"))
        except (TypeError, ValueError):
            return ToolResult(ok=False, output="[参数错误] target 必须是输入框编号（整数）")
        text = str(arguments.get("text") if arguments.get("text") is not None else "")
        submit = bool(arguments.get("submit", True))
        return await _with_session(ctx, lambda s: s.type_text(target, text, submit))


class BrowserBackTool(Tool):
    name = "browser_back"
    description = "后退到上一页（浏览器历史回退），自动返回该页快照。" + _FLOW
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await _with_session(ctx, lambda s: s.back())


class BrowserTextTool(Tool):
    name = "browser_get_text"
    description = (
        "只提取当前页面的正文（不带可交互元素列表），适合到达目标详情页后"
        "读取完整内容。返回内容有长度上限。" + _FLOW
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await _with_session(ctx, lambda s: s.get_text())


class BrowserCloseTool(Tool):
    name = "browser_close"
    description = (
        "关闭浏览器会话（释放标签页与隐身上下文）。任务完成或打算重新开始时调用。" + _FLOW
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        async def close(session):
            services = ctx.services or {}
            await session.close()
            # 从管理器移除，下次 browser_open 得到全新会话
            await services["browser_manager"].drop(services["browser_key"])
            return "浏览器会话已关闭。"

        return await _with_session(ctx, close)
