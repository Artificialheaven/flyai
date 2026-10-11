"""工具注册表：注册/查询/生成 specs/执行（异常全兜底回灌）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..errors import ToolError
from ..schemas import ToolCall
from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger("flyai.tools")


class ToolRegistry:
    """全局工具表。工具名全局唯一。"""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._timeouts_seconds: float = 60.0

    def set_timeout(self, seconds: float) -> None:
        self._timeouts_seconds = seconds

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolError(f"工具重名: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> List[Tool]:
        return list(self._tools.values())

    def specs(self, allowed: Optional[set] = None) -> List[dict]:
        """生成 OpenAI tool specs；allowed 非空时只暴露其中的工具名。"""
        tools = self._tools.values()
        if allowed is not None:
            tools = [t for t in tools if t.name in allowed]
        return [t.spec_openai() for t in tools]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        """执行单个工具调用。任何异常都转为失败 ToolResult，不向循环抛出。"""
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(ok=False, output=f"[工具错误] 未知工具: {call.name}")

        timeout = self._timeouts_seconds
        if ctx.tool_settings is not None:
            timeout = getattr(ctx.tool_settings, "timeout_seconds", timeout)

        try:
            coro = tool.run(call.arguments, ctx)
            if not tool.interactive:
                coro = asyncio.wait_for(coro, timeout=timeout)
            result = await coro
            if not isinstance(result, ToolResult):
                result = ToolResult(output=str(result))
            return result
        except asyncio.TimeoutError:
            return ToolResult(ok=False, output=f"[工具错误] 工具 {call.name} 执行超时（{timeout}s）")
        except Exception as exc:  # 兜底所有异常
            logger.exception("工具 %s 执行异常", call.name)
            return ToolResult(ok=False, output=f"[工具错误] {type(exc).__name__}: {exc}")