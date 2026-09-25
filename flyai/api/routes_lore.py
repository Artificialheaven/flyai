"""角色卡与世界观 CRUD + 头像上传 + 导出/导入接口。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..lore.models import Character, World

router = APIRouter(prefix="/api", tags=["lore"])


class LoreEntryPayload(BaseModel):
    title: str = ""
    content: str = ""


class CharacterPayload(BaseModel):
    id: str = ""
    name: str = ""
    avatar: str = ""
    intro: str = ""
    background: str = ""
    table: List[LoreEntryPayload] = []
    attributes: List[Dict[str, Any]] = []
    prompt_format: str = ""
    prompt_tools: str = ""


class WorldPayload(BaseModel):
    id: str = ""
    name: str = ""
    avatar: str = ""
    content: str = ""
    table: List[LoreEntryPayload] = []
    prompt_format: str = ""
    prompt_tools: str = ""


def _build_character(payload) -> Character:
    from ..lore.models import LoreEntry

    return Character(
        id=payload.id,
        name=payload.name,
        avatar=payload.avatar,
        intro=payload.intro,
        background=payload.background,
        table=[LoreEntry(title=e.title, content=e.content) for e in payload.table],
        attributes=[dict(a) for a in (payload.attributes or [])],
        prompt_format=payload.prompt_format or "",
        prompt_tools=payload.prompt_tools or "",
    )


def _build_world(payload) -> World:
    from ..lore.models import LoreEntry

    return World(
        id=payload.id,
        name=payload.name,
        avatar=payload.avatar,
        content=payload.content,
        table=[LoreEntry(title=e.title, content=e.content) for e in payload.table],
        prompt_format=payload.prompt_format or "",
        prompt_tools=payload.prompt_tools or "",
    )


def _state(request: Request):
    """返回当前用户的数据作用域（含 .lore 与 .upload_dir）。"""
    return request.app.state.flyai.user(request)


# ---- 角色 ----

@router.get("/characters")
def list_characters(request: Request):
    return {"characters": [c.to_dict() for c in _state(request).lore.list_characters()]}


@router.post("/characters")
def create_character(payload: CharacterPayload, request: Request):
    c = _build_character(payload)
    c.id = uuid.uuid4().hex
    _state(request).lore.save_character(c)
    return c.to_dict()


@router.put("/characters/{cid}")
def update_character(cid: str, payload: CharacterPayload, request: Request):
    if _state(request).lore.get_character(cid) is None:
        raise HTTPException(404, "角色不存在")
    c = _build_character(payload)
    c.id = cid
    _state(request).lore.save_character(c)
    return c.to_dict()


@router.delete("/characters/{cid}")
def delete_character(cid: str, request: Request):
    _state(request).lore.delete_character(cid)
    return {"ok": True}


class LoreImportPayload(BaseModel):
    type: str = ""
    data: dict = {}


@router.get("/characters/{cid}/export")
def export_character(cid: str, request: Request):
    sc = _state(request)
    c = sc.lore.get_character(cid)
    if c is None:
        raise HTTPException(404, "角色不存在")
    body = {"type": "flyai-character", "version": 1, "name": c.name, "data": c.to_dict()}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="character-{c.id[:8]}.json"'},
    )


@router.post("/characters/import")
def import_character(payload: LoreImportPayload, request: Request):
    data = payload.data or {}
    if not (data.get("name") or "").strip():
        raise HTTPException(400, "导入的角色缺少名称")
    c = Character.from_dict(data)
    c.id = uuid.uuid4().hex  # 重新生成 id 避免冲突
    _state(request).lore.save_character(c)
    return c.to_dict()


# ---- 世界观 ----

@router.get("/worlds")
def list_worlds(request: Request):
    return {"worlds": [w.to_dict() for w in _state(request).lore.list_worlds()]}


@router.post("/worlds")
def create_world(payload: WorldPayload, request: Request):
    w = _build_world(payload)
    w.id = uuid.uuid4().hex
    _state(request).lore.save_world(w)
    return w.to_dict()


@router.put("/worlds/{wid}")
def update_world(wid: str, payload: WorldPayload, request: Request):
    if _state(request).lore.get_world(wid) is None:
        raise HTTPException(404, "世界观不存在")
    w = _build_world(payload)
    w.id = wid
    _state(request).lore.save_world(w)
    return w.to_dict()


@router.delete("/worlds/{wid}")
def delete_world(wid: str, request: Request):
    _state(request).lore.delete_world(wid)
    return {"ok": True}


@router.get("/worlds/{wid}/export")
def export_world(wid: str, request: Request):
    sc = _state(request)
    w = sc.lore.get_world(wid)
    if w is None:
        raise HTTPException(404, "世界观不存在")
    body = {"type": "flyai-world", "version": 1, "name": w.name, "data": w.to_dict()}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="world-{w.id[:8]}.json"'},
    )


@router.post("/worlds/import")
def import_world(payload: LoreImportPayload, request: Request):
    data = payload.data or {}
    if not (data.get("name") or "").strip():
        raise HTTPException(400, "导入的世界观缺少名称")
    w = World.from_dict(data)
    w.id = uuid.uuid4().hex
    _state(request).lore.save_world(w)
    return w.to_dict()


# ---- 头像上传 ----

_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@router.post("/upload")
async def upload_avatar(request: Request, file: UploadFile = File(...)):
    state = _state(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(400, "仅支持图片格式: png/jpg/jpeg/gif/webp")
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = state.upload_dir / fname
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "图片过大（>5MB）")
    dest.write_bytes(content)
    return {"url": f"/uploads/{fname}"}