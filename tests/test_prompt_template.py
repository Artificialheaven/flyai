"""酒馆提示词模板（ST-Prompt-Template / EJS）降级处理。

FlyAI 不执行卡内 JS：EJS 标记必须从注入给模型的文本里移除，
只保留模板外的静态文本；简单的 getvar 输出尽量展开。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.models import Character, LoreEntry
from flyai.lore.query import build_system_prompt
from flyai.lore.trigger import render_trigger_block
from flyai.tavern import render_tavern_macros, strip_prompt_template


EJS = (
    "【静态规则】\n"
    "1. 安全期：受孕几率 0%。\n"
    "<%_\n"
    "var borrowers = getvar('stat_data.借款人列表', { defaults: {} });\n"
    "_%>\n"
    "<%_ for (const [name, data] of Object.entries(borrowers)) { _%>\n"
    "【<%= String(name) %>】当前数值：<%= getvar('stat_data.hp') %>\n"
    "<%_ } _%>\n"
)


def test_plain_text_untouched():
    text = "普通文本 {{user}} 没有模板"
    assert strip_prompt_template(text) == text


def test_scriptlets_removed_and_static_text_kept():
    out = strip_prompt_template("规则A\n<%_ var x = 1; _%>\n规则B")
    assert "<%" not in out
    assert "规则A" in out and "规则B" in out


def test_getvar_output_expanded_when_resolvable():
    variables = {"stat_data": {"hp": 7, "名字": "苏晚晴"}}
    assert strip_prompt_template("<%= getvar('stat_data.hp') %>", variables) == "7"
    assert (
        strip_prompt_template("<%= getvar('stat_data.名字', { defaults: '' }) %>",
                              variables)
        == "苏晚晴"
    )
    # 解析不到时展开为空串，而不是留下模板代码
    assert strip_prompt_template("<%= getvar('stat_data.缺失') %>", variables) == ""


def test_unknown_ejs_expression_removed():
    out = strip_prompt_template("前文<%= loopVariable %>后文")
    assert out == "前文后文"


def test_render_tavern_macros_strips_ejs():
    out = render_tavern_macros(
        "{{user}}：<%= String(name) %>\n<%_ if (x) { _%>正文<%_ } _%>",
        user_name="玩家",
        variables={"stat_data": {}},
    )
    assert "<%" not in out
    assert "玩家" in out
    assert "正文" in out


def test_pinned_entry_strips_ejs_with_and_without_mvu():
    c = Character(
        name="测试卡",
        table=[LoreEntry(title="EJS世界书条目", content=EJS, pinned=True)],
    )
    with_mvu = build_system_prompt(
        c, None, "base", variables={"stat_data": {"hp": 7}}
    )
    assert "<%" not in with_mvu
    assert "【静态规则】" in with_mvu
    assert "当前数值：7" in with_mvu

    without_mvu = build_system_prompt(c, None, "base")
    assert "<%" not in without_mvu
    assert "【静态规则】" in without_mvu


def test_trigger_block_strips_ejs():
    block = render_trigger_block(
        [("world", LoreEntry(title="设定", content="前文<%_ x _%>后文"))]
    )
    assert "<%" not in block
    assert "前文" in block and "后文" in block
