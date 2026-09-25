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
from ..lore.query import LoreQuerier, build_system_prompt
from ..sessions.resolve import effective_character, effective_world
from ..schemas import Message
from ..sse import sse_event

logger = logging.getLogger("flyai.chat")

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    provider_id: Optional[str] = None
    tools_enabled: bool = True


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