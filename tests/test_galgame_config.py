"""Galgame 配置：max_loops 钳制 + 交互式工具免超时。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from pydantic import ValidationError

from flyai.config import GalgameSettings
from flyai.schemas import ToolCall
from flyai.tools.base import Tool, ToolContext, ToolResult
from flyai.tools.registry import ToolRegistry


def test_max_loops_defaults():
    assert GalgameSettings().max_loops == 40


def test_max_loops_clamp():
    assert GalgameSettings(max_loops=0).max_loops == 1
    assert GalgameSettings(max_loops=201).max_loops == 200
    assert GalgameSettings(max_loops=50).max_loops == 50


def test_max_loops_bad_input():
    # 非数字字符串在 pydantic-core int 校验阶段被拒（与项目其他 int 字段一致）
    with pytest.raises(ValidationError):
        GalgameSettings(max_loops="x")
    # 数字字符串可宽松转 int
    assert GalgameSettings(max_loops="30").max_loops == 30


class _SlowTool(Tool):
    name = "slow"

    def __init__(self, interactive):
        self.interactive = interactive

    async def run(self, arguments, ctx):
        await asyncio.sleep(0.2)
        return ToolResult(output="done")


def _registry_with(interactive):
    reg = ToolRegistry()
    reg.set_timeout(0.05)
    reg.register(_SlowTool(interactive))
    return reg


def test_interactive_tool_exempt_from_timeout():
    reg = _registry_with(interactive=True)
    call = ToolCall(id="1", name="slow")

    loop = asyncio.new_event_loop()
    try:
        out = loop.run_until_complete(
            reg.execute(call, ToolContext()))
    finally:
        loop.close()
    assert out.ok and out.output == "done"


def test_non_interactive_tool_still_times_out():
    reg = _registry_with(interactive=False)
    call = ToolCall(id="1", name="slow")

    loop = asyncio.new_event_loop()
    try:
        out = loop.run_until_complete(
            reg.execute(call, ToolContext()))
    finally:
        loop.close()
    assert not out.ok and "超时" in out.output
