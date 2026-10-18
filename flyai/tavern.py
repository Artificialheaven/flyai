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


def render_tavern_macros(
    text: str,
    *,
    char_name: str = "",
    user_name: str = "",
    group_names: Optional[List[str]] = None,
    last_message: str = "",
    rng: Optional[random.Random] = None,
) -> str:
    """把酒馆宏替换为实际值；text 为空/非字符串原样返回。"""
    if not text:
        return text or ""
    rng = rng or random
    out = _COMMENT_RE.sub("", text)
    out = _SETVAR_RE.sub("", out)
    out = _GETVAR_RE.sub("", out)

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
    out = _ANY_MACRO_RE.sub("", out)
    return out
