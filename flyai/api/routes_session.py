"""会话 CRUD、历史与 R7 会话副本 / 导出接口。"""

from __future__ import annotations

import copy
import json
import uuid

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from typing import Dict, List, Optional

from ..ids import InvalidName, check_safe_name
from ..lore.diff import character_diff, world_diff
from ..sessions import versions
from ..sessions.models import RoomMember, Session, Thread
from ..user import ADMIN_UID
from ..sessions.resolve import (
    apply_room_compression,
    clear_character_override,
    effective_character,
    effective_world,
    fork_character,
    fork_world,
    is_character_scoped,
    member_character,
    plan_room_compression,
    primary_thread,
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
        d["message_count"] = s.message_count
        out.append(d)
    return {"sessions": out}


@router.get("/sessions/search")
def search_sessions(request: Request, q: str = "", limit: int = 50):
    """会话全文搜索：标题 + 消息正文（用户 / AI / 墓碑原文）。

    纯内存扫描，不返回消息体与图片，只给标题与命中片段，前端据此跳转。
    """
    query = (q or "").strip()
    if not query:
        return {"query": query, "results": []}
    needle = query.casefold()
    try:
        limit = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit = 50
    sc = request.app.state.flyai.user(request)
    hits = []
    for s in sc.store.list():
        count = 0
        snippet = ""
        title_hit = needle in (s.title or "").casefold()
        if title_hit:
            count += 1
            snippet = s.title or ""
        for m in s.messages:
            texts = [m.content or ""]
            if m.tombstone and m.tombstone.content:
                texts.append(m.tombstone.content)
            if m.name:
                texts.append(m.name)
            for text in texts:
                low = text.casefold()
                pos = low.find(needle)
                if pos < 0:
                    continue
                count += low.count(needle)
                if not snippet:
                    start = max(0, pos - 40)
                    end = min(len(text), pos + len(query) + 40)
                    snippet = text[start:end].replace("\n", " ").strip()
        if count:
            d = _session_dict(s)
            d["match_count"] = count
            d["snippet"] = snippet
            hits.append(d)
    # 最近更新优先（updated_at 为 ISO 字符串），再让置顶项浮到最前
    hits.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    hits.sort(key=lambda item: bool(item.get("pinned")), reverse=True)
    return {"query": query, "results": hits[:limit]}


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
        old_card_id = s.character_id or ""
        if req.character_id != old_card_id and old_card_id:
            # 旧形态会话（顶层 messages）换卡：先按旧卡 id 落标旧对白，
            # 自动回忆按角色 id 过滤时不会把旧卡的历史算给新卡
            for msg in s.messages:
                if msg.role == "assistant" and not (
                    msg.character_id or ""
                ).strip():
                    msg.character_id = old_card_id
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
    # P4 原生 MVU：会话加载时惰性初始化 InitVar，并按可见消息重算状态
    from ..st import mvu as st_mvu

    if st_mvu.sync_session_state(s, sc.lore):
        sc.store.save(s)
    return _session_dict(
        s, with_messages=True, lore=sc.lore,
        user_settings=getattr(sc, "user_settings", None),
        cfg=getattr(request.app.state.flyai, "config", None),
        level=int(getattr(sc, "level", 0) or 0),
        is_admin=sc.uid == ADMIN_UID,
    )


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


@router.delete("/sessions/{sid}/messages/{mid}")
def delete_message_turn(sid: str, mid: str, request: Request):
    """撤回 mid 用户消息所在的一整轮（dm/group 通用），原位留墓碑。"""
    state = request.app.state.flyai
    sc = state.user(request)
    _safe_sid(sid)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    # routes_room 反向依赖本模块的 _session_dict，函数内导入避免环
    from .routes_room import _active_run_409
    _active_run_409(state, sc, s.id)

    from ..sessions.recall import find_target, recall_turn

    thread, messages = find_target(s, mid)
    if messages is None:
        raise HTTPException(404, "消息不存在或已被撤回")
    try:
        payload = recall_turn(
            messages, mid,
            edits=thread.edits if thread is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(400, "只能撤回自己发送的消息") from exc
    # P4 MVU：撤回后按剩余可见消息重算变量状态
    from ..st import mvu as st_mvu

    st_mvu.recompute_state(s, messages)
    sc.store.save(s)
    return {
        "ok": True,
        "thread_id": thread.id if thread is not None else None,
        "anchor": mid,
        "tombstone": payload,
    }


# ---- R7：会话专属角色副本 ----

class CharacterBody(BaseModel):
    character: Dict = {}


def _must_get_session(sc, sid):
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    return s


def _single_ai_seat(s):
    """房间形态下唯一的 AI 座位；多人群聊或旧形态返回 None。

    会话副本的真实载体是 member.override（压缩/对话都读它），顶层
    character_override 仅作兼容镜像同步，保证导出与 effective_character 一致。
    """
    ais = [m for m in s.members if not m.is_user]
    return ais[0] if len(ais) == 1 else None


@router.post("/sessions/{sid}/character/fork")
def session_fork(sid: str, request: Request):
    """把当前有效角色复制为会话专属副本（不碰库主卡）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    from ..lore.models import Character

    m = _single_ai_seat(s)
    try:
        if m is not None:
            base = member_character(m, sc.lore)
            if base is None:
                raise ValueError("当前会话没有绑定角色，无法派生副本")
            copy = Character.from_dict(base.to_dict())
            m.override = copy.to_dict()
            s.character_override = copy.to_dict()
        else:
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

    char = Character.from_dict(body.character or {})
    m = _single_ai_seat(s)
    if m is not None:
        m.override = char.to_dict()
    set_character_override(s, char)
    sc.store.save(s)
    eff = effective_character(s, sc.lore)
    return {"character": eff.to_dict() if eff else None, "scoped": True}


@router.delete("/sessions/{sid}/character/override")
def session_clear_override(sid: str, request: Request):
    """还原为库主卡（删除会话副本）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    m = _single_ai_seat(s)
    if m is not None:
        m.override = None
    clear_character_override(s)
    sc.store.save(s)
    eff = effective_character(s, sc.lore)
    return {"ok": True, "character": eff.to_dict() if eff else None, "scoped": False}


@router.post("/sessions/{sid}/character/publish")
def session_publish(sid: str, request: Request):
    """把会话所用角色发布为新角色卡（供其他对话用）。"""
    sc = request.app.state.flyai.user(request)
    s = _must_get_session(sc, sid)
    m = _single_ai_seat(s)
    eff = member_character(m, sc.lore) if m is not None else effective_character(s, sc.lore)
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
    m = _single_ai_seat(s)
    eff = member_character(m, sc.lore) if m is not None else effective_character(s, sc.lore)
    if eff is None:
        raise HTTPException(404, "会话未绑定角色")
    data = eff.to_dict()
    from ..lore.avatar_image import embed_avatar

    image = embed_avatar(getattr(sc, "upload_dir", None), eff.avatar)
    if image is not None:
        data["avatar_image"] = image
    body = {"type": "flyai-character", "version": 1, "name": eff.name, "data": data}
    head = eff.id[:8] or "scoped"
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="character-{head}.json"'},
    )


# ---- L4-B：酒馆卡沙箱的会话副本写入 ----
#
# 卡只发「意图」，用户在前端逐次确认后才会到达这里。所有写入只落会话副本
# （member.override / session.*_override / 消息 stat_data），根卡与共享卡
# 永不改动；写回要求用户开启 st_card_write_enabled，且有回答运行中时拒绝。

_ST_MVU_MAX_BYTES = 128 * 1024
_ST_MVU_MAX_DEPTH = 16
_ST_MVU_MAX_OPS = 200
_ST_MVU_MAX_PATH = 512
_ST_MVU_OPS = frozenset({"add", "remove", "replace", "move", "copy", "test"})
_ST_WORLD_MAX_ENTRIES = 500
_ST_WORLD_MAX_BYTES = 2 * 1024 * 1024
_ST_CHAR_MAX_BYTES = 1024 * 1024
_ST_CHAR_FIELDS = frozenset({
    "name", "intro", "background", "mes_example", "table",
    "prompt_format", "prompt_tools", "system_prompt",
    "post_history_instructions", "first_mes", "alternate_greetings",
})
_ST_CHAR_TEXT_FIELDS = _ST_CHAR_FIELDS - {
    "table", "alternate_greetings",
}


def _json_size(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        raise HTTPException(400, "数据不是合法 JSON")


def _json_depth(value, limit: int) -> int:
    """迭代求 JSON 嵌套深度；超过上限提前返回。"""
    stack = [(value, 1)]
    while stack:
        cur, depth = stack.pop()
        if depth > limit:
            return depth
        if isinstance(cur, dict):
            stack.extend((v, depth + 1) for v in cur.values())
        elif isinstance(cur, list):
            stack.extend((v, depth + 1) for v in cur)
    return 1


def _st_write_guard(request, sid: str):
    """L4-B 公共校验：会话存在、用户开启写回、非只读、无运行中的回答。"""
    state = request.app.state.flyai
    sc = state.user(request)
    s = _must_get_session(sc, sid)
    us = getattr(sc, "user_settings", None)
    if not bool(getattr(us, "st_card_write_enabled", False)):
        raise HTTPException(403, "卡内写回未开启")
    if (getattr(s, "mode", "normal") or "normal") == "galgame":
        raise HTTPException(400, "只读会话不支持卡内写回")
    run_manager = getattr(state, "run_manager", None)
    if run_manager is not None and run_manager.active_in_session(sc.uid, sid) is not None:
        raise HTTPException(409, "回答正在进行中，请等待结束后再操作")
    return state, sc, s


def _st_dm_thread(s, tid: Optional[str]):
    t = s.thread(tid) if tid else primary_thread(s)
    if t is None:
        raise HTTPException(404 if tid else 400, "私聊窗口不存在")
    if t.kind != "dm":
        raise HTTPException(400, "仅私聊窗口支持卡内写回")
    return t


def _st_last_assistant(t):
    """该窗口最后一条可见且有正文的 assistant 消息；没有则 None。"""
    for m in reversed(t.messages):
        if m.role != "assistant":
            continue
        if getattr(m, "hidden", False) or getattr(m, "tombstone", None):
            continue
        if not (m.content or "").strip():
            continue
        return m
    return None


class StMvuBody(BaseModel):
    mode: str = "patch"
    stat_data: Optional[Dict] = None
    ops: Optional[List[Dict]] = None
    thread_id: Optional[str] = None


@router.post("/sessions/{sid}/st/mvu")
def st_write_mvu(sid: str, body: StMvuBody, request: Request):
    """卡内变量写回：落点为本窗口最后一条可见 AI 消息的 stat_data 快照。"""
    from ..st import mvu as st_mvu

    _state, sc, s = _st_write_guard(request, sid)
    t = _st_dm_thread(s, body.thread_id)
    mode = (body.mode or "patch").strip().lower()
    if mode not in ("patch", "replace"):
        raise HTTPException(400, "mode 仅支持 patch/replace")

    if mode == "replace":
        if not isinstance(body.stat_data, dict):
            raise HTTPException(400, "replace 需要 stat_data 对象")
        if _json_size(body.stat_data) > _ST_MVU_MAX_BYTES:
            raise HTTPException(413, "变量数据超过 128KB 上限")
        if _json_depth(body.stat_data, _ST_MVU_MAX_DEPTH) > _ST_MVU_MAX_DEPTH:
            raise HTTPException(400, "变量嵌套层级过深")
        new_state = copy.deepcopy(body.stat_data)
    else:
        ops = body.ops
        if not isinstance(ops, list) or not ops:
            raise HTTPException(400, "patch 需要非空 ops 数组")
        if len(ops) > _ST_MVU_MAX_OPS:
            raise HTTPException(400, "ops 数量超过 200 上限")
        for op in ops:
            if not isinstance(op, dict):
                raise HTTPException(400, "op 必须是对象")
            name = str(op.get("op") or "").strip().lower()
            if name not in _ST_MVU_OPS:
                raise HTTPException(400, "不支持的 patch 操作: %s" % name)
            path = op.get("path")
            if not isinstance(path, str) or not path or len(path) > _ST_MVU_MAX_PATH:
                raise HTTPException(400, "patch 路径非法")
        if _json_size(ops) > _ST_MVU_MAX_BYTES:
            raise HTTPException(413, "变量更新超过 128KB 上限")
        if _json_depth(ops, _ST_MVU_MAX_DEPTH) > _ST_MVU_MAX_DEPTH:
            raise HTTPException(400, "变量嵌套层级过深")
        base = (getattr(_st_last_assistant(t), "stat_data", None)
                or (s.mvu or {}).get("stat_data") or {})
        try:
            new_state = st_mvu.apply_patch(base, ops)
        except st_mvu.PatchError as exc:
            raise HTTPException(400, str(exc))

    target = _st_last_assistant(t)
    if target is None:
        raise HTTPException(400, "当前窗口没有可写回的 AI 回复")
    target.stat_data = copy.deepcopy(new_state)
    versions.sync_active_reply(t, target)
    s.mvu = dict(s.mvu or {})
    s.mvu["stat_data"] = copy.deepcopy(new_state)
    sc.store.save_merged(s)
    return {"stat_data": new_state}


class StWorldBody(BaseModel):
    entries: List[Dict] = []


@router.post("/sessions/{sid}/st/world")
def st_write_world(sid: str, body: StWorldBody, request: Request):
    """卡内世界书写回：只替换会话副本的条目表；未派生副本时先派生。"""
    from ..lore.models import LoreEntry

    _state, sc, s = _st_write_guard(request, sid)
    raw = body.entries
    if not isinstance(raw, list):
        raise HTTPException(400, "entries 必须是数组")
    if len(raw) > _ST_WORLD_MAX_ENTRIES:
        raise HTTPException(400, "世界书条目超过 500 上限")
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise HTTPException(400, "世界书条目必须是对象")
        entries.append(LoreEntry.from_dict(item).to_dict())
    if _json_size(entries) > _ST_WORLD_MAX_BYTES:
        raise HTTPException(413, "世界书数据超过 2MB 上限")

    if not s.world_override:
        try:
            fork_world(s, sc.lore)
        except ValueError:
            raise HTTPException(400, "world-not-bound")
    override = dict(s.world_override or {})
    override["table"] = entries
    s.world_override = override
    # 不能用 save_merged：world_override 在 MERGED_FIELDS 里，回填会把本次写入抹掉
    sc.store.save(s)
    return {"entries": entries, "scoped": True}


class StCharacterBody(BaseModel):
    fields: Dict = {}


@router.post("/sessions/{sid}/st/character")
def st_write_character(sid: str, body: StCharacterBody, request: Request):
    """卡内角色副本写回：字段白名单，永不触碰 id/作者/公开/头像/音色等。"""
    from ..lore.models import Character, LoreEntry

    _state, sc, s = _st_write_guard(request, sid)
    m = _single_ai_seat(s)
    if m is None or primary_thread(s) is None:
        raise HTTPException(400, "仅单角色私聊支持卡内写回角色")
    fields = body.fields
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(400, "fields 不能为空")
    for key in fields:
        if key not in _ST_CHAR_FIELDS:
            raise HTTPException(400, "不允许写回的字段: %s" % key)
    for key, value in fields.items():
        if key in _ST_CHAR_TEXT_FIELDS and not isinstance(value, str):
            raise HTTPException(400, "字段 %s 必须是字符串" % key)
        if key == "alternate_greetings" and (
            not isinstance(value, list)
            or any(not isinstance(g, str) for g in value)
        ):
            raise HTTPException(400, "alternate_greetings 必须是字符串数组")
        if key == "table" and not isinstance(value, list):
            raise HTTPException(400, "table 必须是数组")
    if _json_size(fields) > _ST_CHAR_MAX_BYTES:
        raise HTTPException(413, "角色写回数据超过 1MB 上限")

    base = member_character(m, sc.lore)
    if base is None:
        raise HTTPException(400, "会话未绑定角色")
    data = base.to_dict()
    patch = dict(fields)
    if "table" in patch:
        patch["table"] = [
            LoreEntry.from_dict(e).to_dict()
            for e in (patch["table"] or []) if isinstance(e, dict)
        ]
    data.update(patch)
    char = Character.from_dict(data)
    m.override = char.to_dict()
    # 顶层仅作镜像（导出/effective_character 与座位保持一致）
    s.character_override = copy.deepcopy(m.override)
    # 不能用 save_merged：character_override 在 MERGED_FIELDS 里，回填会抹掉镜像
    sc.store.save(s)
    return {"character": char.to_dict(), "scoped": True}


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
        "session": {
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
            "attributes": s.attributes,
            "opening_entry": getattr(s, "opening_entry", "") or "",
        },
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
        opening_entry=meta.get("opening_entry", "") or "",
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


def _st_payload(s, char, lore, user_settings, cfg=None,
                level: int = 0, is_admin: bool = False) -> dict:
    """会话打开时下发的酒馆扩展数据：正则脚本（展示侧）与开关状态。

    只下发角色卡内的声明式数据；前端展示侧用原生 JS 正则解释。卡内
    JavaScript 默认不执行，只有用户显式开启沙箱时，整页 HTML+JS 应用才会
    在无同源权限的隔离 iframe 中运行。
    """
    def _flag(name, default):
        if user_settings is None:
            return default
        value = getattr(user_settings, name, None)
        return default if value is None else bool(value)

    # 服务端图片代理（方案 4）：管理员开启且用户等级达标才可用
    proxy_available = False
    seg = getattr(cfg, "st_image", None) if cfg is not None else None
    if seg is not None and bool(getattr(seg, "enabled", False)):
        required = int(getattr(seg, "min_level", 256) or 0)
        proxy_available = bool(is_admin) or int(level or 0) >= required
    # 受控网络代理（L4-C）：默认关闭；管理员开启且等级达标才下发可用标志
    net_available = False
    net_seg = getattr(cfg, "st_net", None) if cfg is not None else None
    if net_seg is not None and bool(getattr(net_seg, "enabled", False)):
        required = int(getattr(net_seg, "min_level", 0) or 0)
        net_available = bool(is_admin) or int(level or 0) >= required
    flags = {
        "regex_prompt": _flag("st_regex_prompt_enabled", True),
        "regex_display": _flag("st_regex_display_enabled", True),
        "html": _flag("st_html_enabled", True),
        "external_images": _flag("st_external_images_enabled", False),
        "image_proxy": proxy_available
        and _flag("st_image_proxy_enabled", False),
        "image_proxy_available": proxy_available,
        # 沙箱执行卡内 JS：管理员默认开启，普通用户默认关闭（可在设置里自行打开）
        "js_sandbox": _flag("st_js_sandbox_enabled", bool(is_admin)),
        # L4：卡内授权动作 / 会话副本写入 / 受控网络（逐次确认，默认关闭）
        "card_actions": _flag("st_card_actions_enabled", False),
        "card_write": _flag("st_card_write_enabled", False),
        "net_proxy": net_available and _flag("st_net_proxy_enabled", False),
        "net_proxy_available": net_available,
        "mvu": _flag("mvu_enabled", True),
    }
    mvu_cfg = getattr(cfg, "mvu", None) if cfg is not None else None
    if mvu_cfg is not None and not bool(getattr(mvu_cfg, "enabled", True)):
        flags["mvu"] = False
    by_character = {}
    if lore is not None:
        ids = [getattr(m, "character_id", "") for m in s.members]
        if s.character_id:
            ids.append(s.character_id)
        for raw_id in ids:
            cid = (raw_id or "").strip() if isinstance(raw_id, str) else ""
            if not cid or cid in by_character:
                continue
            try:
                c = lore.get_character(cid)
            except Exception:
                c = None
            scripts = getattr(c, "regex_scripts", None) if c is not None else None
            if scripts:
                by_character[cid] = scripts
    from ..st.panels import detect_panels

    fallback = list(getattr(char, "regex_scripts", None) or []) if char else []
    return {
        "regex_scripts": fallback,
        "by_character": by_character,
        "panels": detect_panels(char),
        "html": {
            "enabled": flags["html"],
            "external_images": flags["external_images"],
            "image_proxy": flags["image_proxy"],
        },
        "flags": flags,
        "mvu": {
            "enabled": bool(flags["mvu"]),
            "stat_data": (
                (getattr(s, "mvu", None) or {}).get("stat_data") or {}
            ),
        },
    }


def _session_dict(s, with_messages: bool = False, lore=None,
                  user_settings=None, cfg=None, level: int = 0,
                  is_admin: bool = False):
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
        "opening_entry": getattr(s, "opening_entry", "") or "",
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
            d["st"] = _st_payload(
                s, char, lore, user_settings, cfg=cfg,
                level=level, is_admin=is_admin,
            )
    return d