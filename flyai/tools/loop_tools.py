"""长跑模式工具（P2）：申请、免审批启动与收尾。

- loop_request：risk 工具，min_level 取 LoopSettings.request_min_level，
  写 loop_requests(pending) 等待管理员审批；
- loop_start：管理员免审批直接启动（实际启动由路由层注入的 loop_starter
  服务完成，工具层不直接碰 ChatRun/会话锁）；
- loop_finish：不进全局注册表，只在长跑请求里作为 extra 注入，
  run_agent_loop 按工具名识别后置 finished，worker 收尾写 loop_sessions。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from .base import Tool, ToolContext, ToolResult


def _loop_enabled(ctx: ToolContext) -> bool:
    settings = ctx.services.get("loop_settings")
    return bool(getattr(settings, "enabled", False))


class LoopRequestTool(Tool):
    """提交长跑申请（审批制）。注册时按配置覆盖 min_level。"""

    name = "loop_request"
    description = (
        "申请进入长跑模式执行一个需要多轮工具调用的长任务：写下任务目标与申请理由，"
        "提交后进入管理员审批，批准后自动开始。普通对话不要调用。"
    )
    risk = True
    min_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string",
                     "description": "长跑任务目标，一句话说清要完成什么，≤500 字"},
            "reason": {"type": "string",
                       "description": "申请理由（为什么需要长跑而不是普通对话），可选"},
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        if not _loop_enabled(ctx):
            return ToolResult(ok=False, output="[长跑模式未启用] 请管理员先在设置中开启。")
        mgr = ctx.services.get("loop_manager")
        if mgr is None:
            return ToolResult(ok=False, output="[长跑模式未就绪] loop_manager 未注入。")
        task = str(arguments.get("task") or "").strip()
        if not task:
            return ToolResult(ok=False, output="task 不能为空")
        reason = str(arguments.get("reason") or "").strip()
        try:
            req_id = await asyncio.to_thread(
                mgr.request_create, ctx.uid, ctx.session_id,
                task[:500], reason[:500],
            )
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[长跑申请失败] %s: %s"
                                     % (type(exc).__name__, exc))
        return ToolResult(
            output="已提交长跑申请 #%d（任务：%s），等待管理员审批后自动开始。"
                   % (req_id, task[:100])
        )


class LoopStartTool(Tool):
    """管理员免审批直接启动长跑（risk + min_level=0 ⇒ 注册表只放行 admin）。"""

    name = "loop_start"
    description = (
        "管理员专用：免审批直接在当前会话启动一个长跑任务。启动后本工具立即返回，"
        "长跑在独立后台运行中执行，你随后会在新的长跑上下文里继续工作。"
    )
    risk = True
    min_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "长跑任务目标，≤500 字"},
            "reason": {"type": "string", "description": "可选备注"},
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        if not _loop_enabled(ctx):
            return ToolResult(ok=False, output="[长跑模式未启用] 请管理员先在设置中开启。")
        starter = ctx.services.get("loop_starter")
        if starter is None:
            return ToolResult(ok=False, output="[长跑模式未就绪] 当前会话无法启动长跑。")
        task = str(arguments.get("task") or "").strip()
        if not task:
            return ToolResult(ok=False, output="task 不能为空")
        try:
            info = await starter(
                ctx.uid, ctx.session_id, task[:500],
                str(arguments.get("reason") or "")[:500],
            )
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[长跑启动失败] %s: %s"
                                     % (type(exc).__name__, exc))
        if not info.get("ok"):
            return ToolResult(ok=False,
                              output="[长跑启动失败] %s" % info.get("error", "未知错误"))
        return ToolResult(
            output="长跑已启动（loop_id=%s）。本次对话结束后任务将在后台持续执行，"
                   "可通过长跑状态页查看进度。" % info.get("loop_id", "?")
        )


class LoopFinishTool(Tool):
    """长跑收尾：只在 loop_ctx 存在的请求里作为 extra 注入，全局表不可见。"""

    name = "loop_finish"
    description = (
        "长跑任务完成后调用一次：提交最终成果总结，系统据此结束长跑并把结果落库。"
        "只在长跑模式中可见，普通对话无法调用。"
    )
    risk = False
    min_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "summary": {"type": "string",
                        "description": "任务最终成果总结，写给用户看的完整结论"},
        },
        "required": ["summary"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        summary = str(arguments.get("summary") or "").strip()
        if not summary:
            return ToolResult(ok=False, output="summary 不能为空")
        # 真正的 finished 标记由 run_agent_loop 按工具名识别后设置，
        # 这里只把总结作为 tool 结果落进历史（worker 收尾读取同一条消息）
        return ToolResult(output=summary)


# 全局注册的长跑工具（loop_finish 不在其中）
LOOP_TOOLS = (LoopRequestTool, LoopStartTool)
