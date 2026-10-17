"""记忆管理接口（P6）：浏览/删除结构化记忆条目、统计、召回日志。

所有操作限定在当前用户自己的 character-memory 目录内，
char_id 经 CharacterMemoryStore 的 safe_child 校验，不可路径穿越。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from ..sessions.resolve import member_character

router = APIRouter(prefix="/api/my/memory", tags=["memory"])


def _store(request: Request):
    scope = request.app.state.flyai.user(request)
    return scope, getattr(scope, "char_memory", None)


def _book_ids(store) -> List[str]:
    """该用户记忆目录下所有记忆簿的角色 id（文件名来自磁盘，去掉 .ledger.json）。"""
    out: List[str] = []
    for p in sorted(store._dir.glob("*.json")):
        if p.name.endswith(".ledger.json"):
            continue
        out.append(p.name[: -len(".json")])
    return out


def _entry_dict(entry, char_id: str) -> dict:
    d = entry.to_dict()
    d["char_id"] = char_id
    return d


@router.get("")
def list_memory(
    request: Request,
    char_id: Optional[str] = None,
    q: Optional[str] = "",
    include_superseded: bool = False,
    limit: int = 200,
):
    """列出记忆条目；可按角色过滤、关键词过滤（文本包含，OR 拆词）。"""
    scope, store = _store(request)
    if store is None:
        raise HTTPException(500, "记忆存储不可用")
    char_ids = [char_id] if char_id else _book_ids(store)
    terms = [
        t for t in (q or "").replace("，", " ").replace(",", " ").split() if t
    ]
    out: List[dict] = []
    for cid in char_ids:
        book = store.load_book(cid)
        for e in book.entries:
            if e.superseded and not include_superseded:
                continue
            if terms and not any(t in e.text for t in terms):
                continue
            out.append(_entry_dict(e, cid))
    out.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return {"entries": out[: max(1, min(500, limit))], "total": len(out)}


@router.get("/stats")
def memory_stats(request: Request):
    """记忆条目/台账统计（按角色 + 合计）。"""
    scope, store = _store(request)
    if store is None:
        raise HTTPException(500, "记忆存储不可用")
    per = []
    totals = {"entries": 0, "active": 0, "superseded": 0, "ledger": 0}
    for cid in _book_ids(store):
        book = store.load_book(cid)
        active = sum(1 for e in book.entries if not e.superseded)
        sup = len(book.entries) - active
        ledger_n = sum(
            len(items) for items in store.load_ledger(cid).values()
        )
        per.append({
            "char_id": cid,
            "entries": len(book.entries),
            "active": active,
            "superseded": sup,
            "ledger": ledger_n,
        })
        totals["entries"] += len(book.entries)
        totals["active"] += active
        totals["superseded"] += sup
        totals["ledger"] += ledger_n
    return {"per_char": per, "total": totals}


def _book_stats(store, cid: str) -> dict:
    """单个记忆簿的有效条目/失效条目/台账条数。"""
    book = store.load_book(cid)
    active = sum(1 for e in book.entries if not e.superseded)
    return {
        "active": active,
        "superseded": len(book.entries) - active,
        "ledger": sum(len(items) for items in store.load_ledger(cid).values()),
    }


@router.get("/sources")
def memory_sources(request: Request):
    """记忆来源两级列表：每行「角色卡（副本名）· 房间名」，点击按 char_id 钻取。

    聚合键是记忆簿实际归属（memory_char_id 优先，否则座位 character_id），
    因此会话副本/派生卡座位也能正确归到它写入的簿；同一张卡在多个房间使用
    时产生多行（点击都按该 char_id 过滤）。磁盘有簿但当前没有任何房间引用
    的卡（卡被删/旧数据）给一行「未挂载」兜底，避免条目无处可找。
    """
    scope, store = _store(request)
    if store is None:
        raise HTTPException(500, "记忆存储不可用")

    stats: Dict[str, dict] = {}
    for cid in _book_ids(store):
        st = _book_stats(store, cid)
        if st["active"] or st["superseded"] or st["ledger"]:
            stats[cid] = st

    rows: List[dict] = []
    seen = set()
    linked = set()
    for listed in scope.store.list():
        sess = scope.store.get(listed.id)
        if sess is None:
            continue
        owners = {}
        for m in sess.members:
            if getattr(m, "is_user", False):
                continue
            owner = (
                (getattr(m, "memory_char_id", "") or "").strip()
                or (getattr(m, "character_id", "") or "").strip()
            )
            if not owner or owner in owners:
                continue
            char = member_character(m, scope.lore)
            owners[owner] = (
                (getattr(char, "alias", "") or getattr(char, "name", "") or "")
                if char is not None else ""
            )
        for owner, name in owners.items():
            linked.add(owner)
            key = (owner, sess.id)
            if key in seen:
                continue
            seen.add(key)
            st = stats.get(owner)
            if st is None:
                continue  # 簿尚不存在/为空：不出现在记忆管理里
            rows.append({
                "char_id": owner,
                "char_name": name,
                "session_id": sess.id,
                "session_title": sess.title,
                "active": st["active"],
                "superseded": st["superseded"],
                "ledger": st["ledger"],
                "orphan": False,
            })

    for cid, st in stats.items():
        if cid in linked:
            continue
        rows.append({
            "char_id": cid,
            "char_name": "",
            "session_id": "",
            "session_title": "",
            "active": st["active"],
            "superseded": st["superseded"],
            "ledger": st["ledger"],
            "orphan": True,
        })

    rows.sort(key=lambda r: (r["orphan"], -(r["active"] + r["ledger"]),
                             r["char_name"] or r["char_id"]))
    return {"sources": rows}


@router.get("/ledger")
def memory_ledger(request: Request, char_id: str):
    """某张角色卡的剧情台账全文：人物/物品/地点/任务/伏笔，含变迁史。

    每组内未终结（active/open/planted）在前，其后按更新时间倒序。
    """
    _scope, store = _store(request)
    if store is None:
        raise HTTPException(500, "记忆存储不可用")
    cid = (char_id or "").strip()
    if not cid:
        raise HTTPException(400, "缺少 char_id")
    from ..memory.ledger import ACTIVE_STATUS, KINDS

    ledger = store.load_ledger(cid)
    groups: List[dict] = []
    for kind in KINDS:
        items = [it.to_dict() for it in ledger.get(kind) or []]
        items.sort(key=lambda d: d.get("updated_at") or "", reverse=True)
        active_states = ACTIVE_STATUS[kind]
        items.sort(key=lambda d: 0 if d.get("status") in active_states else 1)
        groups.append({"kind": kind, "items": items})
    return {"char_id": cid, "groups": groups}


@router.get("/recall-logs")
def recall_logs(request: Request, limit: int = 100):
    """最近的召回记录（自动/主动），最新在前。"""
    scope = request.app.state.flyai.user(request)
    state = request.app.state.flyai
    buf = getattr(state, "recall_logs", None)
    if buf is None:
        return {"logs": []}
    return {"logs": buf.list()[: max(1, min(200, limit))]}


@router.delete("/{entry_id}")
def delete_entry(
    request: Request,
    entry_id: str,
    char_id: Optional[str] = None,
):
    """删除一条记忆。可指定 char_id 缩小范围，否则扫描该用户全部记忆簿。

    条目 id 为文本哈希，重复 id 会一并删除。返回删除条数。
    """
    scope, store = _store(request)
    if store is None:
        raise HTTPException(500, "记忆存储不可用")
    if "/" in entry_id or "\\" in entry_id or ".." in entry_id:
        raise HTTPException(400, "条目 ID 非法")
    char_ids = [char_id] if char_id else _book_ids(store)
    removed = 0
    for cid in char_ids:
        book = store.load_book(cid)
        keep = [e for e in book.entries if e.id != entry_id]
        if len(keep) != len(book.entries):
            removed += len(book.entries) - len(keep)
            book.entries = keep
            store.save_book(cid, book)
    if not removed:
        raise HTTPException(404, "条目不存在或不属于当前用户")
    return {"ok": True, "removed": removed}
