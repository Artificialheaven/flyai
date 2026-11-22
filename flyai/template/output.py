"""输出侧模板：消息标记、原文哈希与客户端提交的最终结果。

方案 B 只在客户端执行模板；服务端负责：
- 给含 `<% %>` 的助手消息打 pending 标记（仅功能开启时）；
- 校验客户端回传的渲染结果与变量 ops；
- 把消息内容与变量在同一次会话写盘中提交。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Iterable, List, Optional

from ..schemas import Message
from .vars import TemplateOpError, apply_ops

TEMPLATE_MARK = "<%"
MAX_CONTENT_BYTES = 2 * 1024 * 1024


class TemplateCommitError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def contains_template(text: Any) -> bool:
    return isinstance(text, str) and TEMPLATE_MARK in text


def iter_messages(session: Any) -> Iterable[Message]:
    seen = set()
    for thread in getattr(session, "threads", None) or []:
        for message in getattr(thread, "messages", None) or []:
            key = id(message)
            if key in seen:
                continue
            seen.add(key)
            yield message
    for message in getattr(session, "messages", None) or []:
        key = id(message)
        if key in seen:
            continue
        seen.add(key)
        yield message


def find_message(session: Any, mid: str) -> Optional[Message]:
    for message in iter_messages(session):
        if getattr(message, "id", "") == mid:
            return message
    return None


def mark_pending_output(session: Any, enabled: bool) -> int:
    """给尚未渲染的助手模板消息打 pending 标记；返回新标记数量。"""
    if not enabled:
        return 0
    marked = 0
    for message in iter_messages(session):
        if getattr(message, "role", "") != "assistant":
            continue
        if getattr(message, "raw_content", None):
            continue
        if getattr(message, "template_rendered", None) is not None:
            continue
        content = getattr(message, "content", None)
        if not contains_template(content):
            continue
        message.raw_content = content
        message.template_rendered = False
        marked += 1
    return marked


def message_hash(message: Message) -> str:
    raw = getattr(message, "raw_content", None)
    if raw is None:
        raw = getattr(message, "content", None) or ""
    return hashlib.sha256(str(raw).encode("utf-8")).hexdigest()


def _session_vars(session: Any) -> Dict[str, Any]:
    value = getattr(session, "template_vars", None)
    return value if isinstance(value, dict) else {}


def _scope_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _message_scope(session: Any, mid: str) -> Dict[str, Any]:
    message_map = _scope_dict(_session_vars(session).get("message"))
    return _scope_dict(message_map.get(mid))


def _write_scopes(
    session: Any,
    mid: str,
    local: Dict[str, Any],
    message: Dict[str, Any],
) -> None:
    current = _session_vars(session)
    message_map = _scope_dict(current.get("message"))
    if message:
        message_map[mid] = message
    else:
        message_map.pop(mid, None)
    session.template_vars = {
        "local": local or {},
        "message": message_map,
    }


def apply_output_commit(
    session: Any,
    message: Message,
    payload: Dict[str, Any],
    *,
    global_vars: Dict[str, Any],
    updated_at: str = "",
) -> Dict[str, Any]:
    """校验并应用一次输出侧提交；返回给客户端的摘要。

    调用方负责保存 session 与 global 变量文件；本函数只改内存对象。
    """
    if not isinstance(payload, dict):
        raise TemplateCommitError("提交内容格式错误")
    content = payload.get("content")
    if not isinstance(content, str):
        raise TemplateCommitError("缺少渲染后的 content")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise TemplateCommitError("渲染结果超过 2MB", 413)

    raw = getattr(message, "raw_content", None)
    if not contains_template(raw):
        raise TemplateCommitError("该消息没有待渲染的模板", 409)
    if getattr(message, "template_rendered", None) is True:
        render_id = str(payload.get("render_id") or "")
        if render_id and render_id == (
            getattr(message, "template_render_id", None) or ""
        ):
            return {
                "ok": True,
                "idempotent": True,
                "message": message.to_dict(),
            }
        raise TemplateCommitError("该消息已渲染", 409)

    raw_hash = str(payload.get("raw_hash") or "")
    if raw_hash != message_hash(message):
        raise TemplateCommitError("消息原文已变化，请重新渲染", 409)

    mid = getattr(message, "id", "") or ""
    local = _scope_dict(_session_vars(session).get("local"))
    message_vars = _message_scope(session, mid)
    try:
        local, message_vars, global_vars = apply_ops(
            payload.get("ops"),
            local=local,
            message=message_vars,
            global_vars=global_vars,
            default_scope="message",
        )
    except TemplateOpError as exc:
        raise TemplateCommitError(str(exc))

    message.content = content
    message.template_rendered = True
    message.template_vars = message_vars or None
    message.template_render_id = (
        str(payload.get("render_id") or "").strip() or uuid.uuid4().hex
    )
    _write_scopes(session, mid, local, message_vars)

    return {
        "ok": True,
        "idempotent": False,
        "message": message.to_dict(),
        "global_vars": global_vars,
        "updated_at": updated_at,
    }


def strip_template_markers(text: str) -> str:
    """纯字符串剥离 EJS 标记（不执行）；用于把 pending 原文发给模型前的兜底。"""
    if not isinstance(text, str) or TEMPLATE_MARK not in text:
        return text
    out: List[str] = []
    pos = 0
    while True:
        start = text.find(TEMPLATE_MARK, pos)
        if start < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        close = text.find("%>", start + 2)
        if close < 0:
            # 未闭合：从标记处整体丢弃，避免把代码发给模型
            break
        pos = close + 2
    return "".join(out)


def dump_global_vars(vars_: Dict[str, Any]) -> str:
    return json.dumps(vars_ or {}, ensure_ascii=False, sort_keys=True)
