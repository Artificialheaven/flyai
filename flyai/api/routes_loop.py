"""长跑模式 HTTP 路由（P2）：申请审批、免审批启动、状态查询、干净停止、安全点打断。

所有状态落每用户 agent.db（loop_requests/loop_sessions），打断队列为进程内
InterruptHub。审批/启动类操作仅管理员；查询与打断限数据所有者或管理员。
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..user import ADMIN_UID, UserScope

router = APIRouter(prefix="/api/chat/loop", tags=["loop"])


def _manager(state):
    if not getattr(getattr(state, "config", None), "loop", None) \
            or not state.config.loop.enabled:
        raise HTTPException(400, "长跑模式未启用")
    mgr = getattr(state, "loop_manager", None)
    if mgr is None:
        raise HTTPException(400, "长跑状态机未就绪")
    return mgr


def _scope_for(state, uid: str):
    """取（或构建并缓存）目标用户的数据作用域；用户不存在 → 404。"""
    if uid in state._scopes:
        return state._scopes[uid]
    if uid != ADMIN_UID and state.user_store.get_by_id(uid) is None:
        raise HTTPException(404, "用户不存在")
    scope = UserScope.build(
        uid, state.config, state.data_dir, state.user_store
    )
    state._scopes[uid] = scope
    return scope


def _require_admin(sc) -> None:
    if sc.uid != ADMIN_UID:
        raise HTTPException(403, "需要管理员权限")


def _resolve_target(state, sc, uid: Optional[str]):
    """解析目标 uid：非管理员只能操作自己的数据。"""
    target = (uid or "").strip() or sc.uid
    if target != sc.uid:
        _require_admin(sc)
    return _scope_for(state, target), target


class LoopStartBody(BaseModel):
    session_id: str
    task: str
    uid: Optional[str] = None
    provider_id: Optional[str] = None


class LoopDecideBody(BaseModel):
    uid: str
    approve: bool


class LoopStopBody(BaseModel):
    uid: Optional[str] = None


class LoopInterruptBody(BaseModel):
    session_id: str
    text: str


@router.post("/start")
async def loop_start(body: LoopStartBody, request: Request):
    """管理员免审批启动长跑（也可指定 uid 替其他用户启动）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    _require_admin(sc)
    mgr = _manager(state)
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(400, "task 不能为空")
    if len(task) > 500:
        raise HTTPException(400, "task 最长 500 字")
    target_sc, target_uid = _resolve_target(state, sc, body.uid)
    if target_sc.store.get(body.session_id) is None:
        raise HTTPException(404, "会话不存在")

    # 复用聊天主入口：loop_task 非空时它会建 loop_sessions 行并注入 loop_ctx
    from .routes_chat import ChatRequest, submit_chat

    req = ChatRequest(
        session_id=body.session_id,
        message=task,
        loop_task=task,
        provider_id=body.provider_id,
    )
    resp = await submit_chat(state, target_sc, req)
    row = await asyncio.to_thread(
        mgr.running_for, target_uid, body.session_id
    )
    return {
        "ok": True,
        "run_id": resp.headers.get("X-Run-Id"),
        "loop_id": row["id"] if row else None,
    }


@router.get("/requests")
async def loop_requests(request: Request, status: Optional[str] = None,
                        uid: Optional[str] = None):
    """列出长跑申请（管理员可指定 uid；普通用户只看自己）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    mgr = _manager(state)
    _scope, target_uid = _resolve_target(state, sc, uid)
    rows = await asyncio.to_thread(mgr.request_list, target_uid, status)
    return {"items": rows}


@router.post("/requests/{request_id}/decide")
async def loop_decide(request_id: int, body: LoopDecideBody,
                      request: Request):
    """审批长跑申请：通过则立即以申请人身份启动，驳回只改状态。"""
    state = request.app.state.flyai
    sc = state.user(request)
    _require_admin(sc)
    mgr = _manager(state)
    target_sc, target_uid = _resolve_target(state, sc, body.uid)

    row = await asyncio.to_thread(mgr.request_get, target_uid, request_id)
    if row is None:
        raise HTTPException(404, "申请不存在")
    decided = await asyncio.to_thread(
        mgr.decide, target_uid, request_id, bool(body.approve), sc.uid
    )
    if decided is None:
        raise HTTPException(409, "申请已裁决，不能重复操作")
    if not body.approve:
        return {"ok": True, "status": decided["status"]}

    if target_sc.store.get(row["session_id"]) is None:
        raise HTTPException(404, "申请对应的会话已不存在")
    from .routes_chat import ChatRequest, submit_chat

    req = ChatRequest(
        session_id=row["session_id"],
        message=row["task"],
        loop_task=row["task"],
        loop_request_id=row["id"],
    )
    resp = await submit_chat(state, target_sc, req)
    session_row = await asyncio.to_thread(
        mgr.running_for, target_uid, row["session_id"]
    )
    return {
        "ok": True,
        "status": decided["status"],
        "run_id": resp.headers.get("X-Run-Id"),
        "loop_id": session_row["id"] if session_row else None,
    }


@router.get("/sessions")
async def loop_sessions(request: Request, status: Optional[str] = None,
                        uid: Optional[str] = None):
    """列出长跑运行态（管理员可指定 uid；普通用户只看自己）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    mgr = _manager(state)
    _scope, target_uid = _resolve_target(state, sc, uid)
    rows = await asyncio.to_thread(mgr.session_list, target_uid, status)
    return {"items": rows}


@router.post("/sessions/{loop_id}/stop")
async def loop_stop(loop_id: int, body: Optional[LoopStopBody],
                    request: Request):
    """干净停止：只把 DB 状态置 stopped，worker 在下个安全点自行退出。"""
    state = request.app.state.flyai
    sc = state.user(request)
    mgr = _manager(state)
    uid = (body.uid if body is not None else None)
    _scope, target_uid = _resolve_target(state, sc, uid)
    ok = await asyncio.to_thread(mgr.stop, target_uid, loop_id)
    if not ok:
        raise HTTPException(409, "运行不存在或已结束，无法停止")
    return {"ok": True, "loop_id": loop_id, "status": "stopped"}


@router.post("/interrupt")
async def loop_interrupt(body: LoopInterruptBody, request: Request):
    """会话成员投递打断/临时指示：不抢会话锁，进安全点队列。"""
    state = request.app.state.flyai
    sc = state.user(request)
    mgr = _manager(state)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "指示内容为空")
    if sc.store.get(body.session_id) is None:
        raise HTTPException(404, "会话不存在")
    row = await asyncio.to_thread(
        mgr.running_for, sc.uid, body.session_id
    )
    if row is None:
        raise HTTPException(409, "该会话当前没有运行中的长跑")
    hub = getattr(state, "interrupt_hub", None)
    if hub is None:
        raise HTTPException(400, "打断队列未就绪")
    dropped = hub.push(body.session_id, {
        "text": text[:2000], "name": "",
        "uid": sc.uid, "ts": time.time(),
    })
    return {
        "ok": True, "loop_id": row["id"],
        "pending": hub.pending(body.session_id), "dropped": dropped,
    }


@router.get("/hub/pending")
async def loop_hub_pending(session_id: str, request: Request):
    """查询某会话待安全点消费的打断条数（会话成员可用）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    _manager(state)
    if sc.store.get(session_id) is None:
        raise HTTPException(404, "会话不存在")
    hub = getattr(state, "interrupt_hub", None)
    return {"session_id": session_id,
            "pending": hub.pending(session_id) if hub is not None else 0}
