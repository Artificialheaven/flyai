"""内容表触发词扫描（世界书 selective/keys 机制）。

非常驻条目可配置触发词（keys；留空时以标题为触发词）。每轮用户消息进来后，
系统侧扫描命中条目并直接注入系统提示词——不依赖 AI 主动调用 query_lore。

匹配分两级：
- 精确：触发词作为子串出现在消息中（大小写不敏感）；
- 模糊：3 字以上触发词容忍 1 个字写错（等长滑窗相同字数 ≥ n-1），
  4 字以上再容忍 1 字增删（SequenceMatcher ≥ 0.75）。
  2 字词只做精确匹配——中文二字词错一个字歧义太大（「铁剑/铁路」）。

语义路（消息与条目向量余弦达标）由 LoreQuerier.trigger_block 在关键词
命中之外补足，阈值复用 embeddings.score_threshold。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from ..tavern import render_card_text
from .budget import fit_blocks
from .models import LoreEntry

# 只扫描消息尾部，控制长消息的滑窗开销
_SCAN_TAIL = 800
# 一次注入最多多少条 / 合计字符预算
MAX_TRIGGER_ENTRIES = 6
TRIGGER_BUDGET = 1500
# 语义路最多补多少条关键词未命中的条目
SEMANTIC_TOP_UP = 2
_SEMANTIC_FILL_LIMIT = 30

_SEG_RE = re.compile(r"[\s，。！？；：、,.!?;:…—~～\-_（）()【】\[\]\"'‘’“”]+")


def entry_triggers(entry: LoreEntry, render=None) -> List[str]:
    """条目生效的触发词：配置了 keys 用 keys，否则回退标题。

    ``render`` 用于展开触发词里的 ``{{user}}``/``{{char}}``：提示词侧
    已经换成实际名字，键仍是字面量宏时会永远命不中。
    """
    render = render or (lambda text: text or "")
    keys = [
        render(str(k).strip()) for k in (entry.keys or [])
        if str(k or "").strip()
    ]
    if keys:
        return keys
    title = render((entry.title or "").strip())
    return [title] if title else []


def fuzzy_hit(term: str, text: str) -> bool:
    """term 是否命中 text：精确子串优先，否则短词容错滑窗。"""
    t = (term or "").strip().lower()
    blob = (text or "")[-_SCAN_TAIL:].lower()
    if not t or not blob:
        return False
    if t in blob:
        return True
    n = len(t)
    if n < 3:
        # 二字词错一个字歧义过大，不做模糊
        return False
    for seg in _SEG_RE.split(blob):
        if len(seg) < n - 1:
            continue
        upper = len(seg) - n + 1
        for i in range(0, max(1, upper + 1)):
            same_len_w = seg[i:i + n]
            if len(same_len_w) == n:
                # 等长替换 1 字：相同字数 ≥ n-1
                same = sum(1 for a, b in zip(t, same_len_w) if a == b)
                if same >= n - 1:
                    return True
            if n >= 4:
                # 容忍 1 字增删（窗口长度 n-1 / n+1）
                for w in (seg[i:i + n - 1], seg[i:i + n + 1]):
                    if w and abs(len(w) - n) == 1 \
                            and SequenceMatcher(None, t, w).ratio() >= 0.75:
                        return True
    return False


def entry_enabled(entry: LoreEntry) -> bool:
    """ST disable：False 的条目在任何注入路径都不生效。"""
    return getattr(entry, "enabled", True) is not False


def entry_constant(entry: LoreEntry) -> bool:
    """常驻条目：pinned 或 ST constant。"""
    return bool(getattr(entry, "pinned", False) or getattr(entry, "constant", False))


def _key_hit(term: str, text: str, *, case_sensitive: bool = False,
             whole_words: bool = False) -> bool:
    term = (term or "").strip()
    if not term:
        return False
    if case_sensitive:
        if whole_words and term.isascii():
            return re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
                text or "",
            ) is not None
        return term in (text or "")
    if whole_words and term.isascii():
        return re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
            (text or "").lower(),
        ) is not None
    return fuzzy_hit(term, text)


def _secondary_ok(entry: LoreEntry, text: str, render_key) -> bool:
    if getattr(entry, "selective", True) is False:
        return True
    keys = [
        render_key(str(k).strip()) for k in (getattr(entry, "secondary_keys", None) or [])
        if str(k or "").strip()
    ]
    if not keys:
        return True
    case_sensitive = bool(getattr(entry, "case_sensitive", False))
    whole_words = bool(getattr(entry, "match_whole_words", False))
    hits = [
        _key_hit(k, text, case_sensitive=case_sensitive, whole_words=whole_words)
        for k in keys
    ]
    logic = (getattr(entry, "selective_logic", "") or "and_any").lower()
    if logic == "and_all":
        return all(hits)
    if logic == "not_all":
        return not all(hits)
    if logic == "not_any":
        return not any(hits)
    return any(hits)


def entry_matches(entry: LoreEntry, text: str, render_key=None) -> bool:
    """ST selective 语义：主键命中，再按 selective_logic 判定次键。"""
    render_key = render_key or (lambda value: value or "")
    case_sensitive = bool(getattr(entry, "case_sensitive", False))
    whole_words = bool(getattr(entry, "match_whole_words", False))
    primary = entry_triggers(entry, render_key)
    hit = any(
        _key_hit(k, text, case_sensitive=case_sensitive, whole_words=whole_words)
        for k in primary
    )
    if not hit:
        return False
    return _secondary_ok(entry, text, render_key)


def _past_texts(text: str, scan_texts: Optional[List[str]],
                window: Optional[int]) -> List[str]:
    """取最近 window 条“当前消息之前”的历史文本，用于 sticky/cooldown。"""
    if not scan_texts or not window or window <= 0:
        return []
    past = list(scan_texts)
    if text:
        last = past[-1] if past else ""
        if last == text or (last and (text in last or last in text)):
            past = past[:-1]
    return past[-int(window):]


def keyword_hits(tables: List[Tuple[str, List[LoreEntry]]],
                 text: str, render_key=None,
                 scan_texts: Optional[List[str]] = None) -> List[Tuple[str, LoreEntry]]:
    """扫描各表非常驻条目，返回命中的 (kind, entry)，保持表内原序。

    - enabled=False 永不注入；
    - constant/pinned 由常驻块处理，不重复；
    - selective 只控制次键逻辑，主键仍按 keyword 扫描；
    - 条目配了 scan_depth 时，在最近 N 条消息里扫描，否则只看当前文本。
    """
    out: List[Tuple[str, LoreEntry]] = []
    for kind, table in tables:
        for e in table or []:
            if not entry_enabled(e):
                continue
            if entry_constant(e):
                continue  # 常驻条目已由 _pinned_block 注入，不重复
            # delay：会话至少 N 条消息后才允许激活
            delay = getattr(e, "delay", None)
            if delay and scan_texts is not None and len(scan_texts) < int(delay):
                continue
            haystack = text
            depth = getattr(e, "scan_depth", None)
            if depth and depth > 0 and scan_texts:
                haystack = "\n".join(scan_texts[-int(depth):])
            matched = entry_matches(e, haystack, render_key)
            # sticky：命中后在接下来 N 条消息内继续保持激活
            sticky = getattr(e, "sticky", None)
            if not matched and sticky:
                matched = any(
                    entry_matches(e, item, render_key)
                    for item in _past_texts(haystack, scan_texts, sticky)
                )
            # cooldown：最近 N 条消息内命中过，则本次不再激活
            cooldown = getattr(e, "cooldown", None)
            if matched and cooldown:
                if any(
                    entry_matches(e, item, render_key)
                    for item in _past_texts(haystack, scan_texts, cooldown)
                ):
                    matched = False
            if matched:
                out.append((kind, e))
    # 递归激活：已命中条目的正文再去触发其他条目（ST recursion）。
    if out and len(out) < MAX_TRIGGER_ENTRIES:
        hit_ids = {id(entry) for _k, entry in out}
        pool = "\n".join(
            (entry.content or "")
            for _k, entry in out
            if not getattr(entry, "exclude_recursion", False)
        )
        if pool.strip():
            for kind, table in tables:
                for e in table or []:
                    if len(out) >= MAX_TRIGGER_ENTRIES:
                        break
                    if id(e) in hit_ids:
                        continue
                    if not entry_enabled(e) or entry_constant(e):
                        continue
                    if getattr(e, "prevent_recursion", False):
                        continue
                    if entry_matches(e, pool, render_key):
                        out.append((kind, e))
                        hit_ids.add(id(e))
    return out


def _entry_block(kind: str, e: LoreEntry, *, char_name: str = "",
                 user_name: str = "", group_names: Optional[List[str]] = None,
                 variables=None) -> str:
    label = "角色资料" if kind == "character" else "世界设定"
    title = render_card_text(
        e.title or "(无标题)", char_name=char_name, user_name=user_name,
        group_names=group_names,
    )
    content = render_card_text(
        e.content or "(空内容)", char_name=char_name, user_name=user_name,
        group_names=group_names, variables=variables,
    )
    return "『{0}』（{1}）\n{2}".format(title, label, content)


def render_trigger_block(picked: List[Tuple[str, LoreEntry]], *,
                         char_name: str = "", user_name: str = "",
                         group_names: Optional[List[str]] = None,
                         variables=None) -> str:
    """把命中条目渲染为注入块；预算内放不下的条目丢弃。无条目返回空串。"""
    if not picked:
        return ""
    blocks = [
        _entry_block(
            kind, e, char_name=char_name, user_name=user_name,
            group_names=group_names, variables=variables,
        )
        for kind, e in picked[:MAX_TRIGGER_ENTRIES]
    ]
    kept, _dropped = fit_blocks(blocks, TRIGGER_BUDGET)
    if not kept:
        return ""
    return (
        "【相关设定】以下条目与本轮用户消息相关，作为你已知的背景资料直接沿用，"
        "无需再调用 query_lore 查询；其中内容是设定事实，不要当作刚发生的剧情：\n"
        + "\n\n".join(kept)
    )


def dedupe(picked: List[Tuple[str, LoreEntry]]
           ) -> List[Tuple[str, LoreEntry]]:
    """按 标题+内容 去重（角色表与世界表可能配置了相同条目）。"""
    seen = set()
    out = []
    for kind, e in picked:
        key = ((e.title or "").strip(), (e.content or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, e))
    return out
