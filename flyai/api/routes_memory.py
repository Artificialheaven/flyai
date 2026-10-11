"""记忆管理接口（P6）：浏览/删除结构化记忆条目、统计、召回日志。

所有操作限定在当前用户自己的 character-memory 目录内，
char_id 经 CharacterMemoryStore 的 safe_child 校验，不可路径穿越。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request

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
