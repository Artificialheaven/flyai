"""聊天 SSE 主通道。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.loop import DEFAULT_SYSTEM_PROMPT, run_agent_loop
from ..errors import ProviderError
from ..lore.attributes import AttributeManager, resolve_attribute_defs
from ..lore.query import LoreQuerier, build_member_system_prompt, build_system_prompt
from ..sessions.resolve import (
    effective_character,
    effective_world,
    member_character,
    thread_messages,
)
from ..schemas import Message
from ..sse import sse_event

logger = logging.getLogger("flyai.chat")

router = APIRouter(prefix="/api", tags=["chat"])


def _is_marker(text: str, markers) -> bool:
    t = (text or "").strip()
    return t in markers or t == ""


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    provider_id: Optional[str] = None
    tools_enabled: bool = True
    thread_id: Optional[str] = None  # beta：多 AI 群聊窗口


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)

    session = None
    if req.session_id:
        session = sc.store.get(req.session_id)
        if session is None:
            from fastapi import HTTPException

            raise HTTPException(404, "会话不存在")
    else:
        session = sc.store.create()

    provider = state.get_provider(req.provider_id)
    if provider is None:
        from fastapi import HTTPException

        raise HTTPException(400, "未配置可用提供商，请在设置中添加")

    # beta：按窗口分发。带 thread_id 走房间/群聊，否则走旧单 AI 会话
    if req.thread_id:
        return await _chat_in_thread(state, sc, session, req, provider)

    # ---- 旧单 AI 会话路径 ----
    # 加载角色/世界观（优先会话副本）→ 构建系统提示词与查询/属性服务
    character = effective_character(session, sc.lore)
    world = effective_world(session, sc.lore)
    system_prompt = build_system_prompt(
        character, world, DEFAULT_SYSTEM_PROMPT, roleplay=state.config.roleplay
    )
    services = {}
    if character or world:
        services["lore"] = LoreQuerier(character, world)
    attr_defs = resolve_attribute_defs(character, state.config.roleplay)
    if state.config.roleplay.attributes.enabled and attr_defs:
        services["attributes"] = AttributeManager(session, attr_defs)

    # 追加用户消息；首条用户消息自动设为标题
    session.messages.append(Message(role="user", content=req.message))
    user_count = sum(1 for m in session.messages if m.role == "user")
    if user_count == 1:
        session.set_title(req.message[:20])
    sc.store.save(session)

    async def event_gen():
        try:
            yield sse_event(
                "message_start",
                {
                    "session_id": session.id,
                    "character_id": session.character_id,
                    "world_id": session.world_id,
                    "attributes": (
                        services["attributes"].get_all()
                        if "attributes" in services
                        else session.attributes
                    ),
                },
            )
            async for (ev, data) in run_agent_loop(
                history=session.messages,
                provider=provider,
                registry=state.registry,
                settings=state.config.tools,
                system_prompt=system_prompt,
                format_example=state.config.roleplay.format_example,
                session_id=session.id,
                services=services,
            ):
                yield sse_event(ev, data)
            sc.store.save(session)
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            yield sse_event("error", {"message": str(exc), "fatal": True})
            sc.store.save(session)
        except asyncio.CancelledError:
            sc.store.save(session)
            raise
        except Exception as exc:
            logger.exception("聊天流异常")
            yield sse_event("error", {"message": f"{type(exc).__name__}: {exc}", "fatal": True})
            sc.store.save(session)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _chat_in_thread(state, sc, session, req: ChatRequest, provider):
    """beta：把用户消息投进指定窗口，并按窗口内 AI 座位逐一响应（fan-out）。

    - dm 窗口：单 AI，等价旧单 AI 会话，但历史/角色取该窗口。
    - group 窗口：同一共享历史下按 member_ids 顺序逐个 AI 各生成一段，
      后一个 AI 的系统提示词能看到前面 AI 的发言（互为上下文）。
    """
    from fastapi import HTTPException

    thread = session.thread(req.thread_id)
    if thread is None:
        raise HTTPException(404, "窗口不存在")
    history = thread_messages(session, thread.id)  # 该窗口的独立历史

    # 解析窗口内 AI 座位（按 member_ids 顺序；dm=1 个，group=多个）
    speakers = []
    for mid in thread.member_ids:
        m = session.member(mid)
        if m is not None and not m.is_user:
            speakers.append(m)
    if not speakers:
        raise HTTPException(400, "窗口内没有 AI 参与者")

    world = effective_world(session, sc.lore)

    # 场地参与者名单（仅 group 需要，用于提示词）
    audience_names = []
    for spk in speakers:
        audience_names.append(spk.name)
    audience_names.append("你")

    # 群聊（≥2 个 AI）才启用「AI 自评是否需要发言」；私聊单 AI 必回
    allow_skip = len(speakers) > 1
    skip_marker = ("略", "「略」", "（略）", "[略]", "[跳过]", "(skip)")

    # 追加用户消息到该窗口
    history.append(Message(role="user", content=req.message))
    sc.store.save(session)

    async def event_gen():
        try:
            yield sse_event(
                "message_start",
                {"session_id": session.id, "thread_id": thread.id, "character_id": "", "world_id": ""},
            )
            for spk in speakers:
                char = member_character(spk, sc.lore)
                others = [n for n in audience_names if n != spk.name]
                sys_prompt = build_member_system_prompt(
                    name=spk.name,
                    memory=spk.memory,
                    attributes=spk.attributes,
                    audience=others,
                    character=char,
                    world=world,
                    base=DEFAULT_SYSTEM_PROMPT,
                    roleplay=state.config.roleplay,
                )
                services = {}
                if char or world:
                    services["lore"] = LoreQuerier(char, world)
                # 开始该 AI 的发言：标记当前发言者，必为首帧
                yield sse_event(
                    "speaker",
                    {"member_id": spk.id, "name": spk.name, "character_id": spk.character_id or ""},
                )
                before = len(history)
                async for (ev, data) in run_agent_loop(
                    history=history,
                    provider=provider,
                    registry=state.registry,
                    settings=state.config.tools,
                    system_prompt=sys_prompt,
                    format_example=state.config.roleplay.format_example,
                    session_id=session.id,
                    services=services,
                ):
                    yield sse_event(ev, data)
                # 给本次追加的 assistant 回复打上发言者标记，供后续上下文与展示
                # 群聊里若该 AI 自评「无需发言」（内容为空或略过标记），标记隐藏但不删除，
                # 且通知前端不展示这块输出。
                skipped = False
                for m in history[before:]:
                    if m.role != "assistant":
                        continue
                    m.name = spk.name
                    if (
                        allow_skip
                        and not skipped
                        and (m.content is None or _is_marker(m.content or "", skip_marker))
                    ):
                        m.hidden = True
                        skipped = True
                if skipped:
                    yield sse_event(
                        "speaker_skip", {"member_id": spk.id, "name": spk.name}
                    )
            sc.store.save(session)
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            yield sse_event("error", {"message": str(exc), "fatal": True})
            sc.store.save(session)
        except asyncio.CancelledError:
            sc.store.save(session)
            raise
        except Exception as exc:
            logger.exception("群聊流异常")
            yield sse_event("error", {"message": f"{type(exc).__name__}: {exc}", "fatal": True})
            sc.store.save(session)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )