"""公共市场：发布 / 浏览 / 安装角色卡、世界观、模型预设与历史对话快照。

- 免审：发布立即可见，admin 可事后删除任意条目。
- 复制式使用：安装即复制为安装者的私有副本 / 新会话，与发布者不再关联。
- 等级门槛 min_level：不达标者列表里完全看不到、详情返回 404。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from .. import transfer
from ..lore.models import Character, World
from ..preset.models import Preset
from ..preset.tavern import preset_to_envelope
from ..market.models import KINDS, MarketEntry
from ..market.store import now_stamp
from ..user import ADMIN_UID
from .routes_session import _build_export_data, install_session_zip

router = APIRouter(prefix="/api/market", tags=["market"])


def _state(request):
    return request.app.state.flyai


def _is_admin(sc) -> bool:
    return sc.uid == ADMIN_UID


def _author_name(state, uid: str) -> str:
    """发布者显示名：发布时快照，改名不影响历史条目。"""
    if uid == ADMIN_UID:
        return state.config.auth.admin.username
    u = state.user_store.get_by_id(uid)
    return u.name if u else uid


class PublishBody(BaseModel):
    kind: str
    source_id: str
    title: Optional[str] = None
    intro: str = ""
    cover: str = ""
    min_level: int = 0


class EditBody(BaseModel):
    title: Optional[str] = None
    intro: Optional[str] = None
    cover: Optional[str] = None
    min_level: Optional[int] = None


def _validate_level(value: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, "最低等级必须是数字")
    if value < 0 or value > 256:
        raise HTTPException(400, "最低等级需在 0~256 之间")
    return value


# ---- 发布快照 ----

def _snapshot_payload(state, sc, kind: str, source_id: str):
    """从发布者的原对象拍快照，返回 (payload dict, zip bytes|None, cover)。

    角色卡 / 世界观只允许发布自己的；历史对话导出 v3 信封 + 自包含 zip。
    """
    if kind == "character":
        c = sc.lore.get_character(source_id)
        if c is None or sc.lore.character_source(source_id) != "own":
            raise HTTPException(400, "角色卡不存在或不是你的卡，无法发布")
        payload = {"type": "flyai-character", "version": 1,
                   "name": c.name, "data": c.to_dict()}
        return payload, None, c.avatar

    if kind == "world":
        w = sc.lore.get_world(source_id)
        if w is None:
            raise HTTPException(400, "世界观不存在，无法发布")
        payload = {"type": "flyai-world", "version": 1,
                   "name": w.name, "data": w.to_dict()}
        return payload, None, w.avatar

    if kind == "preset":
        p = getattr(sc, "presets", None)
        if p is None:
            raise HTTPException(400, "预设库不可用")
        preset = p.get(source_id)
        if preset is None:
            raise HTTPException(400, "预设不存在，无法发布")
        return preset_to_envelope(preset), None, ""

    # session
    s = sc.store.get(source_id)
    if s is None:
        raise HTTPException(400, "会话不存在，无法发布")
    data = _build_export_data(sc, s)
    data["session"]["mode"] = s.mode or "normal"
    data["galgame"] = s.galgame or {}
    data["galgame_tts"] = s.galgame_tts or {}
    payload = {"type": "flyai-conversation", "version": 3,
               "name": s.title, "data": data}
    try:
        raw_zip = transfer.build_export_zip(payload, sc.uid, Path(sc.upload_dir))
    except transfer.TransferError as exc:
        raise HTTPException(400, str(exc))
    return payload, raw_zip, ""


def _write_snapshot(market, mid: str, kind: str, payload, raw_zip) -> None:
    market.write_payload(mid, payload, "json")
    if kind == "session":
        market.write_payload(mid, raw_zip, "zip")


@router.post("/publish")
def publish(body: PublishBody, request: Request):
    state = _state(request)
    sc = state.user(request)
    if body.kind not in KINDS:
        raise HTTPException(400, "类型必须是 character / world / preset / session")
    source_id = (body.source_id or "").strip()
    if not source_id:
        raise HTTPException(400, "缺少要发布的对象 id")
    min_level = _validate_level(body.min_level)

    # 同一对象不允许重复发布；更新内容走 /refresh
    for e in state.market.list(kind=body.kind, author_uid=sc.uid):
        if e.source_id == source_id:
            raise HTTPException(400, "该内容已经发布过，可在「我发布的」里更新或下架")

    payload, raw_zip, default_cover = _snapshot_payload(state, sc, body.kind, source_id)

    title = (body.title or "").strip() or payload.get("name") or "未命名"
    e = MarketEntry(
        kind=body.kind,
        source_id=source_id,
        title=title[:60],
        intro=(body.intro or "").strip()[:500],
        cover=(body.cover or default_cover or ""),
        author_uid=sc.uid,
        author_name=_author_name(state, sc.uid),
        min_level=min_level,
    )
    stamp = now_stamp()
    e.created_at = e.updated_at = stamp
    _write_snapshot(state.market, e.id, body.kind, payload, raw_zip)
    state.market.save_meta(e)
    return e.to_dict()


# ---- 浏览 ----

@router.get("/entries")
def list_entries(request: Request, kind: Optional[str] = None,
                 q: Optional[str] = None, mine: int = 0):
    state = _state(request)
    sc = state.user(request)
    if kind and kind not in KINDS:
        raise HTTPException(400, "类型必须是 character / world / preset / session")
    if mine:
        entries = state.market.list(kind=kind or None, q=q or None, author_uid=sc.uid)
    else:
        # admin 不受门槛限制；普通用户只拿到达标条目
        viewer_level = None if _is_admin(sc) else sc.level
        entries = state.market.list(kind=kind or None, q=q or None,
                                    viewer_level=viewer_level)
    return {"entries": [e.to_dict() for e in entries]}


def _visible_entry(state, sc, mid: str) -> MarketEntry:
    e = state.market.get(mid)
    if e is None:
        raise HTTPException(404, "市场条目不存在")
    if not _is_admin(sc) and e.min_level > sc.level:
        # 不达标与不存在同样回 404，不泄露条目存在
        raise HTTPException(404, "市场条目不存在")
    return e


@router.get("/entries/{mid}")
def get_entry(mid: str, request: Request):
    return _visible_entry(_state(request), _state(request).user(request), mid).to_dict()


# ---- 安装 ----

def _copy_author_assets(data: dict, author_uid: str, sc) -> dict:
    """把快照里引用的作者上传文件复制进安装者目录，并把 URL 改写为安装者。

    磁盘上缺失的文件跳过（URL 留悬空，与迁移包行为一致）。作者安装自己的
    发布内容时原样返回。
    """
    if not author_uid or author_uid == sc.uid:
        return data
    uploads_root = Path(sc.upload_dir).parent
    author_root = uploads_root / author_uid
    my_root = Path(sc.upload_dir)
    author_base = transfer._url_prefix(author_uid)
    my_base = transfer._url_prefix(sc.uid)

    text = json.dumps(data, ensure_ascii=False)
    for url in transfer._collect_urls(data, author_uid):
        rel = url[len(author_base):]
        src = author_root / rel
        if not src.is_file():
            continue
        try:  # 双保险：源 / 目标都不得越出各自根目录
            src.resolve().relative_to(author_root.resolve())
            dst = my_root / rel
            dst.resolve().relative_to(my_root.resolve())
        except (OSError, ValueError):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        text = text.replace(url, my_base + rel)
    return json.loads(text)


@router.post("/entries/{mid}/install")
def install_entry(mid: str, request: Request):
    state = _state(request)
    sc = state.user(request)
    e = _visible_entry(state, sc, mid)
    payload = state.market.read_payload_json(mid)
    if payload is None:
        raise HTTPException(404, "条目内容缺失，可能已损坏")

    result = {}
    if e.kind == "character":
        data = _copy_author_assets(payload.get("data") or {}, e.author_uid, sc)
        card = Character.from_dict(data)
        card.id = uuid.uuid4().hex   # 复制为自己的私有根卡，与原卡脱离
        card.parent_id = ""
        card.public = False
        card.author = sc.uid
        sc.lore.save_character(card)
        result = {"character": card.to_dict()}

    elif e.kind == "world":
        data = _copy_author_assets(payload.get("data") or {}, e.author_uid, sc)
        w = World.from_dict(data)
        w.id = uuid.uuid4().hex
        sc.lore.save_world(w)
        result = {"world": w.to_dict()}

    elif e.kind == "preset":
        if getattr(sc, "presets", None) is None:
            raise HTTPException(400, "预设库不可用")
        preset = Preset.from_dict(payload.get("data") or {})
        preset.id = uuid.uuid4().hex   # 安装为自己的私有副本
        sc.presets.save(preset)
        result = {"preset": preset.to_dict()}

    else:
        raw_zip = state.market.read_payload_bytes(mid, "zip")
        if raw_zip is None:
            raise HTTPException(404, "条目内容缺失，可能已损坏")
        result = install_session_zip(sc, raw_zip)

    e.installs += 1
    state.market.save_meta(e)
    result["entry"] = e.to_dict()
    return result


# ---- 发布者管理 ----

def _must_own_entry(state, sc, mid: str) -> MarketEntry:
    e = state.market.get(mid)
    if e is None:
        raise HTTPException(404, "市场条目不存在")
    if e.author_uid != sc.uid:
        raise HTTPException(403, "只有发布者可以操作该条目")
    return e


@router.put("/entries/{mid}")
def edit_entry(mid: str, body: EditBody, request: Request):
    state = _state(request)
    sc = state.user(request)
    e = _must_own_entry(state, sc, mid)
    if body.title is not None:
        title = body.title.strip()
        if title:
            e.title = title[:60]
    if body.intro is not None:
        e.intro = body.intro.strip()[:500]
    if body.cover is not None:
        e.cover = body.cover.strip()
    if body.min_level is not None:
        e.min_level = _validate_level(body.min_level)
    e.updated_at = now_stamp()
    state.market.save_meta(e)
    return e.to_dict()


@router.post("/entries/{mid}/refresh")
def refresh_entry(mid: str, request: Request):
    """从发布者当前对象重新拍快照（revision +1）；源对象已删除则拒绝。"""
    state = _state(request)
    sc = state.user(request)
    e = _must_own_entry(state, sc, mid)
    payload, raw_zip, _cover = _snapshot_payload(state, sc, e.kind, e.source_id)
    _write_snapshot(state.market, e.id, e.kind, payload, raw_zip)
    e.revision += 1
    e.updated_at = now_stamp()
    state.market.save_meta(e)
    return e.to_dict()


@router.delete("/entries/{mid}")
def delete_entry(mid: str, request: Request):
    state = _state(request)
    sc = state.user(request)
    e = state.market.get(mid)
    if e is None:
        raise HTTPException(404, "市场条目不存在")
    if e.author_uid != sc.uid and not _is_admin(sc):
        raise HTTPException(403, "只有发布者本人或管理员可以下架")
    state.market.remove(mid)
    return {"ok": True}
