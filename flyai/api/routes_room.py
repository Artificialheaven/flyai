"""Beta 多 AI 群聊：房间（Session）参与者与窗口管理。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..ids import InvalidName, check_safe_name
from .routes_session import _session_dict


router = APIRouter(prefix="/api", tags=["room"])


def _safe_room_id(rid: str) -> str:
    try:
        return check_safe_name(rid, "房间 ID")
    except InvalidName:
        raise HTTPException(400, "房间 ID 非法")


class RoomCreate(BaseModel):
    title: Optional[str] = None
    character_ids: List[str] = []
    world_id: Optional[str] = None


class GroupCreate(BaseModel):
    member_ids: List[str] = []
    title: Optional[str] = None


class MemberAdd(BaseModel):
    character_id: str = ""
    # 加入时即可设置座位专属 provider / 记忆挂载（也可加入后再 PATCH）
    provider_id: Optional[str] = None
    memory_mode: Optional[str] = None


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
    # 座位专属 LLM 提供商；空串=用房间默认
    provider_id: Optional[str] = None
    # 角色记忆簿挂载：""=不挂载；"rw"=可读写；"ro"=只读
    memory_mode: Optional[str] = None
    # 记忆簿归属角色 id；空串=用本座位自己的角色 id（挂别的卡的记忆簿时填）
    memory_char_id: Optional[str] = None


class ThreadUpdate(BaseModel):
    title: Optional[str] = None


def _must_get(sc, rid):
    _safe_room_id(rid)
    s = sc.store.get(rid)
    if s is None:
        raise HTTPException(404, "会话/房间不存在")
    return s


def _active_run_409(state, sc, sid) -> None:
    """会话内有运行中的回答 → 409（结构变更不允许与生成并发）。"""
    run_manager = getattr(state, "run_manager", None)
    if run_manager is not None and run_manager.active_in_session(sc.uid, sid) is not None:
        raise HTTPException(409, "回答正在进行中，请等待结束后再操作")


def _apply_member_seat_settings(state, sc, m, provider_id, memory_mode,
                                memory_char_id) -> None:
    """校验并写入座位的 provider / 记忆挂载设置。None 字段=不改。"""
    if provider_id is not None:
        pid = (provider_id or "").strip()
        if pid and not any(p.id == pid for p in state.config.providers):
            raise HTTPException(400, "该模型不存在或尚未配置")
        m.provider_id = pid
    if memory_mode is not None:
        mode = (memory_mode or "").strip()
        if mode not in ("", "rw", "ro"):
            raise HTTPException(400, "记忆挂载模式无效（可选：不挂载/读写/只读）")
        m.memory_mode = mode
    if memory_char_id is not None:
        mc = (memory_char_id or "").strip()
        if mc:
            try:
                check_safe_name(mc, "记忆簿归属 ID")
            except InvalidName:
                raise HTTPException(400, "记忆簿归属 ID 非法")
        char_store = getattr(sc, "char_memory", None)
        if mc and (char_store is None or not char_store.exists(mc)):
            raise HTTPException(400, "该角色还没有记忆簿，无法挂载")
        m.memory_char_id = mc


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
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get(sc, rid)
    _active_run_409(state, sc, s.id)
    ch = sc.lore.get_character(body.character_id)
    if ch is None:
        raise HTTPException(400, "角色不存在")
    from ..sessions.models import RoomMember, Thread

    m = RoomMember(name=ch.name, character_id=ch.id)
    _apply_member_seat_settings(
        state, sc, m, body.provider_id, body.memory_mode, None
    )
    s.members.append(m)
    s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    sc.store.save(s)
    return m.to_dict()


@router.patch("/rooms/{rid}/members/{mid}")
def update_member(rid: str, mid: str, body: MemberUpdate, request: Request):
    state = request.app.state.flyai
    sc = state.user(request)
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
    _apply_member_seat_settings(
        state, sc, m, body.provider_id, body.memory_mode, body.memory_char_id
    )
    sc.store.save(s)
    return m.to_dict()


@router.delete("/rooms/{rid}/members/{mid}")
def remove_member(rid: str, mid: str, request: Request):
    """移除房间成员：删其专属私聊窗口、从各群聊窗口摘除（群历史保留）；
    群聊窗口因此空无一人时一并删除。成员自身的历史消息不删除。
    """
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get(sc, rid)
    m = s.member(mid)
    if m is None:
        raise HTTPException(404, "参与者不存在")
    if m.is_user:
        raise HTTPException(400, "不能移除用户自己的座位")
    _active_run_409(state, sc, s.id)

    for t in list(s.threads):
        if t.kind == "dm" and mid in t.member_ids:
            s.remove_thread(t.id)  # 其专属私聊窗口
        elif mid in t.member_ids:
            t.member_ids = [x for x in t.member_ids if x != mid]
            if not t.member_ids:
                s.remove_thread(t.id)  # 群里人被删空 → 窗口无存在意义
    s.remove_member(mid)
    sc.store.save(s)
    return {"removed": mid}


@router.post("/rooms/{rid}/groups")
def create_group(rid: str, body: GroupCreate, request: Request):
    """把多个 AI 放进同一窗口，成组聊天。"""
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get(sc, rid)
    _active_run_409(state, sc, s.id)
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


@router.patch("/rooms/{rid}/threads/{tid}")
def rename_thread(rid: str, tid: str, body: ThreadUpdate, request: Request):
    """重命名窗口（私聊/群聊均可）。"""
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get(sc, rid)
    t = s.thread(tid)
    if t is None:
        raise HTTPException(404, "窗口不存在")
    if body.title is None:
        raise HTTPException(400, "未提供新的窗口名称")
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "窗口名称不能为空")
    t.title = title[:30]
    sc.store.save(s)
    return t.to_dict()


@router.delete("/rooms/{rid}/threads/{tid}")
def delete_thread(rid: str, tid: str, request: Request):
    """删除窗口（含其窗口内全部历史）；房间最后一个窗口不允许删除。"""
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get(sc, rid)
    t = s.thread(tid)
    if t is None:
        raise HTTPException(404, "窗口不存在")
    _active_run_409(state, sc, s.id)
    if len(s.threads) <= 1:
        raise HTTPException(400, "最后一个窗口不能删除")
    s.remove_thread(tid)
    sc.store.save(s)
    return {"removed": tid}


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