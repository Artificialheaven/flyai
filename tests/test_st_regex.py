"""酒馆 regex_scripts 原生引擎（prompt 侧）单元测试。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.schemas import Message
from flyai.st.regex_scripts import (
    MAX_SCRIPTS,
    apply_scripts,
    apply_to_messages,
    compile_scripts,
    invalid_script_count,
)


def _run(text, raw, role="assistant", side="prompt"):
    scripts, warnings = compile_scripts(raw, side=side)
    return apply_scripts(text, role, scripts, side=side), scripts, warnings


# ---- 基本替换 / 字面量 ----

def test_literal_flags_and_global_replace():
    out, _, _ = _run(
        "foo FOO foo",
        [{"findRegex": "/foo/gi", "replaceString": "bar"}],
    )
    assert out == "bar bar bar"


def test_bare_pattern_is_regex():
    out, _, _ = _run(
        "好感度: 42",
        [{"findRegex": r"好感度:\s*(\d+)", "replaceString": "好感 $1"}],
    )
    assert out == "好感 42"


def test_invalid_regex_skipped_not_fatal():
    raw = [
        {"findRegex": "/[unclosed/g", "replaceString": "x"},
        {"findRegex": "/ok/g", "replaceString": "fine"},
    ]
    out, scripts, warnings = _run("ok ok", raw)
    assert out == "fine fine"
    assert len(scripts) == 1
    assert any("无效" in w for w in warnings)
    assert invalid_script_count(raw) == 1


# ---- 替换串语义 ----

def test_dollar_groups_and_named_group():
    raw = [{
        "findRegex": r"/(?<name>\w+)=(?<value>\d+)/g",
        "replaceString": "$<name> → $2；整段=$&；美元=$$",
    }]
    out, _, _ = _run("hp=10", raw)
    assert out == "hp → 10；整段=hp=10；美元=$"


def test_update_variable_beautify():
    # 卡内脚本 3/4 的简化版：把原始更新块包进 <details>
    raw = [{
        "findRegex": r"/<(UpdateVariable)>([\s\S]*?)<\/UpdateVariable>/g",
        "replaceString": "<details><summary>$1</summary>$2</details>",
    }]
    text = "正文\n<UpdateVariable><JSONPatch>[]</JSONPatch></UpdateVariable>\n尾巴"
    out, _, _ = _run(text, raw)
    assert out == (
        "正文\n<details><summary>UpdateVariable</summary>"
        "<JSONPatch>[]</JSONPatch></details>\n尾巴"
    )


def test_backtick_and_apostrophe():
    out, _, _ = _run(
        "abc",
        [{"findRegex": "/b/", "replaceString": "$`[$&]$'"}],
    )
    assert out == "aa[b]cc"


def test_trim_strings():
    out, _, _ = _run(
        "x开",
        [{
            "findRegex": r"/x(开)/g",
            "replaceString": "$1",
            "trimStrings": ["开"],
        }],
    )
    assert out == ""


# ---- 过滤：placement / side / disabled / depth ----

def test_placement_user_vs_ai():
    raw = [{
        "findRegex": "/secret/g",
        "replaceString": "***",
        "placement": [1],
    }]
    scripts, _ = compile_scripts(raw)
    assert apply_scripts("secret", "user", scripts) == "***"
    assert apply_scripts("secret", "assistant", scripts) == "secret"


def test_default_placement_is_ai():
    scripts, _ = compile_scripts([{"findRegex": "/a/g", "replaceString": "b"}])
    assert apply_scripts("a", "user", scripts) == "a"
    assert apply_scripts("a", "assistant", scripts) == "b"


def test_markdown_only_and_prompt_only():
    raw = [
        {"findRegex": "/m/g", "replaceString": "M", "markdownOnly": True},
        {"findRegex": "/p/g", "replaceString": "P", "promptOnly": True},
    ]
    prompt_scripts, _ = compile_scripts(raw, side="prompt")
    display_scripts, _ = compile_scripts(raw, side="display")
    assert apply_scripts("m p", "assistant", prompt_scripts, side="prompt") == "m P"
    assert apply_scripts("m p", "assistant", display_scripts, side="display") == "M p"


def test_disabled_skipped():
    scripts, _ = compile_scripts([
        {"findRegex": "/a/g", "replaceString": "b", "disabled": True},
    ])
    assert scripts == []


def test_depth_limits():
    raw = [{
        "findRegex": "/x/g",
        "replaceString": "y",
        "minDepth": 2,
        "maxDepth": 4,
    }]
    scripts, _ = compile_scripts(raw)
    assert apply_scripts("x", "assistant", scripts, depth=1) == "x"
    assert apply_scripts("x", "assistant", scripts, depth=3) == "y"
    assert apply_scripts("x", "assistant", scripts, depth=5) == "x"


# ---- 消息列表：按角色卡查表且不原地修改 ----

def test_apply_to_messages_copies_and_selects_by_character():
    raw_a = [{"findRegex": "/甲/g", "replaceString": "A"}]
    raw_b = [{"findRegex": "/乙/g", "replaceString": "B"}]
    scripts_a, _ = compile_scripts(raw_a)
    scripts_b, _ = compile_scripts(raw_b)
    first = Message(role="assistant", content="甲", character_id="a")
    second = Message(role="assistant", content="乙", character_id="b")
    out = apply_to_messages(
        [first, second],
        by_character={"a": scripts_a, "b": scripts_b},
    )
    assert [m.content for m in out] == ["A", "B"]
    # 原消息（可能被 _actor_view 按引用持有）绝不能改
    assert first.content == "甲" and second.content == "乙"


def test_apply_to_messages_fallback_for_unmapped():
    scripts, _ = compile_scripts([{"findRegex": "/旧/g", "replaceString": "新"}])
    msg = Message(role="assistant", content="旧内容")
    out = apply_to_messages([msg], fallback=scripts)
    assert out[0].content == "新内容"
    assert msg.content == "旧内容"


def test_apply_to_messages_no_scripts_returns_same_objects():
    msg = Message(role="assistant", content="保持")
    out = apply_to_messages([msg])
    assert out[0] is msg


# ---- 上限 ----

def test_script_count_limit():
    raw = [{"findRegex": "/a/g", "replaceString": "b"}] * (MAX_SCRIPTS + 5)
    scripts, warnings = compile_scripts(raw)
    assert len(scripts) == MAX_SCRIPTS
    assert any("超过" in w for w in warnings)
