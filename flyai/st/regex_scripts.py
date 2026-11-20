"""酒馆（SillyTavern）regex_scripts 的原生解释器（prompt 侧）。

语义对齐 ST 的常用子集：

- ``findRegex``：裸正则字符串，或 ``/pattern/flags`` 字面量；
- ``replaceString``：JS 替换语法（``$1`` / ``$&`` / ``$`` / ``$'`` / ``$<name>``）；
- ``placement``：1=用户消息，2=AI 消息（可同时包含）；其他位置忽略；
- ``markdownOnly`` / ``promptOnly``：只在展示侧 / 只在 prompt 侧执行；
- ``disabled``、``runOnEdit``、``minDepth`` / ``maxDepth``（0/缺省=无限制）、
  ``trimStrings``（按近似语义修剪替换结果首尾）。

安全与健壮性：无效正则（卡内脚本 2 本身就是语法错误）只跳过并计入警告，
绝不抛给调用方；脚本数 / 单 pattern / 输出大小均有上限。Python ``re`` 无法
覆盖的 JS 专有语法（如 ``\\p{...}``）会在编译失败时被跳过。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..schemas import Message

MAX_SCRIPTS = 200
MAX_PATTERN = 64 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
# 持久化（预设 / 角色卡导入）时的整体 JSON 体积上限
MAX_SERIALIZED = 2 * 1024 * 1024

PLACEMENT_USER = 1
PLACEMENT_AI = 2

# 解释器认识的字段白名单；导入时其余字段一律丢弃
SCRIPT_FIELDS = (
    "scriptName",
    "findRegex",
    "replaceString",
    "placement",
    "disabled",
    "markdownOnly",
    "promptOnly",
    "runOnEdit",
    "minDepth",
    "maxDepth",
    "trimStrings",
)


def sanitize_regex_scripts(
    raw: Any, limit: int = MAX_SCRIPTS,
) -> List[Dict[str, Any]]:
    """导入/持久化前的结构清洗：白名单字段 + 条数与体积上限。

    只做结构安全校验（不编译正则）：无效正则留给运行期引擎跳过并计数。
    返回新列表，不修改入参；超限条目被丢弃而不是截断内容。
    """
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    budget = MAX_SERIALIZED
    for item in raw[:limit]:
        if not isinstance(item, dict):
            continue
        find_regex = item.get("findRegex")
        if not isinstance(find_regex, str) or not find_regex:
            continue
        if len(find_regex) > MAX_PATTERN:
            continue
        clean: Dict[str, Any] = {}
        for key in SCRIPT_FIELDS:
            if key not in item:
                continue
            value = item[key]
            if isinstance(value, (str, int, float, bool, list, tuple)) or value is None:
                clean[key] = value
        if "findRegex" not in clean:
            clean["findRegex"] = find_regex
        try:
            size = len(json.dumps(clean, ensure_ascii=False))
        except (TypeError, ValueError):
            continue
        if size > budget:
            break
        budget -= size
        out.append(clean)
    return out

# JS 命名分组 / 命名反向引用 → Python 语法
_JS_NAMED_GROUP_RE = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")
_JS_NAMED_BACKREF_RE = re.compile(r"\\k<([A-Za-z_][A-Za-z0-9_]*)>")


@dataclass
class Script:
    """一条已编译的正则脚本。"""

    name: str
    pattern: "re.Pattern[str]"
    replace: str
    placement: Tuple[int, ...] = (PLACEMENT_AI,)
    markdown_only: bool = False
    prompt_only: bool = False
    run_on_edit: bool = False
    min_depth: Optional[int] = None
    max_depth: Optional[int] = None
    trim_strings: Tuple[str, ...] = ()
    disabled: bool = False


def _split_regex_literal(text: str) -> Tuple[str, str]:
    """``/pattern/flags`` → (pattern, flags)；裸字符串原样返回、flags 为空。"""
    if not text.startswith("/"):
        return text, ""
    # 从尾部找最后一个未被转义的 "/"
    last = -1
    for i in range(len(text) - 1, 0, -1):
        if text[i] != "/":
            continue
        backslashes = 0
        j = i - 1
        while j >= 0 and text[j] == "\\":
            backslashes += 1
            j -= 1
        if backslashes % 2 == 0:
            last = i
            break
    if last <= 0:
        return text, ""
    flags = text[last + 1:]
    if not re.fullmatch(r"[a-zA-Z]*", flags):
        return text, ""
    return text[1:last], flags


def _translate_pattern(pattern: str) -> str:
    """JS 正则中可无损映射的语法 → Python。其余交给编译失败跳过。"""
    pattern = _JS_NAMED_GROUP_RE.sub(r"(?P<\1>", pattern)
    pattern = _JS_NAMED_BACKREF_RE.sub(r"(?P=\1)", pattern)
    return pattern


def _compile_pattern(find_regex: str) -> "re.Pattern[str]":
    pattern, flag_text = _split_regex_literal(find_regex)
    if len(pattern) > MAX_PATTERN:
        raise ValueError("findRegex 过长")
    flags = 0
    for ch in flag_text:
        if ch == "i":
            flags |= re.I
        elif ch == "m":
            flags |= re.M
        elif ch == "s":
            flags |= re.S
        elif ch in ("g", "u", "y", "d"):
            # g 由 re.sub 天然全替换；u/y/d 无 Python 对应或无需对应
            pass
        else:
            raise ValueError("不支持的正则标志 %r" % ch)
    compiled = re.compile(_translate_pattern(pattern), flags)
    if compiled.groups > 100:
        raise ValueError("分组过多")
    return compiled


def _int_or_none(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _placement(raw: Any) -> Tuple[int, ...]:
    if isinstance(raw, (int, float)):
        raw = [raw]
    out: List[int] = []
    if isinstance(raw, (list, tuple)):
        for v in raw:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n in (PLACEMENT_USER, PLACEMENT_AI) and n not in out:
                out.append(n)
    # ST 默认放置为「AI 输出」
    return tuple(out) if out else (PLACEMENT_AI,)


def compile_script(raw: Dict[str, Any], index: int = 0) -> Script:
    """单条脚本 → Script；不合法时抛 ValueError（调用方跳过）。"""
    if not isinstance(raw, dict):
        raise ValueError("脚本不是对象")
    find_regex = raw.get("findRegex")
    if not isinstance(find_regex, str) or not find_regex:
        raise ValueError("缺少 findRegex")
    replace = raw.get("replaceString")
    if not isinstance(replace, str):
        replace = "" if replace is None else str(replace)
    trims = raw.get("trimStrings")
    return Script(
        name=str(raw.get("scriptName") or "script %d" % (index + 1)),
        pattern=_compile_pattern(find_regex),
        replace=replace,
        placement=_placement(raw.get("placement")),
        markdown_only=bool(raw.get("markdownOnly")),
        prompt_only=bool(raw.get("promptOnly")),
        run_on_edit=bool(raw.get("runOnEdit")),
        min_depth=_int_or_none(raw.get("minDepth")),
        max_depth=_int_or_none(raw.get("maxDepth")),
        trim_strings=tuple(
            str(t) for t in (trims if isinstance(trims, list) else [])
            if str(t or "")
        ),
        disabled=bool(raw.get("disabled")),
    )


def compile_scripts(
    raw_scripts: Any,
    side: str = "prompt",
    limit: int = MAX_SCRIPTS,
) -> Tuple[List[Script], List[str]]:
    """批量编译并过滤 side（prompt/display）。返回 (可用脚本, 警告)。"""
    if not isinstance(raw_scripts, list):
        return [], []
    scripts: List[Script] = []
    warnings: List[str] = []
    usable = raw_scripts[:limit]
    if len(raw_scripts) > limit:
        warnings.append("正则脚本超过 %d 条，其余已忽略" % limit)
    for i, raw in enumerate(usable):
        try:
            script = compile_script(raw, i)
        except (ValueError, re.error) as e:
            warnings.append("跳过无效正则脚本 #%d：%s" % (i + 1, e))
            continue
        if script.disabled:
            continue
        if side == "prompt" and script.markdown_only:
            continue
        if side == "display" and script.prompt_only:
            continue
        scripts.append(script)
    return scripts, warnings


def invalid_script_count(raw_scripts: Any) -> int:
    """统计无法编译的脚本数（导入报告用，不抛异常）。"""
    if not isinstance(raw_scripts, list):
        return 0
    count = 0
    for i, raw in enumerate(raw_scripts[:MAX_SCRIPTS]):
        try:
            compile_script(raw, i)
        except (ValueError, re.error):
            count += 1
    return count


def _expand_replacement(replacement: str, match: "re.Match[str]") -> str:
    """JS ``String.replace`` 替换串语义：$& / $` / $' / $n / $<name> / $$。"""
    out: List[str] = []
    i = 0
    n = len(replacement)
    total_groups = match.re.groups
    while i < n:
        ch = replacement[i]
        if ch != "$" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = replacement[i + 1]
        if nxt == "$":
            out.append("$")
            i += 2
        elif nxt == "&":
            out.append(match.group(0))
            i += 2
        elif nxt == "`":
            out.append(match.string[:match.start()])
            i += 2
        elif nxt == "'":
            out.append(match.string[match.end():])
            i += 2
        elif nxt == "<":
            end = replacement.find(">", i + 2)
            if end < 0:
                out.append(ch)
                i += 1
                continue
            name = replacement[i + 2:end]
            try:
                out.append(match.group(name) or "")
            except (IndexError, KeyError):
                out.append("")
            i = end + 1
        elif nxt.isdigit():
            digits = nxt
            j = i + 2
            if j < n and replacement[j].isdigit():
                candidate = digits + replacement[j]
                if int(candidate) <= total_groups:
                    digits = candidate
                    j += 1
            group_index = int(digits)
            if 1 <= group_index <= total_groups:
                out.append(match.group(group_index) or "")
                i = j
            else:
                out.append("$")
                i += 1
        else:
            out.append("$")
            i += 1
    return "".join(out)


def _apply_one(script: Script, text: str) -> str:
    def _sub(match: "re.Match[str]") -> str:
        result = _expand_replacement(script.replace, match)
        # trimStrings：近似 ST 语义，修剪替换结果的首尾
        for trim in script.trim_strings:
            if result.startswith(trim):
                result = result[len(trim):]
            if result.endswith(trim):
                result = result[:-len(trim)]
        return result

    return script.pattern.sub(_sub, text)


def apply_scripts(
    text: Optional[str],
    role: str,
    scripts: Sequence[Script],
    depth: Optional[int] = None,
    side: str = "prompt",
) -> Optional[str]:
    """对单条消息文本依次应用脚本。role 仅 user/assistant 参与。"""
    if not text or not scripts:
        return text
    if role == "user":
        needed = PLACEMENT_USER
    elif role == "assistant":
        needed = PLACEMENT_AI
    else:
        return text
    out = text
    for script in scripts:
        if script.disabled or needed not in script.placement:
            continue
        if side == "prompt" and script.markdown_only:
            continue
        if side == "display" and script.prompt_only:
            continue
        if depth is not None:
            if script.min_depth and depth < script.min_depth:
                continue
            if script.max_depth and depth > script.max_depth:
                continue
        try:
            new_text = _apply_one(script, out)
        except (re.error, RuntimeError, RecursionError):
            continue
        if len(new_text) > MAX_OUTPUT:
            # 异常膨胀：放弃这条脚本，保留上一步结果
            continue
        out = new_text
    return out


def scripts_for_message(
    message: Message,
    by_character: Dict[str, Sequence[Script]],
    fallback: Sequence[Script],
) -> Sequence[Script]:
    cid = (getattr(message, "character_id", "") or "").strip()
    if cid and cid in by_character:
        return by_character[cid]
    return fallback


def apply_to_messages(
    messages: Iterable[Message],
    by_character: Optional[Dict[str, Sequence[Script]]] = None,
    fallback: Optional[Sequence[Script]] = None,
    side: str = "prompt",
    extra: Optional[Sequence[Script]] = None,
) -> List[Message]:
    """对消息列表应用 prompt 侧脚本。返回新列表（未命中的消息复用原对象）。

    depth 以「距离最新一条消息」计（ST 语义）。绝不原地修改，避免改动
    ``_actor_view`` 对当前 actor 返回的引用对象。

    ``extra`` 是预设级全局脚本（与角色卡无关），先于按角色选出的脚本执行；
    两者都命中的消息按 ``extra → 角色卡`` 顺序叠加。
    """
    msgs = list(messages)
    by_character = by_character or {}
    fallback = list(fallback or [])
    extra = list(extra or [])
    if not by_character and not fallback and not extra:
        return msgs
    total = len(msgs)
    out: List[Message] = []
    for i, m in enumerate(msgs):
        own = scripts_for_message(m, by_character, fallback)
        scripts = (extra + list(own)) if extra else own
        if not scripts:
            out.append(m)
            continue
        depth = total - 1 - i
        new_content = apply_scripts(
            getattr(m, "content", None), m.role, scripts,
            depth=depth, side=side,
        )
        if new_content is getattr(m, "content", None):
            out.append(m)
        else:
            from dataclasses import replace

            out.append(replace(m, content=new_content))
    return out
