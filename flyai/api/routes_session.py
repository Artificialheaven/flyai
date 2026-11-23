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
from ..preset.apply import active_preset_regex_scripts
from ..schemas import Message
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
    strip_heavy_extensions,
    user_seat,
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
    # 标题命中来自轻量摘要；消息正文由 store 的正文索引扫描（摘要里没有 messages）
    message_hits = sc.store.message_search(query, limit * 4)
    hits = []
    for s in sc.store.list():
        title_hit = needle in (s.title or "").casefold()
        count = 1 if title_hit else 0
        snippet = (s.title or "") if title_hit else ""
        hit = message_hits.get(s.id)
        if hit is not None:
            count += hit[0]
            if hit[1]:
                snippet = hit[1]
        if not count:
            continue
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

    changed = st_mvu.sync_session_state(s, sc.lore)
    # EJS 输出侧：打开会话时补标记历史里尚未渲染的模板消息
    from ..template import output as template_output
    from ..template import policy as template_policy

    if template_policy.output_enabled(
        getattr(request.app.state.flyai, "config", None),
        getattr(sc, "user_settings", None),
        int(getattr(sc, "level", 0) or 0),
        sc.uid == ADMIN_UID,
    ):
        changed = bool(template_output.mark_pending_output(s, True)) or changed
    if changed:
        sc.store.save(s)
    payload = _session_dict(
        s, with_messages=True, lore=sc.lore,
        user_settings=getattr(sc, "user_settings", None),
        cfg=getattr(request.app.state.flyai, "config", None),
        level=int(getattr(sc, "level", 0) or 0),
        is_admin=sc.uid == ADMIN_UID,
        preset_scripts=active_preset_regex_scripts(sc),
    )
    # 条件请求：会话文件（可能刚被 MVU/模板标记重写）的 mtime+size 作为弱 ETag。
    # 命中时返回 304，省掉整个 JSON body 的传输与下发（压缩在前者之后）。
    stat = sc.store.stat(sid) or {}
    etag = 'W/"sess-%s-%s"' % (stat.get("mtime_ns", 0), stat.get("size", 0))
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)


@router.get("/sessions/{sid}/messages/{mid}/prompt")
def get_message_prompt(sid: str, mid: str, request: Request):
    """查看某条回复实际发给模型的提示词（system/history/tools/参数）。"""
    sc = request.app.state.flyai.user(request)
    _safe_sid(sid)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    anchor = ""
    for thread in s.threads or []:
        ids = [m.id for m in thread.messages]
        if mid not in ids:
            continue
        for msg in reversed(thread.messages[: ids.index(mid) + 1]):
            if msg.role == "user" and msg.id:
                anchor = msg.id
                break
        break
    if not anchor:
        for msg in reversed(s.messages or []):
            if msg.id == mid:
                continue
            if msg.role == "user" and msg.id:
                anchor = msg.id
                break
    from .. import prompt_archive

    data = prompt_archive.load_turn(
        getattr(request.app.state.flyai, "data_dir", None),
        sc.uid, sid, anchor or mid,
    )
    if data is None:
        raise HTTPException(404, "该消息没有提示词记录（功能上线前的历史消息不回填）")
    return data


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
            m.override = strip_heavy_extensions(copy.to_dict())
            s.character_override = strip_heavy_extensions(copy.to_dict())
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
    # 响应里剥离了酒馆助手脚本（体积过大，只经 st.author 下发）；
    # 编辑器回存时不带这段，这里从现有副本补回，避免丢失卡内脚本。
    prev = getattr(s, "character_override", None)
    prev_ext = prev.get("extensions") if isinstance(prev, dict) else None
    prev_ext = prev_ext if isinstance(prev_ext, dict) else {}
    incoming = char.extensions if isinstance(char.extensions, dict) else {}
    for key in ("tavern_helper", "TavernHelper"):
        if key not in incoming and key in prev_ext:
            incoming[key] = copy.deepcopy(prev_ext[key])
    char.extensions = incoming
    m = _single_ai_seat(s)
    if m is not None:
        m.override = strip_heavy_extensions(char.to_dict())
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


def _st_mvu_max_bytes(sc) -> int:
    """用户可调的单次 chat 变量写回上限（KB），默认 128，范围 64–4096。"""
    kb = getattr(getattr(sc, "user_settings", None), "st_mvu_max_kb", None)
    try:
        kb = int(kb)
    except (TypeError, ValueError):
        kb = 0
    if kb <= 0:
        kb = 128
    kb = max(64, min(4096, kb))
    return kb * 1024
_ST_MVU_MAX_PATH = 512
_ST_MVU_OPS = frozenset({"add", "remove", "replace", "move", "copy", "test"})
_ST_WORLD_MAX_ENTRIES = 500
_ST_WORLD_MAX_BYTES = 2 * 1024 * 1024
_ST_CHAR_MAX_BYTES = 2 * 1024 * 1024


def _st_write_max_bytes(request: Request) -> int:
    """卡内角色/世界书写回上限；管理面板 lore.max_card_write_mb（1–5MB）。"""
    try:
        mb = int(request.app.state.flyai.config.lore.max_card_write_mb)
    except (AttributeError, TypeError, ValueError):
        mb = 2
    return max(1, min(mb, 5)) * 1024 * 1024
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

    max_bytes = _st_mvu_max_bytes(sc)
    if mode == "replace":
        if not isinstance(body.stat_data, dict):
            raise HTTPException(400, "replace 需要 stat_data 对象")
        if _json_size(body.stat_data) > max_bytes:
            raise HTTPException(413, "变量数据超过 %dKB 上限" % (max_bytes // 1024))
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
        if _json_size(ops) > max_bytes:
            raise HTTPException(413, "变量更新超过 %dKB 上限" % (max_bytes // 1024))
        if _json_depth(ops, _ST_MVU_MAX_DEPTH) > _ST_MVU_MAX_DEPTH:
            raise HTTPException(400, "变量嵌套层级过深")
        base = (getattr(_st_last_assistant(t), "stat_data", None)
                or (s.mvu or {}).get("stat_data") or {})
        try:
            new_state = st_mvu.apply_patch(base, ops)
        except st_mvu.PatchError as exc:
            raise HTTPException(400, str(exc))

    target = _st_last_assistant(t)
    # 手机/作者脚本可能在没有可见 AI 楼层的窗口写变量（例如新开聊天、
    # 楼层全部隐藏）。此时仍落会话级 s.mvu，保证刷新后能读回。
    if target is not None:
        target.stat_data = copy.deepcopy(new_state)
        versions.sync_active_reply(t, target)
    s.mvu = dict(s.mvu or {})
    s.mvu["stat_data"] = copy.deepcopy(new_state)
    sc.store.save_merged(s)
    return {"stat_data": new_state}


class StWorldBody(BaseModel):
    entries: List[Dict] = []
    # 卡内运行时不需要回显整份条目表（大卡可达数 MB），可只回结果
    compact: bool = False


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
    max_bytes = _st_write_max_bytes(request)
    if _json_size(entries) > max_bytes:
        raise HTTPException(413, "世界书数据超过 %dMB 上限" % (max_bytes // (1024 * 1024)))

    if not s.world_override:
        try:
            fork_world(s, sc.lore)
        except ValueError:
            # 没有绑定世界观时，用角色卡自带世界书派生一份会话副本，
            # 避免卡内 /api/worldinfo/edit 因 world-not-bound 整段失败。
            char = effective_character(s, sc.lore)
            if char is None:
                raise HTTPException(400, "world-not-bound")
            ext = getattr(char, "extensions", None)
            wname = ""
            if isinstance(ext, dict) and isinstance(ext.get("world"), str):
                wname = ext["world"].strip()
            s.world_override = {
                "id": "",
                "name": wname or getattr(char, "name", ""),
                "avatar": "",
                "content": "",
                "table": [e.to_dict() for e in (getattr(char, "table", None) or [])],
                "prompt_format": "",
                "prompt_tools": "",
            }
    override = dict(s.world_override or {})
    override["table"] = entries
    s.world_override = override
    # 不能用 save_merged：world_override 在 MERGED_FIELDS 里，回填会把本次写入抹掉
    sc.store.save(s)
    if body.compact:
        return {"ok": True, "scoped": True}
    return {"entries": entries, "scoped": True}


class StChatMetadataBody(BaseModel):
    metadata: Dict = {}


@router.get("/sessions/{sid}/st/chat-metadata")
def st_get_chat_metadata(sid: str, request: Request):
    """卡内 chatMetadata 读取（地图/手札存档）。"""
    _state, _sc, s = _st_write_guard(request, sid)
    author = (getattr(s, "attributes", None) or {}).get("author") or {}
    data = author.get("chat_metadata") if isinstance(author, dict) else None
    return {"chat_metadata": data if isinstance(data, dict) else {}}


@router.post("/sessions/{sid}/st/chat-metadata")
def st_set_chat_metadata(sid: str, body: StChatMetadataBody, request: Request):
    """卡内 chatMetadata 写回；上限 1MB，存 session.attributes.author。"""
    _state, sc, s = _st_write_guard(request, sid)
    if not isinstance(body.metadata, dict):
        raise HTTPException(400, "metadata 必须是对象")
    if _json_size(body.metadata) > 1024 * 1024:
        raise HTTPException(413, "chatMetadata 超过 1MB 上限")
    s.attributes = dict(getattr(s, "attributes", None) or {})
    author = dict(s.attributes.get("author") or {})
    author["chat_metadata"] = copy.deepcopy(body.metadata)
    s.attributes["author"] = author
    sc.store.save_merged(s)
    return {"ok": True}


class StMessagePatch(BaseModel):
    id: str = ""
    message: Optional[str] = None
    data: Optional[Dict] = None
    is_hidden: Optional[bool] = None
    role: Optional[str] = None
    name: Optional[str] = None
    # ST message.extra（如小手机的 dal_phone_source 楼层身份），saveChat 落库
    extra: Optional[Dict] = None


class StMessageCreate(BaseModel):
    """卡内 createChatMessages 的新楼层（酒馆 message 形状）。"""

    role: str = "user"
    name: Optional[str] = None
    message: Optional[str] = None
    data: Optional[Dict] = None
    extra: Optional[Dict] = None
    is_hidden: Optional[bool] = None


class StMessagesBody(BaseModel):
    thread_id: Optional[str] = None
    changes: List[StMessagePatch] = []
    creates: List[StMessageCreate] = []


@router.patch("/sessions/{sid}/st/messages")
def st_patch_messages(sid: str, body: StMessagesBody, request: Request):
    """卡内 setChatMessages 落库：按消息 id 修改正文/隐藏/变量快照；creates 追加新楼层。"""
    _state, sc, s = _st_write_guard(request, sid)
    t = _st_dm_thread(s, body.thread_id)
    if len(body.changes) > 200:
        raise HTTPException(400, "单次修改超过 200 条上限")
    if len(body.creates) > 20:
        raise HTTPException(400, "单次新增超过 20 条上限")
    by_id = {m.id: m for m in t.messages if getattr(m, "id", "")}
    updated = []
    created = []
    for change in body.changes:
        target = by_id.get(change.id)
        if target is None:
            continue
        if change.message is not None:
            target.content = str(change.message)
        if change.role in ("user", "assistant", "system"):
            target.role = change.role
        if change.name is not None:
            target.name = str(change.name)
        if change.is_hidden is not None:
            target.hidden = bool(change.is_hidden)
        if change.data is not None:
            if not isinstance(change.data, dict):
                raise HTTPException(400, "data 必须是对象")
            if _json_size(change.data) > _ST_MVU_MAX_BYTES:
                raise HTTPException(413, "变量数据超过 128KB 上限")
            target.stat_data = copy.deepcopy(
                change.data.get("stat_data")
                if isinstance(change.data.get("stat_data"), dict)
                else change.data
            )
            # 只有最后一条楼层代表当前状态；改历史楼层不能覆盖会话级变量
            if t.messages and target is t.messages[-1]:
                s.mvu = dict(getattr(s, "mvu", None) or {})
                s.mvu["stat_data"] = copy.deepcopy(target.stat_data)
        if change.extra is not None:
            if not isinstance(change.extra, dict):
                raise HTTPException(400, "extra 必须是对象")
            if _json_size(change.extra) > _ST_MVU_MAX_BYTES:
                raise HTTPException(413, "extra 超过 128KB 上限")
            target.st_extra = copy.deepcopy(change.extra)
        updated.append(change.id)
    for create in body.creates:
        role = create.role if create.role in ("user", "assistant", "system") else "user"
        if create.data is not None:
            if not isinstance(create.data, dict):
                raise HTTPException(400, "data 必须是对象")
            if _json_size(create.data) > _ST_MVU_MAX_BYTES:
                raise HTTPException(413, "变量数据超过 128KB 上限")
        if create.extra is not None:
            if not isinstance(create.extra, dict):
                raise HTTPException(400, "extra 必须是对象")
            if _json_size(create.extra) > _ST_MVU_MAX_BYTES:
                raise HTTPException(413, "extra 超过 128KB 上限")
        stat_data = None
        if create.data is not None:
            stat_data = copy.deepcopy(
                create.data.get("stat_data")
                if isinstance(create.data.get("stat_data"), dict)
                else create.data
            )
        msg = Message(
            role=role,
            content=str(create.message or ""),
            name=create.name,
            hidden=create.is_hidden,
            stat_data=stat_data,
            st_extra=copy.deepcopy(create.extra) if create.extra is not None else None,
            origin="card",
        )
        t.messages.append(msg)
        # 新建的最后一条楼层带 MVU 信封（如开场初始化）：同步会话级状态，
        # 面板与下一轮提示词立即可见，不必等 AI 回复再写一次 UpdateVariable。
        if stat_data is not None:
            s.mvu = dict(getattr(s, "mvu", None) or {})
            s.mvu["stat_data"] = copy.deepcopy(stat_data)
        created.append(
            {
                "id": msg.id,
                "message_id": len(t.messages) - 1,
                "role": msg.role,
            }
        )
    if updated or created:
        sc.store.save_merged(s)
    return {"updated": updated, "created": created}


class AuthorStateBody(BaseModel):
    phone_mainline: Optional[str] = None
    prompts: Optional[List[Dict]] = None
    remove_prompts: Optional[List[str]] = None
    chat_metadata: Optional[Dict] = None


class AuthorGenerateBody(BaseModel):
    purpose: str = "phone"
    messages: List[Dict] = []
    system_prompt: str = ""
    max_tokens: int = 0


@router.post("/sessions/{sid}/author/state")
def author_state(sid: str, body: AuthorStateBody, request: Request):
    """作者面板派生状态：手机主线摘要、剧情锚点/场记提示词注入。"""
    sc = request.app.state.flyai.user(request)
    _safe_sid(sid)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    author = dict((s.attributes or {}).get("author") or {})
    if body.phone_mainline is not None:
        author["phone_mainline"] = str(body.phone_mainline)[:131072]
    if body.prompts is not None:
        current = {
            str(item.get("id")): item
            for item in (author.get("prompts") or [])
            if isinstance(item, dict) and item.get("id")
        }
        for raw in body.prompts[:20]:
            if not isinstance(raw, dict):
                continue
            pid = str(raw.get("id") or "")[:120]
            if not pid:
                continue
            current[pid] = {
                "id": pid,
                "content": str(raw.get("content") or "")[:16384],
                "position": raw.get("position"),
                "depth": raw.get("depth"),
                "scan": bool(raw.get("scan")),
                "role": raw.get("role"),
            }
        author["prompts"] = list(current.values())
    if body.remove_prompts:
        remove = {str(item) for item in body.remove_prompts}
        author["prompts"] = [
            item for item in (author.get("prompts") or [])
            if str(item.get("id")) not in remove
        ]
    if body.chat_metadata is not None:
        raw_meta = json.dumps(
            body.chat_metadata, ensure_ascii=False, separators=(",", ":")
        )
        if len(raw_meta.encode("utf-8")) > 1024 * 1024:
            raise HTTPException(413, "聊天元数据超过 1MB 上限")
        author["chat_metadata"] = body.chat_metadata
    s.attributes = dict(s.attributes or {})
    s.attributes["author"] = author
    sc.store.save_merged(s)
    return {"ok": True}


@router.post("/sessions/{sid}/author/generate")
async def author_generate(sid: str, body: AuthorGenerateBody, request: Request):
    """作者面板静默生成：复用当前会话模型，不写入主聊天记录。"""
    state = request.app.state.flyai
    sc = state.user(request)
    _safe_sid(sid)
    s = sc.store.get(sid)
    if s is None:
        raise HTTPException(404, "会话不存在")
    us = getattr(sc, "user_settings", None)
    is_admin = sc.uid == ADMIN_UID or int(getattr(sc, "level", 0) or 0) >= 256
    allowed = getattr(us, "st_author_scripts_enabled", None)
    if allowed is None:
        allowed = bool(is_admin)
    if not allowed:
        raise HTTPException(403, "作者脚本未开启")
    if body.purpose not in ("phone", "memory", "social"):
        raise HTTPException(400, "不支持的生成用途")
    messages = [
        item for item in body.messages
        if isinstance(item, dict)
        and str(item.get("role") or "") in ("system", "user", "assistant")
        and isinstance(item.get("content"), str)
    ]
    if not messages:
        raise HTTPException(400, "messages 不能为空")
    if len(messages) > 40:
        raise HTTPException(400, "消息条数过多")
    total = sum(len(item["content"]) for item in messages)
    if total > 64000:
        raise HTTPException(413, "消息内容过大")
    max_tokens = max(0, min(int(body.max_tokens or 0), 8192))

    from ..agent.loop import DEFAULT_SYSTEM_PROMPT
    from ..lore.query import build_member_system_prompt
    from ..models_access import visible_providers
    from ..providers.complete import complete
    from ..schemas import Message

    seat = _single_ai_seat(s)
    char = member_character(seat, sc.lore) if seat is not None else None
    world = sc.lore.get_world(s.world_id) if s.world_id else None
    try:
        system_prompt = build_member_system_prompt(
            name=getattr(char, "name", "") or "",
            memory="",
            attributes=None,
            audience=[],
            character=char,
            world=world,
            base=DEFAULT_SYSTEM_PROMPT,
            roleplay=None,
            user_character=None,
            variables=dict((s.mvu or {}).get("stat_data") or {}) or None,
        )
    except Exception:
        system_prompt = DEFAULT_SYSTEM_PROMPT
    if body.system_prompt.strip():
        system_prompt = system_prompt + "\n\n" + body.system_prompt.strip()[:16000]

    wanted = (s.provider_id or state.config.active_provider or "").strip()
    visible = visible_providers(state.config, sc.uid, sc.level)
    cfg = next((p for p in visible if p.id == wanted), None)
    if cfg is None:
        exists = any(p.id == wanted for p in state.config.providers)
        if exists:
            raise HTTPException(403, "你的权限等级不足以使用该模型")
        raise HTTPException(400, "未配置可用提供商")
    provider = state.get_provider(cfg.id)
    try:
        text = await complete(
            provider,
            [Message(role=item["role"], content=item["content"]) for item in messages],
            system_prompt=system_prompt,
            max_tokens=max_tokens or None,
            session_id=s.id,
            partial_ok=True,
        )
    except Exception as exc:
        raise HTTPException(502, "生成失败：%s" % str(exc)[:200])
    return {"ok": True, "text": text}


class StCharacterBody(BaseModel):
    fields: Dict = {}
    # 角色副本可能带十几 MB 的 extensions；卡内运行时不需要回显整卡
    compact: bool = False


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
    max_bytes = _st_write_max_bytes(request)
    if _json_size(fields) > max_bytes:
        raise HTTPException(413, "角色写回数据超过 %dMB 上限" % (max_bytes // (1024 * 1024)))

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
    # 酒馆助手脚本只存库主卡一份；会话副本剥离，读取时再补回
    m.override = strip_heavy_extensions(char.to_dict())
    # 顶层仅作镜像（导出/effective_character 与座位保持一致）
    s.character_override = copy.deepcopy(m.override)
    # 不能用 save_merged：character_override 在 MERGED_FIELDS 里，回填会抹掉镜像
    sc.store.save(s)
    if body.compact:
        return {"ok": True, "scoped": True}
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
                level: int = 0, is_admin: bool = False,
                preset_scripts=None) -> dict:
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
        # 酒馆助手（TavernHelper）作者脚本：管理员默认开，普通用户默认关
        "author_scripts": _flag("st_author_scripts_enabled", bool(is_admin)),
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
    from ..st.author_scripts import collect_author_scripts
    from ..st.panels import detect_panels
    from ..template import policy as template_policy

    # EJS 模板（方案 B）：客户端据此决定是否需要补渲染与能否回传 ops。
    # global 作用域按用户单独存储，客户端按需 GET /api/template/vars。
    tvars = getattr(s, "template_vars", None) or {}
    template_local = tvars.get("local") if isinstance(tvars, dict) else {}
    template_message = tvars.get("message") if isinstance(tvars, dict) else {}
    template_flags = {
        "prompt_enabled": template_policy.prompt_enabled(
            cfg, user_settings, level, is_admin),
        "output_enabled": template_policy.output_enabled(
            cfg, user_settings, level, is_admin),
        "vars": {
            "local": template_local if isinstance(template_local, dict) else {},
            "message": template_message if isinstance(template_message, dict) else {},
        },
    }

    fallback = list(getattr(char, "regex_scripts", None) or []) if char else []
    # 启用预设附带的全局正则：与卡正则同受展示开关控制
    global_scripts = (
        list(preset_scripts or []) if flags["regex_display"] else []
    )
    # 酒馆助手作者脚本：只在开关开启且卡内确实有脚本时下发正文
    author_scripts = []
    author_truncated = False
    if flags["author_scripts"] and char is not None:
        try:
            author_mb = int(getattr(getattr(cfg, "lore", None), "max_author_script_mb", 5))
        except (TypeError, ValueError):
            author_mb = 5
        author_mb = max(1, min(author_mb, 5))
        author_limit = author_mb * 1024 * 1024
        collected = collect_author_scripts(
            char, max_script_chars=author_limit, max_total_chars=author_limit
        )
        author_scripts = collected["scripts"]
        author_truncated = collected["truncated"]
    panels = detect_panels(char)
    # 有作者脚本时，面板由前端作者运行时生成；消息正文里的正则替换结果
    # 只是“脚本未启动”的兜底文案，前端会改用下方状态面板。
    if author_scripts:
        for panel in panels:
            panel["author"] = True
    author_state = (getattr(s, "attributes", None) or {}).get("author") or {}
    chat_metadata = (
        author_state.get("chat_metadata")
        if isinstance(author_state, dict) else None
    )
    author = {
        "enabled": bool(flags["author_scripts"]),
        "scripts": author_scripts,
        "truncated": author_truncated,
        "chat_metadata": chat_metadata if isinstance(chat_metadata, dict) else {},
        "character": {
            "name": getattr(char, "name", "") if char is not None else "",
            "description": getattr(char, "intro", "") if char is not None else "",
            "avatar": getattr(char, "avatar", "") if char is not None else "",
        },
    }
    return {
        "regex_scripts": fallback,
        "regex_scripts_global": global_scripts,
        "by_character": by_character,
        "panels": panels,
        "author": author,
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
        "template": template_flags,
    }


def _strip_heavy_extensions(char_dict):
    """响应里去掉只经 st.author 下发一次的酒馆助手脚本，避免重复十几 MB。"""
    if not isinstance(char_dict, dict) or not isinstance(char_dict.get("extensions"), dict):
        return char_dict
    extensions = dict(char_dict["extensions"])
    if "tavern_helper" not in extensions and "TavernHelper" not in extensions:
        return char_dict
    extensions.pop("tavern_helper", None)
    extensions.pop("TavernHelper", None)
    out = dict(char_dict)
    out["extensions"] = extensions
    return out


def _member_dict_for_client(m):
    d = m.to_dict()
    if isinstance(d.get("override"), dict):
        d["override"] = _strip_heavy_extensions(d["override"])
    return d


def _session_dict(s, with_messages: bool = False, lore=None,
                  user_settings=None, cfg=None, level: int = 0,
                  is_admin: bool = False, preset_scripts=None):
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
        "members": [_member_dict_for_client(m) for m in s.members],
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
            user_char = member_character(user_seat(s), lore)
            char_dict = _strip_heavy_extensions(char.to_dict() if char else None)
            d["character"] = char_dict
            d["world"] = world.to_dict() if world else None
            # 展示侧宏展开 {{user}} 用；与提示词侧回退值保持一致
            d["user_name"] = (
                getattr(user_char, "name", "") if user_char else "用户"
            ) or "用户"
            d["st"] = _st_payload(
                s, char, lore, user_settings, cfg=cfg,
                level=level, is_admin=is_admin,
                preset_scripts=preset_scripts,
            )
    return d