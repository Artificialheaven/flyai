# -*- coding: utf-8 -*-
"""酒馆世界书导入、宏替换、深度注入。"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from flyai.agent.loop import _inject_depth_blocks
from flyai.lore.models import Character, LoreEntry, World
from flyai.lore.query import LoreQuerier
from flyai.lore import tavern
from flyai.lore.card_import import character_from_cc
from flyai.schemas import Message
from flyai.tavern import lenient_json_loads, render_tavern_macros

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_WB = ROOT / "example" / "-404的nsfw优化世界书2.0(1).json"
EXAMPLE_PRESET = ROOT / "example" / "【MoM】果实4.96丨喵喵电波@KKM@YUKITEST.txt"


# ---- 示例文件整体解析 ----

def test_example_worldbook_stats():
    data = json.loads(EXAMPLE_WB.read_text(encoding="utf-8"), strict=False)
    w, stats = tavern.worldbook_from_dict(data, name="示例")
    assert w.name == "示例"
    # 10 条原始 entry，其中 2 条空内容占位被跳过
    assert stats["entries"] == 8
    assert stats["skipped"] == 2
    # 该世界书的增强包默认全部 disable：作为非常驻普通条目保留，
    # 用户在编辑器里手动常驻（与酒馆里手动勾选启用一致）
    assert stats["disabled"] == 8
    assert stats["depth"] == 0
    assert len(w.table) == 8
    assert all(e.pinned is False for e in w.table)
    # 全部条目 position=4（@depth depth=1）：常驻后会插到历史深处
    assert all(e.position == "depth" for e in w.table)
    assert all(e.depth == 1 for e in w.table)


def test_lenient_loader_parses_dirty_preset():
    if not EXAMPLE_PRESET.exists():
        pytest.skip("示例预设文件不存在")
    data = lenient_json_loads(EXAMPLE_PRESET.read_text(encoding="utf-8"))
    assert len(data["prompts"]) == 190
    assert data["temperature"] == 1.2


# ---- 条目映射 ----

def _entry(**over):
    base = {
        "uid": 0, "key": [], "keysecondary": [], "comment": "t", "content": "c",
        "constant": False, "vectorized": False, "selective": True,
        "selectiveLogic": 0, "order": 100, "position": 0, "disable": False,
        "probability": 100, "useProbability": True, "depth": 4, "role": 0,
    }
    base.update(over)
    return base


def test_parse_constant_depth_entry():
    e = tavern.parse_entry(_entry(constant=True, position=4, depth=2,
                                  order=300, role=1))
    assert e.pinned is True
    assert e.position == "depth"
    assert e.depth == 2
    assert e.insert_order == 300
    assert e.role == "user"


def test_parse_disabled_constant_kept_dormant():
    e = tavern.parse_entry(_entry(constant=True, disable=True))
    assert e.pinned is False
    assert e.constant is False
    assert e.enabled is False
    assert e.position == "system"


def test_parse_keys_primary_and_secondary_logic0():
    e = tavern.parse_entry(_entry(
        key=["铁剑, 符文"], keysecondary=["铁剑铭文", "咒文"],
        selective=True, selectiveLogic=0,
    ))
    assert e.keys == ["铁剑", "符文"]
    assert e.secondary_keys == ["铁剑铭文", "咒文"]
    assert e.selective_logic == "and_any"


def test_parse_not_logic_keeps_secondary():
    e = tavern.parse_entry(_entry(
        key=["铁剑"], keysecondary=["咒文"], selectiveLogic=2,
    ))
    assert e.keys == ["铁剑"]
    assert e.secondary_keys == ["咒文"]
    assert e.selective_logic == "not_any"


def test_parse_probability_disabled():
    e = tavern.parse_entry(_entry(probability=30, useProbability=False))
    assert e.probability == 100
    e2 = tavern.parse_entry(_entry(probability=30, useProbability=True))
    assert e2.probability == 30


def test_worldbook_entries_dict_or_list():
    payload = {"entries": {
        "0": {"comment": "a", "content": "A", "key": [], "constant": True},
        "1": {"comment": "b", "content": "B", "key": [], "constant": False},
    }}
    w, stats = tavern.worldbook_from_dict(payload)
    assert [e.title for e in w.table] == ["a", "b"]
    payload2 = {"entries": [payload["entries"]["0"], payload["entries"]["1"]]}
    w2, _ = tavern.worldbook_from_dict(payload2)
    assert [e.title for e in w2.table] == ["a", "b"]


def test_worldbook_missing_entries_raises():
    with pytest.raises(tavern.WorldbookError):
        tavern.worldbook_from_dict({"foo": 1})


# ---- 角色卡内嵌条目复用同一映射 ----

def test_card_book_entries_carry_depth_and_keys():
    card = {"data": {
        "name": "X",
        "character_book": {"entries": [
            {"comment": "深层", "content": "deep", "keys": ["触发"],
             "constant": True, "position": 4, "depth": 3, "role": 2,
             "enabled": True},
            {"comment": "禁用", "content": "off", "keys": [],
             "constant": True, "enabled": False},
        ]},
    }}
    c = character_from_cc(card)
    deep = next(e for e in c.table if e.title == "深层")
    assert deep.keys == ["触发"]
    assert deep.position == "depth" and deep.depth == 3
    assert deep.role == "assistant"
    dormant = next(e for e in c.table if e.title == "禁用")
    assert dormant.pinned is False  # 禁用条目保留但不常驻


# ---- 宏替换 ----

def test_macros_basic():
    rng = random.Random(7)
    out = render_tavern_macros(
        "{{char}}对{{user}}说，在场{{group}}；{{random:a,b}}；"
        "{{setvar::x::1}}{{getvar::x}}{{//注释}}剩{{model}}尾",
        char_name="小爱", user_name="阿明", group_names=["甲", "乙"], rng=rng,
    )
    assert out.startswith("小爱对阿明说，在场甲、乙；")
    assert "{{" not in out
    assert "剩尾" in out
    assert "setvar" not in out and "注释" not in out and "model" not in out


def test_macros_last_user_message_and_defaults():
    assert render_tavern_macros("{{char}}: {{lastUserMessage}}",
                                last_message="你好") == "角色: 你好"
    assert render_tavern_macros("") == ""


def test_lenient_json_repairs_bare_quote():
    assert lenient_json_loads('{"a": "x\\"y"}') == {"a": 'x"y'}
    assert lenient_json_loads('{"a": "x\\u0041"}') == {"a": "xA"}


# ---- 模型往返 ----

def test_lore_entry_new_fields_roundtrip():
    e = LoreEntry("t", "c", pinned=True, position="depth", depth=2,
                  insert_order=300, role="assistant", probability=40)
    d = e.to_dict()
    e2 = LoreEntry.from_dict(d)
    assert (e2.position, e2.depth, e2.insert_order, e2.role, e2.probability) == \
        ("depth", 2, 300, "assistant", 40)
    # 非法值归一
    e3 = LoreEntry.from_dict({"title": "x", "position": "weird", "role": "bot",
                              "depth": "bad", "probability": 999})
    assert e3.position == "system" and e3.role == "system"
    assert e3.depth == 4 and e3.probability == 100


# ---- depth_block_specs 概率 ----

def test_depth_block_specs_probability():
    w = World(table=[
        LoreEntry("必中", "a", pinned=True, position="depth", probability=100),
        LoreEntry("半成", "b", pinned=True, position="depth", probability=50),
        LoreEntry("不中", "c", pinned=True, position="depth", probability=0),
        LoreEntry("系统位", "d", pinned=True, position="system"),
        LoreEntry("非常驻", "e", pinned=False, position="depth"),
    ])
    q = LoreQuerier(None, w)
    rng = random.Random(1)
    specs = q.depth_block_specs(rng=rng)
    titles = {s["title"] for s in specs}
    assert "必中" in titles
    assert "不中" not in titles
    assert "系统位" not in titles
    assert "非常驻" not in titles
    assert all(s["content"] for s in specs)


# ---- 深度插入位置 ----

def _msgs(n):
    return [Message(role="system", content="sys")] + \
           [Message(role="user" if i % 2 == 0 else "assistant", content="m%d" % i)
            for i in range(n)]


def test_inject_depth_blocks_placement():
    msgs = _msgs(10)
    blocks = [
        {"role": "system", "content": "D4", "depth": 4, "order": 100},
        {"role": "system", "content": "D2", "depth": 2, "order": 100},
    ]
    _inject_depth_blocks(msgs, blocks)
    seq = [(m.role, m.content) for m in msgs]
    # 末尾 2 条在 D2 之后；倒数第 3-4 条（m6,m7）在 D4 与 D2 之间
    assert seq.index(("system", "D4")) < seq.index(("system", "D2"))
    assert seq[-1][1] == "m9" and seq[-2][1] == "m8"
    # D4 应位于 m5 与 m6 之间
    i4 = seq.index(("system", "D4"))
    assert seq[i4 - 1][1] == "m5" and seq[i4 + 1][1] == "m6"


def test_inject_depth_blocks_same_depth_order():
    msgs = _msgs(6)
    blocks = [
        {"role": "system", "content": "B", "depth": 2, "order": 200},
        {"role": "system", "content": "A", "depth": 2, "order": 100},
        {"role": "user", "content": "U", "depth": 2, "order": 150},
    ]
    _inject_depth_blocks(msgs, blocks)
    contents = [m.content for m in msgs]
    assert contents[contents.index("A"):contents.index("A") + 3] == ["A", "U", "B"]


def test_inject_depth_blocks_clamps_before_system():
    msgs = _msgs(2)
    _inject_depth_blocks(msgs, [
        {"role": "system", "content": "DEEP", "depth": 9, "order": 1},
    ])
    assert msgs[0].content == "sys"
    assert msgs[1].content == "DEEP"


def test_inject_depth_blocks_empty_noop():
    msgs = _msgs(3)
    _inject_depth_blocks(msgs, [])
    _inject_depth_blocks(msgs, [{"content": "  ", "depth": 1}])
    assert len(msgs) == 4
