"""模板变量路径与 ops 校验。

客户端只回传“改了什么”，服务端按白名单重新校验后写入；任何越权/超限
的 op 都整体拒绝，避免出现一半生效的副作用。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Tuple

MAX_OPS = 500
MAX_PATH_CHARS = 512
MAX_PART_CHARS = 64
MAX_DEPTH = 8
MAX_VALUE_BYTES = 64 * 1024

_FORBIDDEN = {"__proto__", "constructor", "prototype"}
_BAD_CHARS = re.compile(r"[/\\\x00-\x1f]")
SCOPES = ("message", "local", "global")


class TemplateOpError(ValueError):
    """非法的 op：调用方转 400，不落任何变量。"""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def split_path(path: Any) -> List[str]:
    text = _text(path)
    if not text or len(text) > MAX_PATH_CHARS:
        raise TemplateOpError("变量路径为空或过长")
    parts: List[str] = []
    for raw in text.replace("[", ".").replace("]", ".").split("."):
        part = raw.strip().strip("'\"")
        if not part:
            continue
        if len(part) > MAX_PART_CHARS:
            raise TemplateOpError("变量路径段过长")
        if part in _FORBIDDEN:
            raise TemplateOpError("变量路径包含保留字段")
        if _BAD_CHARS.search(part):
            raise TemplateOpError("变量路径包含非法字符")
        parts.append(part)
    if not parts:
        raise TemplateOpError("变量路径为空")
    if len(parts) > MAX_DEPTH:
        raise TemplateOpError("变量路径过深")
    return parts


def _canonical_value(value: Any) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise TemplateOpError("变量值不是合法 JSON")
    if len(text.encode("utf-8")) > MAX_VALUE_BYTES:
        raise TemplateOpError("单个变量值超过 64KB")
    return json.loads(text)


def get_path(root: Dict[str, Any], parts: List[str]) -> Any:
    cur: Any = root
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def set_path(root: Dict[str, Any], parts: List[str], value: Any) -> None:
    cur: Any = root
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def unset_path(root: Dict[str, Any], parts: List[str]) -> None:
    cur: Any = root
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            return
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise TemplateOpError("%s 必须是数字" % what)
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        raise TemplateOpError("%s 必须是数字" % what)
    if not math.isfinite(number):
        raise TemplateOpError("%s 必须是有限数字" % what)
    if number.is_integer() and abs(number) < 1e15:
        return int(number)
    return number


def _scope(raw: Dict[str, Any], default_scope: str) -> str:
    scope = _text(raw.get("scope")) or default_scope
    if scope not in SCOPES:
        raise TemplateOpError("不支持的变量作用域")
    return scope


def apply_ops(
    ops: Any,
    *,
    local: Dict[str, Any],
    message: Dict[str, Any],
    global_vars: Dict[str, Any],
    default_scope: str = "message",
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """按顺序应用变量 ops；返回 (local, message, global)。

    任何一条非法都会抛 TemplateOpError，调用方不应写入任何结果。
    """
    if ops is None:
        ops = []
    if not isinstance(ops, list):
        raise TemplateOpError("ops 必须是数组")
    if len(ops) > MAX_OPS:
        raise TemplateOpError("单次提交的 ops 过多")
    local = dict(local or {})
    message = dict(message or {})
    global_vars = dict(global_vars or {})

    for raw in ops:
        if not isinstance(raw, dict):
            raise TemplateOpError("op 必须是对象")
        name = _text(raw.get("op") or raw.get("type"))
        scope = _scope(raw, default_scope)
        target = {"local": local, "message": message, "global": global_vars}[scope]
        parts = split_path(raw.get("path") or raw.get("name"))

        if name == "vars.set":
            set_path(target, parts, _canonical_value(raw.get("value")))
        elif name == "vars.unset":
            unset_path(target, parts)
        elif name in ("vars.inc", "vars.dec"):
            current = get_path(target, parts)
            if current is None:
                current = 0
            current_num = _number(current, "当前变量")
            delta = _number(raw.get("delta", 1), "delta")
            if name == "vars.dec":
                delta = -delta
            set_path(target, parts, _number(current_num + delta, "计算结果"))
        else:
            raise TemplateOpError("不支持的 op：%s" % (name or "空"))

    return local, message, global_vars
