"""聊天 SSE 主通道。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.loop import DEFAULT_SYSTEM_PROMPT, run_agent_loop
from ..models_access import visible_image_models, visible_providers
from ..errors import ProviderError
from ..lore.attributes import AttributeManager, resolve_attribute_defs
from ..lore.query import LoreQuerier, build_member_system_prompt, build_system_prompt
from ..sessions.resolve import (
    effective_character,
    effective_world,
    member_character,
    thread_messages,
    user_seat,
)
from ..schemas import ImageAttachment, Message
from ..sessions import versions
from ..sse import sse_event

logger = logging.getLogger("flyai.chat")

router = APIRouter(prefix="/api", tags=["chat"])


def _get_querier(sc, character, world):
    """优先用 UserScope 的签名缓存工厂；简易测试 scope 无此方法时直接构造。"""
    factory = getattr(sc, "get_querier", None)
    if factory is not None:
        return factory(character, world)
    return LoreQuerier(character, world)

# 图片输入约束
_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class ImagePayload(BaseModel):
    media_type: str
    data: str  # 不含 data: 前缀的 base64


class ChatRequest(BaseModel):
    message: str = ""
    session_id: Optional[str] = None
    provider_id: Optional[str] = None
    tools_enabled: bool = True
    thread_id: Optional[str] = None  # beta：多 AI 群聊窗口
    images: List[ImagePayload] = []
    # 指定生图模型（不传则用该用户可见的第一个）
    image_model_id: Optional[str] = None
    # 生图引擎："sd" 走本地 Stable Diffusion；其他/空走云端生图模型
    image_engine: Optional[str] = None
    # 历史编辑 / 重新生成：二选一，锚点为该轮用户消息 id（仅 dm 窗口）
    edit_message_id: Optional[str] = None
    regenerate_message_id: Optional[str] = None


def _resolve_visible_provider(state, sc, provider_id: Optional[str]):
    """按当前用户权限解析 LLM 提供商：显式指定优先，否则全局激活。

    存在但不可见 → 403；无可用 → 400。
    """
    wanted = (provider_id or state.config.active_provider or "").strip()
    visible = visible_providers(state.config, sc.uid, sc.level)
    cfg = next((p for p in visible if p.id == wanted), None)
    if cfg is not None:
        return state.get_provider(cfg.id)
    exists = bool(wanted) and any(p.id == wanted for p in state.config.providers)
    if exists:
        raise HTTPException(403, "你的权限等级不足以使用该模型")
    raise HTTPException(400, "未配置可用提供商，请在设置中添加")


def _build_images(req) -> Optional[List[ImageAttachment]]:
    """校验并转换请求中的图片；非法请求直接 400。无 images 字段（内部简易调用）视为无图。"""
    raw_images = getattr(req, "images", None)
    if not raw_images:
        return None
    if len(raw_images) > _MAX_IMAGES:
        raise HTTPException(400, f"每条消息最多附带 {_MAX_IMAGES} 张图片")
    out: List[ImageAttachment] = []
    for img in raw_images:
        if img.media_type not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(400, f"不支持的图片类型: {img.media_type}")
        try:
            raw = base64.b64decode(img.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "图片数据不是合法的 base64")
        if len(raw) == 0:
            raise HTTPException(400, "图片内容为空")
        if len(raw) > _MAX_IMAGE_BYTES:
            raise HTTPException(400, "单张图片不能超过 8MB")
        out.append(ImageAttachment(media_type=img.media_type, data=img.data))
    return out


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

    if not req.message.strip() and not req.images and not req.regenerate_message_id:
        raise HTTPException(400, "消息内容为空")

    provider = _resolve_visible_provider(state, sc, req.provider_id)

    # beta：按窗口分发。带 thread_id 走房间/群聊。
    # 未带 thread_id 但会话已在读取时迁移为房间（members+threads）时，
    # 自动落到主私聊窗口——否则会走旧路径对一个已清空的 session.messages 说话，
    # 丢掉房间里的全部历史。
    thread_id = req.thread_id
    if not thread_id and session.threads:
        from ..sessions.resolve import primary_thread

        pt = primary_thread(session)
        if pt is not None:
            thread_id = pt.id
    if thread_id:
        req.thread_id = thread_id
        return await _chat_in_thread(state, sc, session, req, provider)

    # ---- 旧单 AI 会话路径 ----
    # 加载角色/世界观（优先会话副本）→ 构建系统提示词与查询/属性服务
    character = effective_character(session, sc.lore)
    world = effective_world(session, sc.lore)
    user_char = member_character(user_seat(session), sc.lore)
    system_prompt = build_system_prompt(
        character, world, DEFAULT_SYSTEM_PROMPT, roleplay=state.config.roleplay,
        user_character=user_char,
    )
    services = {}
    if character or world:
        services["lore"] = _get_querier(sc, character, world)
    # 图生图自动取参考图：让工具能扫历史
    services["history"] = session.messages
    # 当前提供商是否支持视觉（搜图工具据此决定筛选指引文案）
    services["vision"] = bool(getattr(getattr(provider, "cfg", None), "vision", False))
    # 生图工具保存目录（该用户的上传目录，经 /uploads/<uid> 访问）
    services["upload_dir"] = getattr(sc, "upload_dir", None)
    services["upload_url_base"] = f"/uploads/{getattr(sc, 'uid', '')}"
    # 当前用户可见的生图模型
    services["image_models"] = visible_image_models(state.config, sc.uid, sc.level)
    services["image_model_id"] = getattr(req, "image_model_id", None)
    # 生图引擎选择（输入区「SD」开关）；子 AI 用当前 LLM 提供商生成 SD 参数
    services["image_engine"] = str(getattr(req, "image_engine", "") or "").strip()
    services["llm_provider"] = provider
    # RAG 语义检索（query_lore / query_chat_memory 共用）：配了 embeddings 才注入
    _get_emb = getattr(state, "get_embedder", None)
    services["embedder"] = _get_emb() if _get_emb else None
    # AI 浏览器（启用时）：会话键按 用户:聊天会话 隔离
    if getattr(state, "browser", None) is not None:
        services["browser_manager"] = state.browser
        services["browser_key"] = "%s:%s" % (sc.uid, session.id)
    # 角色卡自带属性时即启用（不再受全局开关限制）；
    # 旧卡回退全局 list 的逻辑在 resolve_attribute_defs 内已含开关判断
    attr_defs = resolve_attribute_defs(character, state.config.roleplay)
    if attr_defs:
        services["attributes"] = AttributeManager(session, attr_defs)

    # 追加用户消息；首条用户消息自动设为标题
    images = _build_images(req)
    session.messages.append(
        Message(
            role="user",
            content=req.message or None,
            name=user_char.name if user_char else None,
            images=images,
        )
    )
    user_count = sum(1 for m in session.messages if m.role == "user")
    if user_count == 1:
        session.set_title((req.message[:20] or "图片").strip() or "图片")
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
                session_id=session.id,
                services=services,
            ):
                if ev == "done":
                    session.add_usage(data.get("usage"))
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

    # 用户扮演的自设（persona）：用户座位的角色卡；未设则 None（保持原「你」行为）
    user_char = member_character(user_seat(session), sc.lore)
    user_label = user_char.name if user_char else "你"

    # 场地参与者名单（仅 group 需要，用于提示词）
    audience_names = []
    for spk in speakers:
        audience_names.append(spk.name)
    audience_names.append(user_label)

    # 群聊（≥2 个 AI）才启用「AI 自评是否需要发言」；私聊单 AI 必回
    allow_skip = len(speakers) > 1
    skip_marker = ("略", "「略」", "（略）", "[略]", "[跳过]", "(skip)")

    # 历史编辑 / 重新生成（仅 dm 窗口）。getattr：内部 SimpleNamespace 调用
    edit_id = getattr(req, "edit_message_id", None)
    regen_id = getattr(req, "regenerate_message_id", None)
    mode = ""
    anchor = ""
    prev = (0, 0)
    if edit_id or regen_id:
        if edit_id and regen_id:
            raise HTTPException(400, "编辑与重新生成不能同时指定")
        if thread.kind != "dm":
            raise HTTPException(400, "群聊窗口不支持编辑/重新生成")

    images = _build_images(req)
    if edit_id:
        mode = versions.MODE_EDIT
        anchor = edit_id
        try:
            turn_i, _turn_end = versions.locate_turn(history, anchor)
        except ValueError:
            raise HTTPException(400, "要编辑的消息不存在")
        # 纯文本编辑（本次没带新图）时继承原活动用户消息的图片，避免静默丢图
        if not images and history[turn_i].images:
            images = list(history[turn_i].images)
        new_user = Message(
            role="user",
            content=req.message or None,
            name=user_char.name if user_char else None,
            images=images,
        )
        try:
            prev = versions.begin_edit(thread, anchor, new_user)
        except ValueError:
            raise HTTPException(400, "要编辑的消息不存在")
        sc.store.save(session)
    elif regen_id:
        mode = versions.MODE_REGEN
        anchor = regen_id
        try:
            prev = versions.begin_regen(thread, anchor)
        except ValueError:
            raise HTTPException(400, "要重新生成的消息不存在")
        sc.store.save(session)
    else:
        # 追加用户消息到该窗口（用户扮演自设时按角色名标注，便于展示）
        history.append(
            Message(
                role="user",
                content=req.message or None,
                name=user_char.name if user_char else None,
                images=images,
            )
        )
        sc.store.save(session)

    async def event_gen():
        try:
            yield sse_event(
                "message_start",
                {"session_id": session.id, "thread_id": thread.id, "character_id": "", "world_id": ""},
            )
            # 编辑/重生成：流式起点（此刻 history 末尾即锚点用户消息）
            mode_head = len(history) if mode else 0
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
                    user_character=user_char,
                )
                services = {}
                if char or world:
                    services["lore"] = _get_querier(sc, char, world)
                # 图生图自动取参考图：让工具能扫该窗口历史
                services["history"] = history
                # 当前提供商是否支持视觉（搜图工具据此决定筛选指引文案）
                services["vision"] = bool(getattr(getattr(provider, "cfg", None), "vision", False))
                # 记忆工具：让该 AI 能检索其它对话、并更新本会话记忆（注入同一 session/member 引用）
                services["store"] = sc.store
                services["session"] = session
                services["member"] = spk
                # 生图工具保存目录
                services["upload_dir"] = getattr(sc, "upload_dir", None)
                services["upload_url_base"] = f"/uploads/{getattr(sc, 'uid', '')}"
                # 当前用户可见的生图模型
                services["image_models"] = visible_image_models(
                    state.config, sc.uid, sc.level
                )
                services["image_model_id"] = getattr(req, "image_model_id", None)
                # 生图引擎选择；子 AI 用当前 LLM 提供商生成 SD 参数
                services["image_engine"] = str(
                    getattr(req, "image_engine", "") or ""
                ).strip()
                services["llm_provider"] = provider
                # RAG 语义检索：配了 embeddings 提供商才注入，否则工具回退关键词检索
                _get_emb = getattr(state, "get_embedder", None)
                services["embedder"] = _get_emb() if _get_emb else None
                # AI 浏览器（启用时）：会话键按 用户:聊天会话 隔离
                if getattr(state, "browser", None) is not None:
                    services["browser_manager"] = state.browser
                    services["browser_key"] = "%s:%s" % (sc.uid, session.id)
                # 属性跟踪：该 AI 角色卡定义了属性即启用，值落在 member.attributes
                member_attr_defs = resolve_attribute_defs(char, state.config.roleplay)
                if member_attr_defs:
                    services["attributes"] = AttributeManager(spk, member_attr_defs)
                # 开始该 AI 的发言：标记当前发言者，必为首帧
                yield sse_event(
                    "speaker",
                    {"member_id": spk.id, "name": spk.name, "character_id": spk.character_id or ""},
                )
                # 该 AI 的完整发言回合。
                # 群聊里若模型一次给「空回复」（0 个字、且没通过工具得到内容），视为没开口成功：
                # 不要当跳过，用一句系统提醒重试几次，避免『第二个 AI 不说话』。
                # 只有显式回「略」等跳过关字，才算真正的需要跳过。
                head_snapshot = len(history)
                done_data = None
                retry_nudge = ""
                attempts = 0
                skipped = False
                while True:
                    attempts += 1
                    before = len(history)
                    async for (ev, data) in run_agent_loop(
                        history=history,
                        provider=provider,
                        registry=state.registry,
                        settings=state.config.tools,
                        system_prompt=sys_prompt + retry_nudge,
                        session_id=session.id,
                        services=services,
                    ):
                        if ev == "done":
                            done_data = data
                        else:
                            yield sse_event(ev, data)
                    # 给本轮追加的 assistant 回复打上发言者标记（含中间的工具步）。
                    for m in history[before:]:
                        if m.role == "assistant":
                            m.name = spk.name
                    ender = history[before:][-1] if history[before:] else None
                    ender_txt = (
                        (ender.content or "").strip() if (ender and ender.role == "assistant") else None
                    )
                    # 显式跳过关字（「略」等）→ 真正的跳过，不再重试
                    if (
                        allow_skip
                        and ender is not None
                        and ender.role == "assistant"
                        and ender_txt in skip_marker
                    ):
                        skipped = True
                        break
                    # 空回复（没写出任何字）→ 提醒一次后重试，最多尝试 3 次
                    if (
                        allow_skip
                        and ender is not None
                        and ender.role == "assistant"
                        and not ender_txt
                        and attempts < 3
                    ):
                        retry_nudge = (
                            "（系统提醒：上一轮你没有写任何字就结束了。这一轮请务必以你的角色说至少一句"
                            "自然简短的话来回应刚才的话题，不要沉默，也不要回复『略』。）"
                        )
                        continue
                    break
                if done_data:
                    session.add_usage(done_data.get("usage"))
                yield sse_event("done", done_data or {"reason": "stop", "iterations": attempts})
                # 隐藏遗留的空白 assistant 消息（被重试舍弃的空白回合）；真正的跳过则隐藏最后一条。
                if skipped and ender is not None and ender.role == "assistant":
                    ender.hidden = True
                else:
                    for m in history[head_snapshot:]:
                        # 只隐藏真正的空白回合；带工具调用的中间消息
                        # （如先调 generate_image 再补话）不能隐藏，
                        # 否则历史渲染跳过后其工具结果（图片）无处挂载
                        if (
                            m.role == "assistant"
                            and not (m.content or "").strip()
                            and not m.tool_calls
                        ):
                            m.hidden = True
                if skipped:
                    yield sse_event(
                        "speaker_skip", {"member_id": spk.id, "name": spk.name}
                    )
            sc.store.save(session)
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            yield sse_event("error", {"message": str(exc), "fatal": True})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("群聊流异常")
            yield sse_event("error", {"message": f"{type(exc).__name__}: {exc}", "fatal": True})
        finally:
            # 收编本轮回复为新 reply variant；空产出回滚。正常群聊 mode="" 时只保存
            if mode:
                versions.commit_run(thread, anchor, mode_head, mode, prev)
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