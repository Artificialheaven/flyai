"""聊天 SSE 主通道（断线可续：生成在后台任务中进行，事件缓存于 ChatRun）。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..agent.loop import (
    DEFAULT_SYSTEM_PROMPT,
    _IDLE_FINISH_TEXT,
    run_agent_loop,
)
from ..agent.loopmode import (
    CONTEXT_WARN_THRESHOLDS,
    LoopContext,
    history_char_size,
)
from ..agent.runs import ChatRun, ChatRunManager
from ..agentdb.loop_store import (
    LOOP_DONE,
    LOOP_ERROR,
    LOOP_IDLE,
    LOOP_INTERRUPTED,
    LOOP_STOPPED,
)
from ..proactor.integrate import (
    extract_self_after_turn,
    handle_post_turn_mood,
    transcript_from_history,
)
from ..proactor.live_ctx import had_pending_announce, render_for_turn
from ..providers.base import StreamEventType
from ..providers.complete import complete
from ..tools.loop_tools import LoopFinishTool
from .. import billing, feature_prefs, metering
from ..models_access import visible_image_models, visible_providers
from ..errors import ProviderError
from ..ids import InvalidName, check_safe_name
from ..lore.attributes import AttributeManager, resolve_attribute_defs
from ..lore.query import LoreQuerier, build_member_system_prompt, build_system_prompt
from ..sessions.resolve import (
    effective_character,
    effective_world,
    member_character,
    opening_character,
    thread_messages,
    user_seat,
)
from ..schemas import ImageAttachment, Message
from ..sessions import versions
from ..sessions.models import bootstrap_room
from ..sse import sse_event
from ..user import ADMIN_UID

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


def _custom_extras(state, sc, session_id: str) -> list:
    """P1：按调用者自己的 agent.db 构建请求级自定义工具。

    工具不进全局注册表，杜绝用户 A 的自定义工具泄漏进用户 B 的模型上下文；
    未启用/读取失败时返回空列表，绝不阻断正常聊天。
    """
    runtime = getattr(state, "custom_runtime", None)
    if runtime is None or not getattr(getattr(runtime, "settings", None),
                                      "enabled", False):
        return []
    try:
        return runtime.tools_for(sc.uid, session_id)
    except Exception:
        logger.exception("自定义工具加载失败，本轮跳过")
        return []


def _mcp_extras(state) -> list:
    """P4：从 admin 的 MCP 工具缓存生成请求级 mcp__ 桥接工具。

    桥接工具对全体登录用户可见（risk 门禁在注册表层执行），连接本身懒建立，
    因此这里只读缓存不拉起子进程；任何异常都降级为空列表。
    """
    manager = getattr(state, "mcp_manager", None)
    if manager is None or not getattr(
        getattr(manager, "settings", None), "enabled", False
    ):
        return []
    try:
        return manager.bridge_tools()
    except Exception:
        logger.exception("MCP 桥接工具加载失败，本轮跳过")
        return []


# ---- P2 长跑：服务钩子构建 / 压缩摘要 / 收尾 / 免审批启动 ----

_COMPACT_DIRECTIVE = (
    "你是长跑任务的上下文压缩器。把下面的工作历史压写成简短客观的工作纪要："
    "已完成的动作、关键结论、待办事项与约束，保留数字、文件名、ID 等硬信息，"
    "不要寒暄、不要臆造未发生的事。"
)


async def _summarize_for_compact(provider, head_text: str,
                                 directive: Optional[str] = None,
                                 session_id: str = "") -> str:
    """用当前提供商做一次无工具便宜调用归纳头部历史；失败不阻断长跑。"""
    msgs = [
        Message(role="system", content=directive or _COMPACT_DIRECTIVE),
        Message(role="user", content=head_text or "（无历史）"),
    ]
    parts: List[str] = []
    try:
        async for ev in provider.stream_chat(
            msgs, [], deep_thinking=False, max_tokens=800, extra_params=None,
            session_id=session_id,
        ):
            if ev.type == StreamEventType.CONTENT_DELTA:
                parts.append(ev.text)
    except Exception:
        logger.exception("长跑压缩摘要失败，退回仅保留尾部工作轮")
        return "（摘要生成失败，已保留最近工作轮次，早期上下文被截断）"
    note = "".join(parts).strip()
    return note or "（摘要为空，已保留最近工作轮次）"


def _install_proactor_services(services: dict, state, sc, member=None) -> None:
    """P3：把自我记忆/承诺存储按开关注入工具服务；缺失或关闭即不注入。"""
    cfg = getattr(state, "config", None)
    sm_store = getattr(state, "self_memory_store", None)
    if sm_store is not None and feature_prefs.self_memory_active(
            cfg, getattr(sc, "user_settings", None)):
        services["self_memory_store"] = sm_store
    c_store = getattr(state, "commitment_store", None)
    if c_store is not None and getattr(getattr(cfg, "proactor", None),
                                       "enabled", False):
        services["commitment_store"] = c_store
        services["tzname"] = getattr(
            getattr(cfg, "scheduler", None), "timezone", "Asia/Shanghai"
        )
        if member is not None:
            services["member"] = member


async def _touch_proactor(state, sc, session_id: str) -> None:
    """真人消息到达：刷新 opt-in 会话的活跃时间（未开启时存储内空转）。"""
    store = getattr(state, "proactor_store", None)
    if store is None or not getattr(getattr(state.config, "proactor", None),
                                    "enabled", False):
        return
    try:
        await asyncio.to_thread(store.touch_human, sc.uid, session_id)
    except Exception:
        logger.exception("主动行为活跃时间刷新失败")


def _loop_extras(custom_extras: list, loop_ctx: Optional[LoopContext]) -> list:
    """长跑请求额外注入 loop_finish（全局表不可见，只在 loop_ctx 存在时出现）。"""
    extras = list(custom_extras or [])
    if loop_ctx is not None:
        extras.append(LoopFinishTool())
    return extras


def _install_loop_services(services: dict, state, sc, loop_ctx: Optional[LoopContext],
                           provider, *, loop_starter=None,
                           session_id: str = "") -> None:
    """把长跑工具服务与引擎钩子注入 services（同一 dict 也给 ToolContext）。

    功能开启即注入 loop_manager/settings/starter（普通对话里 AI 也能提交申请）；
    四个引擎钩子只在 loop_ctx 存在时注入，缺失时引擎保护自动降级。
    """
    cfg = getattr(state.config, "loop", None)
    mgr = getattr(state, "loop_manager", None)
    if cfg is None or not getattr(cfg, "enabled", False) or mgr is None:
        return
    services["loop_settings"] = cfg
    services["loop_manager"] = mgr
    if loop_starter is not None:
        services["loop_starter"] = loop_starter
    if loop_ctx is None:
        return

    async def _state_hook(ctx: LoopContext) -> None:
        await asyncio.to_thread(mgr.refresh, sc.uid, ctx)

    async def _compact_hook(head_text: str) -> str:
        registry = getattr(state, "prompt_registry", None)
        directive = (
            registry.render("compact") if registry is not None
            else _COMPACT_DIRECTIVE
        )
        return await _summarize_for_compact(
            provider, head_text, directive, session_id=session_id
        )

    services["loop_state"] = _state_hook
    services["loop_compact"] = _compact_hook
    services["interrupt_hub"] = getattr(state, "interrupt_hub", None)

    def _continue_wrap(notice: str) -> str:
        registry = getattr(state, "prompt_registry", None)
        if registry is None:
            return notice
        return registry.render("loop_continue", notice=notice)

    services["loop_continue_wrap"] = _continue_wrap
    if loop_starter is not None:
        services["loop_starter"] = loop_starter


def _last_assistant_text(history) -> str:
    for m in reversed(history):
        if getattr(m, "role", "") == "assistant" and (m.content or "").strip():
            return (m.content or "").strip()
    return ""


async def _finalize_loop(state, sc, session_id: str, loop_ctx: Optional[LoopContext],
                         history, *, failed: bool = False) -> None:
    """drive 收尾：把终态/结果写回 loop_sessions，并清空打断队列。"""
    if loop_ctx is None:
        return
    mgr = getattr(state, "loop_manager", None)
    if mgr is None:
        return
    try:
        if failed:
            status = LOOP_ERROR
        elif loop_ctx.finished:
            status = LOOP_DONE
        elif loop_ctx.stop_status:
            status = loop_ctx.stop_status  # stopped / interrupted（DB 权威状态）
        elif loop_ctx.idle_turns > 0:
            status = LOOP_IDLE
        else:
            # 引擎自然结束但未显式 loop_finish：按完成兜底
            status = LOOP_DONE
        result = loop_ctx.finish_text or _last_assistant_text(history)
        if status == LOOP_IDLE and not result:
            result = _IDLE_FINISH_TEXT
        await asyncio.to_thread(
            mgr.finish, sc.uid, loop_ctx, status, result=result[:20000]
        )
    except Exception:
        logger.exception("长跑收尾写库失败 loop_id=%s",
                         getattr(loop_ctx, "loop_db_id", "?"))
    hub = getattr(state, "interrupt_hub", None)
    if hub is not None:
        hub.clear(session_id)


def _make_loop_starter(state, sc, run_manager: ChatRunManager):
    """loop_start 工具用的启动器：等当前 run 释放会话锁后免审批再起长跑。"""

    async def _start(uid: str, session_id: str, task: str,
                     reason: str = "") -> dict:
        if uid != sc.uid:
            return {"ok": False, "error": "只能为自己启动长跑"}
        # 工具运行在当前 ChatRun 内：轮询等待会话锁释放，最多 60s
        for _ in range(300):
            if run_manager.session_locked(session_id) is None:
                break
            await asyncio.sleep(0.2)
        else:
            return {"ok": False, "error": "会话锁等待超时"}
        req = ChatRequest(
            session_id=session_id, message=task,
            loop_task=task, provider_id=None,
        )
        resp = await submit_chat(state, sc, req, run_manager)
        mgr = getattr(state, "loop_manager", None)
        row = await asyncio.to_thread(mgr.running_for, uid, session_id)
        return {
            "ok": True,
            "loop_id": row["id"] if row else None,
            "run_id": resp.headers.get("X-Run-Id"),
        }

    return _start


def _st_regex_enabled(sc) -> bool:
    """ST 正则的 prompt 侧开关（用户设置缺省开；卡内无脚本时天然无操作）。"""
    us = getattr(sc, "user_settings", None)
    value = getattr(us, "st_regex_prompt_enabled", None)
    return True if value is None else bool(value)


def _st_compile_scripts(sc, raw_scripts):
    """编译某张卡的 prompt 侧脚本；关闭开关或无脚本时返回空表。

    无效正则由引擎内部跳过并计数，绝不抛给请求层。
    """
    if not _st_regex_enabled(sc) or not raw_scripts:
        return []
    from ..st.regex_scripts import compile_scripts

    scripts, _warnings = compile_scripts(raw_scripts, side="prompt")
    return scripts


def _st_regex_map(sc, character_ids) -> Dict[str, list]:
    """角色 id → prompt 侧脚本表（群聊按消息所属卡查表）。"""
    if not _st_regex_enabled(sc):
        return {}
    lore = getattr(sc, "lore", None)
    out: Dict[str, list] = {}
    seen = set()
    for raw_id in character_ids or []:
        cid = (raw_id or "").strip() if isinstance(raw_id, str) else ""
        if not cid or cid in seen:
            continue
        seen.add(cid)
        char = None
        if lore is not None:
            try:
                char = lore.get_character(cid)
            except Exception:
                char = None
        scripts = _st_compile_scripts(
            sc, getattr(char, "regex_scripts", None) if char is not None else None
        )
        if scripts:
            out[cid] = scripts
    return out


def _render_card_text(text, character, user_char, group_names=None,
                      variables=None) -> str:
    """角色卡文本（PHI 等）统一宏渲染：与系统提示词使用同一组名字/变量。"""
    from ..tavern import render_tavern_macros

    return render_tavern_macros(
        text or "",
        char_name=getattr(character, "name", "") or "",
        user_name=(getattr(user_char, "name", "") if user_char else ""),
        group_names=group_names or [],
        variables=variables,
    )


def _lore_depth_specs(querier, character, user_char, last_message,
                      group_names=None, variables=None) -> List[dict]:
    """世界书/角色表的 @depth 常驻条目 + 卡级 depth_prompt：宏替换后返回注入规格。

    depth_prompt 来自酒馆卡 extensions（{"prompt", "depth", "role"}），即使没有
    世界书/角色表条目也要注入，因此这里不再提前 return。
    """
    from ..tavern import render_tavern_macros

    char_name = getattr(character, "name", "") or "角色"
    user_name = getattr(user_char, "name", "") if user_char else ""

    def _render(text: str) -> str:
        return render_tavern_macros(
            text or "",
            char_name=char_name, user_name=user_name or "用户",
            group_names=group_names or [], last_message=last_message or "",
            variables=variables,
        )

    specs: List[dict] = []
    if querier is not None:
        try:
            specs = querier.depth_block_specs()
        except Exception:
            logger.exception("深度常驻条目收集失败")
            specs = []
    out = [dict(s, content=_render(s.get("content") or "")) for s in specs]

    ext = getattr(character, "extensions", None)
    dp = ext.get("depth_prompt") if isinstance(ext, dict) else None
    if isinstance(dp, dict):
        prompt = str(dp.get("prompt") or "").strip()
        if prompt:
            try:
                depth = max(0, int(dp.get("depth") or 4))
            except (TypeError, ValueError):
                depth = 4
            role = dp.get("role")
            if role not in ("user", "assistant", "system"):
                role = {0: "system", 1: "user", 2: "assistant"}.get(role, "system")
            out.append({
                "title": "depth_prompt",
                "content": _render(prompt),
                "role": role,
                "depth": depth,
                "order": 100,
            })
    return out


# ---- P3：发消息前自动召回（keep_turns 的配套）----

_AUTO_RECALL_BUDGET = 1200   # 注入文本合计字符上限


def _keep_start(messages, keep_turns) -> int:
    """与 loop._keep_recent_turns 相同的保留窗起点（只算 user 边界）。"""
    positions = [i for i, m in enumerate(messages) if m.role == "user"]
    if not keep_turns or keep_turns <= 0 or len(positions) <= keep_turns:
        return 0
    return positions[-keep_turns]


def _belongs_to_speaker(m, member, char_id) -> bool:
    """该历史消息是否属于本轮发言角色（自动回忆只收本角色的旧对话）。

    用户消息恒算——用户是同一个人，且当前窗口必有该角色在场。
    AI 消息优先按消息级角色卡 id 归属（换卡后旧消息仍归旧卡）；
    旧数据无 character_id 时回落座位 actor_id，再回落持久化名字。
    member=None（旧单聊兼容路径）且无 character_id 时保持旧行为全收。
    """
    if m.role == "user":
        return True
    cid = (getattr(m, "character_id", "") or "").strip()
    if cid:
        return bool(char_id) and cid == char_id
    if member is not None:
        actor = (getattr(m, "actor_id", "") or "").strip()
        if actor:
            return actor == member.id
        name = (m.name or "").strip()
        if name and (member.name or "").strip():
            return name == member.name.strip()
    return True


async def build_auto_recall_block(*, state, sc, char_id, history, member,
                                  query, cache_namespace) -> Tuple[str, list]:
    """按用户设置自动召回与本轮相关的旧事实，返回 (注入文本, 条目清单)。

    无召回时返回 ("", [])。条目清单元素为 {"source": 来源前缀, "text": 文本}，
    供前端「已召回 N 段记忆」折叠组展示。
    来源只限当前窗口（history）中**属于该角色**的旧消息
    （用户消息 + 本座位/AI 本卡的发言；其它角色的台词不进入候选池）；
    其它聊天窗口的对话与跨会话记忆簿均不参与自动回忆；
    与 keep_turns 保留窗去重（窗内已有的消息不重复注入）；
    上限 auto_recall_max 条、合计 _AUTO_RECALL_BUDGET 字。
    """
    settings = getattr(sc, "user_settings", None)
    if settings is None or not getattr(settings, "auto_recall", False):
        return "", []
    query = (query or "").strip()
    if not query:
        return "", []

    from ..memory.recall import Candidate, hybrid_rank
    from ..tools.memory_cache import item_identity

    keep_turns = getattr(settings, "keep_turns", 0) or 0
    start = _keep_start(history, keep_turns)
    window_text = " ".join(
        (m.content or "") for m in history[start:]
    )

    cands = []
    p = 0
    for idx, m in enumerate(history):
        if m.role not in ("user", "assistant") or m.hidden:
            continue
        if not _belongs_to_speaker(m, member, char_id):
            continue
        text = (m.content or "").strip()
        if not text:
            continue
        who = m.name or ("你" if m.role == "user" else "AI")
        ident = item_identity(cache_namespace, idx, text)
        cands.append(Candidate(text=text, kind="message", position=p,
                               label=cache_namespace, who=who,
                               identity=ident, obj=idx))
        p += 1
    if not cands:
        return "", []

    get_emb = getattr(state, "get_embedder", None)
    embedder = get_emb() if get_emb else None
    try:
        ranked = await hybrid_rank(
            cands, query,
            embedder=embedder,
            vec_cache=getattr(state, "memory_vec_cache", None),
            cache_key=("auto", sc.uid, char_id, cache_namespace),
        )
    except Exception:
        logger.exception("自动召回排序失败")
        return "", []

    lines = []
    items = []
    used = 0
    for cand, _score in ranked:
        if cand.obj >= start:
            continue  # 保留窗内，模型本轮已经看得到
        if cand.text[:24] and cand.text[:24] in window_text:
            continue  # 与保留窗内容重复
        prefix = "旧对话·" + cand.who
        line = "- （{0}）{1}".format(prefix, cand.text)
        if used + len(line) > _AUTO_RECALL_BUDGET:
            continue
        lines.append(line)
        items.append({"source": prefix, "text": cand.text})
        used += len(line)
        if len(lines) >= int(getattr(settings, "auto_recall_max", 5) or 5):
            break
    if not lines:
        return "", []

    log = getattr(state, "recall_logs", None)
    if log is not None:
        try:
            log.add(kind="auto", topic=query, hits=len(lines))
        except Exception:
            pass
    block = (
        "【自动回忆】以下是与本轮相关、但不在最近对话中的旧事实，"
        "请在需要时自然沿用：\n" + "\n".join(lines)
    )
    return block, items


def build_ledger_block(sc, char_id, member) -> str:
    """每轮自动注入的剧情台账状态概览（未终结的任务/伏笔/物品）。

    与自动回忆共用 auto_recall 开关与挂载可见性：关了自动回忆的用户不注入；
    座位对簿不可见（未挂载/挂别人的簿）时不注入。与检索无关，每轮恒定概览。
    """
    settings = getattr(sc, "user_settings", None)
    if settings is None or not getattr(settings, "auto_recall", False):
        return ""
    from ..memory.ledger import render_ledger_reminder
    from ..tools.memory_tool import _book_read_mode, _memory_owner

    owner = _memory_owner(member) if member is not None else char_id
    mode = _book_read_mode(member) if member is not None else "ro"
    if mode not in ("rw", "ro") or not owner:
        return ""
    store = getattr(sc, "char_memory", None)
    if store is None:
        return ""
    try:
        return render_ledger_reminder(store.load_ledger(owner))
    except Exception:
        logger.exception("剧情台账注入失败")
        return ""


def _schedule_extraction(state, sc, char_id, marker_key, history, member,
                         provider) -> None:
    """P4：回答结束后非阻塞触发一次结构化批量抽取（按 extract_markers 增量）。

    marker_key：抽取进度键，必须对应一份独立历史——房间路径传窗口 id，
    旧单 AI 路径传会话 id。
    写记忆簿遵循挂载语义：旧单 AI 路径总是允许；房间路径要求该座位对簿
    可写（自有卡默认读写，挂别人的簿需显式 rw）。
    """
    settings = getattr(sc, "user_settings", None)
    every = int(getattr(settings, "extract_every_turns", 0) or 0)
    if every <= 0 or provider is None or not char_id or not marker_key:
        return
    if member is not None:
        from ..tools.memory_tool import _book_write_mode, _memory_owner

        if _book_write_mode(member) != "rw":
            return
        # 与 update_chat_memory 工具保持同一归属：显式挂载（memory_char_id）
        # 时抽取也必须写进挂载簿，不能写座位自己的簿
        char_id = _memory_owner(member)

    async def _job():
        try:
            from ..memory.extract import maybe_structured_extract

            report = await maybe_structured_extract(
                char_store=sc.char_memory,
                char_id=char_id,
                marker_key=marker_key,
                history=history,
                provider=provider,
                every=every,
            )
            if report is not None:
                logger.info("结构化抽取完成 %s: %s", marker_key, report)
                buf = getattr(state, "recall_logs", None)
                if buf is not None:
                    try:
                        ent = report.get("entries") or {}
                        led = report.get("ledger") or {}
                        buf.add(
                            kind="extract", topic=char_id,
                            added=int(ent.get("added", 0) or 0),
                            updated=int(ent.get("updated", 0) or 0),
                            ledger=int(led.get("added", 0) or 0
                                       + led.get("updated", 0) or 0),
                        )
                    except Exception:
                        pass
        except Exception:
            logger.exception("结构化抽取失败")

    try:
        asyncio.create_task(_job())
    except RuntimeError:
        pass


# ---- 轮后 LLM 收尾（心情判定 / 第一人称自我记忆）----
# 这些收尾在终态 done 帧之后才跑，且只写各自的状态库、不再改会话历史；
# 若继续 await，run 会一直保持 running 并占着会话单飞锁，客户端收到 done
# 后立刻发下一条就会被 409「该会话已有进行中的回答」拒掉。改为后台执行。
_POST_TURN_TASKS = set()


def _spawn_post_turn(label: str, factory) -> None:
    """后台执行轮后收尾；factory 返回协程（延迟创建，避免无事件循环时报错）。"""

    async def _job():
        try:
            await factory()
        except Exception:
            logger.exception("轮后收尾失败 %s", label)

    try:
        task = asyncio.create_task(_job())
    except RuntimeError:
        return
    # 保留强引用，防止任务在完成前被 GC
    _POST_TURN_TASKS.add(task)
    task.add_done_callback(_POST_TURN_TASKS.discard)


async def _post_turn_chat_hooks(state, sc, session_id, character_id,
                                provider, transcript, had_pending) -> None:
    await handle_post_turn_mood(
        state, sc, session_id=session_id,
        character_id=character_id,
        transcript=transcript, provider=provider,
        had_pending=had_pending,
    )
    await extract_self_after_turn(state, sc, transcript)


async def _post_turn_room_hooks(state, sc, jobs, transcript) -> None:
    # jobs: [(session_id, character_id, provider, turn_text, had_pending)]
    for session_id, character_id, provider, turn_text, had_pending in jobs:
        await handle_post_turn_mood(
            state, sc, session_id=session_id,
            character_id=character_id,
            transcript=turn_text, provider=provider,
            had_pending=had_pending,
        )
    await extract_self_after_turn(state, sc, transcript)


# ---- 首轮自动标题 ----
# 首条用户消息后，后台让当前 AI 拟一个短标题替换「新会话/消息前缀」。
# 不阻塞回复、不计费、失败静默；生成期间用户若改过标题则放弃覆盖。
_AUTO_TITLE_PROMPT = (
    "你是会话标题生成器。根据用户的第一条消息，为这段对话拟一个简短标题。"
    "要求：概括主题或意图，不超过 20 个字，使用与用户消息相同的语言；"
    "只输出标题本身，不要引号、书名号、句末标点或任何解释。"
    "消息只有图片或内容极少时，输出「图片分享」。"
)
_AUTO_TITLE_MAX_CHARS = 20
# 标题本身只需几十 token，但思考类模型（DeepSeek/豆包/GLM 等默认思考）会把
# reasoning 算进 max_tokens，64 时常在正文前就吃满、被 finish_reason=length
# 截断。放宽上限只影响「模型啰嗦」的极端情况，正常生成完即停。
_AUTO_TITLE_MAX_TOKENS = 512
_AUTO_TITLE_WRAPPERS = ("「」", "『』", "“”", "\"\"", "《》", "【】", "''")
_AUTO_TITLE_TAIL_PUNCT = "。．.!！?？~～…、，,;；:："


def _is_default_title(title: str) -> bool:
    """标题仍是系统默认值（未由用户显式命名）。"""
    t = (title or "").strip()
    return not t or t == "新会话" or t.startswith("群聊·")


def _count_user_messages(session) -> int:
    """会话内可见（非 hidden）用户消息数；群聊跨全部窗口统计。"""
    threads = getattr(session, "threads", None) or []
    source = []
    if threads:
        for t in threads:
            source.extend(t.messages)
    else:
        source = list(session.messages)
    return sum(
        1 for m in source
        if m.role == "user" and not getattr(m, "hidden", False)
    )


def _clean_auto_title(text: str) -> str:
    """取模型输出的第一行，剥成对引号/书名号与句末标点，截断到上限。"""
    for line in (text or "").splitlines():
        t = line.strip()
        if not t:
            continue
        for prefix in ("标题：", "标题:"):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
        for pair in _AUTO_TITLE_WRAPPERS:
            if len(t) >= 2 and t[0] == pair[0] and t[-1] == pair[1]:
                t = t[1:-1].strip()
                break
        t = t.rstrip(_AUTO_TITLE_TAIL_PUNCT)
        t = " ".join(t.split())
        return t[:_AUTO_TITLE_MAX_CHARS].strip()
    return ""


def _title_provider(state, sc, speakers, req, provider):
    """首轮标题调用用哪个提供商：首个座位专属模型 > 请求默认。

    默认模型缺失/不可见时静默返回 None（标题是可选增强，不能影响请求本身）。
    """
    spk = speakers[0] if speakers else None
    try:
        if spk is not None and (spk.provider_id or "").strip():
            return _resolve_visible_provider(state, sc, spk.provider_id)
        if provider is not None:
            return provider
        return _resolve_visible_provider(
            state, sc, getattr(req, "provider_id", None)
        )
    except HTTPException:
        return None


def _schedule_auto_title(*, sc, run, session_id: str, provider,
                         first_text: str, expected_title: str):
    """后台生成首轮标题并落库；仅在标题仍等于 expected_title 时覆盖。

    store.get() → set_title → save() 之间不得有 await：单事件循环线程内
    该片段相对 drive 的同步落盘是原子的，避免把生成期间的编辑覆盖掉。
    不能用 save_merged（title 属合并字段，会被磁盘旧值还原）。

    返回后台任务（未调度时为 None），供终态 done 判断标题是否仍在生成。
    """
    if provider is None or not session_id:
        return None
    seed = (first_text or "").strip() or "（用户发送了一张图片）"

    async def _job():
        try:
            raw = await complete(
                provider,
                [Message(role="user", content=seed)],
                system_prompt=_AUTO_TITLE_PROMPT,
                max_tokens=_AUTO_TITLE_MAX_TOKENS,
                session_id=session_id,
                partial_ok=True,
            )
            title = _clean_auto_title(raw)
            if not title:
                return
            fresh = sc.store.get(session_id)
            if fresh is None or fresh.title != expected_title:
                return  # 会话已删或标题已被用户改过：不覆盖
            fresh.set_title(title)
            sc.store.save(fresh)
            if run is not None and run.status == "running":
                await run.emit("session_title", {
                    "session_id": session_id,
                    "title": title,
                })
        except Exception:
            logger.exception("自动标题生成失败 sid=%s", session_id)

    try:
        return asyncio.create_task(_job())
    except RuntimeError:
        return None

# 图片输入约束
_ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
_MAX_IMAGES = 4
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class ImagePayload(BaseModel):
    media_type: str
    data: str  # 不含 data: 前缀的 base64


class GalgameResume(BaseModel):
    # 失去响应的 galgame_choice 工具调用 id
    tool_call_id: str
    # 用户选择的选项文案，或自由输入的内容
    text: str


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
    # L4-A 续写：目标是该窗口最后一条可见 AI 回复，生成增量并回同一条消息
    continue_message_id: Optional[str] = None
    # 消息来源标记：目前只接受 "card"（酒馆卡沙箱在用户确认后发起）
    origin: Optional[str] = None
    # 定向发言（客户端解析输入框里的 @角色名）：被点名者按此顺序最先回复，
    # 其余座位跟后；仅 group 窗口生效
    target_member_ids: List[str] = []
    # 群聊发言顺序：true=未定向的座位每轮随机洗牌；false/空=按窗口成员顺序
    shuffle_speakers: bool = False
    # Galgame：续接一个因服务重启而失去响应的 galgame_choice 工具调用
    # （不新增用户消息，改为补上该 tool 响应后继续演出）
    galgame_resume: Optional[GalgameResume] = None
    # 输入框「深度思考」开关：Anthropic 走 extended thinking，
    # OpenAI 兼容厂商合并该模型配置的 thinking_params
    deep_thinking: bool = False
    # 输入框「⋯ → 工具」里手动关闭的工具名：只在功能偏好允许集内做差集，
    # 无法绕过管理员/用户的类别级开关
    disabled_tools: List[str] = []
    # P2 长跑：非空时本次请求作为长跑任务启动（loop 路由/审批内部使用）
    loop_task: Optional[str] = None
    loop_request_id: int = 0
    # 客户端预生成的用户消息 id：回答失败（供应商报错/断网）后，前端仍能用
    # 同一个 id 撤回或重发该轮，不会留下服务端不认识的本地脏消息。
    client_message_id: Optional[str] = None
    # 酒馆卡 context.generate('normal', {quiet_prompt})：不新增可见用户消息，
    # 仅把 quiet_prompt 追加到本轮出站提示词末尾（不落盘），生成一条 AI 楼层。
    quiet_prompt: Optional[str] = None
    automatic_trigger: bool = False


_SAFE_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")



def _prompt_anchor(history) -> str:
    for msg in reversed(history or []):
        if getattr(msg, "role", "") == "user" and getattr(msg, "id", ""):
            return str(msg.id)
    return ""


def _make_prompt_capture(state, sc, session_id: str, history):
    """真实提示词归档：把本轮每次 provider 请求按锚点用户消息落盘。"""
    anchor = _prompt_anchor(history)
    if not anchor:
        return None
    from ..prompt_archive import append_call

    def capture(kind, messages, tools, params):
        append_call(
            getattr(state, "data_dir", None), sc.uid, session_id,
            anchor, kind, messages, tools, params,
        )

    return capture

def _claim_user_message_id(req, messages) -> str:
    """取客户端预生成的消息 id；非法或已存在时退回服务端随机 id。

    已存在说明同一请求被重复提交：直接 409，避免同 id 出现两条消息让
    「撤回/重新生成」按锚点定位到错误的一轮。
    """
    raw = (getattr(req, "client_message_id", "") or "").strip()
    if not raw or not _SAFE_MESSAGE_ID.match(raw):
        return ""
    if any(getattr(m, "id", "") == raw for m in messages):
        raise HTTPException(409, "该消息已提交，请刷新后重试")
    return raw


def _turn_user_message_id(history, turn_start: int) -> str:
    """message_start 告知客户端本轮落盘的用户消息 id。

    续写/Galgame 续接不新增用户消息，此时返回空串；前端据此判断
    「请求是否真的在服务端落过盘」，未落盘的失败轮可以直接回滚输入框。
    """
    if turn_start <= 0 or turn_start > len(history):
        return ""
    last = history[turn_start - 1]
    return last.id if last.role == "user" else ""


def _effective_max_tokens(session, user_settings, preset_max: int = 0) -> Optional[int]:
    """本轮回复 token 上限：单窗口覆盖 > 用户全局 > 预设 > None（跟随提供商配置）。"""
    room_mt = int(getattr(session, "max_tokens", 0) or 0)
    if room_mt > 0:
        return room_mt
    global_mt = int(getattr(user_settings, "max_tokens", 0) or 0)
    if global_mt > 0:
        return global_mt
    return int(preset_max or 0) or None


def _load_active_preset(sc):
    """读取该用户当前启用的预设（实现见 preset.apply，chat/session 共用）。"""
    from ..preset.apply import load_active_preset

    return load_active_preset(sc)


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


def _author_prompt_items(session) -> list:
    author = (getattr(session, "attributes", None) or {}).get("author") or {}
    items = author.get("prompts")
    return items[:20] if isinstance(items, list) else []


def _author_prompt_position(item) -> int:
    raw = item.get("position", 0)
    if isinstance(raw, str):
        key = raw.strip().lower()
        return {"in_chat": 1, "before_prompt": 2, "in_prompt": 0}.get(key, 0)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value in (0, 1, 2) else 0


def _author_prompt_role(item) -> str:
    raw = item.get("role", 0)
    if isinstance(raw, str):
        key = raw.strip().lower()
        return {"user": "user", "assistant": "assistant", "system": "system"}.get(key, "system")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    return {1: "user", 2: "assistant"}.get(value, "system")


def _author_prompt_extra(session) -> str:
    """in_prompt / before_prompt 的扩展提示词（并入系统提示词）。"""
    parts = []
    for item in _author_prompt_items(session):
        if not isinstance(item, dict):
            continue
        if _author_prompt_position(item) == 1:
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip()[:16384])
    return "\n\n".join(parts)


def _author_prompt_depth(session) -> list:
    """in_chat 的扩展提示词：转成 run_agent_loop 的 depth_blocks。"""
    blocks = []
    for index, item in enumerate(_author_prompt_items(session)):
        if not isinstance(item, dict) or _author_prompt_position(item) != 1:
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            depth = max(0, int(item.get("depth") or 0))
        except (TypeError, ValueError):
            depth = 0
        blocks.append({
            "content": content.strip()[:16384],
            "depth": depth,
            "role": _author_prompt_role(item),
            "order": 900 + index,
        })
    return blocks


def _billing_tzname(state) -> str:
    """计费时段/流水日期使用的时区（与定时任务一致）。"""
    try:
        return state.config.scheduler.timezone or "Asia/Shanghai"
    except AttributeError:
        return "Asia/Shanghai"


def _usage_ledger(sc):
    """该用户的用量流水文件；简易测试里没有 settings_path 时返回 None。"""
    base = getattr(sc, "settings_path", None)
    return base.parent / "usage.jsonl" if base is not None else None


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


def _last_visible_assistant(history) -> Optional[Message]:
    """窗口内最后一条可见且有正文的 AI 消息（续写锚点）。"""
    for m in reversed(list(history or [])):
        if m.role != "assistant":
            continue
        if getattr(m, "hidden", False) or getattr(m, "tombstone", None):
            continue
        if not (m.content or "").strip():
            continue
        return m
    return None


def _merge_continue_reply(thread, target, history, turn_start, base,
                          session, mvu_on) -> str:
    """L4-A 续写收尾：把本轮临时 assistant 增量并回目标消息后删除临时消息。

    同一轮不新增消息；MVU 只对增量文本应用补丁。就地修改回复后必须同步活动
    variant 快照，否则下次加载会被 heal_thread 误判成孤儿回复。
    """
    from ..st import mvu as st_mvu

    parts = []
    for m in history[turn_start:]:
        if m.role != "assistant" or getattr(m, "hidden", False):
            continue
        if (m.content or "").strip():
            parts.append(m.content or "")
    delta = "".join(parts)
    if delta:
        target.content = (base or "") + delta
        if mvu_on:
            cur = (target.stat_data
                   or (session.mvu or {}).get("stat_data") or {})
            new_state, _applied, _warns = st_mvu.apply_updates(cur, delta)
            target.stat_data = copy.deepcopy(new_state)
            session.mvu["stat_data"] = copy.deepcopy(new_state)
        versions.sync_active_reply(thread, target)
    del history[turn_start:]
    return delta


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


async def _emit_context_warning(run, session, window_id: str, history) -> None:
    """窗口字数跨过预警阈值时提示一次（同一阈值每个窗口只提示一次）。

    只发提示事件，不压缩也不改历史；真正越过硬上限后的请求侧截断在
    agent.loop 内完成，对用户完全不可见。
    """
    chars = history_char_size(history)
    buckets = session.context_warned
    if not isinstance(buckets, dict):
        buckets = {}
        session.context_warned = buckets
    warned = buckets.setdefault(window_id or "", [])
    fresh = [
        t for t in CONTEXT_WARN_THRESHOLDS
        if chars >= t and t not in warned
    ]
    if not fresh:
        return
    warned.extend(fresh)
    await run.emit("context_warning", {
        "chars": chars,
        "threshold": max(fresh),
        "thresholds": fresh,
    })


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
    return await submit_chat(state, sc, req)


async def submit_chat(state, sc, req: ChatRequest, run_manager=None):
    """聊天/长跑共用的提交入口（loop 审批路由也调它）。

    长跑请求（req.loop_task 非空）会先建 loop_sessions 行再走常规驱动；
    长跑进行中的普通消息不抢会话锁，改道 InterruptHub。
    """
    session = None
    if req.session_id:
        _safe_sid(req.session_id)
        session = sc.store.get(req.session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
    else:
        session = sc.store.create()

    if (not req.message.strip() and not req.images
            and not req.regenerate_message_id and req.galgame_resume is None
            and not getattr(req, "continue_message_id", None)
            and not (getattr(req, "quiet_prompt", None) or "").strip()):
        raise HTTPException(400, "消息内容为空")
    origin = (getattr(req, "origin", None) or "").strip()
    if origin and origin != "card":
        raise HTTPException(400, "非法的消息来源")

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
        sc.store.save_merged(session)
        thread_id = thread.id
    if thread_id:
        req.thread_id = thread_id

    # ---- P2 长跑前置：门禁、单 AI 校验、运行态去重 ----
    loop_task = (getattr(req, "loop_task", None) or "").strip()
    loop_ctx = None
    loop_mgr = getattr(state, "loop_manager", None)
    if loop_task:
        loop_cfg = getattr(state.config, "loop", None)
        if loop_cfg is None or not loop_cfg.enabled:
            raise HTTPException(400, "长跑模式未启用")
        if loop_mgr is None:
            raise HTTPException(400, "长跑状态机未就绪")
        if thread_id:
            th = session.thread(thread_id)
            ai_members = [
                session.member(mid) for mid in (getattr(th, "member_ids", []) or [])
            ]
            ai_count = sum(
                1 for m in ai_members if m is not None and not m.is_user
            )
            if ai_count > 1:
                raise HTTPException(400, "长跑模式仅支持单 AI 会话")
        existing = await asyncio.to_thread(
            loop_mgr.running_for, sc.uid, session.id
        )
        if existing is not None:
            raise HTTPException(409, "该会话已有运行中的长跑任务")
        # P4：长跑首条消息走提示词注册表 loop_start 包装（默认恒等模板，
        # 行为不变）；loop_task 局部变量保留原文供状态机记录
        registry = getattr(state, "prompt_registry", None)
        if registry is not None:
            req.message = registry.render(
                "loop_start", task=req.message or loop_task
            )

    # 会话级互斥快速预检（权威 check-and-set 在各路径落消息前完成）：
    # 同一房间任意窗口有运行中的回答 → 普通会话拒绝；长跑会话改道打断队列
    run_manager = run_manager or _get_run_manager(state)
    if run_manager.active_in_session(sc.uid, session.id) is not None:
        routed = None
        if loop_mgr is not None and req.message.strip():
            routed = await asyncio.to_thread(
                loop_mgr.running_for, sc.uid, session.id
            )
        if routed is not None:
            hub = getattr(state, "interrupt_hub", None)
            if hub is not None:
                hub.push(session.id, {
                    "text": req.message, "name": "",
                    "uid": sc.uid, "ts": time.time(),
                })
                return JSONResponse({
                    "queued": True, "loop_id": routed["id"],
                    "pending": hub.pending(session.id),
                })
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    # 建长跑运行态行（reconcile 权威依据），再进入常规驱动
    if loop_task:
        loop_id = await asyncio.to_thread(
            loop_mgr.session_create, sc.uid, session.id, loop_task,
            int(getattr(req, "loop_request_id", 0) or 0),
        )
        loop_ctx = LoopContext(task=loop_task, loop_db_id=loop_id)

    if thread_id:
        return await _chat_in_thread(
            state, sc, session, req, None, run_manager, loop_ctx=loop_ctx
        )

    # ---- 旧单 AI 会话路径 ----
    # 请求级 provider 在此解析（房间路径的座位级解析在 _chat_in_thread 内）
    provider = _resolve_visible_provider(state, sc, req.provider_id)
    # 加载角色/世界观（优先会话副本）→ 构建系统提示词与查询/属性服务
    character = opening_character(session, effective_character(session, sc.lore))
    world = effective_world(session, sc.lore)
    user_char = member_character(user_seat(session), sc.lore)
    # 用户功能偏好与全局默认合并（工具/扮演/各功能开关）；管理员无覆盖字段
    _us = getattr(sc, "user_settings", None)
    eff_roleplay = feature_prefs.effective_roleplay(state.config, _us)
    eff_tools = feature_prefs.effective_tool_settings(state.config, _us)
    # P4 原生 MVU：卡声明 + 服务端/用户开关；首次进入时按 InitVar 惰性初始化
    from ..st import mvu as st_mvu

    mvu_on = st_mvu.mvu_active(character, _us, state.config)
    _mvu_user_name = str(getattr(user_char, "name", "") or "") if user_char else ""
    if st_mvu.ensure_session_state(session, character,
                                   user_name=_mvu_user_name):
        sc.store.save_merged(session)
    mvu_vars = (
        dict((session.mvu or {}).get("stat_data") or {}) if mvu_on else None
    )
    _registry = getattr(state, "registry", None)
    # P1 请求级自定义工具（每用户独立 agent.db），先于白名单计算以便放行 custom__*
    custom_extras = _custom_extras(state, sc, session.id)
    # P4 请求级 MCP 桥接工具（admin 库缓存，mcp__ 前缀按类别放行）
    mcp_extras = _mcp_extras(state)
    # P2 长跑：追加仅长跑可见的 loop_finish
    loop_extras = _loop_extras(custom_extras + mcp_extras, loop_ctx)
    allowed_tools = (
        feature_prefs.allowed_tool_names(
            _registry, state.config, _us,
            disabled=(
                set(getattr(req, "disabled_tools", None) or [])
                | set(getattr(session, "disabled_tools", None) or [])
            ),
            extra_names={t.name for t in loop_extras},
            level=getattr(sc, "level", 0),
            is_admin=(sc.uid == ADMIN_UID),
        )
        if hasattr(_registry, "all") else None
    )
    # EJS 提示词模板开启时，系统提示词里的卡/预设文本要保留模板到客户端执行
    from ..tavern import reset_preserve_ejs, set_preserve_ejs
    from ..template import policy as template_policy

    _system_tpl_token = None
    if template_policy.prompt_enabled(
        getattr(state, "config", None),
        _us,
        int(getattr(sc, "level", 0) or 0),
        getattr(sc, "uid", "") == ADMIN_UID,
    ):
        _system_tpl_token = set_preserve_ejs(True)
    try:
        system_prompt = build_system_prompt(
            character, world, DEFAULT_SYSTEM_PROMPT, roleplay=eff_roleplay,
            user_character=user_char, variables=mvu_vars,
        )
        author_extra = _author_prompt_extra(session)
        if author_extra:
            system_prompt = system_prompt + "\n\n" + author_extra
    finally:
        if _system_tpl_token is not None:
            reset_preserve_ejs(_system_tpl_token)
    services = {}
    # 沙箱工具依赖（fs_* 仅在 sandbox.enabled 时注册，缺省不影响其他工具）
    services["data_dir"] = getattr(state, "data_dir", None)
    services["sandbox_settings"] = getattr(
        getattr(state, "config", None), "sandbox", None
    )
    # P1 自定义工具管理五件套据此打开调用者自己的 agent.db
    services["custom_runtime"] = getattr(state, "custom_runtime", None)
    # P4：MCP 桥接工具与 watcher 管理工具的运行时（工具内自行做启用/门禁判断）
    services["mcp_manager"] = getattr(state, "mcp_manager", None)
    services["watcher_manager"] = getattr(state, "watcher_manager", None)
    # 召回日志（主动召回记录）
    services["recall_logs"] = getattr(state, "recall_logs", None)
    # TTS：角色卡绑定音色 + 当前用户等级（工具内解析/鉴权）
    services["tts_voice"] = getattr(character, "voice", "") or ""
    services["user_level"] = getattr(sc, "level", 0)
    # DeepSeek 官方服务配置 + 按量计费（搜索/抓取/TTS 工具使用）
    services["deepseek_settings"] = getattr(state.config, "deepseek", None)
    services["usage_meter"] = metering.make_usage_meter(state, sc)
    if character or world:
        services["lore"] = _get_querier(sc, character, world)
    services["macro_ctx"] = {
        "char_name": getattr(character, "name", "") or "",
        "user_name": getattr(user_char, "name", "") if user_char else "",
        "group_names": [],
        "variables": mvu_vars,
    }
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
    # AI 浏览器：该用户功能生效时才懒创建管理器并注入（会话键按 用户:会话 隔离）
    if feature_prefs.feature_enabled("browser", state.config, _us):
        get_browser = getattr(state, "get_browser", None)
        if get_browser is not None:
            services["browser_manager"] = get_browser()
            services["browser_key"] = "%s:%s" % (sc.uid, session.id)
    # 浏览器搜索引擎（engine=chromium 的 web_search 用）：联网功能生效才注入
    if feature_prefs.feature_enabled("web", state.config, _us):
        _get_sb = getattr(state, "get_search_browser", None)
        services["chromium_search"] = _get_sb() if _get_sb else None
    # 角色卡自带属性时即启用（不再受全局开关限制）；
    # 旧卡回退全局 list 的逻辑在 resolve_attribute_defs 内已含开关判断
    attr_defs = resolve_attribute_defs(character, eff_roleplay) if character is not None else []
    if attr_defs:
        services["attributes"] = AttributeManager(session, attr_defs)
    # P2 长跑：功能开启即注入工具服务；loop_ctx 存在时再加引擎四个钩子
    if getattr(getattr(state.config, "loop", None), "enabled", False):
        _install_loop_services(
            services, state, sc, loop_ctx, provider,
            loop_starter=_make_loop_starter(state, sc, run_manager),
            session_id=session.id,
        )
    # P3 自我记忆/承诺工具服务（按各自开关）
    _install_proactor_services(services, state, sc)

    # 权威 check-and-set：独占会话后再落用户消息；抢锁失败则未做任何变更
    run = run_manager.create(sc.uid, session.id)
    if not run_manager.try_lock_for(sc.uid, session.id, run):
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    try:
        # 图片参数先校验（非法直接 400，不能消耗对话次数），再扣费
        images = _build_images(req)
        # 客户端预生成的消息 id 在扣费前认领：重复/非法不能扣了钱才失败
        user_msg_id = _claim_user_message_id(req, session.messages)
        # 计费：按 token 计费的模型开轮前只要求余额 > 0，回复完成后按实际
        # 用量结算；其余模型沿用每轮固定扣费（管理员免扣）。余额不足抛 402，
        # 随 except 释放会话锁
        token_billed = billing.has_pricing(provider)
        cost = 0.0
        if token_billed:
            billing.precheck_or_402(state, sc.uid)
        else:
            cost = billing.provider_rate(provider)
            billing.charge_or_402(state, sc.uid, cost)
        # 追加用户消息；首条用户消息自动设为标题。
        # quiet_prompt（酒馆 context.generate）不落用户消息，只生成 AI 楼层。
        if not (getattr(req, "quiet_prompt", None) or "").strip():
            session.messages.append(
                Message(
                    id=user_msg_id,
                    role="user",
                    content=req.message or None,
                    name=user_char.name if user_char else None,
                    images=images,
                )
            )
        user_count = sum(1 for m in session.messages if m.role == "user")
        if user_count == 1:
            session.set_title((req.message[:20] or "图片").strip() or "图片")
        # 修复此前因重启中断、失去 tool 响应的 assistant（否则直接 400）
        session.messages[:] = _heal_dangling_tool_calls(session.messages)
        sc.store.save_merged(session)
    except Exception:
        run.mark_failed()
        run_manager.unlock_session(session.id, run)
        raise

    # 首轮自动标题：标题此刻是消息前缀（上面刚设），AI 结果仅在未被改动时覆盖
    if user_count == 1:
        _schedule_auto_title(
            sc=sc, run=run, session_id=session.id, provider=provider,
            first_text=req.message or "", expected_title=session.title,
        )

    async def drive():
        from ..tavern import reset_preserve_ejs, set_preserve_ejs
        from ..template import policy as template_policy

        # 本轮组装期间保留卡/预设 EJS，交给 run_agent_loop 的客户端桥执行
        _tpl_token = None
        if template_policy.prompt_enabled(
            getattr(state, "config", None),
            _us,
            int(getattr(sc, "level", 0) or 0),
            getattr(sc, "uid", "") == ADMIN_UID,
        ):
            _tpl_token = set_preserve_ejs(True)
        try:
            await run.emit(
                "message_start",
                {
                    "session_id": session.id,
                    "character_id": session.character_id,
                    "world_id": session.world_id,
                    "user_message_id": _turn_user_message_id(
                        session.messages, len(session.messages)
                    ),
                    "attributes": (
                        services["attributes"].get_all()
                        if "attributes" in services
                        else session.attributes
                    ),
                },
            )
            await _emit_context_warning(run, session, "", session.messages)
            recall_block, recall_items = await build_auto_recall_block(
                state=state, sc=sc,
                char_id=session.character_id or "",
                history=session.messages, member=None,
                query=req.message or "",
                cache_namespace=session.id,
            )
            if recall_block:
                await run.emit(
                    "recall_notice",
                    {"count": len(recall_items), "source": "auto",
                     "items": recall_items},
                )
            # 内容表触发词自动注入（世界书 selective）：系统侧扫描用户消息，
            # 命中条目直接进系统提示词，不依赖 AI 主动 query_lore
            lore_block = ""
            if getattr(_us, "lore_auto_inject", True) and services.get("lore"):
                try:
                    lore_block = await services["lore"].trigger_block(
                        req.message or (getattr(req, "quiet_prompt", None) or ""),
                        services.get("embedder"),
                        char_name=getattr(character, "name", "") or "",
                        user_name=getattr(user_char, "name", "") if user_char else "",
                        group_names=[],
                        variables=mvu_vars,
                        scan_texts=[
                            m.content for m in session.messages if m.content
                        ],
                    )
                except Exception:
                    logger.exception("内容表触发词注入失败")
            ledger_block = build_ledger_block(sc, session.character_id or "", None)
            injected = (("\n\n" + lore_block) if lore_block else "") \
                + (("\n\n" + ledger_block) if ledger_block else "") \
                + (("\n\n" + recall_block) if recall_block else "")
            depth_specs = _lore_depth_specs(
                services.get("lore"), character, user_char, req.message or "",
                variables=mvu_vars,
            )
            depth_specs = depth_specs + _author_prompt_depth(session)
            # 当前启用的模型预设：main 块并入系统提示词，tail 与角色 PHI 同位，
            # depth 块与世界书深度块合并；采样参数/jailbreak 前缀透传给 loop
            from ..preset.apply import render_preset

            active_preset = _load_active_preset(sc)
            rendered_preset = render_preset(
                active_preset,
                char_name=getattr(character, "name", "") or "角色",
                user_name=getattr(user_char, "name", "") if user_char else "",
                group_names=[],
                last_message=req.message or "",
                variables=mvu_vars,
            )
            system_extra = injected
            if rendered_preset.main_text:
                system_extra += "\n\n" + rendered_preset.main_text
            phi_parts = [
                _render_card_text(
                    getattr(character, "post_history_instructions", "") or "",
                    character, user_char, group_names=[], variables=mvu_vars,
                ),
                rendered_preset.tail_text,
            ]
            phi = "\n\n".join(p for p in phi_parts if p.strip())
            recall_head = len(session.messages)
            # P3 LIVE_CTX：逐轮易变内容走尾部 system，静态前缀保持字节稳定
            live_block = await render_for_turn(
                state, sc, session_id=session.id,
                character_id=session.character_id or "",
                query=req.message or "",
            )
            await _touch_proactor(state, sc, session.id)
            # ST 正则（prompt 侧）：单聊按当前角色卡编译，作为全历史的兜底表；
            # 启用预设自带的全局正则放在 extra，先于卡正则生效
            st_scripts = _st_compile_scripts(
                sc, getattr(character, "regex_scripts", None)
            )
            preset_scripts = _st_compile_scripts(
                sc, getattr(active_preset, "regex_scripts", None)
            )
            st_regex = {"fallback": st_scripts} if st_scripts else None
            if preset_scripts:
                st_regex = dict(st_regex or {})
                st_regex["extra"] = preset_scripts
            if mvu_on:
                # MVU 更新块只从出站 prompt 剥离（存储原文留给展示侧正则）
                st_regex = dict(st_regex or {})
                st_regex["strip_updates"] = True
            # EJS 提示词侧（方案 B）：最终骨架里的 <% %> 交给客户端沙箱执行。
            # 功能关闭/用户未开启时不创建桥，提示词逐字节不变。
            from ..template import policy as template_policy
            from ..template.store import user_store

            template_bridge = None
            if template_policy.prompt_enabled(
                getattr(state, "config", None),
                _us,
                int(getattr(sc, "level", 0) or 0),
                getattr(sc, "uid", "") == ADMIN_UID,
            ):
                try:
                    _tpl_store = user_store(state, sc)
                except ValueError:
                    _tpl_store = None
                if _tpl_store is not None:
                    from ..template.prompt import PromptTemplateBridge

                    template_bridge = PromptTemplateBridge(
                        run, session, _tpl_store,
                        uid=str(getattr(sc, "uid", "") or ""),
                        history=session.messages,
                        char=character,
                        user=user_char,
                    )
            done_data = None
            async for (ev, data) in run_agent_loop(
                history=session.messages,
                prompt_capture=_make_prompt_capture(state, sc, session.id, session.messages),
                provider=provider,
                registry=state.registry,
                settings=eff_tools,
                system_prompt=system_prompt + system_extra,
                live_ctx=live_block,
                session_id=session.id,
                services=services,
                web_max_iterations=getattr(
                    getattr(state.config, "web_search", None), "max_iterations", None
                ),
                keep_turns=getattr(
                    getattr(sc, "user_settings", None), "keep_turns", 0
                ) or 0,
                allowed_tools=allowed_tools,
                deep_thinking=getattr(req, "deep_thinking", False),
                post_history_instructions=phi,
                quiet_prompt=(getattr(req, "quiet_prompt", None) or "").strip(),
                max_tokens=_effective_max_tokens(
                    session, _us, rendered_preset.max_tokens
                ),
                depth_blocks=depth_specs + rendered_preset.depth_blocks,
                extra_params=rendered_preset.params or None,
                assistant_prefill=rendered_preset.prefill,
                uid=sc.uid,
                level=sc.level,
                is_admin=(sc.uid == ADMIN_UID),
                extra_tools=loop_extras,
                loop_ctx=loop_ctx,
                st_regex=st_regex,
                macro_ctx={
                    "char_name": getattr(character, "name", "") or "",
                    "user_name": (
                        getattr(user_char, "name", "") if user_char else ""
                    ),
                    "group_names": [],
                    "variables": mvu_vars,
                },
                template_prompt=template_bridge,
            ):
                if ev == "done":
                    session.add_usage(data.get("usage"))
                    if token_billed:
                        # 按实际 token 结算并写用量流水（失败/取消未到 done，不扣）
                        billing.settle_usage(
                            state, sc.uid, provider, data.get("usage"),
                            session_id=session.id,
                            ledger_path=_usage_ledger(sc),
                            tzname=_billing_tzname(state),
                        )
                    done_data = data
                else:
                    await run.emit(ev, data)
            # 本轮 assistant 落角色卡 id（旧单聊路径；自动回忆按角色过滤用）
            for m in session.messages[recall_head:]:
                if m.role == "assistant":
                    m.character_id = session.character_id or ""
            # P4 MVU：应用本轮助手消息里的 <UpdateVariable>，逐条写 stat_data 快照
            if mvu_on:
                # 先按幸存消息重算基准（编辑/重生成/撤回后旧更新不沿用）
                st_mvu.recompute_state(session, session.messages)
                cur = dict((session.mvu or {}).get("stat_data") or {})
                for m in session.messages[recall_head:]:
                    if m.role != "assistant" or not (m.content or "").strip():
                        continue
                    cur, _applied, _warns = st_mvu.apply_updates(cur, m.content)
                    m.stat_data = copy.deepcopy(cur)
                session.mvu["stat_data"] = cur
            # 召回条目随最终 assistant 消息持久化（仅前端展示，不回灌模型）
            _attach_recall(session.messages, recall_head, "", recall_items)
            # EJS 提示词侧：变量 ops 在生成成功后随会话一起落盘
            if template_bridge is not None:
                template_bridge.finalize()
            # EJS 输出侧（方案 B）：含 <% %> 的助手消息先标记 pending，
            # 客户端渲染完成后回传；功能关闭时不改任何字段。
            from ..template import output as template_output
            from ..template import policy as template_policy

            if template_policy.output_enabled(
                getattr(state, "config", None),
                getattr(sc, "user_settings", None),
                int(getattr(sc, "level", 0) or 0),
                getattr(sc, "uid", "") == ADMIN_UID,
            ):
                template_output.mark_pending_output(session, True)
            # 先落盘再发终态帧：客户端收到 terminal done 会立即重拉会话，
            # 磁盘上必须已包含本轮完整消息，否则读到缺回复的旧快照
            sc.store.save_merged(session)
            if done_data is not None:
                await run.emit("done", done_data)
            _schedule_extraction(
                state, sc,
                char_id=session.character_id or "",
                marker_key=session.id,
                history=session.messages, member=None,
                provider=provider,
            )
            # P3 轮后：心情判定/宣布消费 + 第一人称自我记忆抽取（失败不阻断）。
            # 终态帧已经发出：两步都只写各自的状态库、不改会话历史，放到后台
            # 执行，避免拖住会话锁——否则用户收到 done 后马上发下一条会 409。
            turn_text = transcript_from_history(session.messages)
            had_pending_now = had_pending_announce(live_block)
            _spawn_post_turn(
                "mood/self-memory",
                lambda: _post_turn_chat_hooks(
                    state, sc, session.id, session.character_id or "",
                    provider, turn_text, had_pending_now,
                ),
            )
            await _finalize_loop(
                state, sc, session.id, loop_ctx, session.messages
            )
            return "done"
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            billing.refund(state, sc.uid, cost)  # 回复未生成：退回本轮费用
            await run.emit("error", {"message": str(exc), "fatal": True})
            sc.store.save_merged(session)
            await _finalize_loop(
                state, sc, session.id, loop_ctx, session.messages, failed=True
            )
            return "error"
        except asyncio.CancelledError:
            # 用户主动停止：模型已实际调用，不退款；已生成部分照样落盘
            sc.store.save_merged(session)
            if loop_ctx is not None:
                loop_ctx.stop_status = LOOP_STOPPED
                await _finalize_loop(
                    state, sc, session.id, loop_ctx, session.messages
                )
            raise
        except Exception as exc:
            logger.exception("聊天流异常")
            billing.refund(state, sc.uid, cost)
            await run.emit(
                "error",
                {"message": f"{type(exc).__name__}: {exc}", "fatal": True},
            )
            sc.store.save_merged(session)
            await _finalize_loop(
                state, sc, session.id, loop_ctx, session.messages, failed=True
            )
            return "error"
        finally:
            if _tpl_token is not None:
                reset_preserve_ejs(_tpl_token)

    _spawn_run(run, drive, run_manager)
    return _sse_response(run)


def _heal_dangling_tool_calls(history, custom=None):
    """修复缺失 tool 响应的 assistant 消息：在该消息之后补占位 tool 消息。

    服务重启/中断会让带 tool_calls 的 assistant 之后没有对应 tool 响应，
    再发给提供商会直接 HTTP 400。custom：{tool_call_id: content} 用指定文案补响应。
    返回新列表，不改动入参。
    """
    answered = set()
    for m0 in history:
        if m0.role == "tool" and m0.tool_call_id:
            answered.add(m0.tool_call_id)
    out = []
    fixed = set()
    for m0 in history:
        out.append(m0)
        if m0.role != "assistant" or not m0.tool_calls:
            continue
        for call in m0.tool_calls:
            cid = call.id
            if cid in answered or cid in fixed:
                continue
            if custom and cid in custom:
                content = custom[cid]
            else:
                content = (
                    "（系统：工具 %s 的执行因服务重启或连接中断未能完成，"
                    "本次调用作废，需要时请重新调用。）" % (call.name or "?")
                )
            out.append(
                Message(
                    role="tool",
                    tool_call_id=cid,
                    name=call.name,
                    content=content,
                    ok=False,
                    # 继承发起该工具调用的 assistant 座位归属，
                    # 否则补造消息无 actor_id 会泄露给同群其他 AI
                    actor_id=getattr(m0, "actor_id", None) or None,
                )
            )
            fixed.add(cid)
    return out


def _attach_recall(history, start, actor, items):
    """把本轮召回条目挂到该发言者本轮最后一条【可见】assistant 消息上。

    仅作前端折叠组展示（Message.recall 不进提供商请求体）；这样流式结束
    loadThread 重画、刷新页面、断线重连后召回计数/明细都不会归零。
    """
    if not items:
        return
    for m in reversed(history[start:]):
        if m.role != "assistant" or m.hidden:
            continue
        if actor and (getattr(m, "actor_id", "") or "") != actor:
            continue
        m.recall = [dict(it) for it in items]
        return


def _merge_galgame_tts_overlay(state, session) -> None:
    """把 TTS 端点产出的「文本→url」overlay 合并进会话（drive 保存点调用）。"""
    overlay = state.galgame_tts_overlay.get(session.id)
    if not overlay:
        return
    merged = dict(overlay)
    merged.update(session.galgame_tts or {})
    session.galgame_tts = merged


async def _chat_in_thread(state, sc, session, req: ChatRequest, provider=None,
                          run_manager: Optional[ChatRunManager] = None,
                          loop_ctx: Optional[LoopContext] = None):
    """beta：把用户消息投进指定窗口，并按窗口内 AI 座位逐一响应（fan-out）。

    - dm 窗口：单 AI，等价旧单 AI 会话，但历史/角色取该窗口。
    - group 窗口：同一共享历史下按 member_ids 顺序逐个 AI 各生成一段，
      后一个 AI 的系统提示词能看到前面 AI 的发言（互为上下文）。
    """
    if run_manager is None:
        run_manager = _get_run_manager(state)

    from ..st import mvu as st_mvu

    thread = session.thread(req.thread_id)
    if thread is None:
        raise HTTPException(404, "窗口不存在")
    history = thread_messages(session, thread.id)  # 该窗口的独立历史
    quiet_prompt = (getattr(req, "quiet_prompt", None) or "").strip()

    # 解析窗口内 AI 座位（按 member_ids 顺序；dm=1 个，group=多个）
    speakers = []
    for mid in thread.member_ids:
        m = session.member(mid)
        if m is not None and not m.is_user:
            speakers.append(m)
    if not speakers:
        raise HTTPException(400, "窗口内没有 AI 参与者")
    # P2 长跑只承载单 AI 任务（submit_chat 已预校验，这里兜底防内部调用）
    if loop_ctx is not None and len(speakers) > 1:
        raise HTTPException(400, "长跑模式仅支持单 AI 会话")

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
    is_galgame = getattr(session, "mode", "normal") == "galgame"
    # Galgame 等级门槛：admin 恒过；低于门槛的用户不能通过已有房间继续玩
    if is_galgame and sc.uid != ADMIN_UID:
        _access = getattr(state.config, "access", None)
        _required = int(getattr(_access, "galgame_min_level", 0) or 0) if _access else 0
        _level = int(getattr(sc, "level", 0) or 0)
        if _level < _required:
            raise HTTPException(
                403,
                "权限等级不足（Galgame 需要 Lv%d，当前 Lv%d）"
                % (_required, _level),
            )

    # 用户功能偏好与全局默认合并（工具/扮演/各功能开关）；管理员无覆盖字段
    _us = getattr(sc, "user_settings", None)
    eff_roleplay = feature_prefs.effective_roleplay(state.config, _us)
    eff_tools = feature_prefs.effective_tool_settings(state.config, _us)
    _registry = getattr(state, "registry", None)
    # P1 请求级自定义工具（每用户独立 agent.db），先于白名单计算以便放行 custom__*
    custom_extras = _custom_extras(state, sc, session.id)
    # P4 请求级 MCP 桥接工具（admin 库缓存，mcp__ 前缀按类别放行）
    mcp_extras = _mcp_extras(state)
    # P2 长跑：追加仅长跑可见的 loop_finish
    loop_extras = _loop_extras(custom_extras + mcp_extras, loop_ctx)
    allowed_tools = (
        feature_prefs.allowed_tool_names(
            _registry, state.config, _us,
            disabled=(
                set(getattr(req, "disabled_tools", None) or [])
                | set(getattr(session, "disabled_tools", None) or [])
            ),
            extra_names={t.name for t in loop_extras},
            level=getattr(sc, "level", 0),
            is_admin=(sc.uid == ADMIN_UID),
        )
        if hasattr(_registry, "all") else None
    )
    # 当前启用的模型预设（按用户；每座位按自己的角色名渲染宏）
    active_preset = _load_active_preset(sc)
    # 本窗口 max_tokens 覆盖（房间 > 用户全局 > 预设 > 提供商配置）
    eff_max_tokens = _effective_max_tokens(
        session, _us, getattr(active_preset, "max_tokens", 0) or 0
    )

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

    # 卡片来源标记（submit_chat 已校验，只允许空串或 'card'）
    origin = (getattr(req, "origin", None) or "").strip()
    # 历史编辑 / 重新生成（仅 dm 窗口）。getattr：内部 SimpleNamespace 调用
    edit_id = getattr(req, "edit_message_id", None)
    regen_id = getattr(req, "regenerate_message_id", None)
    # L4-A 续写：目标必须是本窗口最后一条可见 AI 回复，生成内容并回原消息
    continue_id = (getattr(req, "continue_message_id", None) or "").strip()
    continue_target = None
    continue_base = ""
    if continue_id:
        if edit_id or regen_id or getattr(req, "galgame_resume", None) is not None:
            raise HTTPException(400, "续写不能与编辑/重新生成同时使用")
        if thread.kind != "dm":
            raise HTTPException(400, "群聊窗口不支持续写")
        if is_galgame:
            raise HTTPException(400, "只读会话不支持续写")
        last = _last_visible_assistant(history)
        if last is None or last.id != continue_id:
            raise HTTPException(400, "只能续写本窗口最后一条 AI 回复")
        if last.tool_calls:
            raise HTTPException(400, "该回复包含工具调用，不支持续写")
        if not (last.content or "").strip():
            raise HTTPException(400, "该回复内容为空，不支持续写")
        continue_target = last
        continue_base = last.content or ""
    mode = ""
    anchor = ""
    prev = (0, 0)
    if edit_id or regen_id:
        if edit_id and regen_id:
            raise HTTPException(400, "编辑与重新生成不能同时指定")
        if thread.kind != "dm":
            raise HTTPException(400, "群聊窗口不支持编辑/重新生成")

    # 座位 provider 预检：不可见/不存在在这里直接 4xx 返回；
    # 否则错误会埋成后台 SSE error，发起请求只拿到 200。
    # 顺带按最终使用的提供商收集每个座位的计费倍率（群聊逐个累计）。
    speaker_rates = {}
    speaker_priced = {}
    for pre in speakers:
        if (pre.provider_id or "").strip():
            # 显式指定座位 provider：不存在/不可见在这里直接 4xx（保持原预检语义）
            pre_provider = _resolve_visible_provider(state, sc, pre.provider_id)
        elif provider is not None:
            pre_provider = provider
        else:
            # 房间默认 provider 仍是懒解析（drive 内才报 400/403；全部座位自带
            # provider 时允许没有默认）。这里只静默取倍率，取不到按 1.0，
            # 不能在请求参数校验之前因 provider 缺失而抛错。
            wanted = (
                (getattr(req, "provider_id", None) or "")
                or getattr(state.config, "active_provider", "")
                or ""
            ).strip()
            pre_provider = next(
                (p for p in visible_providers(state.config, sc.uid, sc.level)
                 if p.id == wanted),
                None,
            )
        speaker_rates[pre.id] = (
            billing.provider_rate(pre_provider)
            if pre_provider is not None else 1.0
        )
        speaker_priced[pre.id] = (
            pre_provider is not None and billing.has_pricing(pre_provider)
        )
    # 按 token 计费的座位不预扣（事后按实际用量结算），只有旧费率座位计入
    total_cost = sum(
        rate for mid, rate in speaker_rates.items() if not speaker_priced[mid]
    )
    any_priced = any(speaker_priced.values())

    # 权威 check-and-set：独占会话后再落用户消息；抢锁失败时未做任何变更
    run = run_manager.create(sc.uid, session.id, thread.id)
    if not run_manager.try_lock_for(sc.uid, session.id, run):
        raise HTTPException(409, "该会话已有进行中的回答，请等待结束或重连续读")

    # 首轮自动标题的后台任务；drive 收尾时据此告知客户端标题仍在生成
    title_task = None

    # 房间默认 provider 延迟解析（参数已给则直接用）：
    # 所有座位都有专属 provider 时可无默认
    try:
        # 先做只读参数校验，再扣费：400 无效请求不能消耗对话次数
        images = _build_images(req)
        resume_body = getattr(req, "galgame_resume", None)
        resume_call_id = ""
        resume_text = ""
        if edit_id:
            try:
                turn_i, _turn_end = versions.locate_turn(history, edit_id)
            except ValueError:
                raise HTTPException(400, "要编辑的消息不存在")
        elif regen_id:
            try:
                versions.locate_turn(history, regen_id)
            except ValueError:
                raise HTTPException(400, "要重新生成的消息不存在")
        elif resume_body is not None:
            # Galgame 续接参数全部是只读校验，提前到扣费之前
            if not is_galgame:
                raise HTTPException(400, "仅 Galgame 会话支持续接选项")
            resume_call_id = (resume_body.tool_call_id or "").strip()
            resume_text = (resume_body.text or "").strip()
            if not resume_call_id or not resume_text:
                raise HTTPException(400, "续接参数不完整")
            known_calls = set()
            for m0 in history:
                if m0.role == "assistant":
                    for c0 in (m0.tool_calls or []):
                        known_calls.add(c0.id)
            if resume_call_id not in known_calls:
                raise HTTPException(400, "对应的选项调用不存在或已失效")

        # 追加用户消息前认领客户端 id（重复→409），必须在扣费之前失败
        new_user_id = ""
        if not (edit_id or regen_id or resume_body is not None
                or continue_target is not None or quiet_prompt):
            new_user_id = _claim_user_message_id(req, history)

        # 计费：校验通过后一次性预扣旧费率座位的费用（跳过/失败/取消在
        # drive 内退回）；有按 token 计费的座位时只要求余额 > 0，事后结算
        if any_priced:
            billing.precheck_or_402(state, sc.uid)
        if total_cost > 0:
            billing.charge_or_402(state, sc.uid, total_cost)

        if edit_id:
            mode = versions.MODE_EDIT
            anchor = edit_id
            # 纯文本编辑（本次没带新图）时继承原活动用户消息的图片，避免静默丢图
            if not images and history[turn_i].images:
                images = list(history[turn_i].images)
            new_user = Message(
                role="user",
                content=req.message or None,
                name=user_char.name if user_char else None,
                images=images,
                origin=origin,
            )
            try:
                prev = versions.begin_edit(thread, anchor, new_user)
            except ValueError:
                raise HTTPException(400, "要编辑的消息不存在")
            sc.store.save_merged(session)
        elif regen_id:
            mode = versions.MODE_REGEN
            anchor = regen_id
            try:
                prev = versions.begin_regen(thread, anchor)
            except ValueError:
                raise HTTPException(400, "要重新生成的消息不存在")
            sc.store.save_merged(session)
        elif resume_body is not None:
            # Galgame：不新增用户消息，给中断的 galgame_choice 补 tool 响应后续演
            custom = {
                resume_call_id: "用户选择了：「%s」\n请严格依据该选择与当前剧情继续演出。"
                % resume_text
            }
            history[:] = _heal_dangling_tool_calls(history, custom)
            sc.store.save_merged(session)
        elif continue_target is not None:
            # L4-A 续写：不新增用户消息，原 AI 回复作为 assistant prefill
            pass
        elif quiet_prompt:
            # 酒馆 context.generate('normal', {quiet_prompt})：
            # 不新增可见用户消息，quiet_prompt 只进本轮出站提示词。
            pass
        else:
            # 追加用户消息到该窗口（用户扮演自设时按角色名标注，便于展示）
            history.append(
                Message(
                    id=new_user_id,
                    role="user",
                    content=req.message or None,
                    name=user_char.name if user_char else None,
                    images=images,
                    origin=origin,
                )
            )
            # 修复此前因重启中断、失去 tool 响应的 assistant（否则直接 400）
            history[:] = _heal_dangling_tool_calls(history)
            sc.store.save_merged(session)
            # 首轮自动标题：会话内第一条可见用户消息且标题仍是系统默认值时
            # 才让 AI 拟标题（用户给房间起过名的保持原样）
            if (_count_user_messages(session) == 1
                    and _is_default_title(session.title)):
                title_task = _schedule_auto_title(
                    sc=sc, run=run, session_id=session.id,
                    provider=_title_provider(state, sc, speakers, req, provider),
                    first_text=req.message or "",
                    expected_title=session.title,
                )
    except Exception:
        run.mark_failed()
        run_manager.unlock_session(session.id, run)
        raise

    async def drive():
        from ..tavern import reset_preserve_ejs, set_preserve_ejs
        from ..template import policy as template_policy

        _tpl_token = None
        if template_policy.prompt_enabled(
            getattr(state, "config", None),
            _us,
            int(getattr(sc, "level", 0) or 0),
            getattr(sc, "uid", "") == ADMIN_UID,
        ):
            _tpl_token = set_preserve_ejs(True)
        unspent = total_cost  # 预扣费用中尚未实际发生的部分，跳过/出错/取消时退回
        # P2 长跑收尾标志：finally 统一写 loop_sessions 终态
        loop_failed = False
        loop_cancelled = False
        # 本轮新增消息的起点；续写成功收尾时已并回目标消息的标记
        # （finally 兜底只在取消/出错、尚未并回时生效）
        turn_start = len(history)
        continue_merged = False
        try:
            await run.emit(
                "message_start",
                {"session_id": session.id, "thread_id": thread.id,
                 "character_id": "", "world_id": "",
                 "user_message_id": _turn_user_message_id(history, turn_start)},
            )
            await _emit_context_warning(run, session, thread.id, history)
            # P3：真人发起的本轮刷新主动行为活跃时间（未 opt-in 时空转）
            await _touch_proactor(state, sc, session.id)
            # 编辑/重生成：流式起点（此刻 history 末尾即锚点用户消息）
            mode_head = len(history) if mode else 0
            # 房间默认 provider 的懒解析副本（勿在 drive 内直接给外层 provider
            # 赋值——那会让它在 drive 作用域整个变成未绑定局部变量）
            default_provider = provider
            # 终态帧之后才有必要跑的轮后收尾（最后一位发言者的心情判定）
            post_turn_jobs = []
            # ST 正则（prompt 侧）：按发言者角色卡建表，供全历史按消息归属查表
            st_by_character = _st_regex_map(
                sc, [m.character_id for m in speakers]
            )
            # 启用预设的正则对所有角色生效（先于角色卡正则）
            preset_st_scripts = _st_compile_scripts(
                sc, getattr(active_preset, "regex_scripts", None)
            )
            # P4 原生 MVU：本窗口以第一张声明 MVU 的卡作为会话级状态载体
            mvu_char = None
            for _spk in speakers:
                _cand = opening_character(session, member_character(_spk, sc.lore))
                if st_mvu.mvu_active(_cand, _us, state.config):
                    mvu_char = _cand
                    break
            mvu_on = mvu_char is not None
            mvu_char_id = str(getattr(mvu_char, "id", "") or "")
            _mvu_user_name = (
                str(getattr(user_char, "name", "") or "") if user_char else ""
            )
            if st_mvu.ensure_session_state(session, mvu_char,
                                           user_name=_mvu_user_name):
                sc.store.save_merged(session)
            mvu_vars = (
                dict((session.mvu or {}).get("stat_data") or {}) if mvu_on else None
            )
            # 本轮新增消息的起点（编辑/重生成已截断历史，之后追加的都是新产出）
            turn_start = len(history)
            # L4-A 续写：本次生成的增量文本（收尾时并回目标消息并删除临时消息）
            continue_delta = ""
            for spk in speakers:
                char = opening_character(session, member_character(spk, sc.lore))
                # MVU 变量只注入载体卡自己的提示词（其他卡不展开变量宏）
                spk_vars = (
                    mvu_vars
                    if mvu_on and mvu_char_id
                    and str(getattr(char, "id", "") or "") == mvu_char_id
                    else None
                )
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
                if is_galgame:
                    # Galgame：视觉小说演出规则替代群聊参与者提示
                    from ..galgame.prompt import build_galgame_prompt

                    sys_prompt = build_galgame_prompt(
                        character=char,
                        world=world,
                        base=DEFAULT_SYSTEM_PROMPT,
                        roleplay=eff_roleplay,
                        user_character=user_char,
                        variables=spk_vars,
                    )
                    author_extra = _author_prompt_extra(session)
                    if author_extra:
                        sys_prompt = sys_prompt + "\n\n" + author_extra
                else:
                    sys_prompt = build_member_system_prompt(
                        name=spk.name,
                        memory=spk.memory,
                        attributes=spk.attributes,
                        audience=others,
                        character=char,
                        world=world,
                        base=DEFAULT_SYSTEM_PROMPT,
                        roleplay=eff_roleplay,
                        user_character=user_char,
                        variables=spk_vars,
                    )
                    author_extra = _author_prompt_extra(session)
                    if author_extra:
                        sys_prompt = sys_prompt + "\n\n" + author_extra
                services = {}
                # 召回日志（主动召回记录）
                services["recall_logs"] = getattr(state, "recall_logs", None)
                # TTS：该座位角色卡绑定音色 + 当前用户等级
                services["data_dir"] = getattr(state, "data_dir", None)
                services["sandbox_settings"] = getattr(
                    getattr(state, "config", None), "sandbox", None
                )
                services["custom_runtime"] = getattr(state, "custom_runtime", None)
                # P4：MCP 桥接与 watcher 管理运行时
                services["mcp_manager"] = getattr(state, "mcp_manager", None)
                services["watcher_manager"] = getattr(state, "watcher_manager", None)
                services["tts_voice"] = getattr(char, "voice", "") or ""
                services["user_level"] = getattr(sc, "level", 0)
                # DeepSeek 官方服务配置 + 按量计费（搜索/抓取/TTS 工具使用）
                services["deepseek_settings"] = getattr(
                    state.config, "deepseek", None
                )
                services["usage_meter"] = metering.make_usage_meter(state, sc)
                if char or world:
                    services["lore"] = _get_querier(sc, char, world)
                services["macro_ctx"] = {
                    "char_name": getattr(char, "name", "") or spk.name,
                    "user_name": (
                        getattr(user_char, "name", "") if user_char else ""
                    ),
                    "group_names": others,
                    "variables": spk_vars,
                }
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
                # P2 长跑：仅首座位携带 loop_ctx（长跑限定单 AI），其余座位只给工具服务
                if getattr(getattr(state, "config", None), "loop", None) is not None \
                        and state.config.loop.enabled:
                    _install_loop_services(
                        services, state, sc,
                        loop_ctx if spk is speakers[0] else None,
                        spk_provider,
                        loop_starter=_make_loop_starter(state, sc, run_manager),
                        session_id=session.id,
                    )
                # P3 自我记忆/承诺工具服务（按各自开关；member 取本座位）
                _install_proactor_services(services, state, sc, spk)
                # RAG 语义检索：配了 embeddings 提供商才注入，否则工具回退关键词检索
                _get_emb = getattr(state, "get_embedder", None)
                services["embedder"] = _get_emb() if _get_emb else None
                # AI 浏览器：该用户功能生效时才懒创建管理器并注入
                if feature_prefs.feature_enabled("browser", state.config, _us):
                    get_browser = getattr(state, "get_browser", None)
                    if get_browser is not None:
                        services["browser_manager"] = get_browser()
                        services["browser_key"] = "%s:%s" % (sc.uid, session.id)
                # 浏览器搜索引擎（engine=chromium 的 web_search 用）：联网生效才注入
                if feature_prefs.feature_enabled("web", state.config, _us):
                    _get_sb = getattr(state, "get_search_browser", None)
                    services["chromium_search"] = _get_sb() if _get_sb else None
                # 属性跟踪：该 AI 角色卡定义了属性即启用，值落在 member.attributes；
                # 无角色卡的座位不挂属性管理器（无提示词说明，挂载只会造成失控写入）
                member_attr_defs = resolve_attribute_defs(char, eff_roleplay) if char is not None else []
                if member_attr_defs:
                    services["attributes"] = AttributeManager(spk, member_attr_defs)
                # Galgame：资产库 / 场景态 / 选项撮合等专用上下文
                if is_galgame:
                    from ..galgame.assets import GalgameAssetStore

                    services["uid"] = sc.uid
                    services["galgame_character"] = char
                    services["galgame_char_id"] = spk.character_id or ""
                    services["galgame_sid"] = session.id
                    services["galgame_state"] = session.galgame
                    services["galgame_choice_manager"] = state.galgame_choices
                    services["galgame_assets"] = GalgameAssetStore(
                        getattr(sc, "upload_dir", None),
                        "/uploads/%s" % getattr(sc, "uid", ""),
                    )
                    # 选项阻塞前由 galgame_choice 工具触发落盘（见该工具）
                    services["galgame_store"] = sc.store
                    services["galgame_session"] = session
                    services["galgame_tts_overlay"] = \
                        state.galgame_tts_overlay.setdefault(session.id, {})
                # 开始该 AI 的发言：标记当前发言者，必为首帧
                await run.emit(
                    "speaker",
                    {"member_id": spk.id, "name": spk.name,
                     "character_id": spk.character_id or ""},
                )
                recall_block, recall_items = await build_auto_recall_block(
                    state=state, sc=sc,
                    char_id=spk.character_id or "",
                    history=history, member=spk,
                    query=req.message or "",
                    cache_namespace=thread.id,
                )
                if recall_block:
                    await run.emit(
                        "recall_notice",
                        {"count": len(recall_items),
                         "name": spk.name, "source": "auto",
                         "items": recall_items},
                    )
                # 内容表触发词自动注入：该座位的角色表 + 共享世界表
                lore_block = ""
                if getattr(_us, "lore_auto_inject", True) and services.get("lore"):
                    try:
                        lore_block = await services["lore"].trigger_block(
                            req.message or quiet_prompt, services.get("embedder"),
                            char_name=getattr(char, "name", "") or spk.name,
                            user_name=(
                                getattr(user_char, "name", "") if user_char else ""
                            ),
                            group_names=others,
                            variables=spk_vars,
                            scan_texts=[
                                m.content for m in history if m.content
                            ],
                        )
                    except Exception:
                        logger.exception("内容表触发词注入失败")
                ledger_block = build_ledger_block(sc, spk.character_id or "", spk)
                injected_parts = ""
                if lore_block:
                    injected_parts += "\n\n" + lore_block
                if ledger_block:
                    injected_parts += "\n\n" + ledger_block
                if recall_block:
                    injected_parts += "\n\n" + recall_block
                # 世界书/角色表的 @depth 常驻块（按概率，宏按本座位角色替换）
                depth_specs = _lore_depth_specs(
                    services.get("lore"), char, user_char,
                    req.message or "", group_names=others,
                    variables=spk_vars,
                )
                depth_specs = depth_specs + _author_prompt_depth(session)
                # 启用中的预设：按本座位角色渲染（main 入系统提示词、
                # tail 与该角色 PHI 同位、depth 与世界书块合并、jailbreak 前缀）
                from ..preset.apply import render_preset

                rendered_preset = render_preset(
                    active_preset,
                    char_name=getattr(char, "name", "") or spk.name,
                    user_name=getattr(user_char, "name", "") if user_char else "",
                    group_names=others,
                    last_message=req.message or "",
                    variables=spk_vars,
                )
                preset_sys = (
                    "\n\n" + rendered_preset.main_text
                    if rendered_preset.main_text else ""
                )
                preset_phi_parts = [
                    _render_card_text(
                        getattr(char, "post_history_instructions", "") or "",
                        char, user_char, group_names=others,
                        variables=spk_vars,
                    ),
                    rendered_preset.tail_text,
                ]
                preset_phi = "\n\n".join(
                    p for p in preset_phi_parts if p.strip()
                )
                # 该 AI 的完整发言回合。
                # 群聊里若模型一次给「空回复」（0 个字、且没通过工具得到内容），视为没开口成功：
                # 不要当跳过，用一句系统提醒重试几次，避免『第二个 AI 不说话』。
                # 只有显式回「略」等跳过关字，才算真正的需要跳过。
                head_snapshot = len(history)
                done_data = None
                # 重试会真实消耗 token：按座位累计每轮 attempt 的用量
                usage_acc = {"prompt_tokens": 0, "completion_tokens": 0,
                             "cached_tokens": 0}
                # 最后一轮 attempt 的 prompt token 才是上下文水位（累加值会偏大）
                last_prompt = 0
                retry_nudge = ""
                attempts = 0
                skipped = False
                # Galgame 一轮内可能多次绘图/切换/选项，放大工具迭代预算
                # （预算取用户合并后的工具设置与 Galgame 循环上限）
                loop_settings = eff_tools
                if is_galgame:
                    loop_settings = eff_tools.model_copy(update={
                        "max_iterations": max(
                            eff_tools.max_iterations,
                            feature_prefs.effective_galgame_loops(
                                state.config, _us
                            ),
                        )
                    })
                # EJS 提示词侧（方案 B）：每个座位独立上下文与变量快照；
                # 同一座位本轮重试复用已渲染单元格。
                from ..template import policy as template_policy
                from ..template.store import user_store

                spk_template_bridge = None
                if template_policy.prompt_enabled(
                    getattr(state, "config", None),
                    _us,
                    int(getattr(sc, "level", 0) or 0),
                    getattr(sc, "uid", "") == ADMIN_UID,
                ):
                    try:
                        _spk_tpl_store = user_store(state, sc)
                    except ValueError:
                        _spk_tpl_store = None
                    if _spk_tpl_store is not None:
                        from ..template.prompt import PromptTemplateBridge

                        spk_template_bridge = PromptTemplateBridge(
                            run, session, _spk_tpl_store,
                            uid=str(getattr(sc, "uid", "") or ""),
                            history=history,
                            char=char,
                            user=user_char,
                            group=[m for m in speakers if m is not spk],
                        )
                # P3 LIVE_CTX：按本座位角色的心情/约定组装尾部实时块
                live_block = await render_for_turn(
                    state, sc, session_id=session.id,
                    character_id=spk.character_id or "",
                    query=req.message or "",
                )
                # ST 正则 prompt 侧：本座位脚本表 + 预设全局脚本 + MVU 更新块剥离
                spk_st_regex = None
                if st_by_character or mvu_on or preset_st_scripts:
                    spk_st_regex = {
                        "by_character": st_by_character,
                        "fallback": st_by_character.get(
                            getattr(spk, "character_id", "") or "", []
                        ),
                    }
                    if preset_st_scripts:
                        spk_st_regex["extra"] = preset_st_scripts
                    if mvu_on:
                        spk_st_regex["strip_updates"] = True
                while True:
                    attempts += 1
                    before = len(history)
                    async for (ev, data) in run_agent_loop(
                        history=history,
                        prompt_capture=_make_prompt_capture(state, sc, session.id, history),
                        provider=spk_provider,
                        registry=state.registry,
                        settings=loop_settings,
                        system_prompt=(
                            sys_prompt + injected_parts + preset_sys + retry_nudge
                        ),
                        session_id=session.id,
                        run_id=run.id,
                        services=services,
                        web_max_iterations=getattr(
                            getattr(state.config, "web_search", None),
                            "max_iterations", None,
                        ),
                        actor=spk.id,
                        actor_name=spk.name,
                        keep_turns=getattr(
                            getattr(sc, "user_settings", None), "keep_turns", 0
                        ) or 0,
                        allowed_tools=(
                            [] if continue_target is not None else allowed_tools
                        ),
                        deep_thinking=getattr(req, "deep_thinking", False),
                        post_history_instructions=preset_phi,
                        live_ctx=live_block,
                        quiet_prompt=quiet_prompt,
                        max_tokens=eff_max_tokens,
                        depth_blocks=depth_specs + rendered_preset.depth_blocks,
                        extra_params=rendered_preset.params or None,
                        assistant_prefill=(
                            continue_base + (rendered_preset.prefill or "")
                            if continue_target is not None
                            else rendered_preset.prefill
                        ),
                        uid=sc.uid,
                        level=sc.level,
                        is_admin=(sc.uid == ADMIN_UID),
                        extra_tools=(
                            [] if continue_target is not None else loop_extras
                        ),
                        loop_ctx=(
                            loop_ctx if spk is speakers[0] else None
                        ),
                        st_regex=spk_st_regex,
                        macro_ctx={
                            "char_name": (
                                getattr(char, "name", "") or spk.name
                            ),
                            "user_name": (
                                getattr(user_char, "name", "")
                                if user_char else ""
                            ),
                            "group_names": others,
                            "variables": spk_vars,
                        },
                        template_prompt=spk_template_bridge,
                    ):
                        if ev == "done":
                            done_data = data
                            attempt_usage = (data or {}).get("usage") or {}
                            try:
                                last_prompt = int(
                                    attempt_usage.get("prompt_tokens")
                                    or last_prompt)
                            except (TypeError, ValueError):
                                pass
                            for ukey in usage_acc:
                                try:
                                    usage_acc[ukey] += int(
                                        attempt_usage.get(ukey) or 0)
                                except (TypeError, ValueError):
                                    pass
                        else:
                            await run.emit(ev, data)
                    # 给本轮追加的 assistant 回复打上发言者标记（含中间的工具步）。
                    # character_id 落盘：座位换卡后旧消息仍归旧卡，自动回忆
                    # 按角色 id 过滤时不会把旧卡的对白算给新卡。
                    for m in history[before:]:
                        if m.role == "assistant":
                            m.name = spk.name
                            m.character_id = spk.character_id or ""
                            if origin:
                                m.origin = origin
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
                # 本座位生成成功：模板变量随会话落盘，并让下一位座位看到变更
                if spk_template_bridge is not None:
                    spk_template_bridge.finalize()
                # 召回条目随最终 assistant 消息持久化（仅前端展示，不回灌模型）
                if not skipped:
                    _attach_recall(history, head_snapshot, spk.id, recall_items)
                # 结算本座位：旧费率座位跳过（回复「略」）的退回其费用，正常完成的
                # 仅从 unspent 移除；按 token 计费的座位用累计实际用量结算
                if any(usage_acc.values()):
                    session.add_usage(usage_acc, context_prompt=last_prompt)
                    if speaker_priced.get(spk.id):
                        billing.settle_usage(
                            state, sc.uid, spk_provider, usage_acc,
                            session_id=session.id,
                            ledger_path=_usage_ledger(sc),
                            tzname=_billing_tzname(state),
                        )
                if not speaker_priced.get(spk.id):
                    if skipped:
                        billing.refund(state, sc.uid, speaker_rates[spk.id])
                    unspent -= speaker_rates[spk.id]
                # 每个 AI 之后发一个 done（terminal=False，前端只记录用量不收尾）；
                # 最后一位发言者的 done 才结束整个运行
                fan_done = dict(done_data or {"reason": "stop", "iterations": attempts})
                fan_done["terminal"] = spk is speakers[-1]
                # 标题仍在生成时，终态帧之后到达的 session_title 已不会被客户端收取；
                # 带上标记让前端在收尾后短轮询把标题补回侧栏
                if (fan_done["terminal"] and title_task is not None
                        and not title_task.done()):
                    fan_done["title_pending"] = True
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
                # L4-A 续写：把本次增量并回目标消息（同一轮不新增消息），
                # MVU 只对增量应用补丁；随后删除本轮临时 assistant 消息。
                if continue_target is not None:
                    continue_delta = _merge_continue_reply(
                        thread, continue_target, history, turn_start,
                        continue_base, session, mvu_on,
                    )
                    continue_merged = True
                # 先落盘再发终态帧：客户端收到 terminal done 会立即重拉会话，
                # 磁盘上必须已包含本轮完整消息，否则读到缺回复的旧快照
                if fan_done["terminal"]:
                    from ..template import output as template_output
                    from ..template import policy as template_policy

                    if template_policy.output_enabled(
                        getattr(state, "config", None),
                        getattr(sc, "user_settings", None),
                        int(getattr(sc, "level", 0) or 0),
                        getattr(sc, "uid", "") == ADMIN_UID,
                    ):
                        template_output.mark_pending_output(session, True)
                    sc.store.save_merged(session)
                await run.emit("done", fan_done)
                if skipped:
                    await run.emit(
                        "speaker_skip",
                        {"member_id": spk.id, "name": spk.name},
                    )
                _schedule_extraction(
                    state, sc,
                    char_id=spk.character_id or "",
                    marker_key=thread.id,
                    history=history, member=spk,
                    provider=spk_provider,
                )
                # P3 轮后心情：跳过的座位没有真实发言，不判定、不消费宣布。
                # 最后一位发言者之后就是终态帧，收尾挪到后台；其余座位还在
                # 生成中（客户端本就在等），照旧同步跑即可。
                if not skipped:
                    if continue_target is not None:
                        # 续写的临时消息已并回目标消息，本轮转写只能用增量
                        turn_text = continue_delta
                    else:
                        turn_text = transcript_from_history(
                            history, head=head_snapshot, actor_id=spk.id
                        )
                    had_pending_now = had_pending_announce(live_block)
                    if spk is speakers[-1]:
                        post_turn_jobs.append((
                            session.id, spk.character_id or "",
                            spk_provider, turn_text, had_pending_now,
                        ))
                    else:
                        await handle_post_turn_mood(
                            state, sc, session_id=session.id,
                            character_id=spk.character_id or "",
                            transcript=turn_text,
                            provider=spk_provider,
                            had_pending=had_pending_now,
                        )
            # P4 MVU：本轮新消息应用变量更新并逐条写快照（仅载体卡的消息）
            if mvu_on:
                # 先按幸存消息重算基准（编辑/重生成/撤回后旧更新不沿用）
                st_mvu.recompute_state(session, history)
                cur = dict((session.mvu or {}).get("stat_data") or {})
                for m in history[turn_start:]:
                    if m.role != "assistant" or not (m.content or "").strip():
                        continue
                    mid = getattr(m, "character_id", "") or ""
                    if mid and mvu_char_id and mid != mvu_char_id:
                        continue
                    cur, _applied, _warns = st_mvu.apply_updates(cur, m.content)
                    m.stat_data = copy.deepcopy(cur)
                session.mvu["stat_data"] = cur
            # P3 第一人称自我记忆：每请求一次（全局视角，不按座位重复）。
            # 与最后一位发言者的心情判定一起放到后台，终态帧后不再占会话锁。
            room_transcript = transcript_from_history(history)
            _merge_galgame_tts_overlay(state, session)
            sc.store.save_merged(session)
            _spawn_post_turn(
                "room mood/self-memory",
                lambda: _post_turn_room_hooks(
                    state, sc, post_turn_jobs, room_transcript
                ),
            )
            return "done"
        except ProviderError as exc:
            logger.warning("provider 错误: %s", exc)
            billing.refund(state, sc.uid, unspent)  # 未发言座位的费用退回
            await run.emit("error", {"message": str(exc), "fatal": True})
            loop_failed = True
            return "error"
        except asyncio.CancelledError:
            # 用户主动停止：未发言座位没执行，退回其费用；已发言的不退
            billing.refund(state, sc.uid, unspent)
            loop_cancelled = True
            raise  # finally 仍执行：edit/regen 回滚或保留 + 保存
        except Exception as exc:
            logger.exception("群聊流异常")
            billing.refund(state, sc.uid, unspent)
            await run.emit(
                "error",
                {"message": f"{type(exc).__name__}: {exc}", "fatal": True},
            )
            loop_failed = True
            return "error"
        finally:
            # 续写中途被停止/出错：把已生成的半截增量并回目标消息，不留孤儿消息
            if continue_target is not None and not continue_merged:
                _merge_continue_reply(
                    thread, continue_target, history, turn_start,
                    continue_base, session, locals().get("mvu_on", False),
                )
            # 收编本轮回复为新 reply variant；空产出回滚。正常群聊 mode="" 时只保存
            if mode:
                versions.commit_run(thread, anchor, mode_head, mode, prev)
            if is_galgame:
                # 清理未被作答的阻塞选项 future
                state.galgame_choices.discard(run.id)
                _merge_galgame_tts_overlay(state, session)
            sc.store.save_merged(session)
            # P2 长跑终态落 agent.db（loop_ctx 为 None 时直接空转）
            if loop_ctx is not None:
                if loop_cancelled:
                    loop_ctx.stop_status = LOOP_STOPPED
                await _finalize_loop(
                    state, sc, session.id, loop_ctx, history,
                    failed=loop_failed,
                )
            if _tpl_token is not None:
                reset_preserve_ejs(_tpl_token)

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
    # 按座位精确匹配窗口：同一张卡挂多个座位时不互相回顾私聊窗口
    sets = QueryChatMemoryTool._candidate_sets(
        session, char_id, getattr(member, "id", "") or ""
    )
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
        _snap_us = getattr(sc, "user_settings", None)
        snap_tools = feature_prefs.effective_tool_settings(state.config, _snap_us)
        # P4 MVU：存档同样按载体卡渲染常驻条目/PHI（不写状态，只读当前快照）
        from ..st import mvu as st_mvu

        carrier = st_mvu.carrier_character(session, sc.lore)
        if carrier is not None and st_mvu.ensure_session_state(
            session, carrier,
            user_name=(getattr(user_char, "name", "") if user_char else ""),
        ):
            sc.store.save_merged(session)
        mvu_on = bool(
            carrier is not None
            and st_mvu.mvu_active(carrier, _snap_us, state.config)
        )
        carrier_id = str(getattr(carrier, "id", "") or "") if carrier else ""
        state_vars = (
            dict((session.mvu or {}).get("stat_data") or {})
            if mvu_on else None
        )
        # 存档只需要内容表/记忆类 base 工具，不暴露联网/绘图等用户功能
        _snap_registry = getattr(state, "registry", None)
        snap_allowed = (
            feature_prefs.base_tool_names(_snap_registry)
            if hasattr(_snap_registry, "all") else None
        )
        for spk in speakers:
            char = opening_character(session, member_character(spk, sc.lore))
            spk_vars = (
                state_vars
                if (mvu_on and str(getattr(char, "id", "") or "") == carrier_id)
                else None
            )
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
                roleplay=feature_prefs.effective_roleplay(state.config, _snap_us),
                user_character=user_char,
                variables=spk_vars,
            ) + _SNAPSHOT_DIRECTIVE

            services = {}
            if char or world:
                services["lore"] = _get_querier(sc, char, world)
            services["macro_ctx"] = {
                "char_name": getattr(char, "name", "") or spk.name,
                "user_name": (
                    getattr(user_char, "name", "") if user_char else ""
                ),
                "group_names": [n for n in audience_names if n != spk.name],
                "variables": spk_vars,
            }
            services["store"] = sc.store
            services["session"] = session
            services["member"] = spk
            services["memory_vec_cache"] = getattr(state, "memory_vec_cache", None)
            services["char_memory"] = getattr(sc, "char_memory", None)
            services["data_dir"] = getattr(state, "data_dir", None)
            services["sandbox_settings"] = getattr(
                getattr(state, "config", None), "sandbox", None
            )
            # 存档动作：即使座位未挂载/只读，也强制写进角色记忆簿
            services["book_force_write"] = True
            services["llm_provider"] = spk_provider
            # DeepSeek 官方服务配置 + 按量计费（快照里的工具调用同样计费）
            services["deepseek_settings"] = getattr(state.config, "deepseek", None)
            services["usage_meter"] = metering.make_usage_meter(state, sc)
            _get_emb = getattr(state, "get_embedder", None)
            services["embedder"] = _get_emb() if _get_emb else None

            snap_history = [Message(role="user", content=transcript)]
            done_data = None
            async for (ev, data) in run_agent_loop(
                history=snap_history,
                prompt_capture=_make_prompt_capture(state, sc, session.id, snap_history),
                provider=spk_provider,
                registry=state.registry,
                settings=snap_tools,
                system_prompt=sys_prompt,
                session_id=session.id,
                services=services,
                actor=spk.id,
                allowed_tools=snap_allowed,
                post_history_instructions=_render_card_text(
                    getattr(char, "post_history_instructions", "") or "",
                    char, user_char,
                    group_names=[n for n in audience_names if n != spk.name],
                    variables=spk_vars,
                ),
                macro_ctx={
                    "char_name": getattr(char, "name", "") or spk.name,
                    "user_name": (
                        getattr(user_char, "name", "") if user_char else ""
                    ),
                    "group_names": [
                        n for n in audience_names if n != spk.name
                    ],
                    "variables": spk_vars,
                },
                uid=sc.uid,
                level=sc.level,
                is_admin=(sc.uid == ADMIN_UID),
            ):
                if ev == "done":
                    done_data = data
                else:
                    await run.emit(ev, data)
            if done_data:
                session.add_usage(done_data.get("usage"))
            fan_done = dict(done_data or {"reason": "stop", "iterations": 1})
            fan_done["terminal"] = spk is speakers[-1]
            # 先落盘再发终态帧：客户端收到 terminal done 会立即重拉会话，
            # 磁盘上必须已包含本轮完整消息，否则读到缺回复的旧快照
            if fan_done["terminal"]:
                sc.store.save_merged(session)
            await run.emit("done", fan_done)
        sc.store.save_merged(session)
        return "done"

    _spawn_run(run, drive, run_manager)
    return _sse_response(run)


# ---- 断线重连端点 ----

@router.get("/chat/active-run")
async def chat_active_run(session_id: str, request: Request):
    """跨设备 / 跨标签页发现：返回该用户在此会话上当前活跃 run 的 id。

    新设备没有本机 run 标记（localStorage），进入会话时先问这里，
    有结果就直接续读事件流，不再盲目 POST /api/chat 撞 409。
    """
    state = request.app.state.flyai
    sc = state.user(request)
    run = _get_run_manager(state).active_in_session(sc.uid, session_id)
    return {"run_id": run.id if run is not None else None}


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
    """主动停止：取消后台生成任务（已生成部分仍会保存）。

    取消是异步投递；这里等任务真正收尾（释放会话锁）再返回，
    否则用户「停止 → 立刻重发」会撞上 409。
    """
    run = _get_owned_run(request, run_id)
    if run.status == "running" and run.task is not None:
        run.task.cancel()
        try:
            await asyncio.wait({run.task}, timeout=3.0)
        except Exception:
            pass
    return {"cancelled": True}
