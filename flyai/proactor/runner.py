"""后台 ChatRun 驱动器（P3）：主动唤醒/定时作业/到期提醒共用。

与交互式 /chat 的区别（对齐技术书 §7.3/§7.4）：
- 单 AI 私聊会话、单轮、无工具调用；
- 触发通知写成 hidden user 消息（仍落盘，但前端不展示）；
- 模型可输出 [静默] 选择不发言，静默轮不产生 assistant 消息；
- 调度侧先 try_lock，拿不到会话锁就跳过本 tick（不排队、不补发，R10）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ..agent.loop import DEFAULT_SYSTEM_PROMPT
from ..lore.query import build_member_system_prompt
from ..providers.base import StreamEventType
from ..schemas import Message
from ..sessions.resolve import (
    effective_world,
    member_character,
    opening_character,
    primary_thread,
    user_seat,
)
from ..user import UserScope

logger = logging.getLogger("flyai.proactor.runner")

SILENT_MARKER = "[静默]"
# 唤醒/搭话类触发携带的近期历史条数（作业/提醒不带历史）
HISTORY_TAIL = 10


async def _complete_capped(provider, messages: List[Message],
                           system_prompt: str, max_tokens: int,
                           session_id: str = "") -> str:
    request_messages: List[Message] = []
    if system_prompt:
        request_messages.append(Message(role="system", content=system_prompt))
    request_messages.extend(messages)
    parts: List[str] = []
    async for ev in provider.stream_chat(
        request_messages, tool_specs=[], max_tokens=max_tokens,
        session_id=session_id,
    ):
        if ev.type == StreamEventType.CONTENT_DELTA and ev.text:
            parts.append(ev.text)
    return "".join(parts)


def _build_scope(state, uid: str) -> Optional[UserScope]:
    try:
        if uid in state._scopes:
            return state._scopes[uid]
        return UserScope.build(
            uid, state.config, state.data_dir, state.user_store
        )
    except Exception:
        logger.exception("后台触发：构建用户作用域失败 uid=%s", uid)
        return None


async def fire(state, uid: str, session_id: str, *, kind: str, brief: str,
               allow_silent: bool = False, with_history: bool = False,
               max_tokens: int = 500) -> Dict[str, Any]:
    """在后台产出一轮 ChatRun。拿不到锁/会话失效等情况返回 ok=False，不抛异常。"""
    run_manager = getattr(state, "run_manager", None)
    if run_manager is None:
        return {"ok": False, "reason": "no_run_manager"}
    sc = _build_scope(state, uid)
    if sc is None:
        return {"ok": False, "reason": "bad_scope"}
    session = sc.store.get(session_id)
    if session is None:
        return {"ok": False, "reason": "session_gone"}
    thread = primary_thread(session)
    if thread is None:
        return {"ok": False, "reason": "no_dm_thread"}
    speakers = [session.member(mid) for mid in thread.member_ids]
    speakers = [m for m in speakers if m is not None and not m.is_user]
    if len(speakers) != 1:
        return {"ok": False, "reason": "not_single_ai"}
    speaker = speakers[0]
    provider = state.get_provider(speaker.provider_id or session.provider_id or None)
    if provider is None:
        return {"ok": False, "reason": "no_provider"}

    run = run_manager.create(uid, session_id, thread.id)
    if not run_manager.try_lock_for(uid, session_id, run):
        # 会话正被交互式请求占用：跳过本 tick，下轮再试，绝不排队补发
        run.status = "cancelled"
        run.finished = time.time()
        return {"ok": False, "reason": "locked", "run_id": run.id}

    async def drive() -> None:
        try:
            char = opening_character(session, member_character(speaker, sc.lore))
            world = effective_world(session, sc.lore)
            user_char = member_character(user_seat(session), sc.lore)
            # 常驻条目里的 MVU 变量宏需要当前快照；只读渲染，不在这里写状态
            from ..st import mvu as st_mvu

            carrier = st_mvu.carrier_character(session, sc.lore)
            if carrier is not None and st_mvu.ensure_session_state(
                session, carrier,
                user_name=st_mvu.session_user_name(session, sc.lore),
            ):
                sc.store.save_merged(session)
            mvu_on = bool(
                carrier is not None
                and st_mvu.mvu_active(
                    carrier, getattr(sc, "user_settings", None),
                    getattr(state, "config", None),
                )
            )
            variables = (
                dict((session.mvu or {}).get("stat_data") or {})
                if mvu_on else None
            )
            system_prompt = build_member_system_prompt(
                name=speaker.name,
                memory=speaker.memory,
                attributes=speaker.attributes,
                audience=None,
                character=char,
                world=world,
                base=DEFAULT_SYSTEM_PROMPT,
                roleplay=getattr(state.config, "roleplay", None),
                user_character=user_char,
                variables=variables,
            )
            notice = Message(
                role="user", content=brief, hidden=True,
                name=kind, actor_id=speaker.id,
            )
            thread.messages.append(notice)
            messages: List[Message] = []
            if with_history:
                messages = [
                    m for m in thread.messages[:-1]
                    if not getattr(m, "hidden", False)
                    and not getattr(m, "tombstone", None)
                ][-HISTORY_TAIL:]
            messages.append(notice)

            await run.emit("message_start", {
                "session_id": session.id,
                "character_id": speaker.character_id,
                "background": kind,
            })
            text = await _complete_capped(
                provider, messages, system_prompt, int(max_tokens),
                session_id=session.id,
            )
            text = (text or "").strip()
            silent = allow_silent and text.startswith(SILENT_MARKER)
            if silent:
                # 静默轮：撤下隐藏通知，不留 assistant 历史
                thread.messages.pop()
            else:
                await run.emit("content_delta", {"text": text})
                thread.messages.append(Message(
                    role="assistant", content=text, actor_id=speaker.id,
                    character_id=speaker.character_id or "",
                ))
            sc.store.save(session)
            await run.emit("done", {
                "text": "" if silent else text,
                "terminal": True,
                "background": kind,
                "silent": silent,
            })
            await run.finish("done")
        except Exception as exc:  # 后台失败收敛为 run error，不炸调度器
            logger.exception("后台 ChatRun 失败 kind=%s sid=%s", kind, session_id)
            try:
                await run.emit("error",
                               {"message": "%s: %s" % (type(exc).__name__, exc),
                                "fatal": True})
            except Exception:
                pass
            await run.finish("error")
        finally:
            run_manager.unlock_session(session_id, run)

    import asyncio

    run.task = asyncio.create_task(drive())
    return {"ok": True, "run_id": run.id, "thread_id": thread.id}
