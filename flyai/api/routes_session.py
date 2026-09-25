"""会话 CRUD、历史与 R7 会话副本 / 导出接口。"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, Optional

from ..sessions.resolve import (
    clear_character_override,
    effective_character,
    effective_world,
    fork_character,
    is_character_scoped,
    set_character_override,
    set_world_override,
)

router = APIRouter(prefix="/api", tags=["session"])


class SessionCreate(BaseModel):
    title: Optional[str] = None
    provider_id: Optional[str] = None
    character_id: Optional[str] = None
    world_id: Optional[str] = None


class SessionUpdate(BaseModel):
    character_id: Optional[str] = None
    world_id: Optional[str] = None


@router.get("/sessions")
def list_sessions(request: Request):
    sc = request.app.state.flyai.user(request)
    out = []
    for s in sc.store.list():
        d = _session_dict(s)
        d["message_count"] = len(s.messages)
        out.append(d)
    return {"sessions": out}


@router.post("/sessions")
def create_session(req: SessionCreate, request: Request):
    sc = request.app.state.flyai.user(request)
    s = sc.store.create(
        title=req.title or "新会话",
        provider_id=req.provider_id or "",
        character_id=req.character_id or "",
        world_id=req.world_id or "",
    )
    return _session_dict(s)


@router.patch("/sessions/{sid}")
def update_session(sid: str, req: SessionUpdate, request: Request):
    """切换会话绑定的角色/世界观（角色扮演开关）。"""
    sc = request.app.state.flyai.user(request)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    # 校验存在性
    if req.character_id is not None and req.character_id and not sc.lore.get_character(req.character_id):
        raise HTTPException(400, "角色不存在")
    if req.world_id is not None and req.world_id and not sc.lore.get_world(req.world_id):
        raise HTTPException(400, "世界观不存在")
    if req.character_id is not None:
        s.character_id = req.character_id
        # 重新绑定主卡时清掉旧会话副本，避免遮蔽新卡
        s.character_override = None
    if req.world_id is not None:
        s.world_id = req.world_id
        s.world_override = None
    sc.store.save(s)
    return _session_dict(s)


@router.get("/sessions/{sid}")
def get_session(sid: str, request: Request):
    sc = request.app.state.flyai.user(request)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    return _session_dict(s, with_messages=True, lore=sc.lore)


@router.delete("/sessions/{sid}")
def delete_session(sid: str, request: Request):
    sc = request.app.state.flyai.user(request)
    sc.store.delete(sid)
    return {"ok": True}


@router.post("/sessions/{sid}/clear")
def clear_session(sid: str, request: Request):
    sc = request.app.state.flyai.user(request)
    if sc.store.get(sid) is None:
        raise HTTPException(404, "会话不存在")
    sc.store.clear(sid)
    return {"ok": True}


# ---- R7：会话专属角色副本 ----

class CharacterBody(BaseModel):
    character: Dict = {}


def _must_get_session(sc, sid):
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    return s


@router.post("/sessions/{sid}/character/fork")
def session_fork(sid: str, request: Request):
    """把当前有效角色复制为会话专属副本（不碰库主卡）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    try:
        copy = fork_character(s, sc.lore)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    sc.store.save(s)
    return {"character": copy.to_dict(), "scoped": True}


@router.put("/sessions/{sid}/character")
def session_set_character(sid: str, body: CharacterBody, request: Request):
    """保存会话专属角色（副本态编辑后写回）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    from ..lore.models import Character

    set_character_override(s, Character.from_dict(body.character or {}))
    sc.store.save(s)
    eff = effective_character(s, sc.lore)
    return {"character": eff.to_dict() if eff else None, "scoped": True}


@router.delete("/sessions/{sid}/character/override")
def session_clear_override(sid: str, request: Request):
    """还原为库主卡（删除会话副本）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    clear_character_override(s)
    sc.store.save(s)
    eff = effective_character(s, sc.lore)
    return {"ok": True, "character": eff.to_dict() if eff else None, "scoped": False}


@router.post("/sessions/{sid}/character/publish")
def session_publish(sid: str, request: Request):
    """把会话所用角色发布为新角色卡（供其他对话用）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    eff = effective_character(s, sc.lore)
    if eff is None:
        raise HTTPException(400, "会话未绑定角色")
    new = eff.to_dict()
    new["id"] = uuid.uuid4().hex
    from ..lore.models import Character

    sc.lore.save_character(Character.from_dict(new))
    return {"character": new}


@router.get("/sessions/{sid}/character/export")
def session_character_export(sid: str, request: Request):
    """将会话所用的角色以其标准卡格式下载。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    eff = effective_character(s, sc.lore)
    if eff is None:
        raise HTTPException(404, "会话未绑定角色")
    body = {"type": "flyai-character", "version": 1, "name": eff.name, "data": eff.to_dict()}
    head = eff.id[:8] or "scoped"
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="character-{head}.json"'},
    )


@router.get("/sessions/{sid}/export")
def session_export(sid: str, request: Request):
    """导出对话包：历史消息 + 所用角色卡 + 世界观。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    char = effective_character(s, sc.lore)
    world = effective_world(s, sc.lore)
    data = {
        "session": {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at, "attributes": s.attributes},
        "character": char.to_dict() if char else None,
        "world": world.to_dict() if world else None,
        "messages": [m.to_dict() for m in s.messages],
    }
    body = {"type": "flyai-conversation", "version": 1, "name": s.title, "data": data}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="conversation-{s.id[:8]}.json"'},
    )


@router.post("/sessions/{sid}/compress")
async def session_compress(sid: str, request: Request):
    """AI 压缩对话历史进角色卡与世界观（自动分类），写入会话副本后返回。"""
    from ..lore.gen import compress_conversation

    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get_session(sc, sid)
    char = effective_character(s, sc.lore)
    world = effective_world(s, sc.lore)
    if char is None:
        raise HTTPException(400, "会话未绑定角色，无法压缩")
    try:
        new_char, new_world = await compress_conversation(state, s, char, world)
    except Exception as exc:
        raise HTTPException(500, f"压缩失败: {exc}")
    # 写入会话副本（隔离主卡）
    set_character_override(s, new_char)
    if new_world is not None and (new_world.content or new_world.table):
        set_world_override(s, new_world)
    sc.store.save(s)
    return {
        "character": new_char.to_dict(),
        "world": new_world.to_dict() if new_world else None,
        "scoped": True,
    }


def _session_dict(s, with_messages: bool = False, lore=None):
    d = {
        "id": s.id,
        "title": s.title,
        "provider_id": s.provider_id,
        "character_id": s.character_id,
        "world_id": s.world_id,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "attributes": s.attributes,
        "scoped": is_character_scoped(s),
        "members": [m.to_dict() for m in s.members],
        "threads": [t.to_dict() for t in s.threads],
    }
    if with_messages:
        d["messages"] = [m.to_dict() for m in s.messages]
        if lore is not None:
            char = effective_character(s, lore)
            world = effective_world(s, lore)
            d["character"] = char.to_dict() if char else None
            d["world"] = world.to_dict() if world else None
    return d