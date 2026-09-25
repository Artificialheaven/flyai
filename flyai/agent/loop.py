"""Agentic Loop 核心。

run_agent_loop 是一个异步生成器，产出 (event_name, data) 元组；
事件序列见各 emit 调用，最终由路由层包装为 SSE 帧转发给前端。

流程：用户消息 → 流式请求（思考/正文增量）→ 若含工具调用则逐次执行并把结果
回灌历史 → 再次请求，直到模型给出不含工具调用的最终回复或达到轮数上限。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from ..config import ToolSettings
from ..providers.base import StreamEventType
from ..schemas import Message, ToolCall
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "你是一个能通过调用工具自行获取信息的 AI 助手。"
    "当需要查询文件、联网或执行任务时，主动调用相应工具，"
    "根据工具返回的结果再决定下一步或给出最终回答。"
)

# 格式示范注入：置于消息尾部、紧邻当前回合（仅影响本次请求，不写入会话）。
# 插在「最后一条用户消息」之前；放在尾部可保持 system+历史前缀逐字节不变，不破坏前缀缓存。
_FORMAT_DEMO_HINT = "（请严格遵循上面示例所示格式来回复）"


def _inject_format_example(request_messages: List[Message], format_example: str) -> None:
    """把 few-shot 格式示范插到末尾用户消息之前（仅当末尾是用户消息时）。"""
    if not format_example:
        return
    if not request_messages or request_messages[-1].role != "user":
        return
    start = len(request_messages) - 1
    request_messages[start:start] = [
        Message(role="user", content=_FORMAT_DEMO_HINT),
        Message(role="assistant", content=format_example),
    ]


# 工具调用状态文案渲染
_STATUS_RENDERERS = {
    "read_file": lambda a: f"正在读取文件: {a.get('path', '')}",
    "query_lore": lambda a: f"正在查询内容表: {a.get('kind', '')}/{a.get('keyword', '') or '全部'}",
    "update_attributes": lambda a: _render_attr_status(a),
}


def _render_attr_status(arguments: Dict[str, Any]) -> str:
    changes = arguments.get("changes") or []
    names = "、".join(str(c.get("key", "")) for c in changes if isinstance(c, dict)) or "属性"
    return f"正在更新属性: {names}"


def _render_status(call: ToolCall) -> str:
    renderer = _STATUS_RENDERERS.get(call.name)
    if renderer:
        try:
            return renderer(call.arguments)
        except Exception:
            pass
    return f"正在调用工具 {call.name}"


async def run_agent_loop(
    history: List[Message],
    provider: Any,
    registry: ToolRegistry,
    settings: ToolSettings,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    format_example: str = "",
    session_id: str = "",
    services: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[Tuple[str, dict]]:
    """执行 agentic 循环。history 会被原地追加 assistant/tool 消息。

    services: 注入到 ToolContext 的服务（如 {'lore': LoreQuerier}）。
    format_example: 格式示范（few-shot），注入到本次请求尾部、紧邻当前用户消息之前，
    仅影响请求、不写入会话，不破坏前缀缓存。
    """

    registry.set_timeout(settings.timeout_seconds)
    ctx = ToolContext(
        allowed_read_dirs=[Path(p).resolve() for p in settings.allowed_read_dirs if p],
        tool_settings=settings,
        session_id=session_id,
        services=services or {},
    )

    for iteration in range(1, settings.max_iterations + 1):
        tool_specs = registry.specs() if settings.enabled else []
        request_messages = list(history)
        if system_prompt:
            request_messages.insert(0, Message(role="system", content=system_prompt))
        _inject_format_example(request_messages, format_example)

        thinking_parts: List[str] = []
        content_parts: List[str] = []
        calls: Optional[List[ToolCall]] = None
        async for ev in provider.stream_chat(request_messages, tool_specs):
            if ev.type == StreamEventType.THINKING_DELTA:
                thinking_parts.append(ev.text)
                yield ("thinking_delta", {"text": ev.text})
            elif ev.type == StreamEventType.CONTENT_DELTA:
                content_parts.append(ev.text)
                yield ("content_delta", {"text": ev.text})
            elif ev.type == StreamEventType.TOOL_CALLS:
                calls = ev.tool_calls

        assistant = Message(
            role="assistant",
            content="".join(content_parts) or None,
            thinking="".join(thinking_parts) or None,
            tool_calls=calls,
        )
        history.append(assistant)

        if not calls:
            yield ("done", {"reason": "stop", "iterations": iteration})
            return

        # 有工具调用：逐个串行执行，结果回灌历史后继续下一轮
        yield ("tool_calls", {"calls": [c.to_dict() for c in calls]})
        attr_changed = False
        for call in calls:
            attr_changed = attr_changed or (call.name == "update_attributes")
            yield (
                "tool_start",
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "status_text": _render_status(call),
                },
            )
            result = await registry.execute(call, ctx)
            preview = (result.output or "")[:200]
            yield (
                "tool_end",
                {"id": call.id, "name": call.name, "ok": result.ok, "preview": preview},
            )
            history.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    name=call.name,
                    content=result.output,
                    ok=result.ok,
                )
            )

        # 属性被修改后，把最新属性值推给前端
        if attr_changed and "attributes" in ctx.services:
            yield ("attributes", {"attributes": ctx.services["attributes"].get_all()})

    # 达到轮数上限
    yield (
        "error",
        {"message": f"已达到最大工具调用轮数（{settings.max_iterations}），停止执行", "fatal": False},
    )
    yield ("done", {"reason": "max_iterations", "iterations": settings.max_iterations})