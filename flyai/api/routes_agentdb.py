"""P4 生态扩展管理路由：MCP server、watcher、提示词分区（全部 admin 门禁）。

MCP 配置与提示词覆盖集中存 admin 的 agent.db；watcher 登记按调用者 uid
分库，管理员可带 uid 参数代管。同步 DAL 统一走 asyncio.to_thread，
路由函数保持可被测试用 SimpleNamespace 假 request 直接调用。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, validator

from ..mcp_client.stdio import valid_server_id
from ..user import ADMIN_UID

router = APIRouter(prefix="/api/agentdb", tags=["agentdb"])


def _require_admin(sc) -> None:
    if sc.uid != ADMIN_UID:
        raise HTTPException(403, "需要管理员权限")


def _state(request: Request):
    return request.app.state.flyai


# ---------- 请求体 ----------

class McpServerBody(BaseModel):
    server_id: str
    command: str
    args: List[str] = []
    env: Dict[str, str] = {}
    enabled: bool = True

    @validator("server_id")
    def _id_rule(cls, v):  # noqa: N805
        v = (v or "").strip()
        if not valid_server_id(v):
            raise ValueError(
                "server_id 必须匹配 ^[a-z][a-z0-9_]{0,31}$"
            )
        return v

    @validator("command")
    def _cmd_rule(cls, v):  # noqa: N805
        v = (v or "").strip()
        if not v:
            raise ValueError("command 不能为空")
        return v

    @validator("args")
    def _args_rule(cls, v):  # noqa: N805
        if not isinstance(v, list):
            raise ValueError("args 必须是字符串数组")
        return [str(a) for a in v][:64]

    @validator("env")
    def _env_rule(cls, v):  # noqa: N805
        if not isinstance(v, dict):
            raise ValueError("env 必须是字符串对象")
        return {str(k): str(val) for k, val in list(v.items())[:64]}


class McpUpdateBody(BaseModel):
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None


class McpEnabledBody(BaseModel):
    enabled: bool


class WatcherStopBody(BaseModel):
    id: int
    uid: Optional[str] = None


class PromptBody(BaseModel):
    text: str

    @validator("text")
    def _text_rule(cls, v):  # noqa: N805
        if not (v or "").strip():
            raise ValueError("text 不能为空")
        return v[:8000]


# ---------- MCP server ----------

@router.get("/mcp/servers")
async def mcp_list(request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(400, "MCP 未就绪")
    rows = await asyncio.to_thread(manager.store.list, ADMIN_UID)
    return {"items": rows}


@router.post("/mcp/servers")
async def mcp_create(body: McpServerBody, request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(400, "MCP 未就绪")
    try:
        row = await asyncio.to_thread(
            manager.store.create,
            ADMIN_UID, body.server_id, body.command,
            body.args, body.env, body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@router.put("/mcp/servers/{server_id}")
async def mcp_update(server_id: str, body: McpUpdateBody, request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(400, "MCP 未就绪")
    try:
        row = await asyncio.to_thread(
            manager.store.update,
            ADMIN_UID, server_id,
            command=body.command, args=body.args,
            env=body.env, enabled=body.enabled,
        )
    except KeyError:
        raise HTTPException(404, "server 不存在")
    # 连接参数变更后关掉旧连接，下次调用懒重连
    old = manager._clients.pop(server_id, None)
    if old is not None:
        with contextlib.suppress(Exception):
            await old.close()
    return {"ok": True, "item": row}


@router.post("/mcp/servers/{server_id}/enabled")
async def mcp_set_enabled(server_id: str, body: McpEnabledBody,
                          request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(400, "MCP 未就绪")
    try:
        row = await asyncio.to_thread(
            manager.store.set_enabled, ADMIN_UID, server_id, body.enabled
        )
    except KeyError:
        raise HTTPException(404, "server 不存在")
    if not body.enabled:
        old = manager._clients.pop(server_id, None)
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close()
    return {"ok": True, "item": row}


@router.post("/mcp/servers/{server_id}/test")
async def mcp_test(server_id: str, request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(400, "MCP 未就绪")
    result = await manager.test_connection(server_id)
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "连接失败")
    return {"ok": True, "tools": result.get("tools") or []}


@router.delete("/mcp/servers/{server_id}")
async def mcp_delete(server_id: str, request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(400, "MCP 未就绪")
    old = manager._clients.pop(server_id, None)
    if old is not None:
        with contextlib.suppress(Exception):
            await old.close()
    ok = await asyncio.to_thread(
        manager.store.delete, ADMIN_UID, server_id
    )
    if not ok:
        raise HTTPException(404, "server 不存在")
    return {"ok": True}


# ---------- watcher ----------

@router.get("/watchers")
async def watcher_list(request: Request, uid: Optional[str] = None,
                       session_id: Optional[str] = None):
    state = _state(request)
    sc = state.user(request)
    target = (uid or "").strip() or sc.uid
    if target != sc.uid:
        _require_admin(sc)
    manager = getattr(state, "watcher_manager", None)
    if manager is None:
        raise HTTPException(400, "watcher 未就绪")
    rows = await asyncio.to_thread(
        manager.list, target, session_id
    )
    return {"items": rows}


@router.post("/watchers/stop")
async def watcher_stop(body: WatcherStopBody, request: Request):
    state = _state(request)
    sc = state.user(request)
    target = (body.uid or "").strip() or sc.uid
    if target != sc.uid:
        _require_admin(sc)
    manager = getattr(state, "watcher_manager", None)
    if manager is None:
        raise HTTPException(400, "watcher 未就绪")
    try:
        row = await asyncio.to_thread(manager.stop, target, body.id)
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": row}


@router.post("/watchers/reconcile")
async def watcher_reconcile(request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    manager = getattr(state, "watcher_manager", None)
    if manager is None:
        raise HTTPException(400, "watcher 未就绪")
    changed = await asyncio.to_thread(manager.reconcile_all)
    return {"ok": True, "stopped": changed}


# ---------- 提示词分区 ----------

@router.get("/prompts")
async def prompt_list(request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    registry = getattr(state, "prompt_registry", None)
    if registry is None:
        raise HTTPException(400, "提示词注册表未就绪")
    return {"items": await asyncio.to_thread(registry.describe)}


@router.put("/prompts/{key}")
async def prompt_set(key: str, body: PromptBody, request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    registry = getattr(state, "prompt_registry", None)
    if registry is None:
        raise HTTPException(400, "提示词注册表未就绪")
    try:
        await asyncio.to_thread(registry.set_override, key, body.text)
    except KeyError:
        raise HTTPException(404, "未知提示词分区")
    return {"ok": True}


@router.delete("/prompts/{key}")
async def prompt_reset(key: str, request: Request):
    state = _state(request)
    sc = state.user(request)
    _require_admin(sc)
    registry = getattr(state, "prompt_registry", None)
    if registry is None:
        raise HTTPException(400, "提示词注册表未就绪")
    try:
        ok = await asyncio.to_thread(registry.reset_override, key)
    except KeyError:
        raise HTTPException(404, "未知提示词分区")
    return {"ok": True, "reset": ok}
