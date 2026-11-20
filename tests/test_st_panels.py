"""酒馆卡状态面板识别：字面量占位符 → HTML 展示脚本。"""

from __future__ import annotations

from flyai.lore.models import Character
from flyai.st.panels import detect_panels

TOKEN = "<StatusPlaceHolderImpl/>"


def _card(scripts, first_mes="你好\n" + TOKEN + "\n", greetings=None) -> Character:
    return Character(
        name="测试",
        first_mes=first_mes,
        alternate_greetings=list(greetings or []),
        regex_scripts=list(scripts),
    )


def _panel_script(**overrides):
    script = {
        "scriptName": "状态栏界面",
        "findRegex": TOKEN,
        "replaceString": "<div class=\"panel\">{{format_message_variable::stat_data.时间}}</div>",
        "placement": [2],
        "markdownOnly": True,
    }
    script.update(overrides)
    return script


def test_detect_literal_panel_token():
    panels = detect_panels(_card([_panel_script()]))
    assert panels == [{"token": TOKEN, "title": "状态栏界面"}]


def test_title_falls_back_to_token():
    script = _panel_script()
    script.pop("scriptName")
    panels = detect_panels(_card([script]))
    assert panels[0]["title"] == TOKEN


def test_skip_prompt_only_disabled_and_empty_replacement():
    scripts = [
        _panel_script(promptOnly=True),
        _panel_script(disabled=True),
        _panel_script(replaceString="   "),
    ]
    assert detect_panels(_card(scripts)) == []


def test_skip_regex_patterns():
    # /pattern/flags 字面量、裸正则、含元字符的 token 都不算占位符
    scripts = [
        _panel_script(findRegex=r"/<StatusPlaceHolderImpl\/>/g"),
        _panel_script(findRegex="<StatusPlaceHolder.*?>"),
        _panel_script(findRegex="[状态栏]"),
        _panel_script(findRegex=""),
    ]
    assert detect_panels(_card(scripts)) == []


def test_skip_token_not_in_greeting():
    script = _panel_script(findRegex="<OtherToken/>")
    assert detect_panels(_card([script])) == []


def test_token_in_alternate_greeting():
    script = _panel_script(findRegex="<AltToken/>")
    card = _card([script], first_mes="普通开场白", greetings=["换装\n<AltToken/>"])
    assert [p["token"] for p in detect_panels(card)] == ["<AltToken/>"]


def test_dedupe_and_limit():
    dup = [_panel_script(), _panel_script(scriptName="第二个")]
    assert len(detect_panels(_card(dup))) == 1

    many = []
    tokens = []
    for i in range(12):
        token = "<Token%d/>" % i
        tokens.append(token)
        many.append(_panel_script(findRegex=token, scriptName="面板%d" % i))
    panels = detect_panels(_card(many, first_mes="\n".join(tokens)))
    assert len(panels) == 8


def test_tolerates_bad_entries_and_none_card():
    assert detect_panels(None) == []
    assert detect_panels(_card(["不是字典", None, 42])) == []
    assert detect_panels(Character(name="空卡")) == []
