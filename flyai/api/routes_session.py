"""会话 CRUD、历史与 R7 会话副本 / 导出接口。"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, Response
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
    # 单窗口覆盖：0/None=跟随；禁用工具名列表（全量替换，非法名忽略不 400）
    max_tokens: Optional[int] = None
    disabled_tools: Optional[List[str]] = None


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
    if req.max_tokens is not None:
        try:
            s.max_tokens = max(0, min(100000, int(req.max_tokens)))
        except (TypeError, ValueError):
            raise HTTPException(400, "max_tokens 必须是整数")
    if req.disabled_tools is not None:
        if not isinstance(req.disabled_tools, list):
            raise HTTPException(400, "disabled_tools 必须是字符串数组")
        # 只信任注册表里真实存在的工具名；注册表缺失时全部忽略（无法验证）
        registry = getattr(request.app.state.flyai, "registry", None)
        known = ({t.name for t in registry.all()}
                 if hasattr(registry, "all") else set())
        picked, seen = [], set()
        for name in req.disabled_tools:
            name = str(name or "").strip()
            # 只收已注册工具名；未知工具静默忽略（可能来自已卸载插件/旧客户端）
            if name and name in known and name not in seen:
                seen.add(name)
                picked.append(name)
        s.disabled_tools = picked
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


def _build_export_data(sc, s) -> dict:
    """导出包 data 部分：完整文本（含工具调用/结果）+ 角色卡 + 世界观。
    JSON 导出与 zip 导出共用；zip 会再补 mode / 场景 / 语音映射。"""
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

    return {
        "session": {"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at, "attributes": s.attributes},
        "character": char.to_dict() if char else None,
        "world": world.to_dict() if world else None,
        "messages": [m.to_dict() for m in s.messages],
        "members": [m.to_dict() for m in s.members],
        "threads": [t.to_dict() for t in s.threads],
        "characters": list(cards.values()),
    }


@router.get("/sessions/{sid}/export")
def session_export(sid: str, request: Request):
    """导出对话 JSON 包（v2，仅文本）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    body = {"type": "flyai-conversation", "version": 2,
            "name": s.title, "data": _build_export_data(sc, s)}
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="conversation-{s.id[:8]}.json"'},
    )


@router.get("/sessions/{sid}/export-zip")
def session_export_zip(sid: str, request: Request, include_tts: bool = True):
    """导出自包含 zip 包（v3）：对话 + 立绘 / 背景 / BGM；include_tts=false
    时不含已合成语音（同时清空语音映射，导入端按新文本重新合成）。"""
    from pathlib import Path

    from .. import transfer

    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    data = _build_export_data(sc, s)
    data["session"]["mode"] = s.mode or "normal"
    data["galgame"] = s.galgame or {}
    data["galgame_tts"] = (s.galgame_tts or {}) if include_tts else {}

    body = {"type": "flyai-conversation", "version": 3,
            "name": s.title, "data": data}
    try:
        raw = transfer.build_export_zip(body, sc.uid, Path(sc.upload_dir))
    except transfer.TransferError as exc:
        raise HTTPException(400, str(exc))
    return Response(
        content=raw,
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="conversation-{s.id[:8]}.zip"'},
    )


class SessionImportPayload(BaseModel):
    type: str = ""
    version: int = 0
    name: str = ""
    data: dict = {}


def _build_imported_session(d: dict, fallback_name: str) -> Session:
    """由导出包 data 部分重建会话（新 id，不覆盖已有会话）。

    v3 包额外恢复 mode / galgame 场景状态 / 语音映射；v2 包缺这些字段，
    按普通会话处理。
    """
    if not isinstance(d, dict) or not isinstance(d.get("session"), dict):
        raise HTTPException(400, "导出包内容不完整")
    meta = d.get("session") or {}

    mode = meta.get("mode") or "normal"
    galgame = d.get("galgame") if isinstance(d.get("galgame"), dict) else {}
    galgame_tts = (
        d.get("galgame_tts") if isinstance(d.get("galgame_tts"), dict) else {}
    )

    return Session(
        id=uuid.uuid4().hex,  # 新会话 id：store 以 id 为文件名，复用会覆盖原会话
        title=(meta.get("title") or fallback_name or "导入的对话").strip() or "导入的对话",
        provider_id=meta.get("provider_id", "") or "",
        created_at=meta.get("created_at", "") or "",  # 保留原始创建时间
        attributes=meta.get("attributes", {}) or {},
        members=[RoomMember.from_dict(m) for m in d.get("members", []) or []],
        threads=[Thread.from_dict(t) for t in d.get("threads", []) or []],
        pinned=False,  # 不导入置顶，避免打乱当前排序
        mode=mode,
        galgame=galgame,
        galgame_tts=galgame_tts,
    )


def _resolve_import_cards_world(s: Session, d: dict, sc) -> Dict[str, int]:
    """解析随包角色卡 / 世界观，并自愈线程成员引用。

    随包角色卡：本库按 id 已存在 → 复用库引用；不存在 → 降为会话专属副本
    （member.override），不塞进角色卡库。世界观同理。
    """
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

    return {"reused_cards": reused, "scoped_cards": converted}


@router.post("/sessions/import")
def import_session(payload: SessionImportPayload, request: Request):
    """导入 flyai-conversation JSON 导出包（v2，仅文本）：重建为全新会话。"""
    if payload.type != "flyai-conversation":
        raise HTTPException(400, "不是 FlyAI 对话导出包（type 应为 flyai-conversation）")
    if payload.version != 2:
        raise HTTPException(400, "不支持的导出包版本: %s（仅支持 2）" % payload.version)
    sc = request.app.state.flyai.user(request)
    d = payload.data or {}
    s = _build_imported_session(d, payload.name)
    summary = _resolve_import_cards_world(s, d, sc)
    sc.store.save(s)
    out = _session_dict(s)
    out["import_summary"] = summary
    return out


def install_session_zip(sc, raw: bytes, fallback_name: str = ""):
    """由 v3 zip 原始字节重建会话并释放素材（市场安装与导入路由共用）。

    立绘 / 背景 / BGM / 语音释放到本账号上传目录，文本中的 URL 同步改写。
    返回 _session_dict（含 import_summary）。
    """
    from pathlib import Path

    from .. import transfer

    try:
        text, entries = transfer.open_export_zip(raw)
        text = transfer.rewrite_urls(text, entries, sc.uid)
        envelope = json.loads(text)
    except transfer.TransferError as exc:
        raise HTTPException(400, str(exc))
    except ValueError:
        raise HTTPException(400, "对话数据解析失败")

    if envelope.get("type") != "flyai-conversation":
        raise HTTPException(400, "不是 FlyAI 对话导出包（type 应为 flyai-conversation）")
    if envelope.get("version") != 3:
        raise HTTPException(400, "不支持的 zip 包版本: %s（仅支持 3）" % envelope.get("version"))

    d = envelope.get("data") or {}
    s = _build_imported_session(d, str(envelope.get("name") or fallback_name or ""))
    summary = _resolve_import_cards_world(s, d, sc)

    try:
        transfer.materialize_files(entries, Path(sc.upload_dir))
    except transfer.TransferError as exc:
        raise HTTPException(400, str(exc))

    sc.store.save(s)
    out = _session_dict(s)
    summary["assets"] = len(entries)
    out["import_summary"] = summary
    return out


@router.post("/sessions/import-zip")
async def import_session_zip(request: Request, file: UploadFile = File(...)):
    """导入自包含 zip 迁移包（v3）：重建会话并把立绘 / 背景 / BGM / 语音
    释放到本账号上传目录，对话文本中的 URL 同步改写为本账号地址。"""
    sc = request.app.state.flyai.user(request)
    raw = await file.read()
    return install_session_zip(sc, raw)


@router.post("/sessions/import-chatbox")
async def import_chatbox(request: Request, file: UploadFile = File(...)):
    """导入 Chatbox 导出的对话历史：备份 JSON（可一次含多个对话）或单对话
    Markdown。每个对话重建为一个全新普通会话（用户座位 + 一个 AI 私聊窗口）。

    单个对话返回会话 dict（与 /import 口径一致）；多个对话返回
    {"sessions": [...]}，由前端逐个加入列表。
    """
    from ..sessions.chat_import import ChatImportError, parse_external_chats

    sc = request.app.state.flyai.user(request)
    raw = await file.read()
    try:
        items = parse_external_chats(raw)
    except ChatImportError as exc:
        raise HTTPException(400, str(exc))

    built = []
    total_msgs = 0
    total_images = 0
    for d in items:
        title = (d.get("session") or {}).get("title") or "Chatbox 对话"
        s = _build_imported_session(d, title)
        _resolve_import_cards_world(s, d, sc)
        sc.store.save(s)
        n_msgs = sum(len(t.messages) for t in s.threads)
        n_imgs = sum(
            len(m.images or []) for t in s.threads for m in t.messages
        )
        total_msgs += n_msgs
        total_images += n_imgs
        out = _session_dict(s)
        out["message_count"] = n_msgs
        built.append(out)

    summary = {
        "conversations": len(built),
        "messages": total_msgs,
        "images": total_images,
    }
    if len(built) == 1:
        built[0]["import_summary"] = summary
        return built[0]
    return {"sessions": built, "import_summary": summary}


class CompressApplyBody(BaseModel):
    proposal_id: str


def _single_base_signatures(s, lore) -> Dict[str, str]:
    """单 AI 会话当前有效角色/世界的签名（无则空签名）。

    已有会话副本时直接签原始 override dict；经 Character/World 对象往返会因
    id 为空时重新生成 uuid 而让签名每次都变。
    """
    from ..lore.diff import content_signature

    if s.character_override:
        char_sig = content_signature(s.character_override)
    else:
        char = effective_character(s, lore)
        char_sig = content_signature(char.to_dict() if char else None)
    if s.world_override:
        world_sig = content_signature(s.world_override)
    else:
        world = effective_world(s, lore)
        world_sig = content_signature(world.to_dict() if world else None)
    return {"character": char_sig, "world": world_sig}


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
        scoped = bool(
            s.world_override
            or any(m.override for m in s.members if not m.is_user)
        )
        return {
            "room": True,
            "proposal_id": pid,
            "scoped": scoped,
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
        "scoped": bool(s.character_override or s.world_override),
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


class CompressSaveAsBody(BaseModel):
    proposal_id: str = ""


@router.post("/sessions/{sid}/compress/save-as")
def session_compress_save_as(sid: str, body: CompressSaveAsBody, request: Request):
    """把压缩结果另存为角色库中的新副本，不改动当前会话副本。

    新卡 parent_id 指向会话绑定的根角色卡，在根卡的副本列表里可见。
    房间会话为每个受影响的 AI 成员各存一张；世界设定不入库（仍随会话）。
    不消费提案——之后仍可再选择应用到当前副本。
    """
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get_session(sc, sid)

    pid = (body.proposal_id or "").strip()
    p = state.get_compress_proposal(pid, sc.uid)
    if p is None:
        raise HTTPException(404, "压缩提案不存在或已过期，请重新压缩")
    if p.get("sid") != s.id:
        raise HTTPException(400, "提案不属于该会话")

    from ..lore.models import Character

    raw = p["raw"]
    saved = []
    if p.get("room"):
        planned = plan_room_compression(s, raw, sc.lore)
        for mid, d in planned["members"].items():
            m = s.member(mid)
            d["id"] = uuid.uuid4().hex
            d["parent_id"] = (m.character_id or d.get("parent_id") or "")
            d["public"] = False
            new_card = Character.from_dict(d)
            sc.lore.save_character(new_card)
            saved.append({"id": new_card.id, "name": new_card.name})
    else:
        from ..lore.gen import apply_conversation_compression

        char = effective_character(s, sc.lore)
        world = effective_world(s, sc.lore)
        if char is None:
            char = Character(author=sc.uid)
        new_char, _new_world = apply_conversation_compression(char, world, raw)
        new_char.id = uuid.uuid4().hex
        new_char.parent_id = (s.character_id or char.id or "")
        new_char.public = False
        sc.lore.save_character(new_char)
        saved.append({"id": new_char.id, "name": new_char.name})

    return {"characters": saved}


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
        "mode": getattr(s, "mode", "normal") or "normal",
        "galgame": getattr(s, "galgame", None) or {},
        "galgame_tts": getattr(s, "galgame_tts", None) or {},
        "max_tokens": int(getattr(s, "max_tokens", 0) or 0),
        "disabled_tools": list(getattr(s, "disabled_tools", None) or []),
    }
    if with_messages:
        d["messages"] = [m.to_dict() for m in s.messages]
        if lore is not None:
            char = effective_character(s, lore)
            world = effective_world(s, lore)
            d["character"] = char.to_dict() if char else None
            d["world"] = world.to_dict() if world else None
    return d