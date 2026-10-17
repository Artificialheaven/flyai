"""角色卡与世界观 CRUD + 头像上传 + 导出/导入接口。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..ids import InvalidName, check_safe_name
from ..lore.models import Character, World
from ..user import ADMIN_UID

router = APIRouter(prefix="/api", tags=["lore"])


def _safe_cid(cid: str) -> str:
    try:
        return check_safe_name(cid, "角色卡 ID")
    except InvalidName:
        raise HTTPException(400, "角色卡 ID 非法")


def _safe_wid(wid: str) -> str:
    try:
        return check_safe_name(wid, "世界观 ID")
    except InvalidName:
        raise HTTPException(400, "世界观 ID 非法")


class LoreEntryPayload(BaseModel):
    title: str = ""
    content: str = ""
    pinned: bool = False


class CharacterPayload(BaseModel):
    id: str = ""
    name: str = ""
    avatar: str = ""
    intro: str = ""
    background: str = ""
    mes_example: str = ""
    table: List[LoreEntryPayload] = []
    attributes: List[Dict[str, Any]] = []
    prompt_format: str = ""
    prompt_tools: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    first_mes: str = ""
    parent_id: str = ""
    alias: str = ""
    voice: str = ""


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
        mes_example=payload.mes_example or "",
        table=[
            LoreEntry(title=e.title, content=e.content, pinned=e.pinned)
            for e in payload.table
        ],
        attributes=[dict(a) for a in (payload.attributes or [])],
        prompt_format=payload.prompt_format or "",
        prompt_tools=payload.prompt_tools or "",
        system_prompt=payload.system_prompt or "",
        post_history_instructions=payload.post_history_instructions or "",
        first_mes=payload.first_mes or "",
        parent_id=payload.parent_id or "",
        alias=payload.alias or "",
        voice=(payload.voice or "").strip(),
    )


def _build_world(payload) -> World:
    from ..lore.models import LoreEntry

    return World(
        id=payload.id,
        name=payload.name,
        avatar=payload.avatar,
        content=payload.content,
        table=[
            LoreEntry(title=e.title, content=e.content, pinned=e.pinned)
            for e in payload.table
        ],
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
    sc = _state(request)
    c = _build_character(payload)
    c.id = uuid.uuid4().hex
    c.author = sc.uid  # author 只能由服务端写入
    if c.parent_id:
        root = sc.lore.get_character(c.parent_id)
        if root is None:
            raise HTTPException(400, "根角色卡不存在")
        # 副本挂在自己拥有的根卡下；共享卡请先 fork
        if sc.lore.character_source(c.parent_id) != "own":
            raise HTTPException(400, "不能在共享根卡下直接建副本，请先复制为我的角色卡")
        c.parent_id = root.id
    sc.lore.save_character(c)
    return c.to_dict()


@router.put("/characters/{cid}")
def update_character(cid: str, payload: CharacterPayload, request: Request):
    sc = _state(request)
    _safe_cid(cid)
    source = sc.lore.character_source(cid)
    if source is None:
        raise HTTPException(404, "角色不存在")
    if source == "shared":
        raise HTTPException(403, "共享角色卡不可修改，请先复制为我的角色卡")
    c = _build_character(payload)
    c.id = cid
    cur = sc.lore.get_character(cid)
    c.author = cur.author  # 作者不可改
    c.public = cur.public
    sc.lore.save_character(c)
    # 已发布的卡：改动同步到共享目录
    if cur.public and source == "own":
        sc.lore.publish_character(c)
    return c.to_dict()


@router.delete("/characters/{cid}")
def delete_character(cid: str, request: Request):
    sc = _state(request)
    _safe_cid(cid)
    source = sc.lore.character_source(cid)
    if source is None:
        raise HTTPException(404, "角色不存在")
    if source == "shared":
        raise HTTPException(403, "共享角色卡不可删除")
    cur = sc.lore.get_character(cid)
    if cur.public:
        # 删除已发布的卡：连同共享副本与全部已发布副本一起清理
        for v in sc.lore.list_characters():
            if v.parent_id == cid and sc.lore.character_source(v.id) == "own":
                sc.lore.unpublish_character(v.id)
        sc.lore.unpublish_character(cid)
    if cur.parent_id:
        sc.lore.delete_character(cid)
    else:
        sc.lore.delete_character_cascade(cid)
    return {"ok": True}


@router.post("/characters/{cid}/fork")
def fork_character_to_own(cid: str, request: Request):
    """把可见（含共享）卡复制为自己的独立根角色卡：新 id、自己作者、脱离父卡。"""
    sc = _state(request)
    _safe_cid(cid)
    src = sc.lore.get_character(cid)
    if src is None:
        raise HTTPException(404, "角色不存在")
    d = src.to_dict()
    d["id"] = uuid.uuid4().hex
    d["parent_id"] = ""
    d["public"] = False
    c = Character.from_dict(d)
    c.author = sc.uid
    sc.lore.save_character(c)
    return c.to_dict()


@router.post("/characters/{cid}/publish")
def publish_character_route(cid: str, request: Request):
    """admin 发布角色卡为全体用户共享。发布根卡级联发布其全部副本；
    发布副本时自动带上根卡。"""
    sc = _state(request)
    _safe_cid(cid)
    if sc.uid != ADMIN_UID:
        raise HTTPException(403, "仅管理员可发布共享角色卡")
    target = sc.lore.get_character(cid)
    if target is None or sc.lore.character_source(cid) != "own":
        raise HTTPException(404, "角色不存在或不是自己的角色卡")

    def _publish_one(c):
        sc.lore.publish_character(c)

    if target.parent_id:
        root = sc.lore.get_character(target.parent_id)
        if root is not None:
            _publish_one(root)
        _publish_one(target)
    else:
        _publish_one(target)
        for v in sc.lore.list_characters():
            if v.parent_id == target.id and sc.lore.character_source(v.id) == "own":
                _publish_one(v)
    return {"ok": True}


@router.post("/characters/{cid}/unpublish")
def unpublish_character_route(cid: str, request: Request):
    """admin 取消发布。取消根卡级联取消其全部副本；取消副本只影响该副本。"""
    sc = _state(request)
    _safe_cid(cid)
    if sc.uid != ADMIN_UID:
        raise HTTPException(403, "仅管理员可取消共享")
    if sc.lore.character_source(cid) is None:
        raise HTTPException(404, "角色不存在")
    target = sc.lore.get_character(cid)
    if not target.parent_id:
        for v in sc.lore.list_characters():
            if v.parent_id == cid and sc.lore.character_source(v.id) == "own":
                sc.lore.unpublish_character(v.id)
    sc.lore.unpublish_character(cid)
    return {"ok": True}


class LoreImportPayload(BaseModel):
    type: str = ""
    data: dict = {}


@router.get("/characters/{cid}/export")
def export_character(cid: str, request: Request):
    sc = _state(request)
    _safe_cid(cid)
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
    _safe_wid(wid)
    if _state(request).lore.get_world(wid) is None:
        raise HTTPException(404, "世界观不存在")
    w = _build_world(payload)
    w.id = wid
    _state(request).lore.save_world(w)
    return w.to_dict()


@router.delete("/worlds/{wid}")
def delete_world(wid: str, request: Request):
    _safe_wid(wid)
    _state(request).lore.delete_world(wid)
    return {"ok": True}


@router.get("/worlds/{wid}/export")
def export_world(wid: str, request: Request):
    _safe_wid(wid)
    sc = _state(request)
    w = sc.lore.get_world(wid)
    if w is None:
        raise HTTPException(404, "世界观不存在")
    body = {"type": "flyai-world", "version": 1, "name": w.name, "data": w.to_dict()}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="world-{w.id[:8]}.json"'},
    )


@router.post("/characters/import-card")
async def import_character_card(request: Request, file: UploadFile = File(...)):
    """导入酒馆（CCv2/v3）角色卡：PNG 卡（图片即头像）或 JSON 卡。"""
    sc = _state(request)
    raw = await file.read()
    from ..lore import card_import

    try:
        card = card_import.extract_card_json(raw)
        c = card_import.character_from_cc(card)
    except card_import.CardImportError as e:
        raise HTTPException(400, str(e))

    c.id = uuid.uuid4().hex
    c.author = sc.uid
    if raw.startswith(card_import.PNG_SIGNATURE):
        fname = f"{uuid.uuid4().hex}.png"
        (sc.upload_dir / fname).write_bytes(raw)
        c.avatar = f"/uploads/{sc.uid}/{fname}"
    sc.lore.save_character(c)
    return c.to_dict()


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
    return {"url": f"/uploads/{state.uid}/{fname}"}