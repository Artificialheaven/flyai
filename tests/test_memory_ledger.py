"""台账 ops：新增/更新/终结/复活 + 检索（P5）。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.character_memory import CharacterMemoryStore
from flyai.memory.ledger import (
    KINDS,
    apply_ops,
    load_ledger,
    match_items,
    open_summary,
)


def _empty():
    return {k: [] for k in KINDS}


def test_upsert_adds_then_updates():
    ledger = _empty()
    r1 = apply_ops(ledger, [
        {"kind": "item", "name": "银钥匙", "state": "在用户手中",
         "op": "upsert"},
    ])
    assert r1["added"] == 1
    assert ledger["item"][0].status == "active"

    r2 = apply_ops(ledger, [
        {"kind": "item", "name": "银钥匙", "state": "交给了老板娘",
         "op": "upsert"},
    ])
    assert r2["updated"] == 1
    item = ledger["item"][0]
    assert item.state == "交给了老板娘"
    assert len(item.history) == 1
    assert item.history[0]["state"] == "在用户手中"


def test_resolve_and_cancel():
    ledger = _empty()
    apply_ops(ledger, [
        {"kind": "task", "name": "修理护盾", "state": "进行中",
         "op": "upsert"},
        {"kind": "foreshadow", "name": "神秘来信", "state": "未拆",
         "op": "upsert"},
    ])
    report = apply_ops(ledger, [
        {"kind": "task", "name": "修理护盾", "op": "resolve"},
        {"kind": "foreshadow", "name": "神秘来信", "op": "cancel"},
    ])
    assert report["closed"] == 2
    assert ledger["task"][0].status == "done"
    assert ledger["foreshadow"][0].status == "cancelled"


def test_terminal_op_without_existing_creates_terminal():
    ledger = _empty()
    report = apply_ops(ledger, [
        {"kind": "task", "name": "来路不明的任务", "op": "resolve",
         "state": "直接完成"},
    ])
    assert report["added"] == 1
    assert ledger["task"][0].status == "done"


def test_closed_task_revives_on_upsert():
    ledger = _empty()
    apply_ops(ledger, [
        {"kind": "task", "name": "采购", "op": "resolve"},
    ])
    apply_ops(ledger, [
        {"kind": "task", "name": "采购", "state": "重新开始",
         "op": "upsert"},
    ])
    assert ledger["task"][0].status == "open"
    assert ledger["task"][0].state == "重新开始"


def test_match_items_or_substring():
    ledger = _empty()
    apply_ops(ledger, [
        {"kind": "item", "name": "银钥匙", "state": "老板娘持有",
         "op": "upsert"},
        {"kind": "item", "name": "旧地图", "state": "抽屉里",
         "op": "upsert"},
    ])
    hit = match_items(ledger, ["银钥匙"])
    assert len(hit) == 1 and hit[0].name == "银钥匙"
    hit2 = match_items(ledger, ["抽屉"])
    assert len(hit2) == 1 and hit2[0].name == "旧地图"
    assert match_items(ledger, ["不存在"]) == []


def test_open_summary_excludes_closed():
    ledger = _empty()
    apply_ops(ledger, [
        {"kind": "task", "name": "未完成任务", "op": "upsert"},
        {"kind": "task", "name": "已完成任务", "op": "resolve"},
        {"kind": "foreshadow", "name": "待回收伏笔", "op": "upsert"},
    ])
    summary = open_summary(ledger)
    names = [it.name for it in summary]
    assert "未完成任务" in names and "待回收伏笔" in names
    assert "已完成任务" not in names


def test_ledger_persist_through_store(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    ledger = store.load_ledger("TA")
    apply_ops(ledger, [
        {"kind": "item", "name": "银钥匙", "state": "用户手中",
         "op": "upsert"},
    ])
    store.save_ledger("TA", ledger)
    reloaded = store.load_ledger("TA")
    assert reloaded["item"][0].name == "银钥匙"

    # 损坏文档跳过不炸（库里塞坏行）
    store._db.execute(
        "INSERT INTO ledger(k, doc, updated_at) VALUES(?,?,?)",
        ("TB", "{bad", 0.0),
    )
    assert store.load_ledger("TB")["item"] == []
