"""角色卡与世界观 CRUD + 头像上传 + 导出/导入接口。"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..ids import InvalidName, check_safe_name
from ..lore.avatar_image import embed_avatar, materialize_avatar
from ..lore.models import Character, LoreEntry, World
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
    keys: List[str] = []
    position: str = "system"
    depth: int = 4
    insert_order: int = 100
    role: str = "system"
    probability: int = 100


def _build_entry(e) -> LoreEntry:
    return LoreEntry(
        title=e.title,
        content=e.content,
        pinned=e.pinned,
        keys=[str(k).strip() for k in (e.keys or []) if str(k or "").strip()],
        position="depth" if e.position == "depth" else "system",
        depth=int(e.depth or 4),
        insert_order=int(e.insert_order or 100),
        role=e.role if e.role in ("user", "assistant") else "system",
        probability=max(1, min(100, int(e.probability or 100))),
    )


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
    # 酒馆卡保真字段：None=调用方未提供（PUT 时保留原值；POST 时视为空），
    # 显式 []/{} 表示清空。
    regex_scripts: Optional[List[Dict[str, Any]]] = None
    extensions: Optional[Dict[str, Any]] = None
    alternate_greetings: Optional[List[str]] = None
    assets: Optional[List[Dict[str, Any]]] = None


class WorldPayload(BaseModel):
    id: str = ""
    name: str = ""
    avatar: str = ""
    content: str = ""
    table: List[LoreEntryPayload] = []
    prompt_format: str = ""
    prompt_tools: str = ""


def _build_character(payload, base: Optional[Character] = None) -> Character:
    """payload → Character。保真字段为 None 时从 base 继承（PUT 保真），
    显式 []/{} 表示清空；POST 不传 base 时 None 即空。"""
    if payload.regex_scripts is not None:
        regex_scripts = [dict(s) for s in payload.regex_scripts if isinstance(s, dict)]
    else:
        regex_scripts = list(base.regex_scripts or []) if base is not None else []
    if payload.extensions is not None:
        extensions = dict(payload.extensions)
    else:
        extensions = dict(base.extensions or {}) if base is not None else {}
    if payload.alternate_greetings is not None:
        alternate_greetings = [
            str(g) for g in payload.alternate_greetings if str(g or "").strip()
        ]
    else:
        alternate_greetings = list(base.alternate_greetings or []) if base is not None else []
    if payload.assets is not None:
        assets = [dict(a) for a in payload.assets if isinstance(a, dict)]
    else:
        assets = list(base.assets or []) if base is not None else []
    return Character(
        id=payload.id,
        name=payload.name,
        avatar=payload.avatar,
        intro=payload.intro,
        background=payload.background,
        mes_example=payload.mes_example or "",
        table=[_build_entry(e) for e in payload.table],
        attributes=[dict(a) for a in (payload.attributes or [])],
        prompt_format=payload.prompt_format or "",
        prompt_tools=payload.prompt_tools or "",
        system_prompt=payload.system_prompt or "",
        post_history_instructions=payload.post_history_instructions or "",
        first_mes=payload.first_mes or "",
        parent_id=payload.parent_id or "",
        alias=payload.alias or "",
        voice=(payload.voice or "").strip(),
        regex_scripts=copy.deepcopy(regex_scripts),
        extensions=copy.deepcopy(extensions),
        alternate_greetings=alternate_greetings,
        assets=copy.deepcopy(assets),
    )


def _build_world(payload) -> World:
    return World(
        id=payload.id,
        name=payload.name,
        avatar=payload.avatar,
        content=payload.content,
        table=[_build_entry(e) for e in payload.table],
        prompt_format=payload.prompt_format or "",
        prompt_tools=payload.prompt_tools or "",
    )


def _state(request: Request):
    """返回当前用户的数据作用域（含 .lore 与 .upload_dir）。"""
    return request.app.state.flyai.user(request)


def _max_card_bytes(request: Request) -> int:
    """角色卡导入大小上限（管理面板可调，缺省 50MB）。"""
    cfg = getattr(request.app.state.flyai, "config", None)
    lore = getattr(cfg, "lore", None)
    mb = int(getattr(lore, "max_card_mb", 50) or 50)
    return max(1, mb) * 1024 * 1024


# ---- 角色 ----

@router.get("/characters")
def list_characters(request: Request, summary: bool = False):
    lore = _state(request).lore
    chars = lore.list_characters()
    if summary:
        return {
            "characters": [c.summary(lore.character_digest(c.id)) for c in chars]
        }
    return {"characters": [c.to_dict() for c in chars]}


@router.get("/characters/{cid}")
def get_character(cid: str, request: Request):
    _safe_cid(cid)
    c = _state(request).lore.get_character(cid)
    if c is None:
        raise HTTPException(404, "角色不存在")
    return c.to_dict()


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
    body = c.to_dict()
    body["md5"] = sc.lore.character_digest(c.id)
    return body


@router.put("/characters/{cid}")
def update_character(cid: str, payload: CharacterPayload, request: Request):
    sc = _state(request)
    _safe_cid(cid)
    source = sc.lore.character_source(cid)
    if source is None:
        raise HTTPException(404, "角色不存在")
    if source == "shared":
        raise HTTPException(403, "共享角色卡不可修改，请先复制为我的角色卡")
    cur = sc.lore.get_character(cid)
    c = _build_character(payload, base=cur)
    c.id = cid
    c.author = cur.author  # 作者不可改
    c.public = cur.public
    sc.lore.save_character(c)
    # 已发布的卡：改动同步到共享目录
    if cur.public and source == "own":
        sc.lore.publish_character(c)
    body = c.to_dict()
    body["md5"] = sc.lore.character_digest(c.id)
    return body


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
    body = c.to_dict()
    body["md5"] = sc.lore.character_digest(c.id)
    return body


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
    data = c.to_dict()
    # 头像内嵌：JSON 单独拷到别的设备/账号也能带回图片；失败则只留 URL。
    image = embed_avatar(getattr(sc, "upload_dir", None), c.avatar)
    if image is not None:
        data["avatar_image"] = image
    body = {"type": "flyai-character", "version": 1, "name": c.name, "data": data}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="character-{c.id[:8]}.json"'},
    )


@router.post("/characters/import")
def import_character(payload: LoreImportPayload, request: Request):
    sc = _state(request)
    data = dict(payload.data or {})
    limit = _max_card_bytes(request)
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > limit:
        raise HTTPException(
            400, "角色卡内容过大（>%dMB）" % (limit // (1024 * 1024)))
    if not (data.get("name") or "").strip():
        raise HTTPException(400, "导入的角色缺少名称")
    blob = data.pop("avatar_image", None)
    c = Character.from_dict(data)
    c.id = uuid.uuid4().hex  # 重新生成 id 避免冲突
    # 作者/共享位由服务端决定，不信任 JSON 里的值
    c.author = sc.uid
    c.public = False
    if blob is not None:
        # 内嵌头像损坏/非法时丢弃图片，但不能留一个指向导出者目录的坏 URL
        c.avatar = materialize_avatar(
            getattr(sc, "upload_dir", None), sc.uid, blob) or ""
    sc.lore.save_character(c)
    body = c.to_dict()
    # 前端上传后立即把整卡写进本地缓存，下次打开靠 md5 命中直接读本地
    body["md5"] = sc.lore.character_digest(c.id)
    return body


# ---- 世界观 ----

@router.get("/worlds")
def list_worlds(request: Request, summary: bool = False):
    lore = _state(request).lore
    worlds = lore.list_worlds()
    if summary:
        return {"worlds": [w.summary(lore.world_digest(w.id)) for w in worlds]}
    return {"worlds": [w.to_dict() for w in worlds]}


@router.get("/worlds/{wid}")
def get_world(wid: str, request: Request):
    _safe_wid(wid)
    w = _state(request).lore.get_world(wid)
    if w is None:
        raise HTTPException(404, "世界观不存在")
    return w.to_dict()


@router.post("/worlds")
def create_world(payload: WorldPayload, request: Request):
    sc = _state(request)
    w = _build_world(payload)
    w.id = uuid.uuid4().hex
    sc.lore.save_world(w)
    body = w.to_dict()
    body["md5"] = sc.lore.world_digest(w.id)
    return body


@router.put("/worlds/{wid}")
def update_world(wid: str, payload: WorldPayload, request: Request):
    _safe_wid(wid)
    if _state(request).lore.get_world(wid) is None:
        raise HTTPException(404, "世界观不存在")
    sc = _state(request)
    w = _build_world(payload)
    w.id = wid
    sc.lore.save_world(w)
    body = w.to_dict()
    body["md5"] = sc.lore.world_digest(w.id)
    return body


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
        card = card_import.extract_card_json(
            raw, max_bytes=_max_card_bytes(request))
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
    report = card_import.import_report(c)
    # 增量双形态：保留旧顶层字段（旧前端/旧调用方直接读 name/id），
    # 同时附加 character/stats/warnings（世界书导入已有 stats 先例）。
    body = c.to_dict()
    body.update(report)
    body["character"] = c.to_dict()
    body["md5"] = sc.lore.character_digest(c.id)
    body["character"]["md5"] = body["md5"]
    return body


@router.post("/worlds/import-worldbook")
async def import_worldbook(request: Request, file: UploadFile = File(...)):
    """导入酒馆世界书 JSON（v2 entries 字典；脏 JSON 容错解析）。"""
    sc = _state(request)
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "世界书文件过大（>10MB）")
    from ..lore import tavern

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "文件不是 UTF-8 文本 JSON")
    fallback = Path(file.filename or "").stem
    try:
        w, stats = tavern.worldbook_from_text(text, name=fallback)
    except (tavern.WorldbookError, ValueError, TypeError) as e:
        raise HTTPException(400, "世界书解析失败：%s" % e)
    if not w.table:
        raise HTTPException(400, "世界书里没有可用的非空条目")
    w.id = uuid.uuid4().hex
    sc.lore.save_world(w)
    return {
        "world": w.to_dict(),
        "stats": stats,
        "md5": sc.lore.world_digest(w.id),
    }


@router.post("/worlds/import")
def import_world(payload: LoreImportPayload, request: Request):
    data = payload.data or {}
    if not (data.get("name") or "").strip():
        raise HTTPException(400, "导入的世界观缺少名称")
    sc = _state(request)
    w = World.from_dict(data)
    w.id = uuid.uuid4().hex
    sc.lore.save_world(w)
    body = w.to_dict()
    body["md5"] = sc.lore.world_digest(w.id)
    return body


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