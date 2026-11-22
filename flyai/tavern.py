"""酒馆（SillyTavern）导入通用工具：脏 JSON 容错解析 + 提示词宏替换。

世界书 / 模型预设的酒馆导出文件不一定是严格 JSON：实测存在坏转义
（``\\x`` 之类）与字符串内未转义的裸引号。lenient_json_loads 在标准
json 解析失败时逐条修复，尽量把文件读出来。

宏替换覆盖世界书条目与预设 prompt 块中常见的酒馆宏：
``{{char}}/{{user}}/{{group}}/{{lastUserMessage}}``、``{{random:a,b}}``、
``{{//注释}}``、``{{getvar::x}}/{{setvar::x::v}}``（本项目无变量系统，
getvar 展开为空、setvar 不产生文本），其余未识别宏一律剥除，避免把
``{{...}}`` 原样发给模型。
"""

from __future__ import annotations

import json
import random
import re
from contextvars import ContextVar
from typing import List, Optional


def lenient_json_loads(text: str):
    """容错解析酒馆导出的脏 JSON：坏转义 + 字符串内裸引号。"""
    # 非法 \\u（后非 4 位十六进制）→ 先转义反斜杠本身
    fixed = re.sub(r"\\u(?![0-9a-fA-F]{4})", lambda m: "\\" + m.group(0), text)
    # 其余非法转义（\\x、\\' 等）→ 双写反斜杠
    fixed = re.sub(r'\\(?!["\\/bfnrtu])', lambda m: r"\\", fixed)
    for _ in range(20000):
        try:
            return json.loads(fixed, strict=False)
        except json.JSONDecodeError as e:
            q = None
            # 裸引号：报错点可能在引号本身，也可能在其后一个字符
            if e.msg == "Expecting ',' delimiter":
                if e.pos < len(fixed) and fixed[e.pos] == '"':
                    q = e.pos
                elif e.pos > 0 and fixed[e.pos - 1] == '"':
                    q = e.pos - 1
            if q is None:
                raise
            fixed = fixed[:q] + '\\"' + fixed[q + 1:]
    raise ValueError("JSON 修复次数超限")


# ---- 宏替换 ----

_COMMENT_RE = re.compile(r"\{\{//[\s\S]*?\}\}", re.IGNORECASE)
_SETVAR_RE = re.compile(r"\{\{setvar::[\s\S]*?\}\}", re.IGNORECASE)
_GETVAR_RE = re.compile(r"\{\{getvar::[^}]*\}\}", re.IGNORECASE)
_RANDOM_RE = re.compile(r"\{\{(?:random|pickrandom|dice)::([^}]*)\}\}", re.IGNORECASE)
_ANY_MACRO_RE = re.compile(r"\{\{[^{}]*\}\}")
# MVU 变量宏：{{format_message_variable::stat_data.<path>}} / get_message_variable
_VAR_MACRO_RE = re.compile(
    r"\{\{\s*(?:format|get)_message_variable::([^}]*)\}\}", re.IGNORECASE
)


def _path_parts(path: str) -> List[str]:
    """``stat_data.a.b[0]`` → ["a", "b", "0"]；不支持带点号的引号键。"""
    norm = (path or "").replace("[", ".").replace("]", ".")
    return [seg.strip().strip("'\"") for seg in norm.split(".") if seg.strip()]


def resolve_message_variable(variables, path: str):
    """按点路径读取 MVU 变量；variables 可为完整状态或其 stat_data。

    路径以 ``stat_data`` 开头且 variables 带该键时从其中取值；否则在
    stat_data（缺失时整个字典）上取值。任一段不存在返回 None。
    """
    if not isinstance(variables, dict):
        return None
    path = (path or "").strip()
    has_root = isinstance(variables.get("stat_data"), (dict, list))
    if not path:
        path = "stat_data"
    if path == "stat_data" or path.startswith("stat_data.") \
            or path.startswith("stat_data["):
        rest = path[len("stat_data"):]
        cur = variables.get("stat_data") if has_root else variables
    else:
        rest = path
        cur = variables.get("stat_data") if has_root else variables
    for part in _path_parts(rest):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            return None
    return cur


def _format_variable(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ST-Prompt-Template / EJS 标记：FlyAI 不执行卡内 JS，只做安全降级。
_EJS_TAG_RE = re.compile(r"<%[\s\S]*?%>")
_EJS_GETVAR_OUT_RE = re.compile(
    r"<%=\s*getvar\(\s*(['\"])([^'\"]+)\1(?:\s*,[\s\S]*?)?\)\s*%>"
)


def strip_prompt_template(text: str, variables=None) -> str:
    """降级处理酒馆提示词模板（ST-Prompt-Template / EJS）。

    卡内世界书条目常用 ``<%_ … _%>`` 模板按变量生成提示词。FlyAI 没有
    EJS / JS 引擎，也不在服务端执行卡内脚本，这里只做保守降级：

    - ``<%= getvar('path', …) %>`` 能从 variables 解析时替换为实际值；
    - 其余 ``<% … %>`` 标记整体移除，保留模板外的静态文本；
    - 压缩因移除产生的多余空行，避免把模板代码原样发给模型。

    不含 ``<%`` 的普通文本原样返回。variables 为 None 时只做标记移除。
    """
    if not isinstance(text, str) or "<%" not in text:
        return text or ""
    out = text
    if variables is not None:
        def _getvar(match: "re.Match[str]") -> str:
            return _format_variable(
                resolve_message_variable(variables, match.group(2))
            )

        out = _EJS_GETVAR_OUT_RE.sub(_getvar, out)
    out = _EJS_TAG_RE.sub("", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# EJS 提示词模板开启时，卡/预设/世界书文本不能在宏渲染阶段被剥掉：
# 先保留 `<% … %>`，交给客户端沙箱执行后再做宏展开。
_PRESERVE_EJS: ContextVar[bool] = ContextVar("flyai_preserve_ejs", default=False)


def set_preserve_ejs(enabled: bool):
    """在提示词组装期间保留 EJS；返回 token 供 reset_preserve_ejs 恢复。"""
    return _PRESERVE_EJS.set(bool(enabled))


def reset_preserve_ejs(token) -> None:
    _PRESERVE_EJS.reset(token)


def preserve_ejs_enabled() -> bool:
    return bool(_PRESERVE_EJS.get())


def _outside_ejs(text: str, transform) -> str:
    """只对 EJS 标签之外的片段应用 transform，标签内原样保留。"""
    out: List[str] = []
    pos = 0
    for match in _EJS_TAG_RE.finditer(text or ""):
        out.append(transform((text or "")[pos:match.start()]))
        out.append(match.group(0))
        pos = match.end()
    out.append(transform((text or "")[pos:]))
    return "".join(out)


def render_name_macros(
    text: str,
    *,
    char_name: str = "",
    user_name: str = "",
    group_names: Optional[List[str]] = None,
    last_message: str = "",
) -> str:
    """只展开角色/用户/群名/上一条消息，保留其余宏。

    用于非 MVU 会话：既要把 ``{{user}}``/``{{char}}`` 换成人名，又不能像
    完整渲染那样对 ``{{random}}`` 等宏逐次求值或把未知宏直接剥空。
    """
    if not text:
        return text or ""
    out = _COMMENT_RE.sub("", text)
    out = _SETVAR_RE.sub("", out)
    out = _GETVAR_RE.sub("", out)
    group_text = "、".join(group_names or [])
    repl = [
        (r"\{\{\s*char\s*\}\}", char_name or "角色"),
        (r"\{\{\s*user\s*\}\}", user_name or "用户"),
        (r"\{\{\s*group\s*\}\}", group_text),
        (r"\{\{\s*lastUserMessage\s*\}\}", last_message or ""),
    ]
    for pat, val in repl:
        out = re.sub(pat, lambda _m, v=val: v, out, flags=re.IGNORECASE)
    return out


def render_card_text(
    text: str,
    *,
    char_name: str = "",
    user_name: str = "",
    group_names: Optional[List[str]] = None,
    last_message: str = "",
    variables=None,
    rng: Optional[random.Random] = None,
    preserve_ejs: Optional[bool] = None,
) -> str:
    """卡内文本统一渲染入口。

    - 有 MVU 变量时：完整宏渲染（含 ``{{format_message_variable}}``）并剥 EJS；
    - 无变量时：剥 EJS + 只展开名字宏，``{{random}}`` 等保持原样，避免每次
      构建都被重新求值。
    - EJS 提示词模板开启（preserve_ejs=True）：保留 ``<% … %>``，宏只作用于
      标签之外的文本，等客户端沙箱执行后再做后置宏展开。
    """
    preserve = (
        preserve_ejs_enabled() if preserve_ejs is None else bool(preserve_ejs)
    )
    if variables is not None:
        return render_tavern_macros(
            text or "",
            char_name=char_name,
            user_name=user_name,
            group_names=group_names,
            last_message=last_message,
            variables=variables,
            rng=rng,
            preserve_ejs=preserve,
        )
    if preserve:
        return _outside_ejs(
            text or "",
            lambda chunk: render_name_macros(
                chunk,
                char_name=char_name,
                user_name=user_name,
                group_names=group_names,
                last_message=last_message,
            ),
        )
    return render_name_macros(
        strip_prompt_template(text or "", variables=None),
        char_name=char_name,
        user_name=user_name,
        group_names=group_names,
        last_message=last_message,
    )


def _expand_macros(
    text: str,
    *,
    char_name: str,
    user_name: str,
    group_names: Optional[List[str]],
    last_message: str,
    variables,
    rng,
) -> str:
    out = _COMMENT_RE.sub("", text or "")
    out = _SETVAR_RE.sub("", out)
    out = _GETVAR_RE.sub("", out)

    if variables is not None:
        def _var(m):
            return _format_variable(
                resolve_message_variable(variables, m.group(1))
            )

        out = _VAR_MACRO_RE.sub(_var, out)

    def _pick(m):
        choices = [c.strip() for c in m.group(1).split(",")]
        choices = [c for c in choices if c]
        return rng.choice(choices) if choices else ""

    out = _RANDOM_RE.sub(_pick, out)

    group_text = "、".join(group_names or [])
    repl = [
        (r"\{\{\s*char\s*\}\}", char_name or "角色"),
        (r"\{\{\s*user\s*\}\}", user_name or "用户"),
        (r"\{\{\s*group\s*\}\}", group_text),
        (r"\{\{\s*lastUserMessage\s*\}\}", last_message or ""),
    ]
    for pat, val in repl:
        out = re.sub(pat, lambda _m, v=val: v, out, flags=re.IGNORECASE)
    # 其余未识别宏（{{personality}}/{{model}} 等）一律剥除
    return _ANY_MACRO_RE.sub("", out)


def render_tavern_macros(
    text: str,
    *,
    char_name: str = "",
    user_name: str = "",
    group_names: Optional[List[str]] = None,
    last_message: str = "",
    variables=None,
    rng: Optional[random.Random] = None,
    preserve_ejs: Optional[bool] = None,
) -> str:
    """把酒馆宏替换为实际值；text 为空/非字符串原样返回。

    variables：MVU 变量状态（``stat_data`` 或含该键的完整状态）；提供时展开
    ``{{format_message_variable::stat_data.<path>}}`` 宏，缺失字段展开为空串。
    preserve_ejs=True 时不剥 EJS，宏只作用于标签之外的片段。
    """
    if not text:
        return text or ""
    preserve = (
        preserve_ejs_enabled() if preserve_ejs is None else bool(preserve_ejs)
    )
    rng = rng or random
    if preserve:
        return _outside_ejs(
            text,
            lambda chunk: _expand_macros(
                chunk,
                char_name=char_name,
                user_name=user_name,
                group_names=group_names,
                last_message=last_message,
                variables=variables,
                rng=rng,
            ),
        )
    out = _expand_macros(
        text,
        char_name=char_name,
        user_name=user_name,
        group_names=group_names,
        last_message=last_message,
        variables=variables,
        rng=rng,
    )
    # EJS 模板标记：不执行，只移除/尽量展开 getvar
    return strip_prompt_template(out, variables)
