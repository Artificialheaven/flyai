"""P0 执行期授权：risk 工具按管理员/等级拦截。

specs 只控制模型可见性，execute 才是授权收口；未授权时返回 ok=False
且工具 run 绝不能被执行。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.schemas import ToolCall
from flyai.tools.base import Tool, ToolContext, ToolResult
from flyai.tools.registry import ToolRegistry


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _RecordingTool(Tool):
    def __init__(self, name, risk=False, min_level=0):
        self.name = name
        self.risk = risk
        self.min_level = min_level
        self.ran = 0

    async def run(self, arguments, ctx):
        self.ran += 1
        return ToolResult(output="ran:%s" % ctx.uid)


def _ctx(uid="u1", level=0, is_admin=False):
    return ToolContext(uid=uid, level=level, is_admin=is_admin)


def _call(name):
    return ToolCall(id="1", name=name)


def test_non_risk_tool_runs_for_any_identity():
    reg = ToolRegistry()
    tool = _RecordingTool("open_thing")
    reg.register(tool)
    out = _run(reg.execute(_call("open_thing"), _ctx()))
    assert out.ok and tool.ran == 1


def test_admin_only_risk_tool_denies_normal_user():
    reg = ToolRegistry()
    tool = _RecordingTool("dangerous", risk=True, min_level=0)
    reg.register(tool)

    out = _run(reg.execute(_call("dangerous"), _ctx(level=200)))
    assert not out.ok
    assert "权限不足" in out.output and "管理员" in out.output
    assert tool.ran == 0  # 未授权绝不执行


def test_admin_only_risk_tool_allows_admin():
    reg = ToolRegistry()
    tool = _RecordingTool("dangerous", risk=True, min_level=0)
    reg.register(tool)

    out = _run(reg.execute(
        _call("dangerous"), _ctx(uid="admin", level=256, is_admin=True)
    ))
    assert out.ok and "ran:admin" in out.output
    assert tool.ran == 1


def test_level_gated_risk_tool():
    reg = ToolRegistry()
    tool = _RecordingTool("level_thing", risk=True, min_level=10)
    reg.register(tool)

    denied = _run(reg.execute(_call("level_thing"), _ctx(level=9)))
    assert not denied.ok and "等级 10" in denied.output and tool.ran == 0

    allowed = _run(reg.execute(_call("level_thing"), _ctx(level=10)))
    assert allowed.ok and tool.ran == 1

    admin = _run(reg.execute(
        _call("level_thing"), _ctx(uid="admin", level=256, is_admin=True)
    ))
    assert admin.ok and tool.ran == 2


def test_visibility_independent_of_authorization():
    # specs 不过滤 risk 工具：能不能看见与能不能执行是两层
    reg = ToolRegistry()
    reg.register(_RecordingTool("dangerous", risk=True, min_level=0))
    specs = reg.specs()
    assert len(specs) == 1 and specs[0]["function"]["name"] == "dangerous"


def test_unknown_tool_still_reports_unknown():
    reg = ToolRegistry()
    out = _run(reg.execute(_call("nope"), _ctx(is_admin=True)))
    assert not out.ok and "未知工具" in out.output
