"""酒馆角色卡（CCv2/v3 PNG / JSON）导入：PNG chunk 解析与字段映射。"""

import base64
import io
import json
import os
import struct
import sys
import zlib
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore import card_import
from flyai.lore.card_import import CardImportError, character_from_cc, extract_card_json
from flyai.lore.store import LoreStore


# ---- 造卡 ----

def _chunk(ctype: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + ctype
        + data
        + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    )


def _make_png(card: dict, key: str = "chara", payload: bytes = None) -> bytes:
    if payload is None:
        payload = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8"))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1 RGB
    return b"".join([
        card_import.PNG_SIGNATURE,
        _chunk(b"IHDR", ihdr),
        _chunk(b"tEXt", key.encode("latin-1") + b"\x00" + payload),
        _chunk(b"IEND", b""),
    ])


V3_CARD = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "鲁迪",
        "description": "一个灰发少年。",
        "personality": "爱逞强，实则温柔。",
        "scenario": "菲托亚村的午后",
        "mes_example": "{{user}}：早<START>{{char}}：早呀",
        "first_mes": "{{char}}转过身：「{{user}}，你来了。」",
        "alternate_greetings": ["备选1", "备选2"],
        "post_history_instructions": "保持角色语气。",
        "system_prompt": "你是鲁迪。",
        "creator": "某作者",
        "creator_notes": "随便备注",
        "tags": ["标签"],
        "assets": [{"type": "icon", "uri": "data:image/png;base64,AAAA"}],
        "character_book": {
            "entries": [
                {"comment": "常驻设定", "content": "会圣掏拳", "constant": True, "enabled": True},
                {"keys": ["魔术"], "content": "擅长无吟唱魔术", "selective": True, "enabled": True},
                {"comment": "被禁用", "content": "不应导入", "constant": True, "enabled": False},
                {"comment": "空内容", "content": "   ", "enabled": True},
            ]
        },
    },
}


# ---- PNG / JSON 载体解析 ----

def test_extract_from_png_v2_chara_key():
    card = {"spec": "chara_card_v2", "spec_version": "2.0",
            "data": {"name": "小梅", "description": "猫娘"}}
    raw = _make_png(card)
    out = extract_card_json(raw)
    assert out["spec"] == "chara_card_v2"
    assert out["data"]["name"] == "小梅"


def test_extract_prefers_ccv3_key():
    raw = _make_png(V3_CARD, key="ccv3")
    assert extract_card_json(raw)["spec"] == "chara_card_v3"


def test_extract_from_plain_json_bytes():
    raw = json.dumps(V3_CARD).encode("utf-8")
    out = extract_card_json(b"  \n" + raw)
    assert out["data"]["name"] == "鲁迪"


def test_extract_bad_png_raises():
    with pytest.raises(CardImportError):
        extract_card_json(b"not a png or json")
    # PNG 但没有 chara 文本块
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    bare = card_import.PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
    with pytest.raises(CardImportError):
        extract_card_json(bare)


def test_extract_rejects_bad_crc():
    # 翻转 tEXt chunk base64 数据区的一个字节（IHDR 占 8+25 字节），CRC 应不匹配
    raw = bytearray(_make_png(V3_CARD))
    raw[60] ^= 0xFF
    with pytest.raises(CardImportError):
        extract_card_json(bytes(raw))


# ---- 字段映射 ----

def test_v3_full_mapping():
    c = character_from_cc(V3_CARD)
    assert c.name == "鲁迪"
    assert "一个灰发少年。" in c.intro
    assert "【性格】" in c.intro and "爱逞强" in c.intro
    assert c.background == "菲托亚村的午后"
    assert c.mes_example == "{{user}}：早<START>{{char}}：早呀"
    assert c.first_mes == "{{char}}转过身：「{{user}}，你来了。」"
    assert c.post_history_instructions == "保持角色语气。"
    assert c.system_prompt == "你是鲁迪。"

    # 世界书：constant → pinned；selective 普通条目；禁用/空内容丢弃
    assert len(c.table) == 2
    pinned = [e for e in c.table if e.pinned]
    assert len(pinned) == 1 and pinned[0].title == "常驻设定"
    other = [e for e in c.table if not e.pinned][0]
    assert other.title == "魔术" and other.content == "擅长无吟唱魔术"
    # selective 条目的 keys 迁移为触发词；常驻条目没有 keys
    assert other.keys == ["魔术"]
    assert pinned[0].keys == []


def test_book_entry_keys_split_string_and_separators():
    # keys 可能是逗号/顿号分隔的字符串，或元素本身带分隔符
    card = {
        "spec": "chara_card_v3",
        "data": {
            "name": "阿明",
            "character_book": {"entries": [
                {"comment": "A", "content": "甲", "keys": "铁剑，剑术、剑气/剑心"},
                {"comment": "B", "content": "乙", "keys": ["魔术", "魔法, 魔力"]},
                {"comment": "C", "content": "丙", "constant": True},
            ]},
        },
    }
    c = character_from_cc(card)
    by_title = {e.title: e for e in c.table}
    assert by_title["A"].keys == ["铁剑", "剑术", "剑气", "剑心"]
    assert by_title["B"].keys == ["魔术", "魔法", "魔力"]
    assert by_title["C"].keys == []


def test_book_entries_as_dict_and_title_fallback():
    card = {
        "spec": "chara_card_v3",
        "data": {
            "name": "阿明",
            "character_book": {"entries": {
                "1": {"name": "名字字段优先", "content": "A", "constant": True},
                "2": {"keys": ["关键词当标题"], "content": "B"},
            }},
        },
    }
    c = character_from_cc(card)
    by_title = {e.title: e for e in c.table}
    assert set(by_title) == {"名字字段优先", "关键词当标题"}
    assert by_title["名字字段优先"].pinned is True
    assert by_title["关键词当标题"].pinned is False


def test_v1_flat_card():
    # 老 v1 卡：字段平铺在顶层，无 data 信封
    c = character_from_cc({
        "name": "旧卡", "description": "描述", "personality": "无口",
        "first_mes": "……", "mes_example": "{{char}}：嗨",
    })
    assert c.name == "旧卡" and "描述" in c.intro
    assert c.first_mes == "……" and c.mes_example == "{{char}}：嗨"


def test_missing_name_raises():
    with pytest.raises(CardImportError):
        character_from_cc({"spec": "chara_card_v2", "data": {"description": "无名"}})
    with pytest.raises(CardImportError):
        character_from_cc({"data": "bad"})


def test_creator_and_assets_dropped():
    c = character_from_cc(V3_CARD)
    d = c.to_dict()
    assert "creator" not in d and "assets" not in d and "tags" not in d


# ---- HTTP 端点：PNG 头像落盘 + 保存角色 ----

def _png_upload(raw, filename="card.png"):
    from starlette.datastructures import UploadFile

    return UploadFile(filename=filename, file=io.BytesIO(raw))


def _import_request(tmp_path):
    lore = LoreStore(tmp_path / "lore")
    sc = SimpleNamespace(
        uid="u1", lore=lore,
        upload_dir=tmp_path / "uploads" / "u1",
    )
    sc.upload_dir.mkdir(parents=True, exist_ok=True)
    state = SimpleNamespace(user=lambda request: sc)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state))), sc


def test_import_card_endpoint_png_saves_avatar(tmp_path):
    from flyai.api.routes_lore import import_character_card

    request, sc = _import_request(tmp_path)
    out = _run_async(import_character_card(request, file=_png_upload(_make_png(V3_CARD))))

    assert out["name"] == "鲁迪"
    saved = sc.lore.get_character(out["id"])
    assert saved is not None and saved.first_mes.startswith("{{char}}")
    # 头像已写入该用户上传目录并在卡上引用
    assert saved.avatar.startswith("/uploads/u1/")
    avatar_path = tmp_path / "uploads" / "u1" / saved.avatar.split("/")[-1]
    assert avatar_path.read_bytes().startswith(card_import.PNG_SIGNATURE)


def test_import_card_endpoint_json_no_avatar(tmp_path):
    from flyai.api.routes_lore import import_character_card

    request, sc = _import_request(tmp_path)
    raw = json.dumps(V3_CARD, ensure_ascii=False).encode("utf-8")
    out = _run_async(
        import_character_card(request, file=_png_upload(raw, "card.json")))
    saved = sc.lore.get_character(out["id"])
    assert saved is not None and saved.avatar == ""


def test_import_card_endpoint_bad_file_400(tmp_path):
    from fastapi import HTTPException

    from flyai.api.routes_lore import import_character_card

    request, _sc = _import_request(tmp_path)
    with pytest.raises(HTTPException) as ei:
        _run_async(
            import_character_card(request, file=_png_upload(b"garbage", "x.png")))
    assert ei.value.status_code == 400


# ---- 异步小工具（避免额外插件依赖）----

def _run_async(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
