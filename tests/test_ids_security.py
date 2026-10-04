"""外部 id / 文件名收口回归：路径穿越、分隔符、控制字符一律挡在存储层之外。"""

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.ids import InvalidName, check_model_id, check_safe_name, safe_child
from flyai.lore.character_memory import CharacterMemoryStore
from flyai.lore.models import Character
from flyai.lore.store import LoreStore
from flyai.sessions.json_store import JsonSessionStore


# ---- flyai.ids 原语 ----

@pytest.mark.parametrize("name", [
    "abc123", "deepseek-v4.1-flash", "deepseek-ai_DeepSeek-V3.2", "小雪",
    "a" * 100,
])
def test_check_safe_name_accepts_normal(name):
    assert check_safe_name(name) == name


@pytest.mark.parametrize("name", [
    "", ".", "..", "../x", "a/b", "a\\b", "a:b", "..\\..\\users",
    "a\nb", "a\x00b", "a\x7fb", "a" * 101,
])
def test_check_safe_name_rejects_dangerous(name):
    with pytest.raises(InvalidName):
        check_safe_name(name)


@pytest.mark.parametrize("mid", [
    "abc", "m1", "deepseek-v4.1-flash", "model.id.here", "a_b-c.d",
])
def test_check_model_id_accepts(mid):
    assert check_model_id(mid) == mid


@pytest.mark.parametrize("mid", [
    "deepseek-ai/DeepSeek-V3.2",  # 遗留含 / id：新建不再允许（加载不拦）
    '"onfocus="alert(1)', "a b", "<x>", ".leading-dot", "-leading", "",
    "a" * 101,
])
def test_check_model_id_rejects(mid):
    with pytest.raises(InvalidName):
        check_model_id(mid)


def test_safe_child_allows_inside(tmp_path):
    p = safe_child(tmp_path, "x.json")
    assert p == (tmp_path / "x.json").resolve()


@pytest.mark.parametrize("name", [
    "../x.json", "..\\x.json", "a/b.json",
])
def test_safe_child_blocks_escape(tmp_path, name):
    with pytest.raises(InvalidName):
        safe_child(tmp_path, name)


# ---- 会话存储 ----

def test_session_store_path_blocks_traversal(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    with pytest.raises(InvalidName):
        store._path("..\\..\\users")
    with pytest.raises(InvalidName):
        store.get("../../outside")
    with pytest.raises(InvalidName):
        store.delete("..\\..\\outside")


def test_session_store_normal_id_roundtrip(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s = store.create(title="t")
    assert store.get(s.id) is not None
    store.clear(s.id)
    store.delete(s.id)
    assert store.get(s.id) is None


# ---- 角色/世界观存储 ----

def _lore(tmp_path):
    return LoreStore(base_dir=tmp_path / "data", shared_base=tmp_path / "shared")


def test_lore_character_traversal_is_none(tmp_path):
    lore = _lore(tmp_path)
    assert lore.get_character("../../users") is None
    assert lore.character_source("..\\..\\users") is None


def test_lore_delete_character_traversal_raises(tmp_path):
    lore = _lore(tmp_path)
    with pytest.raises(InvalidName):
        lore.delete_character("../../outside")
    with pytest.raises(InvalidName):
        lore.delete_world("..\\..\\outside")


def test_lore_save_character_bad_id_raises(tmp_path):
    lore = _lore(tmp_path)
    with pytest.raises(InvalidName):
        lore.save_character(Character(id="../evil", name="x"))


def test_lore_normal_character_roundtrip(tmp_path):
    lore = _lore(tmp_path)
    c = Character(id="c1", name="角色")
    lore.save_character(c)
    assert lore.get_character("c1") is not None
    assert lore.character_source("c1") == "own"
    lore.delete_character("c1")
    assert lore.get_character("c1") is None


# ---- 角色记忆簿 ----

def test_character_memory_traversal_blocked(tmp_path):
    mem = CharacterMemoryStore(tmp_path)
    with pytest.raises(InvalidName):
        mem.exists("../../users")
    with pytest.raises(InvalidName):
        mem.append("..\\evil", "text")


def test_character_memory_normal_roundtrip(tmp_path):
    mem = CharacterMemoryStore(tmp_path)
    assert mem.exists("c1") is False
    entries = mem.append("c1", "记住我")
    assert entries and mem.load("c1") == entries


# ---- 路由层 400 预检 ----

def test_route_helpers_raise_400():
    from flyai.api.routes_lore import _safe_cid, _safe_wid
    from flyai.api.routes_room import _safe_room_id
    from flyai.api.routes_session import _safe_sid
    from flyai.api.routes_chat import _safe_sid as _safe_chat_sid

    for fn in (_safe_cid, _safe_wid, _safe_room_id, _safe_sid, _safe_chat_sid):
        with pytest.raises(HTTPException) as ei:
            fn("../bad")
        assert ei.value.status_code == 400
