"""Galgame 资产库：立绘/背景入库、index 持久化、防穿越。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.galgame.assets import GalgameAssetStore
from flyai.ids import InvalidName

_PNG = b"\x89PNG\r\n\x1a\nFAKE-PNG-DATA"


def _store(tmp_path):
    return GalgameAssetStore(tmp_path / "uploads", "/uploads/u1")


def test_sprite_roundtrip_and_persistence(tmp_path):
    store = _store(tmp_path)
    rec = store.add_sprite("cid1", _PNG, ".png", name="常服", prompt="p1")
    assert rec.url.startswith("/uploads/u1/galgame/sprites/cid1/")
    assert rec.name == "常服"

    # 新 store 实例：index.json 仍可读
    store2 = _store(tmp_path)
    listed = store2.list_sprites("cid1")
    assert len(listed) == 1 and listed[0].id == rec.id
    got = store2.get_sprite("cid1", rec.id)
    assert got is not None and got.url == rec.url
    assert (tmp_path / "uploads" / "galgame" / "sprites" / "cid1" / rec.file).is_file()


def test_sprites_bound_per_character(tmp_path):
    store = _store(tmp_path)
    store.add_sprite("cid1", _PNG, ".png")
    store.add_sprite("cid2", _PNG, ".png")
    assert len(store.list_sprites("cid1")) == 1
    assert len(store.list_sprites("cid2")) == 1


def test_background_roundtrip_scoped_by_session(tmp_path):
    store = _store(tmp_path)
    rec = store.add_background("sid1", _PNG, ".png", name="教室")
    assert rec.url.startswith("/uploads/u1/galgame/backgrounds/sid1/")
    assert store.get_background("sid1", rec.id).name == "教室"
    assert store.list_backgrounds("other-sid") == []


def test_bad_ext_defaults_png(tmp_path):
    store = _store(tmp_path)
    rec = store.add_sprite("cid1", _PNG, ".bmp")
    assert rec.file.endswith(".png")


_MID = b"MThd\x00\x00\x00\x06FAKE-MIDI"


def test_music_roundtrip_scoped_by_session(tmp_path):
    store = _store(tmp_path)
    rec = store.add_music("sid1", _MID, ".mid", name="日常BGM", prompt="{}")
    assert rec.file.endswith(".mid")
    assert rec.url.startswith("/uploads/u1/galgame/music/sid1/")
    store2 = _store(tmp_path)
    listed = store2.list_music("sid1")
    assert len(listed) == 1 and listed[0].id == rec.id
    assert store2.get_music("sid1", rec.id).name == "日常BGM"
    assert (tmp_path / "uploads" / "galgame" / "music" / "sid1" / rec.file).is_file()
    assert store2.list_music("other-sid") == []


def test_music_bad_ext_defaults_mid(tmp_path):
    rec = _store(tmp_path).add_music("sid1", _MID, ".bmp")
    assert rec.file.endswith(".mid")


def test_unsafe_character_id_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(InvalidName):
        store.list_sprites("../evil")


def test_unsafe_asset_id_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(InvalidName):
        store.get_sprite("cid1", "../x")


def test_get_missing_returns_none(tmp_path):
    store = _store(tmp_path)
    store.add_sprite("cid1", _PNG, ".png")
    assert store.get_sprite("cid1", "deadbeef") is None


def test_corrupt_index_returns_empty(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "uploads" / "galgame" / "sprites" / "cid1"
    d.mkdir(parents=True)
    (d / "index.json").write_text("not-json", encoding="utf-8")
    assert store.list_sprites("cid1") == []
