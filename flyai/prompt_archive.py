"""真实提示词归档：把每轮实际发给模型的 system/history/tools 存到用户数据库。

不写进会话 JSON（避免再次把会话撑大数 MB），按「会话 + 消息 id」一行存进
`data/<uid>/prompts.db` 的 `turns` 表。旧布局
`data/<uid>/prompts/<sid>/<message_id>.json` 在首次打开时自动导入并改名
`.bak-<时间戳>`。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ids import check_safe_name
from .sqlite_store import JsonTable, SqliteDB, migrate_legacy_dir

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 200_000
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024

# 同一个用户的 prompts.db 只建一个 JsonTable（连接是线程本地的，缓存跨线程安全）。
_TABLES: Dict[str, JsonTable] = {}
_MIGRATED: set = set()
_GUARD = threading.Lock()


def _truncate(text: Any, limit: int = MAX_MESSAGE_CHARS) -> Any:
    if not isinstance(text, str):
        return text
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（已截断 %d 字符）" % (len(text) - limit)


def _message_dict(msg: Any) -> Dict[str, Any]:
    """Message / dict → 归档结构（只保留查看所需字段）。"""
    if isinstance(msg, dict):
        get = msg.get
        role = get("role", "")
        content = get("content")
        tool_calls = get("tool_calls")
        out: Dict[str, Any] = {"role": role}
    else:
        role = getattr(msg, "role", "")
        content = getattr(msg, "content", None)
        tool_calls = getattr(msg, "tool_calls", None)
        out = {"role": role}
    out["content"] = _truncate(content)
    if tool_calls:
        out["tool_calls"] = tool_calls
    if not isinstance(msg, dict):
        for key in ("name", "tool_call_id", "hidden", "thinking"):
            value = getattr(msg, key, None)
            if value:
                out[key] = _truncate(value) if key == "thinking" else value
    return out


def _tool_dict(tool: Any) -> Optional[Dict[str, Any]]:
    if tool is None:
        return None
    if isinstance(tool, dict):
        return tool
    name = getattr(tool, "name", None)
    if not name:
        return None
    return {
        "name": name,
        "description": _truncate(getattr(tool, "description", "") or "", 2000),
        "parameters": getattr(tool, "parameters", None),
    }


# ---------- 存储定位 / 迁移 ----------
def _db_path(data_dir: Path, uid: str) -> Path:
    return Path(data_dir) / uid / "prompts.db"


def _table(db_path: Path) -> JsonTable:
    key = str(db_path)
    with _GUARD:
        table = _TABLES.get(key)
        if table is None:
            table = JsonTable(SqliteDB(db_path), "turns")
            _TABLES[key] = table
        if key not in _MIGRATED:
            _migrate_legacy(table, db_path.parent / "prompts")
            _MIGRATED.add(key)
    return table


def _migrate_legacy(table: JsonTable, legacy_root: Path) -> None:
    """导入 prompts/<sid>/<mid>.json；坏 JSON 跳过，导入后改名 .bak。"""
    if not legacy_root.is_dir():
        return
    try:
        sessions = sorted(p for p in legacy_root.iterdir() if p.is_dir())
    except OSError:
        return
    for sid_dir in sessions:
        sid = sid_dir.name

        def _import(message_id: str, doc: Any, _sid: str = sid) -> None:
            if not isinstance(doc, dict):
                return
            row = _row_key(_sid, message_id)
            if not table.exists(row):
                table.put(row, doc)

        try:
            migrate_legacy_dir(sid_dir, _import)
        except Exception:
            logger.warning("迁移提示词归档失败：%s", sid_dir, exc_info=True)
            continue
        try:
            sid_dir.rmdir()
        except OSError:
            pass


def _row_key(session_id: str, message_id: str) -> str:
    # 长度前缀保证不同会话的 key 不会互相混淆（会话 id 本身可能含 ":"）。
    return "%d:%s:%s" % (len(session_id), session_id, message_id)


def _fit(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_ARCHIVE_BYTES:
        for call in payload.get("calls", []):
            for msg in call.get("messages", []):
                if isinstance(msg, dict):
                    msg["content"] = _truncate(msg.get("content"), 20_000)
    return payload


# ---------- 公开 API ----------
def save_turn(
    data_dir: Optional[Path],
    uid: str,
    session_id: str,
    message_id: str,
    captures: List[Dict[str, Any]],
) -> None:
    """保存本轮所有 provider 调用的请求快照；任何失败只告警。"""
    if not data_dir or not uid or not session_id or not message_id or not captures:
        return
    try:
        check_safe_name(message_id, "消息 id")
        table = _table(_db_path(data_dir, uid))
        payload = {
            "session_id": session_id,
            "message_id": message_id,
            "calls": captures,
        }
        table.put(_row_key(session_id, message_id), _fit(payload))
    except Exception:
        logger.warning("保存提示词归档失败：%s/%s", session_id, message_id, exc_info=True)


def load_turn(
    data_dir: Optional[Path], uid: str, session_id: str, message_id: str
) -> Optional[Dict[str, Any]]:
    if not data_dir or not uid or not session_id or not message_id:
        return None
    try:
        check_safe_name(message_id, "消息 id")
        table = _table(_db_path(data_dir, uid))
        return table.get(_row_key(session_id, message_id))
    except Exception:
        logger.warning("读取提示词归档失败：%s/%s", session_id, message_id, exc_info=True)
        return None


def delete_session(data_dir: Optional[Path], uid: str, session_id: str) -> None:
    if not data_dir or not uid or not session_id:
        return
    try:
        table = _table(_db_path(data_dir, uid))
        for key, doc in list(table.items()):
            if str(doc.get("session_id") or "") == session_id:
                table.delete(key)
    except Exception:
        logger.warning("清理提示词归档失败：%s", session_id, exc_info=True)


def append_call(
    data_dir: Optional[Path],
    uid: str,
    session_id: str,
    anchor_id: str,
    kind: str,
    messages: List[Any],
    tools: List[Any],
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """按「本轮锚点用户消息 id」追加一次 provider 调用快照。

    一次回复可能包含多轮工具调用，前端查看该轮任一 assistant 消息时都会
    解析到同一个锚点行。写失败只告警，不影响生成。
    """
    if not data_dir or not uid or not session_id or not anchor_id:
        return
    try:
        check_safe_name(anchor_id, "消息 id")
        table = _table(_db_path(data_dir, uid))
        row = _row_key(session_id, anchor_id)
        payload = table.get(row)
        if not isinstance(payload, dict) or not isinstance(payload.get("calls"), list):
            payload = {"session_id": session_id, "anchor_id": anchor_id, "calls": []}
        payload.setdefault("session_id", session_id)
        payload.setdefault("anchor_id", anchor_id)
        call = {
            "kind": kind,
            "messages": [_message_dict(m) for m in (messages or [])],
            "tools": [t for t in (_tool_dict(x) for x in (tools or [])) if t],
            "params": params or {},
        }
        payload["calls"].append(call)
        table.put(row, _fit(payload))
    except Exception:
        logger.warning("追加提示词归档失败：%s/%s", session_id, anchor_id, exc_info=True)
