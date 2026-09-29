"""Beta 多 AI 群聊：房间（Session）参与者与窗口管理。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from .routes_session import _session_dict


router = APIRouter(prefix="/api", tags=["room"])


class RoomCreate(BaseModel):
    title: Optional[str] = None
    character_ids: List[str] = []
    world_id: Optional[str] = None


class GroupCreate(BaseModel):
    member_ids: List[str] = []
    title: Optional[str] = None


class MemberAdd(BaseModel):
    character_id: str = ""


class TurnSwitch(BaseModel):
    # 历史版本切换：二选一或都给（先切 edit 再在该 edit 内切 reply）
    edit_index: Optional[int] = None
    reply_index: Optional[int] = None


class MemberUpdate(BaseModel):
    memory: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    override: Optional[Dict[str, Any]] = None
    # R8：设为用户扮演的自设角色卡（用户座位）；空串清除
    character_id: Optional[str] = None


def _must_get(sc, rid):
    s = sc.store.get(rid)
    if s is None:
        raise HTTPException(404, "会话/房间不存在")
    return s


@router.post("/rooms")
def create_room(body: RoomCreate, request: Request):
    """建房间：每个 AI 角色自动获得一条私聊窗口。"""
    sc = request.app.state.flyai.user(request)
    from ..sessions.models import RoomMember, Thread

    s = sc.store.create(title=body.title or "新会话")
    if body.world_id:
        if body.world_id and not sc.lore.get_world(body.world_id):
            raise HTTPException(400, "世界观不存在")
        s.world_id = body.world_id
    s.members.append(RoomMember(name="你", is_user=True))  # 用户座位
    for cid in (body.character_ids or []):
        ch = sc.lore.get_character(cid)
        if ch is None:
            raise HTTPException(400, f"角色不存在: {cid}")
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    sc.store.save(s)
    return _session_dict(s, with_messages=True, lore=sc.lore)


@router.post("/rooms/{rid}/members")
def add_member(rid: str, body: MemberAdd, request: Request):
    sc = request.app.state.flyai.user(request)
    s = _must_get(sc, rid)
    ch = sc.lore.get_character(body.character_id)
    if ch is None:
        raise HTTPException(400, "角色不存在")
    from ..sessions.models import RoomMember, Thread

    m = RoomMember(name=ch.name, character_id=ch.id)
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    sc.store.save(s)
    return m.to_dict()


@router.patch("/rooms/{rid}/members/{mid}")
def update_member(rid: str, mid: str, body: MemberUpdate, request: Request):
    sc = request.app.state.flyai.user(request)
    s = _must_get(sc, rid)
    m = s.member(mid)
    if m is None:
        raise HTTPException(404, "参与者不存在")
    if body.memory is not None:
        m.memory = body.memory
    if body.attributes is not None:
        m.attributes = body.attributes
    if body.override is not None:
        m.override = body.override
    if body.character_id is not None:
        # 设/改用户扮演的自设：校验角色存在；空串表示清除
        if body.character_id and not sc.lore.get_character(body.character_id):
            raise HTTPException(400, "角色不存在")
        m.character_id = body.character_id
        # 换卡时清掉旧的会话专属副本，避免遮蔽库卡
        m.override = None
    sc.store.save(s)
    return m.to_dict()


@router.post("/rooms/{rid}/groups")
def create_group(rid: str, body: GroupCreate, request: Request):
    """把多个 AI 放进同一窗口，成组聊天。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get(sc, rid)
    ais = []
    for mid in (body.member_ids or []):
        m = s.member(mid)
        if m is None or m.is_user:
            raise HTTPException(400, "请选择 AI 参与者")
        ais.append(m)
    if not ais:
        raise HTTPException(400, "至少选择一个 AI 参与者")
    from ..sessions.models import Thread

    t = Thread(
        kind="group",
        title=body.title or ("群聊·" + "、".join(m.name for m in ais)),
        member_ids=[m.id for m in ais],
    )
    s.threads.append(t)
    sc.store.save(s)
    return t.to_dict()


@router.get("/rooms/{rid}/threads/{tid}")
def get_thread(rid: str, tid: str, request: Request):
    sc = request.app.state.flyai.user(request)
    s = _must_get(sc, rid)
    t = s.thread(tid)
    if t is None:
        raise HTTPException(404, "窗口不存在")
    return t.to_dict()


@router.patch("/rooms/{rid}/threads/{tid}/turns/{anchor}")
def switch_turn(rid: str, tid: str, anchor: str, body: TurnSwitch, request: Request):
    """切换某轮的历史 edit / reply 版本，不触发模型。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get(sc, rid)
    t = s.thread(tid)
    if t is None:
        raise HTTPException(404, "窗口不存在")
    if t.kind != "dm":
        raise HTTPException(400, "群聊窗口没有历史版本")
    if anchor not in t.edits:
        raise HTTPException(404, "该轮没有历史版本")
    if body.edit_index is None and body.reply_index is None:
        raise HTTPException(400, "未指定要切换的版本序号")
    from ..sessions import versions

    try:
        versions.switch_version(
            t, anchor, edit_index=body.edit_index, reply_index=body.reply_index
        )
    except (IndexError, KeyError):
        raise HTTPException(400, "版本序号无效")
    sc.store.save(s)
    return t.to_dict()