"""酒馆助手作者脚本：抽取、限额与下发开关。"""

from __future__ import annotations

from types import SimpleNamespace

from flyai.api.routes_session import _st_payload
from flyai.lore.models import Character
from flyai.st.author_scripts import (
    MAX_SCRIPT_CHARS,
    MAX_SCRIPTS,
    collect_author_scripts,
    has_author_scripts,
)
from flyai.user import UserSettings


def _card(*scripts) -> Character:
    return Character(
        name="御主地界",
        intro="[[YZDJ-RULES v1]]",
        extensions={"tavern_helper": {"scripts": list(scripts)}},
    )


def test_collects_enabled_scripts_and_keeps_minimal_fields():
    card = _card(
        {"id": "yzdj", "name": "面板", "content": "console.log(1)", "enabled": True},
        {"id": "off", "name": "禁用", "content": "x", "enabled": False},
        {"id": "blank", "name": "空", "content": "   "},
        "不是字典",
    )
    result = collect_author_scripts(card)
    assert [s["id"] for s in result["scripts"]] == ["yzdj"]
    assert result["scripts"][0]["name"] == "面板"
    assert result["truncated"] is False
    assert has_author_scripts(card) is True


def test_flattens_folder_tree():
    card = _card(
        {
            "type": "folder",
            "name": "目录",
            "scripts": [
                {"id": "a", "name": "A", "content": "a"},
                {"id": "b", "name": "B", "content": "b", "enabled": False},
                {"id": "c", "name": "C", "content": "c"},
            ],
        }
    )
    assert [s["id"] for s in collect_author_scripts(card)["scripts"]] == ["a", "c"]


def test_ignores_missing_or_bad_extension():
    assert collect_author_scripts(None)["scripts"] == []
    assert collect_author_scripts(Character(name="空卡"))["scripts"] == []
    bad = Character(extensions={"tavern_helper": {"scripts": "nope"}})
    assert collect_author_scripts(bad)["scripts"] == []
    legacy = Character(extensions={"TavernHelper": {"scripts": [
        {"id": "x", "name": "X", "content": "x"}
    ]}})
    assert [s["id"] for s in collect_author_scripts(legacy)["scripts"]] == ["x"]


def test_limits_script_count_and_size():
    many = _card(*[
        {"id": "s%d" % i, "name": "S", "content": "x"} for i in range(MAX_SCRIPTS + 5)
    ])
    result = collect_author_scripts(many)
    assert len(result["scripts"]) == MAX_SCRIPTS
    assert result["truncated"] is True

    huge = _card({"id": "huge", "name": "大", "content": "x" * 101})
    result = collect_author_scripts(huge, max_script_chars=100, max_total_chars=100)
    assert result["scripts"] == []
    assert result["truncated"] is True
    # 默认上限足够容纳整页面板脚本（约 4.3MB）
    assert MAX_SCRIPT_CHARS == 5 * 1024 * 1024


def _session():
    return SimpleNamespace(members=[], character_id="", mvu={})


def test_payload_defaults_by_role_and_override():
    card = _card({"id": "s", "name": "S", "content": "x"})
    admin = _st_payload(_session(), card, None, UserSettings(), cfg=None, is_admin=True)
    assert admin["flags"]["author_scripts"] is True
    assert admin["author"]["enabled"] is True
    assert admin["author"]["scripts"][0]["content"] == "x"
    assert admin["author"]["character"]["description"] == "[[YZDJ-RULES v1]]"

    user = _st_payload(_session(), card, None, UserSettings(), cfg=None, is_admin=False)
    assert user["flags"]["author_scripts"] is False
    assert user["author"]["scripts"] == []

    opted_in = _st_payload(
        _session(), card, None, UserSettings(st_author_scripts_enabled=True),
        cfg=None, is_admin=False,
    )
    assert opted_in["flags"]["author_scripts"] is True
    assert opted_in["author"]["scripts"][0]["id"] == "s"


def test_author_scripts_mark_panels():
    token = "<StatusPlaceHolderImpl/>"
    card = _card({"id": "s", "name": "S", "content": "x"})
    card.first_mes = "开场\n" + token
    card.regex_scripts = [{
        "scriptName": "状态栏",
        "findRegex": token,
        "replaceString": '<div data-yzdj>脚本未启动</div>',
        "placement": [2],
    }]
    payload = _st_payload(
        _session(), card, None, UserSettings(st_author_scripts_enabled=True),
        cfg=None, is_admin=False,
    )
    assert payload["panels"][0]["author"] is True

    off = _st_payload(_session(), card, None, UserSettings(), cfg=None, is_admin=False)
    assert "author" not in off["panels"][0]


def test_override_survives_roundtrip():
    settings = UserSettings(st_author_scripts_enabled=True)
    restored = UserSettings.from_dict(settings.to_dict())
    assert restored.st_author_scripts_enabled is True
