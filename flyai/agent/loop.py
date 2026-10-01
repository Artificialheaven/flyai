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
from ..schemas import ImageAttachment, Message, ToolCall
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "你是一个能通过调用工具自行获取信息的 AI 助手。"
    "当需要查询文件、联网或执行任务时，主动调用相应工具，"
    "根据工具返回的结果再决定下一步或给出最终回答。"
)

# 工具调用状态文案渲染
_STATUS_RENDERERS = {
    "read_file": lambda a: f"正在读取文件: {a.get('path', '')}",
    "query_lore": lambda a: f"正在查询内容表: {a.get('kind', '')}/{a.get('keyword', '') or '全部'}",
    "update_attributes": lambda a: _render_attr_status(a),
    "generate_image": lambda a: "正在生成图片…",
    "edit_image": lambda a: "正在以参考图生成图片…",
    "search_pixiv": lambda a: f"正在搜索 P 站插画: {a.get('keyword', '')}",
    "pick_pixiv": lambda a: "正在下载精选原图…",
    "web_search": lambda a: f"正在联网搜索: {a.get('query', '')}",
    "web_fetch": lambda a: f"正在抓取网页: {a.get('url', '')}",
}


def _render_attr_status(arguments: Dict[str, Any]) -> str:
    changes = arguments.get("changes") or []
    names = "、".join(str(c.get("key", "")) for c in changes if isinstance(c, dict)) or "属性"
    return f"正在更新属性: {names}"


def _provider_supports_vision(provider) -> bool:
    cfg = getattr(provider, "cfg", None)
    return bool(getattr(cfg, "vision", False))


def _meta_images(meta: Dict[str, Any]) -> Optional[List[ImageAttachment]]:
    """把 ToolResult.meta 里的图片转为工具消息附件。

    meta.images：多张（如 P 站候选）；meta.image：单张（生图/读图，向后兼容）。
    """
    raw_list = meta.get("images")
    if raw_list:
        out = []
        for im in raw_list:
            if isinstance(im, dict) and im.get("data"):
                out.append(
                    ImageAttachment(
                        media_type=im.get("media_type", "image/jpeg"),
                        data=im.get("data", ""),
                    )
                )
        return out or None
    single = meta.get("image")
    if single and single.get("data"):
        return [
            ImageAttachment(
                media_type=single.get("media_type", "image/jpeg"),
                data=single.get("data", ""),
            )
        ]
    return None


def _render_status(call: ToolCall) -> str:
    renderer = _STATUS_RENDERERS.get(call.name)
    if renderer:
        try:
            return renderer(call.arguments)
        except Exception:
            pass
    return f"正在调用工具 {call.name}"


# 拥有独立轮数预算的联网工具
WEB_TOOL_NAMES = {"web_search", "web_fetch"}


async def run_agent_loop(
    history: List[Message],
    provider: Any,
    registry: ToolRegistry,
    settings: ToolSettings,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    session_id: str = "",
    services: Optional[Dict[str, Any]] = None,
    web_max_iterations: Optional[int] = None,
) -> AsyncIterator[Tuple[str, dict]]:
    """执行 agentic 循环。history 会被原地追加 assistant/tool 消息。

    services: 注入到 ToolContext 的服务（如 {'lore': LoreQuerier}）。
    web_max_iterations：web_search/web_fetch 的独立轮数预算，与通用工具轮数
    （settings.max_iterations）分开计数、互不占用；None 时回退为通用值。
    """

    registry.set_timeout(settings.timeout_seconds)
    ctx = ToolContext(
        allowed_read_dirs=[Path(p).resolve() for p in settings.allowed_read_dirs if p],
        tool_settings=settings,
        session_id=session_id,
        services=services or {},
    )

    # 整轮（可能含多次 LLM 调用）实际计费 token：各次调用累加
    usage_in = 0
    usage_out = 0

    web_limit = int(web_max_iterations or settings.max_iterations)
    # 两套独立计数：非联网轮 / 联网轮（一轮混合两类调用则两边各记一次）
    general_used = 0
    web_used = 0
    iteration = 0
    while True:
        iteration += 1
        tool_specs = registry.specs() if settings.enabled else []
        request_messages = list(history)
        if system_prompt:
            request_messages.insert(0, Message(role="system", content=system_prompt))

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
            elif ev.type == StreamEventType.USAGE and ev.usage:
                usage_in += int(ev.usage.get("prompt_tokens") or 0)
                usage_out += int(ev.usage.get("completion_tokens") or 0)

        assistant = Message(
            role="assistant",
            content="".join(content_parts) or None,
            # 纯空白思考（如模型只吐一个换行）落库为 None，
            # 避免历史里出现展开为空的「思考过程」
            thinking="".join(thinking_parts).strip() or None,
            tool_calls=calls,
        )
        history.append(assistant)

        if not calls:
            yield ("done", {"reason": "stop", "iterations": iteration,
                            "usage": _usage_payload(usage_in, usage_out)})
            return

        # 先判定本轮类型：纯联网轮只占 web 预算，纯其他轮只占通用预算
        call_names = {c.name for c in calls}
        web_round = bool(call_names & WEB_TOOL_NAMES)
        other_round = bool(call_names - WEB_TOOL_NAMES)

        # 预算检查：上一轮结束后某类可能已触顶。
        # 上限只拦截同类工具的下一轮调用——已用满 N 轮后模型仍可直接给出最终回复；
        # 纯另一类工具的调用也不受影响（两套预算不共享）。
        cap_reason = None
        cap_message = ""
        if other_round and general_used >= settings.max_iterations:
            cap_reason = "max_iterations"
            cap_message = f"已达到最大工具调用轮数（{settings.max_iterations}），停止执行"
        elif web_round and web_used >= web_limit:
            cap_reason = "max_web_iterations"
            cap_message = (f"已达到联网工具最大轮数（web_search/web_fetch 共 {web_limit} 轮），"
                           "停止执行")
        if cap_reason is not None:
            yield ("tool_calls", {"calls": [c.to_dict() for c in calls]})
            for call in calls:
                yield ("tool_start", {"id": call.id, "name": call.name,
                                      "arguments": call.arguments,
                                      "status_text": "已达到轮数上限"})
                yield ("tool_end", {"id": call.id, "name": call.name, "ok": False,
                                    "preview": cap_message})
                # 补 tool 消息占位：assistant 的 tool_calls 必须有对应 tool 消息，
                # 否则历史回灌给提供商时格式非法
                history.append(Message(role="tool", tool_call_id=call.id, name=call.name,
                                       content=cap_message, ok=False))
            yield ("error", {"message": cap_message, "fatal": False})
            yield ("done", {"reason": cap_reason, "iterations": iteration,
                            "usage": _usage_payload(usage_in, usage_out)})
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
            # 多图标记卡（如 search_pixiv 含十几行候选标记）需完整输出供前端解析，
            # 普通工具文本也只在 SSE 传输不落库，给到 4000 字符
            preview = (result.output or "")[:4000]
            yield (
                "tool_end",
                {"id": call.id, "name": call.name, "ok": result.ok, "preview": preview},
            )
            tool_images = None
            if result.meta and _provider_supports_vision(provider):
                tool_images = _meta_images(result.meta)
            history.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    name=call.name,
                    content=result.output,
                    ok=result.ok,
                    images=tool_images,
                )
            )

        # 属性被修改后，把最新属性值推给前端
        if attr_changed and "attributes" in ctx.services:
            yield ("attributes", {"attributes": ctx.services["attributes"].get_all()})

        # 本轮执行完再计数：上限在下一轮同类工具调用时才拦截
        if other_round:
            general_used += 1
        if web_round:
            web_used += 1


def _usage_payload(usage_in: int, usage_out: int) -> Dict[str, int]:
    return {
        "prompt_tokens": usage_in,
        "completion_tokens": usage_out,
        "total_tokens": usage_in + usage_out,
    }