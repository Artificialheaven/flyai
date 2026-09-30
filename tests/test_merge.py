"""按标题合并：merge_tables / merge_character_patch / merge_world_patch。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.merge import (
    merge_character_patch,
    merge_tables,
    merge_world_patch,
    normalized_title_key,
)
from flyai.lore.models import LoreEntry


def test_normalized_title_key():
    assert normalized_title_key("魔 法 剑") == normalized_title_key("魔法剑")
    assert normalized_title_key("『标题』") == normalized_title_key("标题")
    assert normalized_title_key("ABC") == normalized_title_key("abc")


def test_omitted_titles_are_not_lost():
    base = [LoreEntry(title="旧设定", content="旧内容")]
    out = merge_tables(base, [LoreEntry(title="新设定", content="新内容")])
    assert [e.title for e in out] == ["旧设定", "新设定"]


def test_same_title_updates_content():
    base = [LoreEntry(title="设定", content="原内容")]
    out = merge_tables(base, [LoreEntry(title="设定", content="新内容")])
    assert len(out) == 1
    assert out[0].content == "新内容"


def test_empty_content_does_not_overwrite():
    base = [LoreEntry(title="设定", content="保留内容")]
    out = merge_tables(base, [LoreEntry(title="设定", content="   ")])
    assert out[0].content == "保留内容"


def test_pinned_only_increases():
    base = [LoreEntry(title="设定", content="x", pinned=True)]
    out = merge_tables(base, [LoreEntry(title="设定", content="y", pinned=False)])
    assert out[0].pinned is True

    base2 = [LoreEntry(title="设定2", content="x", pinned=False)]
    out2 = merge_tables(base2, [LoreEntry(title="设定2", content="y", pinned=True)])
    assert out2[0].pinned is True


def test_blank_title_and_content_skipped():
    out = merge_tables([], [LoreEntry(title="", content="")])
    assert out == []


def test_accepts_dicts_and_preserves_pinned_in_dict():
    out = merge_tables(
        [{"title": "甲", "content": "旧", "pinned": True}],
        [{"title": "甲", "content": "新"}],
    )
    assert out[0].content == "新"
    assert out[0].pinned is True


def test_character_patch_intro_and_table():
    base = {"name": "初", "intro": "旧简介", "table": [{"title": "旧", "content": "x"}]}
    patch = {
        "intro": "新简介",
        "table": [{"title": "旧", "content": "y"}, {"title": "追加", "content": "z"}],
    }
    out = merge_character_patch(base, patch)
    assert out["intro"] == "新简介"
    assert [t["title"] for t in out["table"]] == ["旧", "追加"]
    assert out["table"][0]["content"] == "y"
    assert "pinned" in out["table"][0]


def test_character_patch_empty_table_untouched():
    base = {"table": [{"title": "旧", "content": "x"}]}
    out = merge_character_patch(base, {"table": []})
    assert out["table"] == base["table"]


def test_world_patch():
    base = {"content": "旧世界", "table": [{"title": "规则", "content": "a"}]}
    patch = {
        "content": "新世界",
        "table": [{"title": "规则", "content": "b"}],
    }
    out = merge_world_patch(base, patch)
    assert out["content"] == "新世界"
    assert out["table"][0]["content"] == "b"
