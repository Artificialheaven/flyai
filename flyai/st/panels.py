"""酒馆卡状态面板检测。

很多卡用「占位符 → HTML 面板」的展示脚本实现状态栏：开场白里写一个字面量
标记（如 ``<StatusPlaceHolderImpl/>``），展示侧正则把它替换成含
``{{format_message_variable::stat_data.*}}`` 宏的整块 HTML。

本模块只做**声明式识别**：找出这类字面量占位符，供前端把它们渲染成可单独
打开的状态面板。仍然绝不执行卡内 JavaScript；替换结果由前端用同一套展示
侧正则引擎生成，再经 DOMPurify 白名单清洗。

识别规则（保守，宁可漏掉也不误判）：

- 脚本未禁用、在展示侧生效（``promptOnly`` 单独为真时跳过）；
- ``findRegex`` 是**字面量**：非空、≤200 字符、不以 ``/`` 开头（否则是正则
  字面量）、不含任何正则元字符；
- 该字面量确实出现在开场白或替换问候中；
- ``replaceString`` 非空；
- 按占位符去重，最多 8 个。
"""

from __future__ import annotations

from typing import Any, Dict, List

MAX_PANELS = 8
MAX_TOKEN = 200
MAX_TITLE = 80

# 出现任一字符即视为正则而非字面量（保守跳过）
_REGEX_META = set("\\[](){}.*+?^$|")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _literal_token(pattern: Any) -> str:
    token = _text(pattern).strip()
    if not token or len(token) > MAX_TOKEN:
        return ""
    if token.startswith("/"):
        return ""
    if any(ch in _REGEX_META for ch in token):
        return ""
    return token


def _greeting_texts(character: Any) -> List[str]:
    texts = [_text(getattr(character, "first_mes", None))]
    for alt in getattr(character, "alternate_greetings", None) or []:
        texts.append(_text(alt))
    return [t for t in texts if t]


def detect_panels(character: Any) -> List[Dict[str, str]]:
    """返回 ``[{"token": ..., "title": ...}]``；无面板时返回空列表。"""
    if character is None:
        return []
    texts = _greeting_texts(character)
    if not texts:
        return []
    seen = set()
    panels: List[Dict[str, str]] = []
    for raw in getattr(character, "regex_scripts", None) or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("disabled"):
            continue
        # 只看是否在展示侧生效：promptOnly 单独为真才排除；
        # 两个标记同时为真按 ST 语义仍在展示侧执行。
        if raw.get("promptOnly") and not raw.get("markdownOnly"):
            continue
        token = _literal_token(raw.get("findRegex"))
        if not token or token in seen:
            continue
        if not any(token in text for text in texts):
            continue
        if not _text(raw.get("replaceString")).strip():
            continue
        seen.add(token)
        title = _text(raw.get("scriptName")).strip() or token
        panels.append({"token": token, "title": title[:MAX_TITLE]})
        if len(panels) >= MAX_PANELS:
            break
    return panels
