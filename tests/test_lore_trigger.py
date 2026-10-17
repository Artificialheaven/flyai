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
