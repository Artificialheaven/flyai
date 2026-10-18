"""酒馆（SillyTavern / CCv2/v3）角色卡导入。

支持两种载体：
- PNG 卡片：tEXt/iTXt/zTXt chunk 中 key 为 ``chara``（v2）或 ``ccv3``（v3），
  值为 base64 编码的卡片 JSON；PNG 图片本身作为角色头像。
- JSON 卡片：同样的信封（{"spec": ..., "data": {...}}），或更老的 v1 平铺字段。

只转换影响扮演效果的字段；creator / tags / assets（表情贴图）等元数据丢弃。
alternate_greetings（备选开场白）目前没有对应 UI，仅取 first_mes。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from typing import Any, Dict, Optional

from .models import Character

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CARD_KEYS = ("ccv3", "chara")
_MAX_BYTES = 10 * 1024 * 1024  # 卡片（含内嵌 JSON 与头像像素）上限


class CardImportError(ValueError):
    """卡片无法解析或缺少必需字段。"""


# ---- 载体解析：PNG chunk / 纯 JSON ----

def _iter_png_chunks(raw: bytes):
    """按 (类型, 数据) 逐个产出 PNG chunk，已做长度/CRC 边界校验。"""
    if not raw.startswith(PNG_SIGNATURE):
        raise CardImportError("不是有效的 PNG 文件")
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        start = pos + 8
        end = start + length
        if end + 4 > len(raw):
            raise CardImportError("PNG 数据损坏（chunk 越界）")
        data = raw[start:end]
        (crc,) = struct.unpack(">I", raw[end:end + 4])
        if crc != zlib.crc32(ctype + data) & 0xFFFFFFFF:
            raise CardImportError("PNG 数据损坏（CRC 校验失败）")
        yield ctype, data
        pos = end + 4
        if ctype == b"IEND":
            break


def _decode_text_chunk(ctype: bytes, data: bytes) -> Optional[tuple]:
    """返回 (keyword, text)；非文本块/解析失败返回 None。"""
    try:
        if ctype == b"tEXt":
            keyword, value = data.split(b"\x00", 1)
            return keyword.decode("latin-1"), value.decode("latin-1")
        if ctype == b"zTXt":
            keyword, rest = data.split(b"\x00", 1)
            return keyword.decode("latin-1"), zlib.decompress(rest[1:]).decode("latin-1")
        if ctype == b"iTXt":
            keyword, rest = data.split(b"\x00", 1)
            comp_flag = rest[0]
            # comp_method(1) + language tag \0 + translated keyword \0
            parts = rest[2:].split(b"\x00", 2)
            if len(parts) < 3:
                return None
            text_bytes = parts[2]
            if comp_flag:
                text_bytes = zlib.decompress(text_bytes)
            return keyword.decode("latin-1"), text_bytes.decode("utf-8", "replace")
    except (ValueError, zlib.error, UnicodeDecodeError):
        return None
    return None


def extract_card_json(raw: bytes) -> Dict[str, Any]:
    """从 PNG 或纯 JSON 字节中取出卡片信封 dict。"""
    if len(raw) > _MAX_BYTES:
        raise CardImportError("卡片文件过大（>10MB）")
    if raw.lstrip()[:1] == b"{":
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise CardImportError("卡片 JSON 解析失败") from e
    if not raw.startswith(PNG_SIGNATURE):
        raise CardImportError("仅支持 PNG 角色卡或 JSON 卡")

    texts: Dict[str, str] = {}
    for ctype, data in _iter_png_chunks(raw):
        decoded = _decode_text_chunk(ctype, data)
        if decoded:
            texts[decoded[0]] = decoded[1]
    for key in _CARD_KEYS:
        if key not in texts:
            continue
        try:
            card_bytes = base64.b64decode(texts[key].strip())
            return json.loads(card_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
            raise CardImportError("卡片内嵌数据解析失败") from e
    raise CardImportError("PNG 中没有找到角色卡数据（chara/ccv3 文本块）")


# ---- 字段映射 ----

def _book_entries(book: Any):
    """character_book.entries 可能是列表（v2/v3 常规）或 id→entry 字典。"""
    if not isinstance(book, dict):
        return []
    entries = book.get("entries")
    if isinstance(entries, dict):
        return list(entries.values())
    if isinstance(entries, list):
        return entries
    return []


def _build_table(data: Dict[str, Any]):
    """character_book 条目复用 lore.tavern 的统一酒馆条目映射
    （constant→pinned、keys→触发词、position=4→深度注入、概率/角色）。

    enabled=False（或 disable=True）的条目不再丢弃：作为非常驻、无触发词
    的普通条目导入，用户可在编辑器里手动常驻；空内容仍跳过。
    """
    from . import tavern

    table = []
    for raw_entry in _book_entries(data.get("character_book")):
        if not isinstance(raw_entry, dict):
            continue
        if not (raw_entry.get("content") or "").strip():
            continue
        table.append(tavern.parse_entry(raw_entry))
    return table


def character_from_cc(card: Dict[str, Any]) -> Character:
    """把 CC 卡片信封映射为 FlyAI Character（新 id、未保存）。"""
    if not isinstance(card, dict):
        raise CardImportError("卡片数据格式不正确")
    data = card.get("data")
    if not isinstance(data, dict):
        data = card  # v1：字段平铺在顶层

    name = (data.get("name") or "").strip()
    if not name:
        raise CardImportError("卡片缺少角色名称（name）")

    description = (data.get("description") or "").strip()
    personality = (data.get("personality") or "").strip()
    scenario = (data.get("scenario") or "").strip()

    intro_parts = []
    if description:
        intro_parts.append(description)
    if personality:
        intro_parts.append("【性格】\n" + personality)

    return Character(
        name=name,
        intro="\n\n".join(intro_parts),
        background=scenario,
        mes_example=(data.get("mes_example") or "").strip(),
        first_mes=(data.get("first_mes") or "").strip(),
        post_history_instructions=(data.get("post_history_instructions") or "").strip(),
        system_prompt=(data.get("system_prompt") or "").strip(),
        table=_build_table(data),
    )
