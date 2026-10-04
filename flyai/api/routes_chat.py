"""聊天 SSE 主通道（断线可续：生成在后台任务中进行，事件缓存于 ChatRun）。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import random
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.loop import DEFAULT_SYSTEM_PROMPT, run_agent_loop
from ..agent.runs import ChatRun, ChatRunManager
from ..models_access import visible_image_models, visible_providers
from ..errors import ProviderError
from ..ids import InvalidName, check_safe_name
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
from ..sessions.models import bootstrap_room
from ..sse import sse_event

logger = logging.getLogger("flyai.chat")

router = APIRouter(prefix="/api", tags=["chat"])


def _safe_sid(sid: str) -> str:
    try:
        return check_safe_name(sid, "会话 ID")
    except InvalidName:
        raise HTTPException(400, "会话 ID 非法")


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
    # 定向发言（客户端解析输入框里的 @角色名）：被点名者按此顺序最先回复，
    # 其余座位跟后；仅 group 窗口生效
    target_member_ids: List[str] = []
    # 群聊发言顺序：true=未定向的座位每轮随机洗牌；false/空=按窗口成员顺序
    shuffle_speakers: bool = False


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


# 跳过语外层壳对（剥到稳定为止）
_SKIP_WRAPPERS = (
    ("「", "」"), ("『", "』"), ("（", "）"), ("(", ")"),
    ("[", "]"), ("【", "】"),
)
_SKIP_TAILS = ("。", ".", "~", "～", "!", "！", "?", "？")


def _normalize_skip_text(text: Optional[str]) -> str:
    """规范化跳过语：strip 后反复剥成对外层壳与句尾标点/波浪号。

    『（略）。』→『略』；只做整体相等比较，绝不用 startswith，
    避免把「略微想了想」这类正常发言误判为跳过。
    """
    s = (text or "").strip()
    changed = True
    while changed and s:
        changed = False
        for left, right in _SKIP_WRAPPERS:
            if len(s) >= 2 and s.startswith(left) and s.endswith(right):
                s = s[1:-1].strip()
                changed = True
                break
        if s and s[-1] in _SKIP_TAILS:
            s = s[:-1].strip()
            changed = True
    return s


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


def _get_run_manager(state) -> ChatRunManager:
    """取 AppState 的运行管理器；简易测试替身没有时用临时实例（不影响现有测试）。"""
    manager = getattr(state, "run_manager", None)
    return manager if manager is not None else ChatRunManager()


def _spawn_run(run: ChatRun, drive, run_manager: ChatRunManager) -> None:
    """把 drive() 协程包装为 run 的后台任务：异常/取消都收敛到 run 终态。

    任务退出（含取消）时一定释放会话级独占锁。
    """

    async def runner():
        try:
            status = await drive()
            await run.finish(status)
        except asyncio.CancelledError:
            try:
                await run.finish("cancelled")
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.exception("聊天运行任务异常")
            try:
                await run.emit(
                    "error",
                    {"message": f"{type(exc).__name__}: {exc}", "fatal": True},
                )
            except Exception:
                pass
            await run.finish("error")
        finally:
            run_manager.unlock_session(run.session_id, run)

    run.task = asyncio.create_task(runner())


def _sse_response(run: ChatRun) -> StreamingResponse:
    return StreamingResponse(
        run.stream(0),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Run-Id": run.id,
        },
    )


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)

    session = None
    if req.session_id:
        _safe_sid(req.session_id)
        session = sc.store.get(req.session_id)
        if session is None:
            from fastapi import HTTPException

            raise HTTPException(404, "会话不存在")
    else:
        session = sc.store.create()

    if not req.message.strip() and not req.images and not req.regenerate_message_id:
        raise HTTPException(400, "消息内容为空")

    # provider 延迟解析：多 AI 房间可能每个座位都指定了自己的 provider，
    # 此时即使没有房间默认提供商也应能发言

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
    elif not thread_id and not session.threads:
        # 旧形态会话立即升级再入线程路径：否则后台 drive 保存旧形态，
        # 会覆盖刷新时 store.get() 做的迁移，线程 id 每次加载都随机变化
        thread = bootstrap_room(session)
        sc.store.save(session)
        thread_id = thread.id
    if thread_id:
        req.thread_id = thread_id

    # 会话级互斥快速预检（权威 check-and-set 在各路径落消息前完成）：
    # 同一房间任意窗口有运行中的回答 → 拒绝重复发起，客户端应走重连
    run_manager = _get_run_manager(state)
    if run_manager.active_in_session(sc.uid, session.id) is not None:
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    if thread_id:
        return await _chat_in_thread(state, sc, session, req, None, run_manager)

    # ---- 旧单 AI 会话路径 ----
    # 请求级 provider 在此解析（房间路径的座位级解析在 _chat_in_thread 内）
    provider = _resolve_visible_provider(state, sc, req.provider_id)
    # 加载角色/世界观（优先会话副本）→ 构建系统提示词与查询/属性服务
    character = effective_character(session, sc.lore)
    world = effective_world(session, sc.lore)
    user_char = member_character(user_seat(session), sc.lore)
    system_prompt = build_system_prompt(
        character, world, DEFAULT_SYSTEM_PROMPT, roleplay=state.config.roleplay,
        user_character=user_char,
    )
    services = {}
    # TTS：角色卡绑定音色 + 当前用户等级（工具内解析/鉴权）
    services["tts_voice"] = getattr(character, "voice", "") or ""
    services["user_level"] = getattr(sc, "level", 0)
    if character or world:
        services["lore"] = _get_querier(sc, character, world)
    # 图生图自动取参考图：让工具扫历史
    services["history"] = session.messages
    # web_fetch 分页缓存
    services["page_cache"] = getattr(state, "page_cache", None)
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
    # 浏览器搜索引擎（engine=chromium 的 web_search 用；引擎本身懒启动）
    _get_sb = getattr(state, "get_search_browser", None)
    services["chromium_search"] = _get_sb() if _get_sb else None
    # 角色卡自带属性时即启用（不再受全局开关限制）；
    # 旧卡回退全局 list 的逻辑在 resolve_attribute_defs 内已含开关判断
    attr_defs = resolve_attribute_defs(character, state.config.roleplay)
    if attr_defs:
        services["attributes"] = AttributeManager(session, attr_defs)

    # 权威 check-and-set：独占会话后再落用户消息；抢锁失败则未做任何变更
    run = run_manager.create(sc.uid, session.id)
    if not run_manager.try_lock_for(sc.uid, session.id, run):
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    try:
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
    except Exception:
        run_manager.unlock_session(session.id, run)
        raise

    async def drive():
        try:
            await run.emit(
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
                web_max_iterations=getattr(
                    getattr(state.config, "web_search", None), "max_iterations", None
                ),
            ):
                if ev == "done":
                    session.add_usage(data.get("usage"))
                await run.emit(ev, data)
            sc.store.save(session)
            return "done"
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            await run.emit("error", {"message": str(exc), "fatal": True})
            sc.store.save(session)
            return "error"
        except asyncio.CancelledError:
            # 用户主动停止：已生成部分照样落盘
            sc.store.save(session)
            raise
        except Exception as exc:
            logger.exception("聊天流异常")
            await run.emit(
                "error",
                {"message": f"{type(exc).__name__}: {exc}", "fatal": True},
            )
            sc.store.save(session)
            return "error"

    _spawn_run(run, drive, run_manager)
    return _sse_response(run)


async def _chat_in_thread(state, sc, session, req: ChatRequest, provider=None,
                          run_manager: Optional[ChatRunManager] = None):
    """beta：把用户消息投进指定窗口，并按窗口内 AI 座位逐一响应（fan-out）。

    - dm 窗口：单 AI，等价旧单 AI 会话，但历史/角色取该窗口。
    - group 窗口：同一共享历史下按 member_ids 顺序逐个 AI 各生成一段，
      后一个 AI 的系统提示词能看到前面 AI 的发言（互为上下文）。
    """
    from fastapi import HTTPException

    if run_manager is None:
        run_manager = _get_run_manager(state)

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

    # 发言顺序（仅 group；dm 恒为单座位，不受影响）：
    # 被 @ 的座位按 @ 出现顺序优先（去重），其余座位跟后；
    # 无 @ 且勾选随机时，所有座位洗牌
    target_ids = getattr(req, "target_member_ids", None) or []
    if thread.kind == "group" and target_ids:
        by_id = {m.id: m for m in speakers}
        directed = []
        seen = set()
        for mid in target_ids:
            if mid in by_id and mid not in seen:
                directed.append(by_id[mid])
                seen.add(mid)
        rest = [m for m in speakers if m.id not in seen]
        if getattr(req, "shuffle_speakers", False):
            random.shuffle(rest)
        speakers = directed + rest
    elif thread.kind == "group" and getattr(req, "shuffle_speakers", False):
        random.shuffle(speakers)

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
    # 规范化后的跳过语集合（比较前先 _normalize_skip_text）
    skip_marker = {"略", "跳过", "skip"}

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

    # 座位 provider 预检：不可见/不存在在这里直接 4xx 返回；
    # 否则错误会埋成后台 SSE error，发起请求只拿到 200
    for pre in speakers:
        if (pre.provider_id or "").strip():
            _resolve_visible_provider(state, sc, pre.provider_id)

    # 权威 check-and-set：独占会话后再落用户消息；抢锁失败时未做任何变更
    run = run_manager.create(sc.uid, session.id, thread.id)
    if not run_manager.try_lock_for(sc.uid, session.id, run):
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    # 房间默认 provider 延迟解析（参数已给则直接用）：
    # 所有座位都有专属 provider 时可无默认
    try:
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
    except Exception:
        run_manager.unlock_session(session.id, run)
        raise

    async def drive():
        try:
            await run.emit(
                "message_start",
                {"session_id": session.id, "thread_id": thread.id,
                 "character_id": "", "world_id": ""},
            )
            # 编辑/重生成：流式起点（此刻 history 末尾即锚点用户消息）
            mode_head = len(history) if mode else 0
            # 房间默认 provider 的懒解析副本（勿在 drive 内直接给外层 provider
            # 赋值——那会让它在 drive 作用域整个变成未绑定局部变量）
            default_provider = provider
            for spk in speakers:
                char = member_character(spk, sc.lore)
                # 座位级 provider：座位指定了就用它（权限按本用户校验），
                # 否则懒解析并复用房间默认 provider
                if (spk.provider_id or "").strip():
                    spk_provider = _resolve_visible_provider(
                        state, sc, spk.provider_id
                    )
                else:
                    if default_provider is None:
                        default_provider = _resolve_visible_provider(
                            state, sc, req.provider_id
                        )
                    spk_provider = default_provider
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
                # TTS：该座位角色卡绑定音色 + 当前用户等级
                services["tts_voice"] = getattr(char, "voice", "") or ""
                services["user_level"] = getattr(sc, "level", 0)
                if char or world:
                    services["lore"] = _get_querier(sc, char, world)
                # 图生图自动取参考图：让工具能扫该窗口历史
                services["history"] = history
                # web_fetch 分页缓存
                services["page_cache"] = getattr(state, "page_cache", None)
                # 当前座位提供商是否支持视觉（搜图工具据此决定筛选指引文案）
                services["vision"] = bool(
                    getattr(getattr(spk_provider, "cfg", None), "vision", False)
                )
                # 记忆工具：让该 AI 能检索其它对话、并更新本会话记忆
                services["store"] = sc.store
                services["session"] = session
                services["member"] = spk
                # 记忆向量增量缓存 + 角色级全局记忆簿（batch 3/4）
                services["memory_vec_cache"] = getattr(state, "memory_vec_cache", None)
                services["char_memory"] = getattr(sc, "char_memory", None)
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
                services["llm_provider"] = spk_provider
                # RAG 语义检索：配了 embeddings 提供商才注入，否则工具回退关键词检索
                _get_emb = getattr(state, "get_embedder", None)
                services["embedder"] = _get_emb() if _get_emb else None
                # AI 浏览器（启用时）：会话键按 用户:聊天会话 隔离
                if getattr(state, "browser", None) is not None:
                    services["browser_manager"] = state.browser
                    services["browser_key"] = "%s:%s" % (sc.uid, session.id)
                # 浏览器搜索引擎（engine=chromium 的 web_search 用；懒启动）
                _get_sb = getattr(state, "get_search_browser", None)
                services["chromium_search"] = _get_sb() if _get_sb else None
                # 属性跟踪：该 AI 角色卡定义了属性即启用，值落在 member.attributes
                member_attr_defs = resolve_attribute_defs(char, state.config.roleplay)
                if member_attr_defs:
                    services["attributes"] = AttributeManager(spk, member_attr_defs)
                # 开始该 AI 的发言：标记当前发言者，必为首帧
                await run.emit(
                    "speaker",
                    {"member_id": spk.id, "name": spk.name,
                     "character_id": spk.character_id or ""},
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
                        provider=spk_provider,
                        registry=state.registry,
                        settings=state.config.tools,
                        system_prompt=sys_prompt + retry_nudge,
                        session_id=session.id,
                        services=services,
                        web_max_iterations=getattr(
                            getattr(state.config, "web_search", None),
                            "max_iterations", None,
                        ),
                        actor=spk.id,
                    ):
                        if ev == "done":
                            done_data = data
                        else:
                            await run.emit(ev, data)
                    # 给本轮追加的 assistant 回复打上发言者标记（含中间的工具步）。
                    for m in history[before:]:
                        if m.role == "assistant":
                            m.name = spk.name
                    ender = history[before:][-1] if history[before:] else None
                    ender_txt = (
                        (ender.content or "").strip()
                        if (ender and ender.role == "assistant")
                        else None
                    )
                    # 显式跳过关字（规范化后整体等于「略/跳过/skip」）→ 真正跳过，不重试
                    if (
                        allow_skip
                        and ender is not None
                        and ender.role == "assistant"
                        and _normalize_skip_text(ender_txt) in skip_marker
                    ):
                        skipped = True
                        break
                    # 空回复（没写出任何字）→ 提醒一次后重试，最多尝试 3 次
                    # 达到工具轮数上限时的空结尾不重试（再跑一遍只会再次撞上限）
                    if (
                        allow_skip
                        and ender is not None
                        and ender.role == "assistant"
                        and not ender_txt
                        and (done_data or {}).get("reason")
                        not in ("max_iterations", "max_web_iterations")
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
                # 每个 AI 之后发一个 done（terminal=False，前端只记录用量不收尾）；
                # 最后一位发言者的 done 才结束整个运行
                fan_done = dict(done_data or {"reason": "stop", "iterations": attempts})
                fan_done["terminal"] = spk is speakers[-1]
                await run.emit("done", fan_done)
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
                    await run.emit(
                        "speaker_skip",
                        {"member_id": spk.id, "name": spk.name},
                    )
            sc.store.save(session)
            return "done"
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            await run.emit("error", {"message": str(exc), "fatal": True})
            return "error"
        except asyncio.CancelledError:
            raise  # finally 仍执行：edit/regen 回滚或保留 + 保存
        except Exception as exc:
            logger.exception("群聊流异常")
            await run.emit(
                "error",
                {"message": f"{type(exc).__name__}: {exc}", "fatal": True},
            )
            return "error"
        finally:
            # 收编本轮回复为新 reply variant；空产出回滚。正常群聊 mode="" 时只保存
            if mode:
                versions.commit_run(thread, anchor, mode_head, mode, prev)
            sc.store.save(session)

    _spawn_run(run, drive, run_manager)
    return _sse_response(run)


# ---- 记忆存档：让房间里所有 AI 各自回顾并写进跨房间角色记忆簿 ----

class MemorySnapshotRequest(BaseModel):
    # 房间默认 provider（座位各自指定时无需）
    provider_id: Optional[str] = None


_SNAPSHOT_WINDOW_CHARS = 4000   # 单个窗口回顾最多取的字符数
_SNAPSHOT_TOTAL_CHARS = 9000    # 每个 AI 回顾总长上限

_SNAPSHOT_DIRECTIVE = (
    "\n\n【现在进行记忆存档】上面给出的是你在本房间各个窗口里经历的对话记录。"
    "请以你自己的身份回顾，把仍值得长期记住的内容——重要人物与关系、约定与时间地点、"
    "已经发生的事件、各人当前的状态、对方明确交代过的信息——用 update_chat_memory 逐条记下。"
    "可以在同一轮里发起多个 update_chat_memory 调用，每条只写一个简洁、客观的事实；"
    "只写确实新的、长期有用的内容，不要逐句抄写、不要记客套话，也不要写『以后』的猜测。"
    "记完后用一句很短的话确认（如：记下了。）。"
)


def _snapshot_transcript(session, member) -> str:
    """组装该 AI 在本房间各窗口（仅限它参与的窗口）的回顾文本，
    每个窗口取最新若干字、总长封顶；无内容返回空串。
    """
    from ..tools.memory_tool import QueryChatMemoryTool

    char_id = (getattr(member, "character_id", "") or "").strip()
    sets = QueryChatMemoryTool._candidate_sets(session, char_id)
    chunks: List[str] = []
    used = 0
    for _tid, label, msgs in sets:
        body = "\n".join(f"{w}: {txt}" for w, txt, _i in msgs)
        if len(body) > _SNAPSHOT_WINDOW_CHARS:
            body = "……\n" + body[-_SNAPSHOT_WINDOW_CHARS:]
        block = f"【窗口·{label}】\n{body}"
        if used + len(block) > _SNAPSHOT_TOTAL_CHARS:
            block = block[:max(0, _SNAPSHOT_TOTAL_CHARS - used)]
        if block.strip():
            chunks.append(block)
            used += len(block)
        if used >= _SNAPSHOT_TOTAL_CHARS:
            break
    return "\n\n".join(chunks)


@router.post("/rooms/{rid}/memory-snapshot")
async def memory_snapshot(rid: str, request: Request,
                          body: Optional[MemorySnapshotRequest] = None):
    """让该房间的每个 AI 各自回顾本房间对话，并把重要内容写进自己的
    跨房间角色记忆簿（无论该座位是否挂载了记忆簿——这是用户显式的存档动作）。
    同一角色在其他房间默认只读可见这些记忆。
    """
    state = request.app.state.flyai
    sc = state.user(request)
    _safe_sid(rid)
    session = sc.store.get(rid)
    if session is None:
        raise HTTPException(404, "房间不存在")

    run_manager = _get_run_manager(state)
    if run_manager.active_in_session(sc.uid, session.id) is not None:
        raise HTTPException(409, "回答正在进行中，请等待结束后再记录")

    speakers = [m for m in session.members if not m.is_user]
    if not speakers:
        raise HTTPException(400, "房间里没有 AI 参与者")

    req = body or MemorySnapshotRequest()
    world = effective_world(session, sc.lore)
    user_char = member_character(user_seat(session), sc.lore)
    user_label = user_char.name if user_char else "你"
    audience_names = [spk.name for spk in speakers] + [user_label]

    # 座位 provider 预检：不可见直接 4xx，避免错误埋进后台
    for pre in speakers:
        if (pre.provider_id or "").strip():
            _resolve_visible_provider(state, sc, pre.provider_id)

    run = run_manager.create(sc.uid, session.id, "")
    if not run_manager.try_lock_for(sc.uid, session.id, run):
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    async def drive():
        await run.emit(
            "message_start",
            {"session_id": session.id, "thread_id": "",
             "character_id": "", "world_id": ""},
        )
        default_provider = None
        for spk in speakers:
            char = member_character(spk, sc.lore)
            if (spk.provider_id or "").strip():
                spk_provider = _resolve_visible_provider(
                    state, sc, spk.provider_id)
            else:
                if default_provider is None:
                    default_provider = _resolve_visible_provider(
                        state, sc, req.provider_id)
                spk_provider = default_provider

            await run.emit(
                "speaker",
                {"member_id": spk.id, "name": spk.name,
                 "character_id": spk.character_id or ""},
            )
            transcript = _snapshot_transcript(session, spk)
            if not transcript:
                # 该 AI 在本房间没有任何可回顾的内容
                await run.emit(
                    "speaker_skip",
                    {"member_id": spk.id, "name": spk.name},
                )
                await run.emit(
                    "done",
                    {"reason": "stop", "iterations": 1,
                     "terminal": spk is speakers[-1]},
                )
                continue

            sys_prompt = build_member_system_prompt(
                name=spk.name,
                memory=spk.memory,
                attributes=spk.attributes,
                audience=[n for n in audience_names if n != spk.name],
                character=char,
                world=world,
                base=DEFAULT_SYSTEM_PROMPT,
                roleplay=state.config.roleplay,
                user_character=user_char,
            ) + _SNAPSHOT_DIRECTIVE

            services = {}
            if char or world:
                services["lore"] = _get_querier(sc, char, world)
            services["store"] = sc.store
            services["session"] = session
            services["member"] = spk
            services["memory_vec_cache"] = getattr(state, "memory_vec_cache", None)
            services["char_memory"] = getattr(sc, "char_memory", None)
            # 存档动作：即使座位未挂载/只读，也强制写进角色记忆簿
            services["book_force_write"] = True
            services["llm_provider"] = spk_provider
            _get_emb = getattr(state, "get_embedder", None)
            services["embedder"] = _get_emb() if _get_emb else None

            snap_history = [Message(role="user", content=transcript)]
            done_data = None
            async for (ev, data) in run_agent_loop(
                history=snap_history,
                provider=spk_provider,
                registry=state.registry,
                settings=state.config.tools,
                system_prompt=sys_prompt,
                session_id=session.id,
                services=services,
                actor=spk.id,
            ):
                if ev == "done":
                    done_data = data
                else:
                    await run.emit(ev, data)
            if done_data:
                session.add_usage(done_data.get("usage"))
            fan_done = dict(done_data or {"reason": "stop", "iterations": 1})
            fan_done["terminal"] = spk is speakers[-1]
            await run.emit("done", fan_done)
        sc.store.save(session)
        return "done"

    _spawn_run(run, drive, run_manager)
    return _sse_response(run)


# ---- 断线重连端点 ----

def _get_owned_run(request: Request, run_id: str) -> ChatRun:
    state = request.app.state.flyai
    sc = state.user(request)
    run = _get_run_manager(state).get(run_id, sc.uid)
    if run is None:
        raise HTTPException(404, "运行不存在或已过期")
    return run


@router.get("/chat/runs/{run_id}")
async def chat_run_status(run_id: str, request: Request):
    """查询运行状态（刷新后先探活，再决定续读还是丢弃标记）。"""
    run = _get_owned_run(request, run_id)
    return {
        "id": run.id,
        "status": run.status,
        "session_id": run.session_id,
        "thread_id": run.thread_id,
    }


@router.get("/chat/runs/{run_id}/events")
async def chat_run_events(run_id: str, request: Request, after: int = 0):
    """续读事件：先补发序号 after 之后的缓存事件，再实时跟随至终态。"""
    run = _get_owned_run(request, run_id)
    return StreamingResponse(
        run.stream(after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "X-Run-Id": run.id},
    )


@router.post("/chat/runs/{run_id}/cancel")
async def cancel_chat_run(run_id: str, request: Request):
    """主动停止：取消后台生成任务（已生成部分仍会保存）。"""
    run = _get_owned_run(request, run_id)
    if run.status == "running" and run.task is not None:
        run.task.cancel()
    return {"cancelled": True}
