"""AI 自定义工具的管理工具 + 已上线工具的函数包装（P1）。

管理五件套（custom_tool_validate/publish/list/toggle/remove）全部 risk=True、
仅管理员可执行；真正的自定义函数工具 CustomFunctionTool 按请求注入
（run_agent_loop 的 extra_tools），不进全局注册表，杜绝跨用户泄漏。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from .base import Tool, ToolContext, ToolResult

_SCHEMA_PARAM = {
    "type": "string",
    "description": (
        "JSON Schema 字符串，形如 "
        '{"type":"object","properties":{"x":{"type":"string"}}}，'
        "properties 的 key 就是传给 tool(args) 的参数名"
    ),
}
_CODE_PARAM = {
    "type": "string",
    "description": "工具代码，必须定义 def tool(args: dict, ctx: dict) -> str",
}


def _runtime(ctx: ToolContext):
    rt = ctx.services.get("custom_runtime")
    if rt is None or not getattr(getattr(rt, "settings", None), "enabled", False):
        return None
    return rt


class _ManageTool(Tool):
    risk = True
    min_level = 0

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        rt = _runtime(ctx)
        if rt is None:
            return ToolResult(ok=False, output="[自定义工具未启用] 请管理员先在设置中开启。")
        try:
            text = await self._call(rt, arguments, ctx)
        except Exception as exc:  # 管理操作异常也不允许打断 agent loop
            return ToolResult(ok=False, output="[自定义工具错误] %s: %s"
                              % (type(exc).__name__, exc))
        return ToolResult(output=text, ok=not text.startswith("❌"))

    async def _call(self, rt, arguments: Dict[str, Any], ctx: ToolContext) -> str:
        raise NotImplementedError


class CustomToolValidate(_ManageTool):
    name = "custom_tool_validate"
    description = (
        "试写一个自定义工具并做隔离 dry-run：静态检查 + 子进程加载 + 可选样例调用，"
        "全程不写库、不上线。校验通过后再调用 custom_tool_publish。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "工具名，^[a-z][a-z0-9_]{0,31}$，上线后模型看到的是 custom__<name>"},
            "description": {"type": "string",
                            "description": "告诉模型什么时候该调用这个工具，必填，≤500 字"},
            "params_schema": _SCHEMA_PARAM,
            "code": _CODE_PARAM,
            "test_args": {"type": "string",
                          "description": "可选：样例参数 JSON 字符串，用于 dry-run 试跑一次"},
        },
        "required": ["name", "description", "params_schema", "code"],
        "additionalProperties": False,
    }

    async def _call(self, rt, arguments, ctx) -> str:
        return await rt.validate(ctx.uid, ctx.session_id, arguments)


class CustomToolPublish(_ManageTool):
    name = "custom_tool_publish"
    description = (
        "发布/更新自定义工具（先自动做一次子进程加载验证，失败则旧版保留、不上线）。"
        "scope=user 对该用户全部会话生效；scope=session 仅当前会话生效（同名覆盖用户级）。"
        "owner_only=true 时只有管理员能触发。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "工具名，^[a-z][a-z0-9_]{0,31}$"},
            "title": {"type": "string", "description": "可选短标题，≤20 字"},
            "description": {"type": "string", "description": "调用时机说明，必填，≤500 字"},
            "params_schema": _SCHEMA_PARAM,
            "code": _CODE_PARAM,
            "scope": {"type": "string", "enum": ["user", "session"],
                      "description": "user=用户级（默认），session=仅当前会话"},
            "owner_only": {"type": "boolean",
                           "description": "true 时仅管理员可触发，默认 false"},
        },
        "required": ["name", "description", "params_schema", "code"],
        "additionalProperties": False,
    }

    async def _call(self, rt, arguments, ctx) -> str:
        return await rt.publish(ctx.uid, ctx.session_id, arguments)


class CustomToolList(_ManageTool):
    name = "custom_tool_list"
    description = "列出当前用户的全部自定义工具（含停用项），方括号里是 toggle/remove 用的 id。"
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}

    async def _call(self, rt, arguments, ctx) -> str:
        return await rt.list_text(ctx.uid, ctx.session_id)


class CustomToolToggle(_ManageTool):
    name = "custom_tool_toggle"
    description = "启用/停用指定 id 的自定义工具（停用后模型不再看到它，代码保留在库中）。"
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "custom_tool_list 里方括号中的 id"},
            "enabled": {"type": "boolean", "description": "true 启用 / false 停用"},
        },
        "required": ["id", "enabled"],
        "additionalProperties": False,
    }

    async def _call(self, rt, arguments, ctx) -> str:
        return await rt.toggle(ctx.uid, arguments)


class CustomToolRemove(_ManageTool):
    name = "custom_tool_remove"
    description = "彻底删除指定 id 的自定义工具（不可恢复；停用请用 custom_tool_toggle）。"
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "custom_tool_list 里方括号中的 id"},
        },
        "required": ["id"],
        "additionalProperties": False,
    }

    async def _call(self, rt, arguments, ctx) -> str:
        return await rt.remove(ctx.uid, arguments)


MANAGE_TOOLS: List[type] = [
    CustomToolValidate,
    CustomToolPublish,
    CustomToolList,
    CustomToolToggle,
    CustomToolRemove,
]


class CustomFunctionTool(Tool):
    """已上线自定义工具的请求级包装；一次调用一个隔离子进程。"""

    def __init__(self, row: dict, runtime: Any) -> None:
        self._row = row
        self._runtime = runtime
        self.name = "custom__" + row["name"]
        self.description = "[自定义工具] " + row["description"]
        try:
            self.parameters = json.loads(row["params_schema"])
        except (ValueError, TypeError):
            self.parameters = {"type": "object", "properties": {}}
        # owner_only 收紧为管理员工具；普通工具不带 risk，同 uid 会话内可正常触发
        self.risk = bool(row.get("owner_only"))
        self.min_level = 0

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        call_ctx = {
            "uid": ctx.uid,
            "session_id": ctx.session_id,
            "run_id": ctx.run_id,
            "workspace": str(
                Path(self._runtime.data_dir) / (ctx.uid or "anon") / "workspace"
            ),
            "now": int(time.time()),
            "dry_run": False,
        }
        timeout = float(getattr(self._runtime.settings, "call_timeout", 20.0))
        res = await asyncio.to_thread(
            self._runtime.call_blocking, self._row, arguments or {}, call_ctx, timeout
        )
        if not res.get("ok"):
            return ToolResult(ok=False, output=res.get("error", "自定义工具执行失败"))
        return ToolResult(output=res.get("result", ""))
