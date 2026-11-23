"""内容表触发词扫描：精确/模糊命中、keys 优先、常驻跳过、预算与语义补足。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore import trigger
from flyai.lore.models import Character, LoreEntry, World
from flyai.lore.query import LoreQuerier


# ---- entry_triggers：keys 优先，留空回退标题 ----

def test_entry_triggers_prefers_keys():
    e = LoreEntry("标题A", "内容", keys=["铁剑", "剑术"])
    assert trigger.entry_triggers(e) == ["铁剑", "剑术"]


def test_entry_triggers_falls_back_to_title():
    assert trigger.entry_triggers(LoreEntry("铁剑", "内容")) == ["铁剑"]
    assert trigger.entry_triggers(LoreEntry("", "内容", keys=["  "])) == []


# ---- fuzzy_hit ----

def test_fuzzy_hit_exact_substring_case_insensitive():
    assert trigger.fuzzy_hit("Alice", "where is alice now")
    assert trigger.fuzzy_hit("铁剑", "我那把铁剑呢")


def test_fuzzy_hit_cjk_three_char_one_typo():
    # 3 字词容忍 1 字写错（等长滑窗相同字数 ≥ n-1）
    assert trigger.fuzzy_hit("魔术师", "我遇见了魔术师，他点点头")
    # 完全不相干不命中
    assert not trigger.fuzzy_hit("魔术师", "今天晚饭吃红烧肉")


def test_fuzzy_hit_four_char_one_insertion_deletion():
    # 4 字以上容忍 1 字增删（SequenceMatcher ≥ 0.75）
    assert trigger.fuzzy_hit("骑士团长", "那位骑士团走了过来")


def test_fuzzy_hit_two_char_no_fuzzy():
    # 二字词错一个字歧义过大，不做模糊（铁剑 ≠ 铁箭）
    assert not trigger.fuzzy_hit("铁剑", "我那把铁箭呢")
    # 精确仍然命中
    assert trigger.fuzzy_hit("铁剑", "我那把铁剑呢")


def test_fuzzy_hit_empty_inputs():
    assert not trigger.fuzzy_hit("", "abc")
    assert not trigger.fuzzy_hit("abc", "")


# ---- keyword_hits：pinned 跳过、keys 命中 ----

def test_keyword_hits_skips_pinned_and_uses_keys():
    pinned = LoreEntry("常驻条目", "常驻内容", pinned=True, keys=["铁剑"])
    hit = LoreEntry("某标题", "与铁剑相关", keys=["铁剑"])
    miss = LoreEntry("别的", "无关", keys=["魔法"])
    tables = [("character", [pinned, hit, miss])]
    out = trigger.keyword_hits(tables, "我把铁剑放哪了")
    assert out == [("character", hit)]


def test_keyword_hits_fuzzy_key():
    # 「魔术师」写成「魔木师」（错一字），3 字词模糊命中
    e = LoreEntry("标题", "内容", keys=["魔术师"])
    out = trigger.keyword_hits([("world", [e])], "一位魔木师走来")
    assert out == [("world", e)]


def test_keyword_hits_skips_disabled_and_constant():
    disabled = LoreEntry("禁用", "内容", keys=["铁剑"], enabled=False)
    constant = LoreEntry("常驻", "内容", keys=["铁剑"], constant=True)
    hit = LoreEntry("命中", "内容", keys=["铁剑"])
    out = trigger.keyword_hits([("world", [disabled, constant, hit])], "铁剑")
    assert out == [("world", hit)]


def test_keyword_hits_secondary_and_any_and_all():
    entry = LoreEntry(
        "二次键", "内容", keys=["铁剑"], secondary_keys=["铭文", "咒文"],
        selective_logic="and_all",
    )
    assert not trigger.keyword_hits([("world", [entry])], "铁剑 铭文")
    assert trigger.keyword_hits([("world", [entry])], "铁剑 铭文 咒文")


def test_keyword_hits_secondary_not_any():
    entry = LoreEntry(
        "排除", "内容", keys=["铁剑"], secondary_keys=["已损坏"],
        selective_logic="not_any",
    )
    assert trigger.keyword_hits([("world", [entry])], "完好的铁剑")
    assert not trigger.keyword_hits([("world", [entry])], "铁剑已损坏")


def test_keyword_hits_scan_depth_reads_recent_history():
    entry = LoreEntry("时线", "内容", keys=["时间线"], scan_depth=3)
    tables = [("world", [entry])]
    # 当前消息没提，但最近 3 条里提过 → 命中
    assert trigger.keyword_hits(
        tables, "继续", scan_texts=["时间线更新", "无关", "继续"]
    ) == [("world", entry)]
    # 提到它的消息已经超出扫描深度 → 不命中
    assert trigger.keyword_hits(
        tables, "继续", scan_texts=["时间线更新", "a", "b", "继续"]
    ) == []


def test_keyword_hits_sticky_keeps_entry_active():
    entry = LoreEntry("黏性", "内容", keys=["时间线"], sticky=2)
    tables = [("world", [entry])]
    assert trigger.keyword_hits(
        tables, "继续", scan_texts=["时间线更新", "普通", "继续"]
    ) == [("world", entry)]
    assert trigger.keyword_hits(
        tables, "继续", scan_texts=["时间线更新", "a", "b", "继续"]
    ) == []


def test_keyword_hits_cooldown_suppresses_reactivation():
    entry = LoreEntry("冷却", "内容", keys=["时间线"], cooldown=2)
    tables = [("world", [entry])]
    # 两轮前刚激活过 → 本次命中也被冷却压掉
    assert trigger.keyword_hits(
        tables, "时间线更新", scan_texts=["时间线更新", "普通", "时间线更新"]
    ) == []
    # 冷却窗口外 → 正常激活
    assert trigger.keyword_hits(
        tables, "时间线更新", scan_texts=["时间线更新", "a", "b", "时间线更新"]
    ) == [("world", entry)]


def test_keyword_hits_recursion_from_activated_content():
    first = LoreEntry("铁剑", "这把铁剑刻有古老符文", keys=["铁剑"])
    second = LoreEntry("符文", "符文会发光", keys=["符文"])
    out = trigger.keyword_hits([("world", [first, second])], "我拔出了铁剑")
    assert out == [("world", first), ("world", second)]


def test_keyword_hits_recursion_blocked_by_flags():
    first = LoreEntry("铁剑", "这把铁剑刻有古老符文", keys=["铁剑"])
    blocked = LoreEntry("符文", "内容", keys=["符文"], prevent_recursion=True)
    out = trigger.keyword_hits([("world", [first, blocked])], "铁剑")
    assert out == [("world", first)]

    first_out = LoreEntry("铁剑", "符文", keys=["铁剑"], exclude_recursion=True)
    second = LoreEntry("符文", "内容", keys=["符文"])
    out = trigger.keyword_hits([("world", [first_out, second])], "铁剑")
    assert out == [("world", first_out)]


def test_keyword_hits_delay_waits_for_enough_messages():
    entry = LoreEntry("延迟", "内容", keys=["线索"], delay=3)
    tables = [("world", [entry])]
    assert trigger.keyword_hits(tables, "线索", scan_texts=["线索"]) == []
    assert trigger.keyword_hits(
        tables, "线索", scan_texts=["a", "b", "线索"]
    ) == [("world", entry)]


def test_keyword_hits_case_and_whole_word():
    entry = LoreEntry(
        "英文", "内容", keys=["Sword"], case_sensitive=True, match_whole_words=True,
    )
    assert trigger.keyword_hits([("world", [entry])], "take the Sword")
    assert not trigger.keyword_hits([("world", [entry])], "take the sword")
    assert not trigger.keyword_hits([("world", [entry])], "Swordsman")


# ---- render_trigger_block：格式、去重、预算 ----

def test_render_trigger_block_empty():
    assert trigger.render_trigger_block([]) == ""


def test_render_trigger_block_format_and_labels():
    block = trigger.render_trigger_block([
        ("character", LoreEntry("铁剑", "剑身由寒铁铸造")),
        ("world", LoreEntry("亚瑟王国", "位于大陆东部")),
    ])
    assert "【相关设定】" in block
    assert "『铁剑』（角色资料）" in block and "剑身由寒铁铸造" in block
    assert "『亚瑟王国』（世界设定）" in block


def test_render_trigger_block_dedupes_across_tables():
    e1 = ("character", LoreEntry("铁剑", "同一内容"))
    e2 = ("world", LoreEntry("铁剑", "同一内容"))
    picked = trigger.dedupe([e1, e2])
    assert len(picked) == 1
    block = trigger.render_trigger_block(picked)
    assert block.count("『铁剑』") == 1


def test_render_trigger_block_respects_count_and_char_budget():
    picked = [
        ("world", LoreEntry("条目%02d" % i, "甲" * 400))
        for i in range(8)
    ]
    block = trigger.render_trigger_block(picked)
    # 最多 6 条；1500 字预算下只放得下前 3~4 条（每条 400+）
    assert "『条目00』" in block
    assert "『条目07』" not in block and "『条目06』" not in block
    body = block.split("：\n", 1)[1]
    assert len(body) <= trigger.TRIGGER_BUDGET + 20  # 允许省略标记误差


# ---- LoreQuerier.trigger_block 端到端（无 embedder）----

def _querier(char_entries=None, world_entries=None):
    return LoreQuerier(
        Character(name="艾拉", table=char_entries or []),
        World(name="艾尔", table=world_entries or []),
    )


@pytest.mark.asyncio
async def test_trigger_block_injects_keyword_and_fuzzy():
    q = _querier(
        char_entries=[
            LoreEntry("佩剑", "寒铁短剑，随身佩戴", pinned=True),  # 常驻，不参与
            LoreEntry("身世线索", "与王室有关", keys=["魔术师"]),
        ],
        world_entries=[LoreEntry("亚瑟王国", "大陆东部的国家")],
    )
    block = await q.trigger_block("那位魔木师知道亚瑟王国吗？")
    assert "『身世线索』（角色资料）" in block  # 错一字模糊命中 keys
    assert "『亚瑟王国』（世界设定）" in block   # 标题精确命中
    assert "佩剑" not in block                    # pinned 不重复注入


@pytest.mark.asyncio
async def test_trigger_block_empty_text_and_no_hit():
    q = _querier(char_entries=[LoreEntry("铁剑", "寒铁铸造")])
    assert await q.trigger_block("") == ""
    assert await q.trigger_block("   ") == ""
    assert await q.trigger_block("今天天气不错") == ""


# ---- 语义补足 ----

class _TriggerEmbedder:
    """确定性向量：含「母亲/信物」的文本落在特征 0，其余落在特征 1。"""

    def __init__(self, threshold=0.55, fail=False):
        self.cfg = SimpleNamespace(score_threshold=threshold, top_k=3)
        self.fail = fail

    async def embed(self, texts):
        if self.fail:
            raise RuntimeError("嵌入服务崩溃")
        out = []
        for t in texts:
            v = [0.0, 0.0, 1.0]
            if ("母亲" in t) or ("信物" in t):
                v[0] += 1.0
            else:
                v[1] += 1.0
            out.append(v)
        return out


@pytest.mark.asyncio
async def test_trigger_block_semantic_top_up():
    # 「母亲的信物」无关键词命中，只能靠语义路补足；「铁剑」关键词命中
    q = _querier(char_entries=[
        LoreEntry("铁剑", "寒铁铸造", keys=["铁剑"]),
        LoreEntry("母亲的信物", "一枚旧银币，常年贴身收藏"),
    ])
    block = await q.trigger_block("她低头看着那枚旧银币，想起母亲",
                                  _TriggerEmbedder())
    assert "『铁剑』" not in block          # 查询与铁剑无关
    assert "『母亲的信物』" in block        # 语义补足
    assert "旧银币" in block


@pytest.mark.asyncio
async def test_trigger_block_semantic_does_not_duplicate_keyword_hit():
    e = LoreEntry("铁剑", "寒铁铸造", keys=["铁剑"])
    q = _querier(char_entries=[e])
    block = await q.trigger_block("我的铁剑", _TriggerEmbedder())
    assert block.count("『铁剑』") == 1


@pytest.mark.asyncio
async def test_trigger_block_embedder_failure_falls_back_to_keywords():
    q = _querier(char_entries=[LoreEntry("铁剑", "寒铁铸造", keys=["铁剑"])])
    block = await q.trigger_block("我的铁剑", _TriggerEmbedder(fail=True))
    assert "『铁剑』" in block


# ---- 宏渲染：触发条目也要按当前角色/用户/MVU 变量展开 ----

def test_render_trigger_block_expands_name_and_variable_macros():
    e = LoreEntry(
        "{{char}}的资料",
        "{{user}}的体力={{format_message_variable::stat_data.体力}}",
    )
    out = trigger.render_trigger_block(
        [("character", e)],
        char_name="零号", user_name="小明",
        variables={"stat_data": {"体力": 9}},
    )
    assert "零号的资料" in out
    assert "小明的体力=9" in out
    assert "{{" not in out


def test_entry_triggers_expands_name_macros_for_matching():
    e = LoreEntry("", "", keys=["{{user}}"])
    assert trigger.entry_triggers(
        e, lambda s: s.replace("{{user}}", "小明")
    ) == ["小明"]
