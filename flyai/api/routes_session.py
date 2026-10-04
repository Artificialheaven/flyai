"""会话 CRUD、历史与 R7 会话副本 / 导出接口。"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Dict, List, Optional

from ..ids import InvalidName, check_safe_name
from ..lore.diff import character_diff, world_diff
from ..sessions.models import RoomMember, Session, Thread
from ..sessions.resolve import (
    apply_room_compression,
    clear_character_override,
    effective_character,
    effective_world,
    fork_character,
    is_character_scoped,
    member_character,
    plan_room_compression,
    room_base_signatures,
    set_character_override,
    set_world_override,
)

router = APIRouter(prefix="/api", tags=["session"])


def _safe_sid(sid: str) -> str:
    try:
        return check_safe_name(sid, "会话 ID")
    except InvalidName:
        raise HTTPException(400, "会话 ID 非法")


class SessionCreate(BaseModel):
    title: Optional[str] = None
    provider_id: Optional[str] = None
    character_id: Optional[str] = None
    world_id: Optional[str] = None


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
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
    """重命名/置顶，或切换会话绑定的角色/世界观（角色扮演开关）。"""
    sc = request.app.state.flyai.user(request)
    _safe_sid(sid)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    if req.title is not None:
        title = req.title.strip()
        if title:
            s.title = title[:60]
    if req.pinned is not None:
        s.pinned = bool(req.pinned)
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
    _safe_sid(sid)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    return _session_dict(s, with_messages=True, lore=sc.lore)


@router.delete("/sessions/{sid}")
def delete_session(sid: str, request: Request):
    sc = request.app.state.flyai.user(request)
    _safe_sid(sid)
    sc.store.delete(sid)
    return {"ok": True}


class BatchDeleteBody(BaseModel):
    ids: List[str] = []


@router.post("/sessions/batch-delete")
def batch_delete_sessions(body: BatchDeleteBody, request: Request):
    """批量删除：逐个删除，不存在的跳过。返回实际删除数。
    body 里的 id 同样校验（正斜杠穿越在请求体里全平台成立）。"""
    sc = request.app.state.flyai.user(request)
    deleted = 0
    for sid in body.ids or []:
        _safe_sid(sid)
        if sc.store.get(sid) is not None:
            sc.store.delete(sid)
            deleted += 1
    return {"ok": True, "deleted": deleted}


@router.post("/sessions/{sid}/clear")
def clear_session(sid: str, request: Request):
    sc = request.app.state.flyai.user(request)
    _safe_sid(sid)
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


def _export_card(c, fallback_uid) -> dict:
    """角色卡导出 dict：补 Author+ID 复合标识。旧卡 author 为空时以导出名下属 uid 兜底。"""
    d = c.to_dict()
    author = d.get("author") or fallback_uid
    d["ref"] = f"{author}:{d['id']}"
    return d


@router.get("/sessions/{sid}/export")
def session_export(sid: str, request: Request):
    """导出对话包：全部窗口的完整文本（含工具调用/结果）+ 角色卡（Author+ID）+ 世界观。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    char = effective_character(s, sc.lore)
    world = effective_world(s, sc.lore)

    # 房间：收集每个 AI 成员的有效角色卡，按 ref 去重
    cards = {}
    for m in s.members:
        if m.is_user:
            continue
        c = member_character(m, sc.lore)
        if c is None:
            continue
        d = _export_card(c, sc.uid)
        cards.setdefault(d["ref"], d)
    if char is not None:
        d = _export_card(char, sc.uid)
        cards.setdefault(d["ref"], d)

    data = {
        "session": {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at, "attributes": s.attributes},
        "character": char.to_dict() if char else None,
        "world": world.to_dict() if world else None,
        "messages": [m.to_dict() for m in s.messages],
        "members": [m.to_dict() for m in s.members],
        "threads": [t.to_dict() for t in s.threads],
        "characters": list(cards.values()),
    }
    body = {"type": "flyai-conversation", "version": 2, "name": s.title, "data": data}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="conversation-{s.id[:8]}.json"'},
    )


class SessionImportPayload(BaseModel):
    type: str = ""
    version: int = 0
    name: str = ""
    data: dict = {}


@router.post("/sessions/import")
def import_session(payload: SessionImportPayload, request: Request):
    """导入 flyai-conversation 导出包：重建为全新会话（新 id，不覆盖已有会话）。

    随包角色卡：本库按 id 已存在 → 复用库引用；不存在 → 降为会话专属副本
    （member.override），不塞进角色卡库。世界观同理。
    """
    if payload.type != "flyai-conversation":
        raise HTTPException(400, "不是 FlyAI 对话导出包（type 应为 flyai-conversation）")
    if payload.version != 2:
        raise HTTPException(400, "不支持的导出包版本: %s（仅支持 2）" % payload.version)
    sc = request.app.state.flyai.user(request)
    d = payload.data or {}
    if not isinstance(d, dict) or not isinstance(d.get("session"), dict):
        raise HTTPException(400, "导出包内容不完整")
    meta = d.get("session") or {}

    s = Session(
        id=uuid.uuid4().hex,  # 新会话 id：store 以 id 为文件名，复用会覆盖原会话
        title=(meta.get("title") or payload.name or "导入的对话").strip() or "导入的对话",
        provider_id=meta.get("provider_id", "") or "",
        created_at=meta.get("created_at", "") or "",  # 保留原始创建时间
        attributes=meta.get("attributes", {}) or {},
        members=[RoomMember.from_dict(m) for m in d.get("members", []) or []],
        threads=[Thread.from_dict(t) for t in d.get("threads", []) or []],
        pinned=False,  # 不导入置顶，避免打乱当前排序
    )

    # 随包角色卡按 id 索引；导出时补的 ref 字段不属于卡本身，副本落盘前去掉
    packaged = {}
    for c in d.get("characters", []) or []:
        if isinstance(c, dict) and c.get("id"):
            card = dict(c)
            card.pop("ref", None)
            packaged[c["id"]] = card

    reused = 0
    converted = 0

    def resolve_card(member):
        nonlocal reused, converted
        if member.override or not member.character_id:
            return  # 原本就是会话副本 / 无角色座位（如未挂自设的用户座）
        cid = member.character_id
        if sc.lore.get_character(cid) is not None:
            reused += 1
            return
        card = packaged.get(cid)
        if card is not None:
            # 库里没有：降为会话副本，不强行入库
            member.override = card
            converted += 1
        member.character_id = ""  # 清掉悬空引用（含包里也找不到卡的情况）

    for m in s.members:
        resolve_card(m)

    # 世界观：随包世界卡的 id 在本库存在 → 复用引用；否则用随包世界做会话副本
    world_pkg = d.get("world")
    wid = world_pkg.get("id", "") if isinstance(world_pkg, dict) else ""
    if wid and sc.lore.get_world(wid) is not None:
        s.world_id = wid
    elif isinstance(world_pkg, dict) and wid:
        s.world_override = world_pkg

    # 窗口引用的成员 id 必须存在（导出包残缺时自保，heal_thread 之后还会处理消息）
    known_mids = {m.id for m in s.members}
    for t in s.threads:
        t.member_ids = [mid for mid in t.member_ids if mid in known_mids]

    sc.store.save(s)
    out = _session_dict(s)
    out["import_summary"] = {"reused_cards": reused, "scoped_cards": converted}
    return out


class CompressApplyBody(BaseModel):
    proposal_id: str


def _single_base_signatures(s, lore) -> Dict[str, str]:
    """单 AI 会话当前有效角色/世界的签名（无则空签名）。"""
    from ..lore.diff import content_signature

    char = effective_character(s, lore)
    world = effective_world(s, lore)
    return {
        "character": content_signature(char.to_dict() if char else None),
        "world": content_signature(world.to_dict() if world else None),
    }


@router.post("/sessions/{sid}/compress")
async def session_compress(sid: str, request: Request):
    """AI 生成压缩「提案」：只计算并暂存，不写入任何数据。

    返回 before/after/diff；前端确认后调 /compress/apply 才写入会话副本。
    """
    from ..lore.gen import (
        apply_conversation_compression,
        build_conversation_compression,
        compress_room,
    )

    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get_session(sc, sid)

    if s.members:
        try:
            data = await compress_room(state, s, sc.lore)
        except Exception as exc:
            raise HTTPException(500, f"压缩失败: {exc}")
        planned = plan_room_compression(s, data, sc.lore)

        before_members: Dict[str, dict] = {}
        for mid in planned["members"]:
            m = s.member(mid)
            cur = member_character(m, sc.lore)
            before_members[mid] = cur.to_dict() if cur else (m.override or {"name": m.name})
        cur_world = effective_world(s, sc.lore)
        before_world = cur_world.to_dict() if cur_world else None

        basesig = room_base_signatures(s, sc.lore)
        pid = state.put_compress_proposal(sc.uid, s.id, True, data, basesig)

        member_diffs = {mid: character_diff(before_members[mid], planned["members"][mid])
                        for mid in planned["members"]}
        return {
            "room": True,
            "proposal_id": pid,
            "scoped": True,
            "before": {"members": before_members, "world": before_world},
            "after": {"members": planned["members"], "world": planned["world"]},
            "diff": {"members": member_diffs, "world": world_diff(before_world, planned["world"])},
        }

    char = effective_character(s, sc.lore)
    world = effective_world(s, sc.lore)
    if char is None:
        from ..lore.models import Character

        char = Character(author=sc.uid)
    try:
        data = await build_conversation_compression(state, s, char, world)
    except Exception as exc:
        raise HTTPException(500, f"压缩失败: {exc}")
    new_char, new_world = apply_conversation_compression(char, world, data)

    before_char = char.to_dict()
    before_world = world.to_dict() if world else None
    basesig = _single_base_signatures(s, sc.lore)
    pid = state.put_compress_proposal(sc.uid, s.id, False, data, basesig)

    return {
        "room": False,
        "proposal_id": pid,
        "scoped": True,
        "before": {"character": before_char, "world": before_world},
        "after": {
            "character": new_char.to_dict(),
            "world": new_world.to_dict() if new_world else None,
        },
        "diff": {
            "character": character_diff(before_char, new_char.to_dict()),
            "world": world_diff(before_world, new_world.to_dict() if new_world else None),
        },
    }


@router.post("/sessions/{sid}/compress/apply")
async def session_compress_apply(sid: str, body: CompressApplyBody, request: Request):
    """确认写入压缩提案：校验归属与 base 未变化后写入会话副本并持久化。"""
    from ..lore.gen import apply_conversation_compression

    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get_session(sc, sid)

    pid = (body.proposal_id or "").strip()
    existing = state._compress_proposals.get(pid)
    if existing is None:
        raise HTTPException(404, "压缩提案不存在或已过期，请重新压缩")
    if existing.get("uid") != sc.uid:
        raise HTTPException(403, "无权使用该压缩提案")
    p = state.get_compress_proposal(pid, sc.uid)
    if p is None:
        raise HTTPException(404, "压缩提案不存在或已过期，请重新压缩")
    if p.get("sid") != s.id:
        raise HTTPException(400, "提案不属于该会话")

    if p.get("room"):
        if room_base_signatures(s, sc.lore) != p.get("basesig"):
            raise HTTPException(409, "提案生成后会话设定已变化，请重新压缩")
        applied = apply_room_compression(s, p["raw"], sc.lore)
        sc.store.save(s)
        state.drop_compress_proposal(pid)
        return {
            "room": True,
            "members": applied["members"],
            "world": applied["world"],
            "scoped": True,
        }

    if _single_base_signatures(s, sc.lore) != p.get("basesig"):
        raise HTTPException(409, "提案生成后会话设定已变化，请重新压缩")

    char = effective_character(s, sc.lore)
    world = effective_world(s, sc.lore)
    if char is None:
        from ..lore.models import Character

        char = Character(author=sc.uid)
    new_char, new_world = apply_conversation_compression(char, world, p["raw"])
    set_character_override(s, new_char)
    if new_world is not None and (new_world.content or new_world.table):
        set_world_override(s, new_world)
    sc.store.save(s)
    state.drop_compress_proposal(pid)
    return {
        "character": new_char.to_dict(),
        "world": new_world.to_dict() if new_world else None,
        "scoped": True,
    }


def _session_dict(s, with_messages: bool = False, lore=None):
    d = {
        "id": s.id,
        "title": s.title,
        "pinned": s.pinned,
        "provider_id": s.provider_id,
        "character_id": s.character_id,
        "world_id": s.world_id,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "attributes": s.attributes,
        "scoped": is_character_scoped(s),
        "members": [m.to_dict() for m in s.members],
        "threads": [t.to_dict() for t in s.threads],
        "token_usage": s.token_usage or {},
    }
    if with_messages:
        d["messages"] = [m.to_dict() for m in s.messages]
        if lore is not None:
            char = effective_character(s, lore)
            world = effective_world(s, lore)
            d["character"] = char.to_dict() if char else None
            d["world"] = world.to_dict() if world else None
    return d