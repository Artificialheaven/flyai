"""提示词注入的字符预算截断。

不引入 tokenizer：按字符数估算（CJK 约 1 字符≈1 token，是保守上限；
英文按字符计实际更宽松）。所有 limit<=0 均表示不限。
"""

from __future__ import annotations

import re
from typing import List, Tuple

ELLIPSIS = "…（下略）"

_SENTENCE_RE = re.compile(r".+?(?:[。！？!?；;]+|$)", re.S)
# 注意必须用非捕获组：findall 含捕获组时只返回组内文本（标点）而非整句
_BOUNDARY_CHARS = " \t\n，。、；：！？,.!?:;）)】》」』"


def fit_budget(text: str, limit: int) -> str:
    """段落感知截断：尽量在段落/句界处截断，末尾加省略标记。

    limit<=0/None 或文本未超时 → 原文返回。保证返回长度 <= limit。
    """
    if not limit or limit <= 0:
        return text or ""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= limit:
        return text

    budget = limit - len(ELLIPSIS)
    if budget < 1:
        return "…"

    # 第一级：整段贪心
    paragraphs = text.split("\n")
    kept: List[str] = []
    used = 0
    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        add = len(p) if not kept else len(p) + 1
        if used + add > budget:
            break
        kept.append(p)
        used += add
        i += 1

    if i < len(paragraphs):
        rest_budget = budget - used - (1 if kept else 0)
        part = _fit_paragraph(paragraphs[i], rest_budget)
        if part:
            kept.append(part)

    out = "\n".join(kept).rstrip()
    if not out:
        return text[:budget].rstrip() + ELLIPSIS
    return out + ELLIPSIS


def _fit_paragraph(paragraph: str, budget: int) -> str:
    """在一段内按句界贪心；第一句就超时时硬切到边界。"""
    if budget <= 0:
        return ""
    if len(paragraph) <= budget:
        return paragraph

    sentences = _SENTENCE_RE.findall(paragraph)
    kept: List[str] = []
    used = 0
    for s in sentences:
        if used + len(s) > budget:
            break
        kept.append(s)
        used += len(s)
    if kept:
        return "".join(kept).rstrip()

    # 硬切：回退到切点附近的边界
    cut = paragraph[:budget]
    for j in range(len(cut) - 1, max(0, len(cut) - 20), -1):
        if cut[j] in _BOUNDARY_CHARS:
            return cut[: j + 1].rstrip()
    return cut.rstrip()


def fit_blocks(blocks: List[str], limit: int) -> Tuple[List[str], bool]:
    """整块贪心保留（join 符 \\n\\n），不截半块；返回 (保留块, 是否有丢弃)。

    第一块本身即超时时，用 fit_budget 处理该块保证至少注入；
    此时若还有后续块，dropped=True。
    """
    blocks = [b for b in (blocks or []) if b]
    if not blocks:
        return [], False
    if not limit or limit <= 0:
        return list(blocks), False

    kept: List[str] = []
    used = 0
    for i, b in enumerate(blocks):
        add = len(b) if not kept else len(b) + 2
        # 非最后一块：若它之后还有丢弃，需给省略标记预留位置，保证总长 <= limit
        reserve = 0 if i == len(blocks) - 1 else len(ELLIPSIS)
        if used + add + reserve <= limit:
            kept.append(b)
            used += add
            continue
        # 放不下：首块用 fit_budget 截断（标记自带），其后整块丢弃
        if i == 0:
            first = fit_budget(b, limit)
            if first:
                kept.append(first)
        if kept and not kept[-1].endswith(ELLIPSIS) and used + len(ELLIPSIS) <= limit:
            kept[-1] = kept[-1] + ELLIPSIS
        return kept, True
    return kept, False
