"""历史消息「编辑 / 重新生成」的版本管理（纯逻辑，不做模型调用）。

版本以**用户消息 id（anchor）**锚定一整轮：用户消息 + 紧随其后、直到下一条
用户消息之前的全部 assistant/tool 回复。当前活动（active）的一轮始终体现在
thread.messages 扁平列表里；非活动版本只存在 thread.edits 中，永不发给模型。

thread.edits 结构：
  { "<anchor>": {
      "edits": [
        {"user": <msg dict>,
         "reply_variants": [ [<reply msg dict>], ... ],
         "reply_active": int}
      ],
      "edit_active": int
  } }

编辑产生的新用户消息 id 强制等于 anchor（同一逻辑轮）。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Tuple

from ..schemas import Message

MODE_EDIT = "edit"
MODE_REGEN = "regenerate"


def locate_turn(messages: List[Message], anchor: str) -> Tuple[int, int]:
    """返回 anchor 用户消息索引与轮的排他终点（下一条 user 索引或 len）。

    anchor 不存在，或命中的不是 user 消息 → ValueError。
    """
    idx = None
    for i, m in enumerate(messages):
        if m.id == anchor:
            idx = i
            break
    if idx is None:
        raise ValueError(f"找不到消息: {anchor}")
    if messages[idx].role != "user":
        raise ValueError(f"锚点不是用户消息: {anchor}")
    end = len(messages)
    for j in range(idx + 1, len(messages)):
        # 撤回墓碑也是轮边界：否则编辑更早轮时切片会把墓碑吞掉
        if messages[j].role == "user" or getattr(messages[j], "tombstone", None):
            end = j
            break
    return idx, end


def _entry(thread, anchor: str) -> Dict[str, Any]:
    return thread.edits[anchor]


def _active_slot(entry: Dict[str, Any]) -> Dict[str, Any]:
    return entry["edits"][entry["edit_active"]]


def ensure_snapshot(thread, anchor: str) -> None:
    """anchor 尚无版本记录时，把当前扁平活动轮快照成「1 edit + 1 reply」。

    已有记录则为 no-op —— 全项目唯一快照点，保证活动轮不会被重复记录。
    """
    if anchor in thread.edits:
        return
    i, end = locate_turn(thread.messages, anchor)
    user_dict = copy.deepcopy(thread.messages[i].to_dict())
    replies = [copy.deepcopy(m.to_dict()) for m in thread.messages[i + 1:end]]
    slot = {"user": user_dict, "reply_variants": [replies], "reply_active": 0}
    thread.edits[anchor] = {"edits": [slot], "edit_active": 0}


def rebuild_active(thread, anchor: str) -> None:
    """按活动 edit + 活动 reply variant 重写扁平轮区域。允许 variant 为空。"""
    i, end = locate_turn(thread.messages, anchor)
    entry = _entry(thread, anchor)
    slot = _active_slot(entry)
    user = Message.from_dict(copy.deepcopy(slot["user"]))
    user.id = anchor  # 同一逻辑轮
    replies = []
    if slot["reply_variants"]:
        replies = [
            Message.from_dict(copy.deepcopy(d))
            for d in slot["reply_variants"][slot["reply_active"]]
        ]
    thread.messages[i:end] = [user] + replies


def _detach_suffix(thread, i: int, end: int) -> None:
    """把该轮**之后**的对话移出扁平历史：流式产出在尾部追加、且 suffix 不能发给
    模型。持久化到 entry['pending_suffix']（而非内存属性），硬 kill 也不丢。"""
    suffix = thread.messages[end:]
    if suffix:
        _entry(thread, thread.messages[i].id)["pending_suffix"] = (
            [copy.deepcopy(m.to_dict()) for m in suffix]
        )
        del thread.messages[end:]


def _restore_suffix(thread, anchor: str) -> None:
    entry = _entry(thread, anchor)
    dicts = entry.pop("pending_suffix", None)
    if dicts:
        thread.messages.extend(
            Message.from_dict(copy.deepcopy(d)) for d in dicts
        )


def begin_edit(thread, anchor: str, new_user_msg: Message) -> Tuple[int, int]:
    """快照旧轮 → 追加新 edit 槽（暂无回复）→ 扁平淡出旧回复与 suffix、
    只留 prefix + 新用户消息（流式时锚点在列表末尾）。

    返回 (旧 edit_active, 旧 reply_active) 供失败时回滚。
    """
    i, end = locate_turn(thread.messages, anchor)
    ensure_snapshot(thread, anchor)
    entry = _entry(thread, anchor)
    prev_edit = entry["edit_active"]
    prev_reply = _active_slot(entry)["reply_active"]

    new_user_msg.id = anchor
    slot = {
        "user": copy.deepcopy(new_user_msg.to_dict()),
        "reply_variants": [],
        "reply_active": 0,
    }
    entry["edits"].append(slot)
    entry["edit_active"] = len(entry["edits"]) - 1
    thread.messages[i:end] = [new_user_msg]
    # 此刻该轮在末尾（end == i+1）；detach i 之后
    _detach_suffix(thread, i, i + 1)
    return prev_edit, prev_reply


def begin_regen(thread, anchor: str) -> Tuple[int, int]:
    """快照旧轮 → 从扁平滑出活动回复与 suffix（不预建 variant，崩溃不留空槽）。"""
    i, end = locate_turn(thread.messages, anchor)
    ensure_snapshot(thread, anchor)
    entry = _entry(thread, anchor)
    slot = _active_slot(entry)
    prev = (entry["edit_active"], slot["reply_active"])
    _detach_suffix(thread, i, end)
    thread.messages[i + 1:] = []  # suffix 已拆走，这里只删旧回复
    return prev


def _has_usable(messages: List[Message]) -> bool:
    for m in messages:
        if (m.content or "").strip():
            return True
        if m.tool_calls:
            return True
    return False


def commit_run(
    thread, anchor: str, head_snapshot: int, mode: str, prev: Tuple[int, int]
) -> None:
    """流式结束后把本轮产出收编为新 reply variant；产出不可用则回滚。"""
    produced = thread.messages[head_snapshot:]
    entry = _entry(thread, anchor)
    if _has_usable(produced):
        slot = _active_slot(entry)
        dicts = [copy.deepcopy(m.to_dict()) for m in produced]
        slot["reply_variants"].append(dicts)
        slot["reply_active"] = len(slot["reply_variants"]) - 1
    elif mode == MODE_EDIT:
        # 新 edit 槽无可用回复：丢弃该槽，恢复旧 edit
        entry["edits"].pop()
        entry["edit_active"] = prev[0]
    else:
        _active_slot(entry)["reply_active"] = prev[1]
    rebuild_active(thread, anchor)
    _restore_suffix(thread, anchor)


def switch_version(
    thread,
    anchor: str,
    edit_index: int = None,
    reply_index: int = None,
) -> None:
    """切换活动 edit / reply variant。越界抛 IndexError/KeyError。"""
    entry = thread.edits.get(anchor)
    if entry is None:
        raise KeyError(anchor)
    if edit_index is not None:
        if not isinstance(edit_index, int) or not (0 <= edit_index < len(entry["edits"])):
            raise IndexError("edit 序号无效")
        entry["edit_active"] = edit_index
    slot = _active_slot(entry)
    if reply_index is not None:
        if not isinstance(reply_index, int) or not (
            0 <= reply_index < len(slot["reply_variants"])
        ):
            raise IndexError("reply 序号无效")
        slot["reply_active"] = reply_index
    rebuild_active(thread, anchor)


def _sig(d: Dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def heal_thread(thread) -> None:
    """加载后自愈：扁平回复区与记录的活动 variant 不一致（典型为流式中途被
    硬 kill）时，保留这些孤儿消息——空则还原记录、非空则补成一个 variant。"""
    for anchor in list(thread.edits.keys()):
        try:
            i, end = locate_turn(thread.messages, anchor)
        except ValueError:
            continue
        entry = _entry(thread, anchor)
        slot = _active_slot(entry)
        variants = slot["reply_variants"]
        active = variants[slot["reply_active"]] if variants else []
        flat = thread.messages[i + 1:end]

        if not flat:
            if active:
                # 记录有回复而扁平为空：未完成的重生成，还原旧回复
                rebuild_active(thread, anchor)
        else:
            flat_sigs = [_sig(m.to_dict()) for m in flat]
            active_sigs = [_sig(d) for d in active]
            if flat_sigs != active_sigs:
                # 孤儿的部分回复：补成本轮新 variant 并指向它（下次加载即一致）
                dicts = [copy.deepcopy(m.to_dict()) for m in flat]
                variants.append(dicts)
                slot["reply_active"] = len(variants) - 1

        # 流式前拆出的 suffix：轮处理完后原样接回（begin 已存盘，硬 kill 也不丢）
        _restore_suffix(thread, anchor)
