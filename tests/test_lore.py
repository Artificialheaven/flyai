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
    from flyai.lore.query import GENERAL_ASSISTANT_PROMPT

    assert (
        build_system_prompt(None, None, "你是一个助手。")
        == GENERAL_ASSISTANT_PROMPT + "\n\n你是一个助手。"
    )


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


# ---- 触发词 keys：序列化与检索打分 ----

def test_lore_entry_keys_roundtrip():
    e = LoreEntry("铁剑", "寒铁铸造", keys=["铁剑", "剑术"])
    d = e.to_dict()
    assert d["keys"] == ["铁剑", "剑术"]
    assert LoreEntry.from_dict(d).keys == ["铁剑", "剑术"]


def test_lore_entry_keys_legacy_compat():
    # 旧数据缺 keys → 空列表；字符串/带空白元素归一化
    assert LoreEntry.from_dict({"title": "旧条目", "content": "x"}).keys == []
    assert LoreEntry.from_dict(
        {"title": "t", "content": "x", "keys": "铁剑,剑术"}
    ).keys == ["铁剑,剑术"]  # 字符串整体保留为单个触发词（前端导入负责拆分）
    assert LoreEntry.from_dict(
        {"title": "t", "content": "x", "keys": [" 铁剑 ", "", "剑术"]}
    ).keys == ["铁剑", "剑术"]


def test_search_matches_and_ranks_by_keys():
    # 标题与正文都不含「铁剑」，只有 keys 命中 → 仍可被 AI 主动查询检出
    hit = LoreEntry("身世线索", "与王室有关的旧物", keys=["铁剑"])
    body_hit = LoreEntry("另一标题", "一把普通的铁剑罢了")
    miss = LoreEntry("魔法", "火球术", keys=["魔术"])
    q = LoreQuerier(Character(name="艾拉", table=[miss, hit, body_hit]), World(name="艾尔"))
    rows = q.search("character", "铁剑")
    assert hit in rows
    # keys 精确（+50）应排在正文命中（+1）之前
    assert rows[0] is hit
    assert miss not in rows


# ---- PHI / 开场白字段 ----

def test_character_phi_and_first_mes_roundtrip():
    c = Character(
        id="c5", name="鲁迪",
        post_history_instructions="保持角色语气。",
        first_mes="{{char}}睁开眼：「{{user}}，你来了。」",
    )
    d = c.to_dict()
    assert d["post_history_instructions"] == "保持角色语气。"
    assert "{{char}}" in d["first_mes"]
    c2 = Character.from_dict(d)
    assert c2.post_history_instructions == "保持角色语气。"
    assert c2.first_mes == "{{char}}睁开眼：「{{user}}，你来了。」"


def test_character_phi_and_first_mes_legacy_defaults():
    # 旧卡数据缺这两个键：缺省为空串，不报错
    d = Character(id="c6", name="旧卡").to_dict()
    d.pop("post_history_instructions")
    d.pop("first_mes")
    c = Character.from_dict(d)
    assert c.post_history_instructions == ""
    assert c.first_mes == ""
    # None / 非字符串也归一化为空串
    c2 = Character.from_dict({"id": "c7", "name": "怪卡",
                              "post_history_instructions": None,
                              "first_mes": None})
    assert c2.post_history_instructions == "" and c2.first_mes == ""


def test_build_character_carries_phi_and_first_mes():
    from flyai.api.routes_lore import CharacterPayload, _build_character

    c = _build_character(CharacterPayload(
        id="c8", name="小梅",
        post_history_instructions=" 用括号写心理活动 ",
        first_mes="你好呀",
    ))
    assert c.post_history_instructions == " 用括号写心理活动 "
    assert c.first_mes == "你好呀"

    c2 = _build_character(CharacterPayload(id="c9", name="阿明"))
    assert c2.post_history_instructions == "" and c2.first_mes == ""


# ---- 保存保真字段（regex_scripts/extensions/alternate_greetings/assets） ----

def _fidelity_character():
    return Character(
        id="c10", name="保真",
        regex_scripts=[{"findRegex": "a", "replaceString": "b"}],
        extensions={"depth_prompt": {"prompt": "深度"}},
        alternate_greetings=["开场 A", "开场 B"],
        assets=[{"type": "icon", "uri": "ccdefault:"}],
    )


def test_build_character_carries_fidelity_fields():
    from flyai.api.routes_lore import CharacterPayload, _build_character

    c = _build_character(CharacterPayload(
        id="c11", name="新卡",
        regex_scripts=[{"findRegex": "x", "replaceString": "y"}],
        extensions={"world": "和睦小镇"},
        alternate_greetings=["开场 1", "  ", "开场 2"],
        assets=[{"type": "bg", "uri": "ccdefault:"}],
    ))
    assert c.regex_scripts == [{"findRegex": "x", "replaceString": "y"}]
    assert c.extensions == {"world": "和睦小镇"}
    # 空条目被过滤
    assert c.alternate_greetings == ["开场 1", "开场 2"]
    assert c.assets == [{"type": "bg", "uri": "ccdefault:"}]


def test_build_character_missing_fidelity_fields_inherits_base():
    from flyai.api.routes_lore import CharacterPayload, _build_character

    base = _fidelity_character()
    c = _build_character(CharacterPayload(id="c10", name="改名"), base=base)
    assert c.name == "改名"
    assert c.regex_scripts == base.regex_scripts
    assert c.extensions == base.extensions
    assert c.alternate_greetings == base.alternate_greetings
    assert c.assets == base.assets
    # 深拷贝：改新卡不影响 base
    c.extensions["depth_prompt"]["prompt"] = "改了"
    assert base.extensions["depth_prompt"]["prompt"] == "深度"


def test_build_character_explicit_empty_clears_fidelity_fields():
    from flyai.api.routes_lore import CharacterPayload, _build_character

    c = _build_character(
        CharacterPayload(
            id="c10", name="清空",
            regex_scripts=[], extensions={}, alternate_greetings=[], assets=[],
        ),
        base=_fidelity_character(),
    )
    assert c.regex_scripts == []
    assert c.extensions == {}
    assert c.alternate_greetings == []
    assert c.assets == []


# ---- 开场白/范例宏替换 ----

def test_substitute_chat_macros():
    from flyai.lore.query import substitute_chat_macros

    out = substitute_chat_macros(
        "{{char}}看着{{ user }}，而 {{CHAR}} 没说话", "鲁迪", "你")
    assert out == "鲁迪看着你，而 鲁迪 没说话"
    # 空名回退默认；None/空文本不炸
    assert substitute_chat_macros("{{char}}/{{user}}", "", "") == "角色/用户"
    assert substitute_chat_macros(None, "鲁迪") == ""


def test_parse_mes_example_substitutes_macros():
    from flyai.lore.query import parse_mes_example

    raw = "{{char}}：你好<START>{{user}}：嗨\n{{char}}：再见"
    rounds = parse_mes_example(raw, "鲁迪", "你")
    assert len(rounds) == 2
    assert rounds[0] == "鲁迪：你好"
    assert rounds[1] == "你：嗨\n鲁迪：再见"
    assert parse_mes_example("", "鲁迪") == []
    assert parse_mes_example("<START>  <start>", "鲁迪") == []