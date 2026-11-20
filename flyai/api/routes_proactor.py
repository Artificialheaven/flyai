"""P3 主动行为 HTTP 路由：心情、自我记忆、承诺、定时作业、轮询、会话 opt-in。

数据全部落每用户 agent.db；普通用户只能操作自己的数据，管理员可带 uid
代管。读接口不做功能开关拦截（历史数据随时可查/可清），写接口要求对应
功能段已启用。DAL 为同步短连接，统一经 asyncio.to_thread 调用。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, validator

from ..feature_prefs import self_memory_active
from ..proactor import clock
from ..user import ADMIN_UID, UserScope

router = APIRouter(prefix="/api/proactor", tags=["proactor"])


# ---------- 鉴权与作用域（与 routes_loop 同范式） ----------

def _require_admin(sc) -> None:
    if sc.uid != ADMIN_UID:
        raise HTTPException(403, "需要管理员权限")


def _scope_for(state, uid: str):
    if uid in state._scopes:
        return state._scopes[uid]
    if uid != ADMIN_UID and state.user_store.get_by_id(uid) is None:
        raise HTTPException(404, "用户不存在")
    scope = UserScope.build(
        uid, state.config, state.data_dir, state.user_store
    )
    state._scopes[uid] = scope
    return scope


def _resolve_target(state, sc, uid: Optional[str]):
    target = (uid or "").strip() or sc.uid
    if target != sc.uid:
        _require_admin(sc)
    return _scope_for(state, target), target


def _require_feature(cfg_section, name: str):
    if cfg_section is None or not getattr(cfg_section, "enabled", False):
        raise HTTPException(400, "%s 未启用" % name)


# ---------- 请求体 ----------

class MoodClearBody(BaseModel):
    session_id: str
    character_id: str
    uid: Optional[str] = None


class SelfMemoryBody(BaseModel):
    statement: str
    tags: List[str] = []
    importance: float = 0.5
    uid: Optional[str] = None

    @validator("importance")
    def _clamp(cls, v):  # noqa: N805
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5


class SupersedeBody(BaseModel):
    statement: str
    uid: Optional[str] = None


class CommitmentBody(BaseModel):
    session_id: str
    content: str
    due_at: Any  # epoch 秒或 'YYYY-MM-DD HH:MM'/'HH:MM' 字符串
    owner_name: str = ""
    uid: Optional[str] = None


class IdBody(BaseModel):
    uid: Optional[str] = None


class ToggleBody(BaseModel):
    enabled: bool
    uid: Optional[str] = None


class JobBody(BaseModel):
    session_id: str
    mode: str
    hhmm: str
    brief: str
    weekday: int = -1
    uid: Optional[str] = None


class PollBody(BaseModel):
    session_id: str
    title: str
    probability: int = 10
    wake_every_n: int = 20
    active_hours: float = 12.0
    uid: Optional[str] = None


class OptInBody(BaseModel):
    session_id: str
    enabled: bool
    uid: Optional[str] = None


# ---------- 心情 ----------

@router.get("/mood/view")
async def mood_view(session_id: str, character_id: str, request: Request,
                    uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    store = getattr(state, "mood_store", None)
    if store is None:
        raise HTTPException(400, "心情系统未就绪")
    return await asyncio.to_thread(
        store.view, target_sc.uid, session_id, character_id
    )


@router.get("/mood/timeline")
async def mood_timeline(session_id: str, character_id: str, request: Request,
                        limit: int = 100, uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    store = getattr(state, "mood_store", None)
    if store is None:
        raise HTTPException(400, "心情系统未就绪")
    rows = await asyncio.to_thread(
        store.timeline, target_sc.uid, session_id, character_id, limit
    )
    return {"items": rows}


@router.post("/mood/clear")
async def mood_clear(body: MoodClearBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    _require_feature(getattr(state.config, "mood", None), "心情系统")
    target_sc, _ = _resolve_target(state, sc, body.uid)
    await asyncio.to_thread(
        state.mood_store.clear, target_sc.uid, body.session_id, body.character_id
    )
    return {"ok": True}


# ---------- 自我记忆 ----------

@router.get("/self-memory")
async def self_memory_list(request: Request,
                           include_superseded: bool = False,
                           uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    store = getattr(state, "self_memory_store", None)
    if store is None:
        raise HTTPException(400, "自我记忆未就绪")
    rows = await asyncio.to_thread(
        store.list_all, target_sc.uid, include_superseded
    )
    return {"items": rows}


@router.get("/self-memory/traces")
async def self_memory_traces(request: Request, limit: int = 100,
                             uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    store = getattr(state, "self_memory_store", None)
    if store is None:
        raise HTTPException(400, "自我记忆未就绪")
    rows = await asyncio.to_thread(store.traces, target_sc.uid, limit)
    return {"items": rows}


@router.post("/self-memory")
async def self_memory_add(body: SelfMemoryBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, body.uid)
    if not self_memory_active(getattr(state, "config", None),
                              getattr(target_sc, "user_settings", None)):
        raise HTTPException(400, "自我记忆未启用")
    try:
        row, created = await asyncio.to_thread(
            state.self_memory_store.put, target_sc.uid, body.statement,
            body.tags, body.importance,
        )
    except Exception as exc:
        # QuotaExceeded 等业务失败走 400，不泄露堆栈
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row, "created": created}


@router.post("/self-memory/{memory_id}/supersede")
async def self_memory_supersede(memory_id: int, body: SupersedeBody,
                                request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, body.uid)
    if not self_memory_active(getattr(state, "config", None),
                              getattr(target_sc, "user_settings", None)):
        raise HTTPException(400, "自我记忆未启用")
    try:
        row = await asyncio.to_thread(
            state.self_memory_store.supersede,
            target_sc.uid, memory_id, body.statement,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@router.delete("/self-memory/{memory_id}")
async def self_memory_delete(memory_id: int, request: Request,
                             uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    ok = await asyncio.to_thread(
        state.self_memory_store.delete, target_sc.uid, memory_id
    )
    if not ok:
        raise HTTPException(404, "记忆不存在")
    return {"ok": True}


# ---------- 承诺 ----------

@router.get("/commitments")
async def commitment_list(request: Request, session_id: Optional[str] = None,
                          status: Optional[str] = None,
                          uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    rows = await asyncio.to_thread(
        state.commitment_store.list, target_sc.uid, session_id, status
    )
    return {"items": rows}


@router.post("/commitments")
async def commitment_add(body: CommitmentBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    _require_feature(getattr(state.config, "proactor", None), "主动行为")
    target_sc, _ = _resolve_target(state, sc, body.uid)
    if target_sc.store.get(body.session_id) is None:
        raise HTTPException(404, "会话不存在")
    tzname = state.config.scheduler.timezone
    due = clock.parse_due_at(body.due_at, time.time(), tzname)
    if due is None:
        raise HTTPException(400, "due_at 无法解析（epoch 秒或 YYYY-MM-DD HH:MM）")
    try:
        row = await asyncio.to_thread(
            state.commitment_store.add, target_sc.uid, body.session_id,
            body.content, due, body.owner_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@router.post("/commitments/{commitment_id}/close")
async def commitment_close(commitment_id: int, body: IdBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, body.uid)
    ok = await asyncio.to_thread(
        state.commitment_store.close, target_sc.uid, commitment_id
    )
    if not ok:
        raise HTTPException(404, "承诺不存在或已关闭")
    return {"ok": True}


@router.post("/commitments/{commitment_id}/cancel")
async def commitment_cancel(commitment_id: int, body: IdBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, body.uid)
    ok = await asyncio.to_thread(
        state.commitment_store.cancel, target_sc.uid, commitment_id
    )
    if not ok:
        raise HTTPException(404, "承诺不存在或已关闭")
    return {"ok": True}


# ---------- 定时作业 ----------

@router.get("/jobs")
async def job_list(request: Request, session_id: Optional[str] = None,
                   uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    rows = await asyncio.to_thread(
        state.job_store.list, target_sc.uid, session_id
    )
    tzname = state.config.scheduler.timezone
    for r in rows:
        ts = float(r.get("next_run_at") or 0)
        r["next_run_local"] = clock.format_local(ts, tzname) if ts > 0 else ""
    return {"items": rows, "timezone": tzname}


@router.post("/jobs")
async def job_add(body: JobBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    _require_feature(getattr(state.config, "proactor", None), "主动行为")
    target_sc, _ = _resolve_target(state, sc, body.uid)
    if target_sc.store.get(body.session_id) is None:
        raise HTTPException(404, "会话不存在")
    try:
        row = await asyncio.to_thread(
            state.job_store.create, target_sc.uid, body.session_id,
            body.mode, body.hhmm, body.brief, body.weekday,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@router.post("/jobs/{job_id}/toggle")
async def job_toggle(job_id: int, body: ToggleBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, body.uid)
    ok = await asyncio.to_thread(
        state.job_store.set_enabled, target_sc.uid, job_id, body.enabled
    )
    if not ok:
        raise HTTPException(404, "作业不存在")
    return {"ok": True}


@router.delete("/jobs/{job_id}")
async def job_delete(job_id: int, request: Request,
                     uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    ok = await asyncio.to_thread(
        state.job_store.delete, target_sc.uid, job_id
    )
    if not ok:
        raise HTTPException(404, "作业不存在")
    return {"ok": True}


# ---------- 自建轮询 ----------

@router.get("/polls")
async def poll_list(request: Request, session_id: Optional[str] = None,
                    uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    rows = await asyncio.to_thread(
        state.proactor_store.list_polls, target_sc.uid, session_id
    )
    return {"items": rows}


@router.post("/polls")
async def poll_add(body: PollBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    _require_feature(getattr(state.config, "proactor", None), "主动行为")
    target_sc, _ = _resolve_target(state, sc, body.uid)
    if target_sc.store.get(body.session_id) is None:
        raise HTTPException(404, "会话不存在")
    try:
        row = await asyncio.to_thread(
            state.proactor_store.add_poll, target_sc.uid, body.session_id,
            body.title, body.probability, body.wake_every_n, body.active_hours,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@router.post("/polls/{poll_id}/toggle")
async def poll_toggle(poll_id: int, body: ToggleBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, body.uid)
    ok = await asyncio.to_thread(
        state.proactor_store.update_poll, target_sc.uid, poll_id,
        enabled=1 if body.enabled else 0,
    )
    if not ok:
        raise HTTPException(404, "轮询不存在")
    return {"ok": True}


@router.delete("/polls/{poll_id}")
async def poll_delete(poll_id: int, request: Request,
                      uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    ok = await asyncio.to_thread(
        state.proactor_store.delete_poll, target_sc.uid, poll_id
    )
    if not ok:
        raise HTTPException(404, "轮询不存在")
    return {"ok": True}


# ---------- 会话级主动行为开关 ----------

@router.get("/proactive/state")
async def proactive_state(session_id: str, request: Request,
                          uid: Optional[str] = None):
    state = request.app.state.flyai
    sc = state.user(request)
    target_sc, _ = _resolve_target(state, sc, uid)
    row = await asyncio.to_thread(
        state.proactor_store.get_state, target_sc.uid, session_id
    )
    return {"state": row}


@router.post("/proactive/enable")
async def proactive_enable(body: OptInBody, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
    _require_feature(getattr(state.config, "proactor", None), "主动行为")
    target_sc, _ = _resolve_target(state, sc, body.uid)
    if target_sc.store.get(body.session_id) is None:
        raise HTTPException(404, "会话不存在")
    await asyncio.to_thread(
        state.proactor_store.set_enabled,
        target_sc.uid, body.session_id, body.enabled,
    )
    # 开启时把活跃时间锚到现在，避免一开启就因冷群门永远不掷骰
    if body.enabled:
        await asyncio.to_thread(
            state.proactor_store.touch_human, target_sc.uid, body.session_id
        )
    return {"ok": True, "enabled": body.enabled}
