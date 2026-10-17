"""批量结构化抽取（P4）+ 条目冲突消解。

节奏学酒馆（SillyTavern promptInterval）：每 N 轮把新增对话一次性发给模型，
要求增量输出 JSON，而不是每条消息触发一次 LLM。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

from ..providers.complete import complete
from ..schemas import Message
from .entries import EntryBook, MemoryEntry
from .ledger import apply_ops
from ..lore.gen import _extract_json as extract_json

DEFAULT_EXTRACT_SYS = (
    "你是一名剧情整理员。阅读对话新增部分，把其中沉淀的客观事实增量写入记忆，"
    "并维护物品/任务/伏笔台账。只输出一个 JSON 对象：\n"
    '{"entries": [{"text": "客观事实", "op": "add|update", '
    '"time_anchor": "相对时间", "entities": ["人名/地点"], '
    '"tags": ["身份|约定|状态|关系|伏笔"], "importance": 0.0}],\n'
    ' "ledger": [{"kind": "character|item|location|task|foreshadow", '
    '"name": "名称", "state": "当前状态/归属/位置", '
    '"op": "upsert|resolve|cancel"}]}\n'
    "规则：只写对话中确实出现的信息，不要编造、不要客套话；"
    "op=update 用于既有事实被新信息改变（下方给出了现有条目）；"
    "状态变化通过 ledger 反映，同一物品归属改变时用 upsert、任务完成用 resolve、"
    "伏笔回收用 resolve；没有新增内容时各数组留空。"
)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def apply_entry_ops(book: EntryBook,
                    ops: List[Dict[str, Any]]) -> Dict[str, int]:
    """把抽取的条目 ops 应用到记忆簿，返回 {added, updated} 计数。

    匹配优先级：显式实体交集 → 文本相似度。
    - op=update 或强相似（≥0.8）：就地更新，旧文本入 history（保留 5 条）。
    - 中等相似（0.5~0.8，事实被新说法取代）：新条目生效，旧条目 superseded。
    - 其余：新增。
    """
    report = {"added": 0, "updated": 0}
    for op in ops or []:
        text = str(op.get("text") or "").strip()
        if not text:
            continue
        action = str(op.get("op") or "add").strip()
        entities = [str(e).strip() for e in (op.get("entities") or []) if e]
        tags = [str(t).strip() for t in (op.get("tags") or []) if t]
        anchor = str(op.get("time_anchor") or "").strip()
        try:
            importance = float(op.get("importance", 0.6))
        except (TypeError, ValueError):
            importance = 0.6

        best_i, best_ratio = -1, 0.0
        for i, existing in enumerate(book.entries):
            if existing.superseded:
                continue
            if entities and set(entities) & set(existing.entities):
                best_i, best_ratio = i, 1.0
                break
            ratio = _similarity(text, existing.text)
            if ratio > best_ratio:
                best_i, best_ratio = i, ratio

        if action == "update" or best_ratio >= 0.8:
            if best_i < 0:
                # 模型要求更新却找不到对应条目：退化为新增
                book.entries.append(
                    MemoryEntry(text=text, source="extract",
                                time_anchor=anchor, entities=entities,
                                tags=tags, importance=importance)
                )
                report["added"] += 1
                continue
            old = book.entries[best_i]
            old.history.append({"text": old.text, "at": old.created_at})
            if len(old.history) > 5:
                old.history = old.history[-5:]
            old.text = text
            old.time_anchor = anchor or old.time_anchor
            old.entities = sorted(set(old.entities) | set(entities))
            old.tags = sorted(set(old.tags) | set(tags))
            old.importance = max(old.importance, importance)
            old.superseded = False
            report["updated"] += 1
        elif best_ratio >= 0.5:
            # 旧说法被新事实取代：旧条目标 superseded，新条目独立保留
            book.entries[best_i].superseded = True
            book.entries.append(
                MemoryEntry(text=text, source="extract",
                            time_anchor=anchor, entities=entities,
                            tags=tags, importance=importance)
            )
            report["added"] += 1
        else:
            book.entries.append(
                MemoryEntry(text=text, source="extract",
                            time_anchor=anchor, entities=entities,
                            tags=tags, importance=importance)
            )
            report["added"] += 1
    return report


async def structured_extract(
    char_store,
    char_id: str,
    provider,
    new_script: str,
) -> Dict[str, Any]:
    """执行一次结构化抽取：模型输出 JSON → 应用条目/台账 ops，全部落盘。

    new_script：本次新增轮次的文本。返回计数报告。
    """
    book = char_store.load_book(char_id)
    current_entries = "\n".join(
        "- " + e.text for e in book.entries if not e.superseded
    ) or "（无）"
    ledger = char_store.load_ledger(char_id)
    from .ledger import KINDS

    ledger_lines = []
    for kind in KINDS:
        for it in ledger[kind]:
            ledger_lines.append(f"- [{kind}/{it.status}] {it.name}: {it.state}")
    current_ledger = "\n".join(ledger_lines) or "（无）"

    user = (
        "【现有记忆条目】\n" + current_entries + "\n\n"
        "【现有台账】\n" + current_ledger + "\n\n"
        "【对话新增部分】\n" + new_script + "\n\n"
        "请按规则增量输出 JSON。"
    )
    text = await complete(
        provider, [Message(role="user", content=user)],
        system_prompt=DEFAULT_EXTRACT_SYS,
    )
    data = extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("抽取输出不是 JSON 对象")

    entry_report = apply_entry_ops(book, data.get("entries") or [])
    char_store.save_book(char_id, book)
    ledger_report = apply_ops(ledger, data.get("ledger") or [])
    char_store.save_ledger(char_id, ledger)
    return {"entries": entry_report, "ledger": ledger_report}


async def maybe_structured_extract(*, char_store, char_id: str,
                                   marker_key: str, history, provider,
                                   every: int):
    """达到每 N 轮节奏时执行一次增量抽取；未到节奏返回 None。

    进度按 EntryBook.extract_markers[marker_key]（已见 user 轮数）。
    marker_key 必须对应一份独立历史：房间路径传窗口 id，旧单 AI 路径传
    会话 id——多个窗口共用会话 id 会让进度互相覆盖。
    """
    book = char_store.load_book(char_id)
    user_positions = [
        i for i, m in enumerate(history) if m.role == "user"
    ]
    user_total = len(user_positions)
    seen = int(book.extract_markers.get(marker_key, 0) or 0)
    # 标记领先于当前历史（换历史形态/旧键复用）：按 0 重抽，避免负数差
    # 永远卡在「未到节奏」或把标记回写成更小的值
    if seen > user_total:
        seen = 0
    if user_total - seen < every:
        return None

    start = (
        user_positions[seen] if seen < len(user_positions) else len(history)
    )
    from ..lore.gen import messages_to_script

    # 群聊共享历史必须带发言人名字，否则会把别的角色说的话/做的事
    # 抽进当前角色的记忆簿（单 AI 消息无 name，前缀为空，无副作用）
    new_script = messages_to_script(history[start:], with_names=True)
    report = await structured_extract(
        char_store, char_id, provider, new_script
    )

    book = char_store.load_book(char_id)
    book.extract_markers[marker_key] = user_total
    char_store.save_book(char_id, book)
    return report
