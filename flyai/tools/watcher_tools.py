"""P4 监测进程管理工具：watcher_start/stop/list（全部 risk=admin）。

watcher 本身是 detached 子进程，启停涉及 subprocess，因此只给管理员；
普通用户即便工具名泄漏进上下文，注册表执行期门禁也会返回 ok=False。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from .base import Tool, ToolContext, ToolResult


def _manager(ctx: ToolContext):
    return ctx.services.get("watcher_manager")


class WatcherStartTool(Tool):
    risk = True
    min_level = 0
    name = "watcher_start"
    description = (
        "启动一个监测脚本（detached 后台进程）：脚本必须是此前经沙箱写入"
        "当前会话工作区的 .py 相对路径，禁止 public/ 与点目录。脚本通过"
        " TRIGGER_* 环境变量回调外部触发端点。每会话有数量上限。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "script_path": {
                "type": "string",
                "description": "会话工作区内的相对路径，如 watchers/check_price.py",
            },
            "source": {
                "type": "string",
                "description": "事件来源标识，只允许字母数字及 _.-，1-64 字符",
            },
        },
        "required": ["script_path"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        manager = _manager(ctx)
        if manager is None:
            return ToolResult(ok=False, output="[watcher 未就绪] 请联系管理员。")
        script_path = str(arguments.get("script_path") or "")
        source = str(arguments.get("source") or "watcher")
        try:
            row = await asyncio.to_thread(
                manager.start, ctx.uid, ctx.session_id, script_path, source
            )
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[watcher 启动失败] %s" % exc)
        return ToolResult(
            output="watcher #%s 已启动（pid=%s，日志：%s）"
                   % (row["id"], row["pid"], row.get("log_path", ""))
        )


class WatcherStopTool(Tool):
    risk = True
    min_level = 0
    name = "watcher_stop"
    description = "停止指定 watcher（杀整棵进程树并标记 stopped）。"
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "integer",
                   "description": "watcher 编号（watcher_list 方括号内）"},
        },
        "required": ["id"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        manager = _manager(ctx)
        if manager is None:
            return ToolResult(ok=False, output="[watcher 未就绪] 请联系管理员。")
        try:
            watcher_id = int(arguments.get("id"))
        except (TypeError, ValueError):
            return ToolResult(ok=False, output="id 必须是整数")
        try:
            row = await asyncio.to_thread(
                manager.stop, ctx.uid, watcher_id
            )
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[watcher 停止失败] %s" % exc)
        return ToolResult(output="watcher #%s 已停止（状态：%s）"
                                 % (row["id"], row["status"]))


class WatcherListTool(Tool):
    risk = True
    min_level = 0
    name = "watcher_list"
    description = "列出当前会话（或全部会话）的 watcher 及存活状态。"
    parameters = {
        "type": "object",
        "properties": {
            "all_sessions": {
                "type": "boolean",
                "description": "true=列出自己全部会话，默认仅当前会话",
            },
        },
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        manager = _manager(ctx)
        if manager is None:
            return ToolResult(ok=False, output="[watcher 未就绪] 请联系管理员。")
        session_id = None if arguments.get("all_sessions") else ctx.session_id
        try:
            rows = await asyncio.to_thread(
                manager.list, ctx.uid, session_id
            )
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[watcher 查询失败] %s" % exc)
        if not rows:
            return ToolResult(output="当前没有 watcher。")
        lines = ["watcher 列表（id/状态/存活/pid/来源/脚本）："]
        for r in rows:
            alive = "存活" if r.get("alive") else "已退出"
            lines.append(
                "[%s] %s/%s pid=%s source=%s %s"
                % (r["id"], r["status"], alive, r["pid"],
                   r.get("source", ""), r.get("script_path", ""))
            )
        return ToolResult(output="\n".join(lines))


WATCHER_TOOLS = [WatcherStartTool, WatcherStopTool, WatcherListTool]
