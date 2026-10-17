"""Agentic Loop 核心。

run_agent_loop 是一个异步生成器，产出 (event_name, data) 元组；
事件序列见各 emit 调用，最终由路由层包装为 SSE 帧转发给前端。

流程：用户消息 → 流式请求（思考/正文增量）→ 若含工具调用则逐次执行并把结果
回灌历史 → 再次请求，直到模型给出不含工具调用的最终回复或达到轮数上限。
"""

from __future__ import annotations

import asyncio
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

# 工具回合之后追加的提示（不入库）：模型常把 assistant(tool_calls)→tool 形态
# 当成「自己说完话、用户没再开口」的新一轮，演出「你怎么不说话」。
# 明确工具结果不是用户发言，要求续写同一轮
_POST_TOOL_NUDGE = (
    "（系统提示：上面是工具返回的执行结果，并非用户的新发言，"
    "对话仍停留在你上一条动作的同一轮。请直接以你的角色身份继续或完成上一条回复，"
    "把需要用到的结果自然融入；不要表现成用户沉默、没有说话或没有输入，"
    "不要询问用户为什么不开口，也不要等待用户的下一条指令。）"
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


def _tag_actor(m: Message, actor: str) -> Message:
    if actor:
        m.actor_id = actor
    return m


def _spoken_text(content: str, name: str) -> str:
    """别人的台词加「名字：」前缀（幂等）。

    provider 适配器不发送消息的 name 字段，群聊历史里多个 AI 的 assistant
    消息若无署名，模型会把别人的台词当成自己说过的话，导致串人设。
    """
    text = content or ""
    prefix = f"{name}："
    return text if text.startswith(prefix) else prefix + text


def _actor_view(history: List[Message], actor: str,
                actor_name: str = "") -> List[Message]:
    """群聊发言视图：别人座位产生的工具调用/工具结果/思考对当前发言者隐藏，
    只保留对方说出口的正文（复制成不带 tool_calls 的干净 assistant 消息，
    并加「名字：」前缀，避免模型把别人的台词当成自己的）；
    自己座位的消息原样保留。

    旧数据（actor_id 未落盘时代产生的消息）用持久化的 name 与当前座位名
    比对兜底归属；既无 actor_id 也无 name 的消息无法判断归属，原样保留
    （以维持 tool_call/result 配对合法、避免提供商 400）。
    """
    if not actor:
        return list(history)
    view: List[Message] = []
    for m in history:
        owner = getattr(m, "actor_id", "") or ""
        if not owner:
            # 无归属旧消息：能凭名字认出是别人 → 按别人处理；否则原样。
            # 带 tool_calls 的旧回合不能拆（配对的 tool 结果同样无归属，
            # 拆了会产生孤立 tool 消息触发提供商 400），整条原样保留
            if (m.role == "assistant" and not m.tool_calls and actor_name
                    and (m.name or "") and m.name != actor_name):
                if not m.hidden and (m.content or "").strip():
                    view.append(
                        Message(role="assistant", id=m.id,
                                content=_spoken_text(m.content, m.name),
                                name=m.name)
                    )
                continue
            view.append(m)
            continue
        if owner == actor:
            view.append(m)
            continue
        # 其他发言者：只留正文（跳过隐藏的「略」与工具回合）
        if (m.role == "assistant" and not m.hidden
                and (m.content or "").strip()):
            speaker = m.name or "其他角色"
            view.append(
                Message(role="assistant", id=m.id,
                        content=_spoken_text(m.content, speaker),
                        name=m.name)
            )
    return view


def _keep_recent_turns(messages: List[Message],
                       keep_turns: int) -> List[Message]:
    """只保留最近 keep_turns 个对话轮，返回新列表（不修改入参）。

    一轮 = 一条 user 消息及其后到下一条 user 消息之前的全部消息。
    keep_turns <= 0、或 user 消息数不超过 keep_turns 时原样返回副本。

    防御性修正：切片开头若是失去发起 assistant 的孤儿 tool 消息，按
    tool_call_id 向前找回该 assistant（起点回退）；找不到发起者则跳过
    开头连续的 tool 块。按 user 边界切分时正常不会触发。
    """
    if not keep_turns or keep_turns <= 0:
        return list(messages)
    user_positions = [i for i, m in enumerate(messages) if m.role == "user"]
    if len(user_positions) <= keep_turns:
        return list(messages)
    start = user_positions[-keep_turns]
    while start < len(messages) and messages[start].role == "tool":
        call_id = messages[start].tool_call_id
        owner = -1
        for j in range(start - 1, -1, -1):
            mj = messages[j]
            if (mj.role == "assistant"
                    and any(c.id == call_id for c in (mj.tool_calls or []))):
                owner = j
                break
        if owner >= 0:
            start = owner
        else:
            end = start + 1
            while end < len(messages) and messages[end].role == "tool":
                end += 1
            start = end
    return messages[start:]


async def run_agent_loop(
    history: List[Message],
    provider: Any,
    registry: ToolRegistry,
    settings: ToolSettings,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    session_id: str = "",
    run_id: str = "",
    services: Optional[Dict[str, Any]] = None,
    web_max_iterations: Optional[int] = None,
    actor: str = "",
    actor_name: str = "",
    keep_turns: int = 0,
    allowed_tools: Optional[set] = None,
    deep_thinking: bool = False,
    post_history_instructions: str = "",
    max_tokens: Optional[int] = None,
) -> AsyncIterator[Tuple[str, dict]]:
    """执行 agentic 循环。history 会被原地追加 assistant/tool 消息。

    services: 注入到 ToolContext 的服务（如 {'lore': LoreQuerier}）。
    web_max_iterations：web_search/web_fetch 的独立轮数预算，与通用工具轮数
    （settings.max_iterations）分开计数、互不占用；None 时回退为通用值。
    actor：群聊里当前发言座位 id。本轮回灌的消息都带上该标记；构建请求时，
    其他发言者的工具调用/工具结果/思考对其不可见，只能看到对方说出口的内容
    （见 _actor_view），避免后发言者看到别人已调 update_chat_memory 就不写
    自己的记忆，也避免他人记忆内容串场。actor 为空时不过滤。
    actor_name：当前发言座位显示名，用于给 actor_id 缺失的旧消息按 name
    兜底判断归属。
    keep_turns：发给模型的最近对话轮数（1 轮 = 一条 user 消息及其回复链），
    只截断请求、不动 history；0（默认）表示全量发送。
    allowed_tools：按用户功能偏好过滤后允许暴露的工具名集合；None=全量。
    deep_thinking：本轮开启深度思考（Anthropic 原生 extended thinking；
    OpenAI 兼容厂商合并模型配置的 thinking_params 请求片段）。
    post_history_instructions：角色卡 PHI，每轮追加在聊天历史之后（工具续写
    nudge 之前），不写入 history；空串不追加。
    max_tokens：本轮回复 token 上限覆盖（窗口 > 用户全局 > 提供商配置），
    None/0 时不传，由提供商按自身配置处理。
    """

    registry.set_timeout(settings.timeout_seconds)
    ctx = ToolContext(
        allowed_read_dirs=[Path(p).resolve() for p in settings.allowed_read_dirs if p],
        tool_settings=settings,
        session_id=session_id,
        run_id=run_id,
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
    pending_nudge = False  # 上一轮执行了工具：下轮请求尾部追加续写提示
    while True:
        iteration += 1
        tool_specs = registry.specs(allowed_tools) if settings.enabled else []
        request_messages = _actor_view(history, actor, actor_name)
        request_messages = _keep_recent_turns(request_messages, keep_turns)
        if system_prompt:
            request_messages.insert(0, Message(role="system", content=system_prompt))
        # PHI（角色卡历史后指令）：紧贴历史尾部；工具轮时续写 nudge 放在它之后，
        # 让"继续完成回复"保持为模型看到的最后一句
        phi = (post_history_instructions or "").strip()
        if phi:
            request_messages.append(Message(role="system", content=phi))
        if pending_nudge:
            request_messages.append(
                Message(role="system", content=_POST_TOOL_NUDGE)
            )

        thinking_parts: List[str] = []
        thinking_blocks: Optional[List[Dict[str, Any]]] = None
        content_parts: List[str] = []
        calls: Optional[List[ToolCall]] = None
        try:
            async for ev in provider.stream_chat(
                request_messages, tool_specs,
                deep_thinking=deep_thinking, max_tokens=max_tokens,
            ):
                if ev.type == StreamEventType.THINKING_DELTA:
                    thinking_parts.append(ev.text)
                    yield ("thinking_delta", {"text": ev.text})
                elif ev.type == StreamEventType.THINKING_BLOCKS and ev.blocks:
                    thinking_blocks = ev.blocks
                elif ev.type == StreamEventType.CONTENT_DELTA:
                    content_parts.append(ev.text)
                    yield ("content_delta", {"text": ev.text})
                elif ev.type == StreamEventType.CONTENT_REPLACE:
                    # 厂商把工具调用回声进正文：替换已累积/已推送的正文
                    content_parts[:] = [ev.text]
                    yield ("content_replace", {"text": ev.text})
                elif ev.type == StreamEventType.TOOL_CALLS:
                    calls = ev.tool_calls
                elif ev.type == StreamEventType.USAGE and ev.usage:
                    usage_in += int(ev.usage.get("prompt_tokens") or 0)
                    usage_out += int(ev.usage.get("completion_tokens") or 0)
        except (asyncio.CancelledError, GeneratorExit):
            # 用户点停止：把已经流式产出的半截回复补进历史（drive finally 会保存），
            # 不再像旧版那样中止后答案整段消失
            partial_text = "".join(content_parts)
            partial_think = "".join(thinking_parts).strip()
            if partial_text or partial_think or calls:
                history.append(
                    _tag_actor(
                        Message(
                            role="assistant",
                            content=partial_text or None,
                            thinking=partial_think or None,
                            thinking_blocks=thinking_blocks,
                            tool_calls=calls,
                        ),
                        actor,
                    )
                )
            raise

        assistant = Message(
            role="assistant",
            content="".join(content_parts) or None,
            # 纯空白思考（如模型只吐一个换行）落库为 None，
            # 避免历史里出现展开为空的「思考过程」
            thinking="".join(thinking_parts).strip() or None,
            # Anthropic signature 块：仅在确实开了思考时存在；后续工具轮原样回放
            thinking_blocks=thinking_blocks,
            tool_calls=calls,
        )
        history.append(_tag_actor(assistant, actor))

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
                history.append(_tag_actor(
                    Message(role="tool", tool_call_id=call.id, name=call.name,
                            content=cap_message, ok=False),
                    actor))
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
                _tag_actor(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=result.output,
                        ok=result.ok,
                        images=tool_images,
                    ),
                    actor,
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
        pending_nudge = True  # 下轮（拿工具结果续写）追加提示，不写进 history


def _usage_payload(usage_in: int, usage_out: int) -> Dict[str, int]:
    return {
        "prompt_tokens": usage_in,
        "completion_tokens": usage_out,
        "total_tokens": usage_in + usage_out,
    }