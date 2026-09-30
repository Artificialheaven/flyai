"""关键词打分搜索、语义阈值与回退、向量缓存、构建器新块。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import RoleplaySettings
from flyai.lore.models import Character, LoreEntry, World
from flyai.lore.query import (
    LoreQuerier,
    PromptOptions,
    build_member_system_prompt,
    build_prompt,
    build_system_prompt,
)
from flyai.tools.base import ToolContext
from flyai.tools.lore_tool import QueryLoreTool


def _run(coro):
    # 每次独立循环；需要跨搜索复用同一 querier 的测试必须放进单个协程
    return asyncio.run(coro)


def _querier(entries, world_entries=None):
    char = Character(name="初", table=entries)
    world = World(content="世界", table=world_entries or [])
    return LoreQuerier(char, world)


# ---- 关键词打分 ----

def test_keyword_title_exact_ranks_first():
    q = _querier([
        LoreEntry(title="无关", content="魔法在这里"),
        LoreEntry(title="魔法书", content="x"),
        LoreEntry(title="魔法", content="x"),
    ])
    out = q.search("character", "魔法")
    assert out[0].title == "魔法"           # 精确 +100
    assert out[1].title == "魔法书"         # 标题子串 +10
    assert out[2].title == "无关"           # 正文命中 +1


def test_keyword_searches_body():
    q = _querier([LoreEntry(title="标题", content="藏着暗号的内容")])
    out = q.search("character", "暗号")
    assert len(out) == 1 and out[0].title == "标题"


def test_keyword_limit():
    entries = [LoreEntry(title=str(i), content="命中词") for i in range(10)]
    q = _querier(entries)
    assert len(q.search("character", "命中词", 3)) == 3


def test_multi_term_match_both_ranks_first():
    q = _querier([
        LoreEntry(title="红苹果", content="苹果"),
        LoreEntry(title="苹果香蕉", content="香蕉苹果"),
    ])
    out = q.search("character", "苹果 香蕉")
    assert out[0].title == "苹果香蕉"   # 两词都命中（22）> 只命中苹果（11）


def test_empty_keyword_returns_all():
    q = _querier([LoreEntry(title="甲"), LoreEntry(title="乙")])
    assert len(q.search("character", "")) == 2


# ---- 语义阈值 / 缓存 ----

class _MapEmbedder:
    """查询词→[1,0]；条目文本含「高」→[1,0]，含「低」→[0,1]。"""

    def __init__(self):
        self.cfg = SimpleNamespace(top_k=4)
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            if t == "查询" or "高" in t:
                out.append([1.0, 0.0])
            elif "低" in t:
                out.append([0.0, 1.0])
            else:
                out.append([0.0, 0.0])
        return out


def test_semantic_threshold_filters():
    q = _querier([LoreEntry(title="高条", content="高分内容"), LoreEntry(title="低条", content="低分内容")])
    emb = _MapEmbedder()
    out = _run(
        q.semantic_search("character", "查询", emb, top_k=4, threshold=0.55)
    )
    assert [e.title for _s, e in out] == ["高条"]


def test_semantic_all_below_returns_empty():
    q = _querier([LoreEntry(title="低条", content="低分内容")])
    emb = _MapEmbedder()
    out = _run(
        q.semantic_search("character", "查询", emb, top_k=4, threshold=0.55)
    )
    assert out == []


def test_semantic_zero_threshold_keeps_all():
    q = _querier([LoreEntry(title="低条", content="低分内容")])
    emb = _MapEmbedder()
    out = _run(
        q.semantic_search("character", "查询", emb, top_k=4, threshold=0.0)
    )
    assert len(out) == 1


def test_vector_cache_reused_until_table_changes():
    entries = [LoreEntry(title="高条", content="高分内容")]
    char = Character(name="初", table=entries)
    q = LoreQuerier(char, None)
    emb = _MapEmbedder()

    async def main():
        await q.semantic_search("character", "查询", emb, 4, 0.0)
        await q.semantic_search("character", "查询", emb, 4, 0.0)
        # 首次：query embed + table embed = 2；第二次 query 再 embed（+1），table 命中缓存
        assert emb.calls == 3
        # 表内容变化 → query 与 table 均重新 embed（+2）
        entries.append(LoreEntry(title="新增", content="高分追加"))
        await q.semantic_search("character", "查询", emb, 4, 0.0)
        return emb.calls

    assert _run(main()) == 5


def test_semantic_error_returns_none():
    class _Boom(_MapEmbedder):
        async def embed(self, texts):
            raise RuntimeError("服务挂了")

    q = _querier([LoreEntry(title="甲", content="x")])
    out = _run(
        q.semantic_search("character", "查询", _Boom(), 4, 0.0)
    )
    assert out is None


def test_tool_falls_back_to_keyword_when_all_below_threshold():
    q = _querier([LoreEntry(title="低条", content="低分内容")])
    emb = _MapEmbedder()
    ctx = ToolContext(services={"lore": q, "embedder": emb})
    # 语义全不达标 → 回退关键词；标题含「低条」
    res = _run(
        QueryLoreTool().run({"kind": "character", "keyword": "低条"}, ctx)
    )
    assert res.ok is True and "低条" in res.output


# ---- 构建器新块 ----

def test_mes_example_injected_with_placeholders_and_notice():
    char = Character(name="初", mes_example="{{user}}：你好\n<START>{{char}}：嘿，你好呀")
    prompt = build_system_prompt(char, None, "BASE")
    assert "【对话范例】" in prompt
    assert "未真实发生" in prompt
    assert "初：嘿，你好呀" in prompt
    assert "用户：你好" in prompt
    assert "{{char}}" not in prompt and "{{user}}" not in prompt


def test_mes_example_budget_drops_later_rounds():
    raw = "<START>".join(["第" + str(i) + "轮内容比较长哦" for i in range(4)])
    char = Character(name="初", mes_example=raw)
    rp = RoleplaySettings(mes_example_chars=20)
    prompt = build_system_prompt(char, None, "BASE", roleplay=rp)
    assert "0轮内容比较长哦" in prompt
    assert "3轮内容" not in prompt


def test_pinned_injected_on_both_paths():
    entry = LoreEntry(title="关键", content="必须知道", pinned=True)
    char = Character(name="初", table=[entry])
    p1 = build_system_prompt(char, None, "BASE")
    p2 = build_member_system_prompt("初", "", None, ["别人"], character=char, base="BASE")
    assert "【角色常驻资料】" in p1 and "必须知道" in p1
    assert "【角色常驻资料】" in p2 and "必须知道" in p2


def test_world_pinned_and_guidance_in_member_path():
    world = World(content="世界", table=[LoreEntry(title="规则", content="常驻规则", pinned=True)])
    prompt = build_member_system_prompt(
        "初", "", None, ["别人"], character=Character(name="初"), world=world, base="BASE"
    )
    assert "【世界常驻设定】" in prompt
    assert "query_lore(kind='world'" in prompt     # 成员路径恢复的引导


def test_member_requirements_and_attribute_blocks_restored():
    char = Character(
        name="初",
        attributes=[{"key": "favor", "label": "好感度", "initial": 0, "min": 0, "max": 100}],
    )
    prompt = build_member_system_prompt(
        "初", "", None, ["别人"], character=char, base="BASE", roleplay=RoleplaySettings()
    )
    assert "【对白格式要求】" in prompt
    assert "update_attributes" in prompt


def test_intro_truncation_via_budget():
    char = Character(name="初", intro="这是一段没有标点的超长简介内容啊啊啊啊啊")
    rp = RoleplaySettings(intro_chars=8)
    prompt = build_system_prompt(char, None, "BASE", roleplay=rp)
    # 简介块总长度受限（含省略标记）
    block = [p for p in prompt.split("\n\n") if "角色简介" in p][0]
    assert len(block) <= len("【角色简介】\n") + 8


def test_member_only_blocks_via_options():
    char = Character(name="初")
    prompt = build_prompt(char, None, PromptOptions(base="BASE", audience=["甲"], memory="一段记忆"))
    assert "【在场】" in prompt and "【回应要求】" in prompt
    assert "一段记忆" in prompt

    prompt2 = build_prompt(char, None, PromptOptions(base="BASE"))
    assert "【在场】" not in prompt2 and "记忆】" not in prompt2


def test_wrappers_equal_explicit_build():
    char = Character(name="初", intro="简介")
    world = World(content="世界")
    assert build_system_prompt(char, world, "BASE") == build_prompt(
        char, world, PromptOptions(base="BASE")
    )
    assert build_member_system_prompt(
        "初", "记忆", None, ["甲"], character=char, world=world, base="BASE"
    ) == build_prompt(
        char, world,
        PromptOptions(base="BASE", audience=["甲"], memory="记忆", cross_memory_hint=True),
    )
