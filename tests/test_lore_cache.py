# -*- coding: utf-8 -*-
"""角色卡/世界观解析缓存：stat 命中复用、写删发布后失效、自有卡遮蔽共享卡。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.models import Character, World
from flyai.lore.store import LoreStore


@pytest.fixture()
def stores(tmp_path):
    """(用户自己的 store, 直接写共享目录的 store)"""
    return (
        LoreStore(tmp_path / "u", shared_base=tmp_path / "shared"),
        LoreStore(tmp_path / "shared"),
    )


@pytest.fixture()
def parses(monkeypatch):
    calls = {"n": 0}
    real_char = Character.from_dict.__func__
    real_world = World.from_dict.__func__

    def char_counting(cls, d):
        calls["n"] += 1
        return real_char(cls, d)

    def world_counting(cls, d):
        calls["n"] += 1
        return real_world(cls, d)

    monkeypatch.setattr(Character, "from_dict", classmethod(char_counting))
    monkeypatch.setattr(World, "from_dict", classmethod(world_counting))
    return calls


def test_list_and_get_share_cache(stores, parses):
    store, _ = stores
    c = Character(name="爱丽丝")
    store.save_character(c)

    assert [x.name for x in store.list_characters()] == ["爱丽丝"]
    assert parses["n"] == 1

    before = parses["n"]
    # 列表已缓存，get 不再解析
    assert store.get_character(c.id).name == "爱丽丝"
    assert parses["n"] == before


def test_save_invalidates_cached_character(stores, parses):
    store, _ = stores
    c = Character(name="旧名")
    store.save_character(c)
    store.list_characters()
    assert parses["n"] == 1

    c.name = "新的名字"
    store.save_character(c)
    assert [x.name for x in store.list_characters()] == ["新的名字"]
    assert parses["n"] > 1


def test_delete_prunes_cache(stores):
    store, _ = stores
    c = Character(name="待删")
    store.save_character(c)
    store.list_characters()
    key = str(store._char_dir / (c.id + ".json"))
    assert key in store._cache

    store.delete_character(c.id)
    assert key not in store._cache
    assert store.list_characters() == []


def test_bad_file_hidden_and_cached(stores, parses):
    store, _ = stores
    store.save_character(Character(name="正常卡"))
    (store._char_dir / "bad.json").write_text("{不是 json", encoding="utf-8")

    assert [x.name for x in store.list_characters()] == ["正常卡"]
    before = parses["n"]
    assert [x.name for x in store.list_characters()] == ["正常卡"]
    # 坏文件写了 _INVALID 哨兵，第二次不重复尝试
    assert parses["n"] == before


def test_own_card_shadows_shared_with_warm_cache(stores):
    store, shared = stores
    cid = "shadow-id"
    shared.save_character(Character(id=cid, name="共享版", author="admin"))
    assert [c.name for c in store.list_characters()] == ["共享版"]

    store.save_character(Character(id=cid, name="自己版", author="u1"))
    cards = [c for c in store.list_characters() if c.id == cid]
    assert len(cards) == 1 and cards[0].name == "自己版"
    assert store.get_character(cid).name == "自己版"
    assert store.character_source(cid) == "own"


def test_publish_unpublish_refresh_cache(stores):
    store, _ = stores
    c = Character(name="发布卡")
    store.save_character(c)
    # 先把自身目录缓存暖起来
    assert store.list_characters()[0].public is False

    store.publish_character(c)
    assert c.public is True
    own = store.get_character(c.id)
    assert own.public is True

    store.unpublish_character(c.id)
    after = store.get_character(c.id)
    assert after.public is False
    assert store.character_source(c.id) == "own"


def test_world_cache_invalidation(stores):
    store, _ = stores
    w = World(name="旧大陆")
    store.save_world(w)
    assert [x.name for x in store.list_worlds()] == ["旧大陆"]

    w.name = "新大陆"
    store.save_world(w)
    assert [x.name for x in store.list_worlds()] == ["新大陆"]

    store.delete_world(w.id)
    assert store.list_worlds() == []


def test_concurrent_list_no_error(stores):
    from concurrent.futures import ThreadPoolExecutor

    store, _ = stores
    for i in range(5):
        store.save_character(Character(name="卡{}".format(i)))

    def run(_):
        return sorted(c.name for c in store.list_characters())

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(16)))
    assert all(r == results[0] for r in results)
    assert len(results[0]) == 5
