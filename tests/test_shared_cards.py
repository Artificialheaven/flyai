"""共享角色卡：LoreStore 合并/去重/来源、发布/取消发布、级联删除；模型新字段。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.models import Character
from flyai.lore.store import LoreStore


def _stores(tmp):
    """返回 (用户自己的 store, 直接写共享目录的 store)。"""
    store = LoreStore(tmp / "u", shared_base=tmp / "shared")
    shared_writer = LoreStore(tmp / "shared")
    return store, shared_writer


def test_list_merges_own_and_shared(tmp_path):
    store, shared_writer = _stores(tmp_path)
    own = Character(name="自己卡")
    pub = Character(name="共享卡")
    store.save_character(own)
    shared_writer.save_character(pub)

    names = {c.name for c in store.list_characters()}
    assert names == {"自己卡", "共享卡"}


def test_own_card_shadows_same_id_shared(tmp_path):
    store, shared_writer = _stores(tmp_path)
    cid = "abcd1234"
    store.save_character(Character(id=cid, name="自己版", author="u1"))
    shared_writer.save_character(Character(id=cid, name="共享版", author="admin"))

    assert store.character_source(cid) == "own"
    got = store.get_character(cid)
    assert got.name == "自己版"
    listed = [c for c in store.list_characters() if c.id == cid]
    assert len(listed) == 1 and listed[0].name == "自己版"


def test_character_source_shared_and_missing(tmp_path):
    store, shared_writer = _stores(tmp_path)
    pub = Character(name="共享卡")
    shared_writer.save_character(pub)

    assert store.character_source(pub.id) == "shared"
    assert store.get_character(pub.id).name == "共享卡"
    assert store.character_source("nope") is None
    assert store.get_character("nope") is None


def test_publish_and_unpublish(tmp_path):
    store, _ = _stores(tmp_path)
    c = Character(name="待发布", author="admin")
    store.save_character(c)
    store.publish_character(c)

    # 另一个用户目录、同一共享根：只能读到 shared
    reader = LoreStore(tmp_path / "other", shared_base=tmp_path / "shared")
    assert reader.character_source(c.id) == "shared"
    seen = reader.get_character(c.id)
    assert seen.public is True and seen.author == "admin"

    store.unpublish_character(c.id)
    assert reader.character_source(c.id) is None
    own = store.get_character(c.id)
    assert own.public is False


def test_cascade_delete_root_removes_variants(tmp_path):
    store, _ = _stores(tmp_path)
    root = Character(name="根卡")
    v1 = Character(name="副本一", parent_id=root.id)
    v2 = Character(name="副本二", parent_id=root.id)
    store.save_character(root)
    store.save_character(v1)
    store.save_character(v2)

    store.delete_character_cascade(root.id)
    assert store.get_character(root.id) is None
    assert store.get_character(v1.id) is None
    assert store.get_character(v2.id) is None


def test_character_new_fields_roundtrip():
    c = Character(name="根卡", parent_id="", alias="别名", author="admin", public=True,
                  system_prompt="用古风措辞。")
    d = c.to_dict()
    assert d["parent_id"] == "" and d["alias"] == "别名"
    assert d["author"] == "admin" and d["public"] is True
    assert d["system_prompt"] == "用古风措辞。"

    c2 = Character.from_dict(d)
    assert c2.parent_id == "" and c2.alias == "别名"
    assert c2.author == "admin" and c2.public is True
    assert c2.system_prompt == "用古风措辞。"

    # 旧 JSON（缺新字段）也能读
    old = {"id": c.id, "name": "旧卡"}
    c3 = Character.from_dict(old)
    assert c3.parent_id == "" and c3.alias == "" and c3.author == "" and c3.public is False
    assert c3.system_prompt == ""


def test_display_name_and_ref():
    c = Character(name="正式名", alias="", author="admin")
    assert c.display_name == "正式名"
    assert c.ref == f"admin:{c.id}"
    c.alias = "列表别名"
    assert c.display_name == "列表别名"


def test_default_attributes_list_is_empty():
    from flyai.config import AppConfig

    assert AppConfig().roleplay.attributes.list == []
