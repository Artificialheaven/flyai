"""内容库列表摘要接口：列表返回轻量结构，详情接口按 id 取全量。

大体积酒馆卡（table/extensions 十几 MB）走列表会让内容库打开很慢，
/proc/characters?summary=1 只回展示与选开局所需字段。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_lore import (
    get_character,
    get_world,
    list_characters,
    list_worlds,
)
from flyai.lore.models import Character, LoreEntry, World
from flyai.lore.store import LoreStore


def _env(tmp_path):
    lore = LoreStore(tmp_path / "data" / "u_admin" / "lore")
    sc = SimpleNamespace(uid="admin", level=256, lore=lore)
    state = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return sc, request


def _card(cid="c1"):
    return Character(
        id=cid,
        name="初",
        intro="简介",
        author="admin",
        voice="voice-a",
        table=[
            LoreEntry(title="开局1", content="你醒在天台。"),
            LoreEntry(title="普通条目", content="不该进 openings"),
        ],
        extensions={"big": "x" * 100},
        alternate_greetings=["收到。", " "],
    )


def test_character_list_summary_omits_heavy_fields(tmp_path):
    sc, request = _env(tmp_path)
    sc.lore.save_character(_card())

    body = list_characters(request, summary=True)
    assert len(body["characters"]) == 1
    item = body["characters"][0]
    for heavy in ("table", "extensions", "regex_scripts", "assets", "background"):
        assert heavy not in item
    assert item["id"] == "c1"
    assert item["name"] == "初"
    assert item["voice"] == "voice-a"
    assert item["table_size"] == 2
    assert item["openings"] == [{"title": "开局1", "preview": "你醒在天台。"}]
    assert item["alternate_greetings"] == ["收到。"]


def test_character_list_without_summary_keeps_full_payload(tmp_path):
    sc, request = _env(tmp_path)
    sc.lore.save_character(_card())

    body = list_characters(request, summary=False)
    full = body["characters"][0]
    assert len(full["table"]) == 2
    assert full["extensions"] == {"big": "x" * 100}


def test_character_detail_by_id(tmp_path):
    sc, request = _env(tmp_path)
    sc.lore.save_character(_card("abc"))

    full = get_character("abc", request)
    assert full["id"] == "abc"
    assert [e["title"] for e in full["table"]] == ["开局1", "普通条目"]


def test_world_list_summary_and_detail(tmp_path):
    sc, request = _env(tmp_path)
    sc.lore.save_world(
        World(
            id="w1",
            name="世界",
            content="总述",
            table=[LoreEntry(title="a", content="1"), LoreEntry(title="b", content="2")],
        )
    )

    summary = list_worlds(request, summary=True)["worlds"][0]
    assert summary["entry_count"] == 2
    assert "table" not in summary
    assert summary["content"] == "总述"

    full = get_world("w1", request)
    assert len(full["table"]) == 2
