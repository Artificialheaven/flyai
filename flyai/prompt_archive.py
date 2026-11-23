"""真实提示词归档：把每轮实际发给模型的 system/history/tools 存到用户目录。

不写进会话 JSON（避免再次把会话撑大数 MB），按消息 id 一文件：
`data/<uid>/prompts/<sid>/<message_id>.json`。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ids import safe_child

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 200_000
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024


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
        root = Path(data_dir) / uid / "prompts" / session_id
        root.mkdir(parents=True, exist_ok=True)
        target = safe_child(root, f"{message_id}.json")
        payload = {
            "session_id": session_id,
            "message_id": message_id,
            "calls": captures,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_ARCHIVE_BYTES:
            for call in payload["calls"]:
                for msg in call.get("messages", []):
                    msg["content"] = _truncate(msg.get("content"), 20_000)
            raw = json.dumps(payload, ensure_ascii=False)
        tmp = target.with_name("%s.%s.tmp" % (target.name, uuid.uuid4().hex))
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        logger.warning("保存提示词归档失败：%s/%s", session_id, message_id, exc_info=True)


def load_turn(
    data_dir: Optional[Path], uid: str, session_id: str, message_id: str
) -> Optional[Dict[str, Any]]:
    if not data_dir or not uid or not session_id or not message_id:
        return None
    try:
        root = Path(data_dir) / uid / "prompts" / session_id
        path = safe_child(root, f"{message_id}.json")
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("读取提示词归档失败：%s/%s", session_id, message_id, exc_info=True)
        return None


def delete_session(data_dir: Optional[Path], uid: str, session_id: str) -> None:
    if not data_dir or not uid or not session_id:
        return
    try:
        root = Path(data_dir) / uid / "prompts"
        target = safe_child(root, str(session_id))
        if target.is_dir():
            for item in target.glob("*.json"):
                item.unlink(missing_ok=True)
            target.rmdir()
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
    解析到同一个锚点文件。写失败只告警，不影响生成。
    """
    if not data_dir or not uid or not session_id or not anchor_id:
        return
    try:
        root = Path(data_dir) / uid / "prompts" / session_id
        root.mkdir(parents=True, exist_ok=True)
        target = safe_child(root, f"{anchor_id}.json")
        payload: Dict[str, Any] = {"session_id": session_id, "anchor_id": anchor_id, "calls": []}
        if target.is_file():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                payload.setdefault("calls", [])
            except Exception:
                payload = {"session_id": session_id, "anchor_id": anchor_id, "calls": []}
        call = {
            "kind": kind,
            "messages": [_message_dict(m) for m in (messages or [])],
            "tools": [t for t in (_tool_dict(x) for x in (tools or [])) if t],
            "params": params or {},
        }
        payload["calls"].append(call)
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_ARCHIVE_BYTES:
            for item in payload["calls"]:
                for msg in item.get("messages", []):
                    msg["content"] = _truncate(msg.get("content"), 20_000)
            raw = json.dumps(payload, ensure_ascii=False)
        tmp = target.with_name("%s.%s.tmp" % (target.name, uuid.uuid4().hex))
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        logger.warning("追加提示词归档失败：%s/%s", session_id, anchor_id, exc_info=True)
