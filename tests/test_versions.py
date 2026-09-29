"""历史编辑 / 重新生成版本管理单测（sessions/versions.py）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.schemas import Message, ToolCall
from flyai.sessions.models import Session, Thread
from flyai.sessions import versions
from flyai.sessions.json_store import JsonSessionStore


def make_thread():
    t = Thread(kind="dm")
    t.messages = [
        Message(role="user", content="A"),
        Message(role="assistant", content="reply A"),
        Message(role="user", content="B"),
        Message(role="assistant", content="reply B"),
    ]
    return t


def test_locate_turn():
    t = make_thread()
    a, b = t.messages[0].id, t.messages[2].id
    assert versions.locate_turn(t.messages, a) == (0, 2)
    assert versions.locate_turn(t.messages, b) == (2, 4)
    with pytest.raises(ValueError):
        versions.locate_turn(t.messages, "nope")
    with pytest.raises(ValueError):
        versions.locate_turn(t.messages, t.messages[1].id)  # assistant 不能做锚


def test_snapshot_only_once():
    t = make_thread()
    anchor = t.messages[0].id
    versions.ensure_snapshot(t, anchor)
    entry = t.edits[anchor]
    assert len(entry["edits"]) == 1
    assert entry["edits"][0]["reply_variants"] == [
        [t.messages[1].to_dict()]
    ]
    versions.ensure_snapshot(t, anchor)
    assert len(t.edits[anchor]["edits"]) == 1


def test_begin_edit_first_turn():
    t = make_thread()
    anchor = t.messages[0].id
    old_b_user, old_b_reply = t.messages[2], t.messages[3]
    prev = versions.begin_edit(t, anchor, Message(role="user", content="A2"))

    assert prev == (0, 0)
    # suffix 已拆出扁平历史（不发给模型），存在 pending_suffix
    assert [m.id for m in t.messages] == [anchor]
    entry = t.edits[anchor]
    assert [d["id"] for d in entry["pending_suffix"]] == [old_b_user.id, old_b_reply.id]
    assert t.messages[0].content == "A2"
    assert entry["edit_active"] == 1 and len(entry["edits"]) == 1 + 1
    assert entry["edits"][1]["reply_variants"] == []

    # 流式产出 + 提交
    head = len(t.messages)
    reply_a2 = Message(role="assistant", content="reply A2")
    t.messages.append(reply_a2)
    versions.commit_run(t, anchor, head, versions.MODE_EDIT, prev)

    assert [m.id for m in t.messages] == [
        anchor, reply_a2.id, old_b_user.id, old_b_reply.id
    ]
    assert [m.content for m in t.messages] == ["A2", "reply A2", "B", "reply B"]
    assert "pending_suffix" not in entry
    slot = entry["edits"][1]
    assert len(slot["reply_variants"]) == 1 and slot["reply_active"] == 0
    # 旧版本完好
    old_slot = entry["edits"][0]
    assert old_slot["user"]["content"] == "A"
    assert old_slot["reply_variants"][0][0]["content"] == "reply A"


def test_regen_creates_variant():
    t = make_thread()
    anchor = t.messages[2].id
    prev = versions.begin_regen(t, anchor)
    assert [m.content for m in t.messages] == ["A", "reply A", "B"]
    head = len(t.messages)
    t.messages.append(Message(role="assistant", content="reply B2"))
    versions.commit_run(t, anchor, head, versions.MODE_REGEN, prev)

    assert [m.content for m in t.messages] == ["A", "reply A", "B", "reply B2"]
    slot = t.edits[anchor]["edits"][0]
    assert len(slot["reply_variants"]) == 2 and slot["reply_active"] == 1


def test_switch_and_bad_index():
    t = make_thread()
    anchor = t.messages[2].id
    prev = versions.begin_regen(t, anchor)
    head = len(t.messages)
    t.messages.append(Message(role="assistant", content="reply B2"))
    versions.commit_run(t, anchor, head, versions.MODE_REGEN, prev)

    versions.switch_version(t, anchor, reply_index=0)
    assert t.messages[-1].content == "reply B"
    # prefix 不变
    assert [m.content for m in t.messages[:3]] == ["A", "reply A", "B"]

    with pytest.raises(KeyError):
        versions.switch_version(t, "missing", reply_index=0)
    with pytest.raises(IndexError):
        versions.switch_version(t, anchor, reply_index=9)
    with pytest.raises(IndexError):
        versions.switch_version(t, anchor, edit_index=9)


def test_empty_produced_rolls_back_edit():
    t = make_thread()
    anchor = t.messages[0].id
    prev = versions.begin_edit(t, anchor, Message(role="user", content="A2"))
    head = len(t.messages)
    versions.commit_run(t, anchor, head, versions.MODE_EDIT, prev)

    assert [m.content for m in t.messages] == ["A", "reply A", "B", "reply B"]
    entry = t.edits[anchor]
    assert len(entry["edits"]) == 1 and entry["edit_active"] == 0


def test_empty_produced_rolls_back_regen():
    t = make_thread()
    anchor = t.messages[0].id
    prev = versions.begin_regen(t, anchor)
    head = len(t.messages)
    versions.commit_run(t, anchor, head, versions.MODE_REGEN, prev)

    assert [m.content for m in t.messages] == ["A", "reply A", "B", "reply B"]
    slot = t.edits[anchor]["edits"][0]
    assert len(slot["reply_variants"]) == 1 and slot["reply_active"] == 0


def test_partial_tool_output_is_kept():
    t = make_thread()
    anchor = t.messages[0].id
    prev = versions.begin_regen(t, anchor)
    head = len(t.messages)
    t.messages.append(
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c1", name="generate_image")],
        )
    )
    versions.commit_run(t, anchor, head, versions.MODE_REGEN, prev)
    slot = t.edits[anchor]["edits"][0]
    assert len(slot["reply_variants"]) == 2
    assert slot["reply_variants"][1][0]["tool_calls"][0]["name"] == "generate_image"


def test_heal_orphan_flat_replies():
    t = make_thread()
    anchor = t.messages[0].id
    versions.ensure_snapshot(t, anchor)
    # 模拟硬 kill：扁平区被换成未入 map 的半成品回复
    partial = Message(role="assistant", content="partial…")
    t.messages[1:2] = [partial]

    versions.heal_thread(t)
    slot = t.edits[anchor]["edits"][0]
    assert len(slot["reply_variants"]) == 2 and slot["reply_active"] == 1
    # 再 heal 一次幂等
    versions.heal_thread(t)
    assert len(slot["reply_variants"]) == 2


def test_heal_empty_after_regen_via_store(tmp_path):
    store = JsonSessionStore(base_dir=tmp_path)
    s = store.create()
    t = Thread(kind="dm", title="私聊")
    t.messages = [
        Message(role="user", content="A"),
        Message(role="assistant", content="reply A"),
    ]
    s.threads = [t]
    versions.begin_regen(t, t.messages[0].id)  # 扁平回复被滑出，未提交
    store.save(s)

    s2 = store.get(s.id)
    t2 = s2.threads[0]
    # 旧回复被还原
    assert [m.content for m in t2.messages] == ["A", "reply A"]


def test_heal_midstream_edit_restores_suffix(tmp_path):
    store = JsonSessionStore(base_dir=tmp_path)
    s = store.create()
    t = Thread(kind="dm", title="私聊")
    t.messages = [
        Message(role="user", content="A"),
        Message(role="assistant", content="reply A"),
        Message(role="user", content="B"),
        Message(role="assistant", content="reply B"),
    ]
    s.threads = [t]
    anchor = t.messages[0].id
    versions.begin_edit(t, anchor, Message(role="user", content="A2"))
    t.messages.append(Message(role="assistant", content="partial…"))
    store.save(s)  # 硬 kill：部分回复未 commit，suffix 在 pending_suffix

    s2 = store.get(s.id)  # 加载即 heal
    t2 = s2.threads[0]
    assert [m.content for m in t2.messages] == ["A2", "partial…", "B", "reply B"]
    assert "pending_suffix" not in t2.edits[anchor]
    # partial 收进**新 edit 槽**；旧槽仍是 reply A
    new_slot = t2.edits[anchor]["edits"][1]
    assert len(new_slot["reply_variants"]) == 1
    assert new_slot["reply_variants"][0][0]["content"] == "partial…"
    assert len(t2.edits[anchor]["edits"][0]["reply_variants"]) == 1

    # 再次加载幂等：不新增 variant
    s3 = store.get(s.id)
    t3 = s3.threads[0]
    assert [m.content for m in t3.messages] == ["A2", "partial…", "B", "reply B"]
    assert len(t3.edits[anchor]["edits"][1]["reply_variants"]) == 1


def test_thread_roundtrip_preserves_versions(tmp_path):
    store = JsonSessionStore(base_dir=tmp_path)
    s = store.create()
    t = Thread(kind="dm", title="私聊")
    t.messages = [
        Message(role="user", content="A"),
        Message(role="assistant", content="reply A"),
        Message(role="user", content="B"),
        Message(role="assistant", content="reply B"),
    ]
    s.threads = [t]
    anchor = t.messages[0].id
    b_ids = [m.id for m in t.messages[2:]]
    prev = versions.begin_edit(t, anchor, Message(role="user", content="A2"))
    head = len(t.messages)
    reply_a2 = Message(role="assistant", content="reply A2")
    t.messages.append(reply_a2)
    versions.commit_run(t, anchor, head, versions.MODE_EDIT, prev)
    store.save(s)

    s2 = store.get(s.id)
    t2 = s2.threads[0]
    assert [m.id for m in t2.messages] == [anchor, reply_a2.id] + b_ids
    assert [m.content for m in t2.messages] == ["A2", "reply A2", "B", "reply B"]
    entry = t2.edits[anchor]
    assert entry["edit_active"] == 1 and len(entry["edits"]) == 2
    assert entry["edits"][0]["user"]["content"] == "A"
    assert len(entry["edits"][1]["reply_variants"]) == 1
