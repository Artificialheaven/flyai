"""R7 会话副本（override）与导出相关单测（纯逻辑）。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.lore.models import Character, World
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Session
from flyai.sessions.resolve import (
    clear_character_override,
    effective_character,
    effective_world,
    fork_character,
    is_character_scoped,
)


class _FakeLore:
    def __init__(self, char=None, world=None):
        self._char = char
        self._world = world

    def get_character(self, cid):
        return self._char if self._char and self._char.id == cid else None

    def get_world(self, wid):
        return self._world if self._world and self._world.id == wid else None


def test_override_roundtrip(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s = store.create(title="t")
    s.character_override = {"id": "zz", "name": "副本", "intro": "a"}
    s.world_override = {"id": "ww", "name": "世界", "content": "b"}
    store.save(s)
    s2 = store.get(s.id)
    assert s2.character_override["name"] == "副本"
    assert s2.world_override["content"] == "b"


def test_effective_prefers_override_then_library():
    char = Character(id="c1", name="主卡")
    world = World(id="w1", name="主世界")
    lore = _FakeLore(char=char, world=world)
    s = Session(character_id="c1", world_id="w1")
    assert effective_character(s, lore).name == "主卡"
    assert not is_character_scoped(s)
    s.character_override = Character(id="c1", name="副本", intro="z").to_dict()
    assert effective_character(s, lore).name == "副本"
    assert is_character_scoped(s)
    clear_character_override(s)
    assert not is_character_scoped(s)
    assert effective_character(s, lore).name == "主卡"


def test_fork_creates_override():
    char = Character(id="c1", name="主卡", attributes=[{"key": "a", "label": "甲"}])
    s = Session(character_id="c1")
    copy = fork_character(s, _FakeLore(char=char))
    assert copy.id == "c1" and copy.name == "主卡"
    assert copy.attributes[0]["label"] == "甲"
    assert s.character_override["name"] == "主卡"


def test_fork_no_character_raises():
    s = Session()
    with pytest.raises(ValueError):
        fork_character(s, _FakeLore())