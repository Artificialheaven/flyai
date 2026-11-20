"""酒馆卡保真导入（P1）：regex_scripts / extensions / 备选开场白 / assets。

只验证字段原样保留与导入报告，不执行任何卡内 JavaScript。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_chat import _lore_depth_specs
from flyai.lore.card_import import character_from_cc, import_report
from flyai.lore.models import Character


SCRIPT_STATIC = {
    "scriptName": "状态面板",
    "findRegex": "/<status>(.*?)<\\/status>/gs",
    "replaceString": "<details>$1</details>",
    "placement": [2],
    "markdownOnly": True,
    "trimStrings": [],
}
SCRIPT_BROKEN = {
    "scriptName": "坏正则",
    "findRegex": "/(unclosed/",
    "replaceString": "x",
    "placement": [2],
}


def _card(**data_over):
    data = {"name": "糯米天师", "description": "测试角色"}
    data.update(data_over)
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}


def test_import_keeps_st_fields():
    card = _card(
        extensions={
            "depth_prompt": {"prompt": "状态：{{char}}", "depth": 2, "role": 2},
            "world": "和睦小镇",
            "talkativeness": "0.6",
            "fav": True,
            "tavern_helper": {"scripts": [{"id": "x", "content": "alert(1)"}]},
            "regex_scripts": [SCRIPT_STATIC, SCRIPT_BROKEN],
        },
        alternate_greetings=["开场 A", "", "开场 B"],
        assets=[
            {"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
            {"type": "emotion", "uri": "embeded://a.png", "name": "happy"},
            "not-a-dict",
        ],
    )
    char = character_from_cc(card)

    assert char.regex_scripts == [SCRIPT_STATIC, SCRIPT_BROKEN]
    assert char.extensions["depth_prompt"]["prompt"] == "状态：{{char}}"
    assert char.extensions["tavern_helper"]["scripts"][0]["content"] == "alert(1)"
    assert char.alternate_greetings == ["开场 A", "开场 B"]
    assert len(char.assets) == 2
    assert char.assets[1]["uri"] == "embeded://a.png"

    # 与源卡彻底解耦：改源卡不影响角色，反之亦然
    card["data"]["extensions"]["regex_scripts"][0]["replaceString"] = "改了"
    assert char.regex_scripts[0]["replaceString"] == "<details>$1</details>"


def test_import_accepts_top_level_scripts_and_depth_prompt():
    card = _card(
        regex_scripts=[SCRIPT_STATIC],
        depth_prompt={"prompt": "顶层写法", "depth": 4, "role": "system"},
    )
    char = character_from_cc(card)
    assert char.regex_scripts == [SCRIPT_STATIC]
    assert char.extensions["depth_prompt"]["prompt"] == "顶层写法"


def test_extensions_regex_scripts_takes_precedence():
    card = _card(
        extensions={"regex_scripts": [SCRIPT_STATIC]},
        regex_scripts=[SCRIPT_BROKEN],
    )
    assert character_from_cc(card).regex_scripts == [SCRIPT_STATIC]


def test_import_tolerates_missing_and_wrong_types():
    card = _card(
        extensions="not-a-dict",
        regex_scripts={"oops": 1},
        alternate_greetings="nope",
        assets="nope",
    )
    char = character_from_cc(card)
    assert char.regex_scripts == []
    assert char.extensions == {}
    assert char.alternate_greetings == []
    assert char.assets == []


def test_to_dict_roundtrip_and_legacy_defaults():
    card = _card(
        extensions={"world": "和睦小镇", "regex_scripts": [SCRIPT_STATIC]},
        alternate_greetings=["开场 A"],
        assets=[{"type": "icon", "uri": "ccdefault:"}],
    )
    char = character_from_cc(card)
    back = Character.from_dict(char.to_dict())
    assert back.regex_scripts == char.regex_scripts
    assert back.extensions == char.extensions
    assert back.alternate_greetings == char.alternate_greetings
    assert back.assets == char.assets

    # 旧数据没有新字段：反序列化为空值，不报错
    legacy = Character.from_dict({"id": "a", "name": "旧卡"})
    assert legacy.regex_scripts == []
    assert legacy.extensions == {}
    assert legacy.alternate_greetings == []
    assert legacy.assets == []


def test_import_report_stats_and_warnings():
    card = _card(
        extensions={
            "depth_prompt": {"prompt": "状态：{{char}}", "depth": 2},
            "world": "和睦小镇",
            "tavern_helper": {"scripts": []},
            "regex_scripts": [SCRIPT_STATIC, SCRIPT_BROKEN],
        },
        alternate_greetings=["开场 A"],
        assets=[{"type": "icon", "uri": "ccdefault:"}],
    )
    report = import_report(character_from_cc(card))

    assert report["stats"] == {
        "table": 0,
        "regex_scripts": 2,
        "alternate_greetings": 1,
        "assets": 1,
        "depth_prompt": True,
    }
    text = "\n".join(report["warnings"])
    assert "不会执行任何卡内 JavaScript" in text
    assert "其中 1 条正则无效" in text
    assert "和睦小镇" in text
    assert "assets" in text
    assert "备选开场白" not in text


def test_import_report_quiet_for_plain_card():
    report = import_report(character_from_cc(_card()))
    assert report["stats"]["regex_scripts"] == 0
    assert report["stats"]["depth_prompt"] is False
    assert report["warnings"] == []


def test_depth_prompt_injected_as_depth_spec():
    char = character_from_cc(
        _card(extensions={"depth_prompt": {"prompt": "你是{{char}}，记住规则。", "depth": 3, "role": 2}})
    )
    specs = _lore_depth_specs(None, char, None, "")
    assert len(specs) == 1
    spec = specs[0]
    assert spec["title"] == "depth_prompt"
    assert spec["content"] == "你是糯米天师，记住规则。"
    assert spec["depth"] == 3
    assert spec["role"] == "assistant"


def test_depth_prompt_defaults_and_bad_values():
    char = character_from_cc(
        _card(extensions={"depth_prompt": {"prompt": "规则", "depth": "abc", "role": 9}})
    )
    spec = _lore_depth_specs(None, char, None, "")[0]
    assert spec["depth"] == 4
    assert spec["role"] == "system"

    # 空 prompt 不注入
    empty = character_from_cc(_card(extensions={"depth_prompt": {"prompt": "  "}}))
    assert _lore_depth_specs(None, empty, None, "") == []
