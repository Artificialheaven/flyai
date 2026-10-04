"""角色/世界观内容表查询工具与提示词构建单测。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.models import Character, LoreEntry, World
from flyai.lore.query import LoreQuerier, build_system_prompt
from flyai.tools.base import ToolContext
from flyai.tools.lore_tool import QueryLoreTool


def _ctx():
    c = Character(
        name="爱丽丝",
        table=[LoreEntry("喜欢的食物", "苹果派"), LoreEntry("武器", "短剑")],
    )
    w = World(name="艾尔大陆", table=[LoreEntry("王国", "东部亚瑟王国")])
    querier = LoreQuerier(c, w)
    return ToolContext(allowed_read_dirs=[], services={"lore": querier})


@pytest.mark.asyncio
async def test_query_lore_by_keyword():
    tool = QueryLoreTool()
    r = await tool.run({"kind": "character", "keyword": "食物"}, _ctx())
    assert r.ok and "苹果派" in r.output and "喜欢的食物" in r.output


@pytest.mark.asyncio
async def test_query_lore_list_titles():
    tool = QueryLoreTool()
    r = await tool.run({"kind": "character", "keyword": ""}, _ctx())
    assert r.ok and "喜欢的食物" in r.output and "武器" in r.output


@pytest.mark.asyncio
async def test_query_lore_no_match():
    tool = QueryLoreTool()
    r = await tool.run({"kind": "world", "keyword": "不存在的标题"}, _ctx())
    assert r.ok is False and "王国" in r.output  # 提示可用标题


@pytest.mark.asyncio
async def test_query_lore_missing_service():
    tool = QueryLoreTool()
    r = await tool.run({"kind": "character", "keyword": "x"}, ToolContext())
    assert r.ok is False


class _FakeLoreEmbedder:
    """确定性向量：含「苹果」的特征1、含「武器」的特征2，偏置保证非零。"""
    def __init__(self, top_k=1, fail=False):
        from types import SimpleNamespace
        self.cfg = SimpleNamespace(top_k=top_k)
        self.fail = fail

    async def embed(self, texts):
        if self.fail:
            raise RuntimeError("嵌入服务崩溃")
        rows = []
        for t in texts:
            row = [0.0, 0.0, 1.0]
            if "苹果" in t:
                row[0] += 1
            if "武器" in t:
                row[1] += 1
            rows.append(row)
        return rows


@pytest.mark.asyncio
async def test_query_lore_rag_ranks_by_content():
    # 关键词「苹果」不在任何标题里，只有内容里出现 → 只有语义检索才能命中
    c = Character(name="爱丽丝", table=[LoreEntry("武器", "短剑"), LoreEntry("喜欢的食物", "苹果派")])
    w = World(name="艾尔", table=[])
    querier = LoreQuerier(c, w)
    ctx = ToolContext(services={"lore": querier, "embedder": _FakeLoreEmbedder(top_k=1)})
    r = await QueryLoreTool().run({"kind": "character", "keyword": "苹果"}, ctx)
    assert r.ok
    assert "苹果派" in r.output and "短剑" not in r.output


@pytest.mark.asyncio
async def test_query_lore_rag_falls_back_to_title_keyword_on_error():
    c = Character(name="爱丽丝", table=[LoreEntry("武器", "短剑"), LoreEntry("喜欢的食物", "苹果派")])
    w = World(name="艾尔", table=[])
    querier = LoreQuerier(c, w)
    ctx = ToolContext(services={"lore": querier, "embedder": _FakeLoreEmbedder(fail=True)})
    r = await QueryLoreTool().run({"kind": "character", "keyword": "武器"}, ctx)
    assert r.ok and "短剑" in r.output  # 嵌入失败 → 回退标题关键词


def test_build_system_prompt_with_character_and_world():
    c = Character(name="爱丽丝", intro="一个冒险家", background="来自东部")
    w = World(name="艾尔大陆", content="魔法与剑的世界")
    prompt = build_system_prompt(c, w, "你是一个助手。")
    assert "爱丽丝" in prompt
    assert "一个冒险家" in prompt
    assert "魔法与剑的世界" in prompt
    assert "query_lore" in prompt


def test_build_system_prompt_without_lore():
    assert build_system_prompt(None, None, "你是一个助手。") == "你是一个助手。"


def test_character_prompt_requirements_override_builtin():
    from flyai.config import RoleplaySettings

    c = Character(
        name="爱丽丝",
        intro="一个冒险家",
        prompt_format="本角色的格式要求 X",
        prompt_tools="本角色的工具要求 Y",
    )
    rp = RoleplaySettings(format_enabled=True, format_instruction="内置格式", tool_instruction="内置工具要求")
    prompt = build_system_prompt(c, None, "你是一个助手。", roleplay=rp)
    assert "本角色的格式要求 X" in prompt and "内置格式" not in prompt
    assert "本角色的工具要求 Y" in prompt and "内置工具要求" not in prompt


def test_world_prompt_tools_fallback_to_builtin():
    from flyai.config import RoleplaySettings

    w = World(name="艾尔大陆", content="魔法世界")
    rp = RoleplaySettings(format_enabled=True, tool_instruction="全局工具要求")
    prompt = build_system_prompt(None, w, "你是一个助手。", roleplay=rp)
    assert "全局工具要求" in prompt


def test_character_voice_roundtrip():
    c = Character(id="c1", name="小梅", voice="S_NkpD5org2")
    d = c.to_dict()
    assert d["voice"] == "S_NkpD5org2"
    assert Character.from_dict(d).voice == "S_NkpD5org2"


def test_character_voice_defaults_empty_and_legacy_compat():
    d = Character(id="c2", name="无音色").to_dict()
    assert d["voice"] == ""
    # 旧卡数据缺 voice 键也能正常读取
    d.pop("voice")
    assert Character.from_dict(d).voice == ""


def test_build_character_carries_voice():
    from flyai.api.routes_lore import CharacterPayload, _build_character

    c = _build_character(
        CharacterPayload(id="c3", name="小梅", voice=" S_X ")
    )
    assert c.voice == "S_X"

    c2 = _build_character(CharacterPayload(id="c4", name="阿明"))
    assert c2.voice == ""