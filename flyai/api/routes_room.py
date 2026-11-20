"""Beta 多 AI 群聊：房间（Session）参与者与窗口管理。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..ids import InvalidName, check_safe_name
from ..models_access import visible_providers
from ..sessions.resolve import member_character
from ..user import ADMIN_UID
from .routes_session import _session_dict


router = APIRouter(prefix="/api", tags=["room"])


def _safe_room_id(rid: str) -> str:
    try:
        return check_safe_name(rid, "房间 ID")
    except InvalidName:
        raise HTTPException(400, "房间 ID 非法")


def _stamp_seat_messages(s, member_id: str, card_id: str) -> None:
    """给某座位的旧 assistant 消息补记角色卡 id（换卡时调用）。

    历史消息此前只按座位 actor_id 归属；座位换卡后同一 actor_id 下会混着
    两张卡的对白，自动回忆无法区分。换卡时把旧卡 id 回填到其尚未标注的
    消息上，此后新卡只回忆自己产生的对话。旧卡 id 为空时跳过。
    """
    if not card_id:
        return

    def _stamp(messages) -> None:
        for msg in messages:
            if msg.role != "assistant" or (msg.actor_id or "").strip() != member_id:
                continue
            if not (msg.character_id or "").strip():
                msg.character_id = card_id

    for t in s.threads:
        if member_id in (t.member_ids or []):
            _stamp(t.messages)
    _stamp(s.messages)


class RoomCreate(BaseModel):
    title: Optional[str] = None
    character_ids: List[str] = []
    world_id: Optional[str] = None
    # normal=普通房间（可多角色）；galgame=Galgame 模式（单角色视觉小说）
    mode: str = "normal"
    # 开场白选择：None/0=first_mes；1..N=alternate_greetings 的第 N 条（按存储原下标）
    greeting_index: Optional[int] = None
    # 开局设定：世界书中标题以「开局/序章」开头的条目标题；None/空=按卡默认
    opening_entry: Optional[str] = None


def _pick_greeting(ch, greeting_index: Optional[int]) -> str:
    """按 greeting_index 选开场白；越界或选中条目为空时回落 first_mes。

    下标按存储原列表计算（不做去空压缩），保证与前端下拉选项一致。
    """
    if greeting_index is None or greeting_index <= 0:
        return (ch.first_mes or "").strip()
    alts = list(ch.alternate_greetings or [])
    idx = greeting_index - 1
    if 0 <= idx < len(alts):
        picked = str(alts[idx] or "").strip()
        if picked:
            return picked
    return (ch.first_mes or "").strip()


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
        if pid:
            level = int(getattr(sc, "level", 0) or 0)
            usable = visible_providers(state.config, getattr(sc, "uid", ""), level)
            if not any(p.id == pid for p in usable):
                raise HTTPException(403, "该模型不可用")
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
    state = request.app.state.flyai
    sc = state.user(request)
    from ..sessions.models import RoomMember, Thread
    from .. import feature_prefs

    is_galgame = (body.mode or "normal") == "galgame"
    # Galgame 门槛：全局默认或用户覆盖关闭时不允许创建（管理员默认开放）
    if is_galgame and not feature_prefs.feature_enabled(
        "galgame", state.config, getattr(sc, "user_settings", None)
    ):
        raise HTTPException(403, "Galgame 模式未开放（默认关闭或被管理员禁用）")
    # 等级门槛：admin 恒过，其余用户按 access.galgame_min_level 拦截
    if is_galgame and sc.uid != ADMIN_UID:
        access = getattr(state.config, "access", None)
        required = int(getattr(access, "galgame_min_level", 0) or 0) if access else 0
        level = int(getattr(sc, "level", 0) or 0)
        if level < required:
            raise HTTPException(
                403,
                "权限等级不足（Galgame 需要 Lv%d，当前 Lv%d）" % (required, level),
            )
    # Galgame 第一期：单角色（先校验再落盘，避免留下空会话文件）
    if is_galgame and len(body.character_ids or []) != 1:
        raise HTTPException(400, "Galgame 模式必须且只能选择一个角色")
    if body.world_id and not sc.lore.get_world(body.world_id):
        raise HTTPException(400, "世界观不存在")

    # 先解析全部角色/开局选择再落盘，参数错误时不留下空会话文件
    chosen = []
    for cid in (body.character_ids or []):
        ch = sc.lore.get_character(cid)
        if ch is None:
            raise HTTPException(400, f"角色不存在: {cid}")
        chosen.append((ch, cid))
    opening = (body.opening_entry or "").strip()
    if is_galgame or len(chosen) != 1:
        # 多角色归属不明、Galgame 有自己的开场流程：忽略开局选择
        opening = ""
    elif opening:
        from ..lore.opening import opening_titles

        if opening not in opening_titles(chosen[0][0]):
            raise HTTPException(400, "开局条目不存在")

    s = sc.store.create(title=body.title or "新会话")
    if is_galgame:
        s.mode = "galgame"
    if body.world_id:
        s.world_id = body.world_id
    if opening:
        s.opening_entry = opening
    s.members.append(RoomMember(name="你", is_user=True))  # 用户座位
    seat = None
    for ch, cid in chosen:
        seat = RoomMember(name=ch.name, character_id=cid)
        s.members.append(seat)
        s.threads.append(Thread(kind="dm", title=seat.name, member_ids=[seat.id]))
    # 开场白（first_mes）：仅单角色普通房间自动落一条 AI 首条消息。
    # 多角色归属不明、Galgame 有自己的初始剧情流程，均不播种。
    if not is_galgame and len(chosen) == 1 and seat is not None:
        ch, cid = chosen[0]
        # 单 AI 房间同步顶层绑定字段：会话副本 / 导出 / 压缩校验都读顶层，
        # 与 update_member 换座后的同步规则保持一致。
        s.character_id = cid
        s.character_override = None
        greeting = _pick_greeting(ch, body.greeting_index)
        if greeting:
            from ..lore.query import substitute_chat_macros
            from ..schemas import Message

            greeting = substitute_chat_macros(greeting, ch.name, "你")
            s.threads[0].messages.append(
                Message(role="assistant", content=greeting,
                        actor_id=seat.id, character_id=cid or "")
            )
    sc.store.save(s)
    return _session_dict(
        s, with_messages=True, lore=sc.lore,
        user_settings=getattr(sc, "user_settings", None),
        cfg=getattr(request.app.state.flyai, "config", None),
        level=int(getattr(sc, "level", 0) or 0),
        is_admin=sc.uid == ADMIN_UID,
    )


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
        card_changed = body.character_id != m.character_id
        old_card_id = m.character_id or ""
        if card_changed and not m.is_user:
            # 先把旧卡的对白按旧卡 id 落标，避免同一座位的新卡串旧卡的回忆
            _stamp_seat_messages(s, m.id, old_card_id)
        m.character_id = body.character_id
        # 换卡时清掉旧的会话专属副本，避免遮蔽库卡
        m.override = None
        # AI 座位真正换到另一张卡：旧角色的属性数值与本房间记忆不能带给新角色，
        # 否则新角色会带着旧角色的好感度/记忆出场（串人设）。
        # memory_mode/memory_char_id 是用户显式挂载的跨会话记忆簿配置，保留。
        if card_changed and not m.is_user:
            m.attributes = {}
            m.memory = ""
        # 普通单聊房间：唯一 AI 座位即会话挂载角色，同步顶层兼容字段
        # （GET 会话的 character / 压缩 / 导出均读顶层；群聊与 Galgame 不同步）
        if not m.is_user and (s.mode or "normal") != "galgame":
            ai_seats = [x for x in s.members if not x.is_user]
            if len(ai_seats) == 1:
                s.character_id = body.character_id
                s.character_override = None
                if card_changed:
                    # 旧卡的开局标题不应套到新卡上（不存在时也等于按卡默认）
                    s.opening_entry = ""
    _apply_member_seat_settings(
        state, sc, m, body.provider_id, body.memory_mode, body.memory_char_id
    )
    sc.store.save(s)
    return m.to_dict()


@router.post("/rooms/{rid}/members/{mid}/character/publish")
def publish_member_character(rid: str, mid: str, request: Request):
    """把座位当前有效角色（通常是未入库的会话副本）另存为角色库新卡。

    复制入库：座位仍挂在原来的副本上，库主卡不受影响，新卡获得新 id。
    """
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get(sc, rid)
    m = s.member(mid)
    if m is None:
        raise HTTPException(404, "参与者不存在")
    if m.is_user:
        raise HTTPException(400, "用户座位没有角色卡")
    eff = member_character(m, sc.lore)
    if eff is None:
        raise HTTPException(400, "该座位没有角色卡")
    import uuid

    from ..lore.models import Character

    data = eff.to_dict()
    data["id"] = uuid.uuid4().hex
    new_card = Character.from_dict(data)
    sc.lore.save_character(new_card)
    return {"character": new_card.to_dict()}


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
    # P4 MVU：切换变体后按可见消息最后快照回退/前进变量状态
    from ..st import mvu as st_mvu

    st_mvu.recompute_state(s, t.messages)
    sc.store.save(s)
    return t.to_dict()