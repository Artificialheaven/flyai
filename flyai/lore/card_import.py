"""酒馆（SillyTavern / CCv2/v3）角色卡导入。

支持两种载体：
- PNG 卡片：tEXt/iTXt/zTXt chunk 中 key 为 ``chara``（v2）或 ``ccv3``（v3），
  值为 base64 编码的卡片 JSON；PNG 图片本身作为角色头像。
- JSON 卡片：同样的信封（{"spec": ..., "data": {...}}），或更老的 v1 平铺字段。

字段映射以扮演效果为主；同时尽量保真地保留酒馆扩展数据（默认不执行其中的
JavaScript；tavern_helper 作者脚本需显式开启兼容运行时）：

- ``extensions``：depth_prompt / world / talkativeness / fav / tavern_helper 等；
- ``regex_scripts``：酒馆正则脚本（由 FlyAI 原生引擎解释，见 flyai.st）；
- ``alternate_greetings``、``assets`` 原样保留。

creator / tags 仍丢弃。导入时通过 :func:`import_report` 返回统计与警告。
"""

from __future__ import annotations

import base64
import copy
import json
import struct
import zlib
from typing import Any, Dict, List, Optional

from .models import Character

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CARD_KEYS = ("ccv3", "chara")
_MAX_BYTES = 50 * 1024 * 1024  # 卡片（含内嵌 JSON 与头像像素）上限
_MAX_MB = _MAX_BYTES // (1024 * 1024)


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


def extract_card_json(
    raw: bytes, max_bytes: Optional[int] = None
) -> Dict[str, Any]:
    """从 PNG 或纯 JSON 字节中取出卡片信封 dict。

    max_bytes 由调用方按配置传入；缺省使用模块默认上限。
    """
    limit = _MAX_BYTES if max_bytes is None else max(1, int(max_bytes))
    if len(raw) > limit:
        limit_mb = max(1, limit // (1024 * 1024))
        raise CardImportError("卡片文件过大（>%dMB）" % limit_mb)
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


def _card_extensions(data: Dict[str, Any]) -> Dict[str, Any]:
    """data.extensions，并兼容 depth_prompt 放在 data 顶层的旧写法。"""
    raw = data.get("extensions")
    ext: Dict[str, Any] = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    top_dp = data.get("depth_prompt")
    if isinstance(top_dp, dict) and not isinstance(ext.get("depth_prompt"), dict):
        ext["depth_prompt"] = copy.deepcopy(top_dp)
    return ext


def _card_regex_scripts(data: Dict[str, Any],
                        extensions: Dict[str, Any]) -> List[Dict[str, Any]]:
    """正则脚本在 CCv3 里位于 data.extensions.regex_scripts，兼容顶层写法。"""
    raw = extensions.get("regex_scripts")
    if not isinstance(raw, list):
        raw = data.get("regex_scripts")
    if not isinstance(raw, list):
        return []
    return [copy.deepcopy(s) for s in raw if isinstance(s, dict)]


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

    extensions = _card_extensions(data)
    greetings = data.get("alternate_greetings")
    assets = data.get("assets")

    return Character(
        name=name,
        intro="\n\n".join(intro_parts),
        background=scenario,
        mes_example=(data.get("mes_example") or "").strip(),
        first_mes=(data.get("first_mes") or "").strip(),
        post_history_instructions=(data.get("post_history_instructions") or "").strip(),
        system_prompt=(data.get("system_prompt") or "").strip(),
        table=_build_table(data),
        regex_scripts=_card_regex_scripts(data, extensions),
        extensions=extensions,
        alternate_greetings=[
            str(g) for g in (greetings if isinstance(greetings, list) else [])
            if str(g or "").strip()
        ],
        assets=[
            copy.deepcopy(a) for a in (assets if isinstance(assets, list) else [])
            if isinstance(a, dict)
        ],
    )


def import_report(character: Character) -> Dict[str, Any]:
    """导入统计与用户可读警告（全部为数据，不执行卡内 JS）。

    返回 ``{"stats": {...}, "warnings": [...]}``，供 /characters/import-card
    在保持旧响应字段的同时附加展示。
    """
    ext = character.extensions if isinstance(character.extensions, dict) else {}
    dp = ext.get("depth_prompt")
    has_depth_prompt = isinstance(dp, dict) and bool(str(dp.get("prompt") or "").strip())
    world = str(ext.get("world") or "").strip()

    stats = {
        "table": len(character.table),
        "regex_scripts": len(character.regex_scripts),
        "alternate_greetings": len(character.alternate_greetings),
        "assets": len(character.assets),
        "depth_prompt": has_depth_prompt,
    }
    warnings: List[str] = []
    if ext.get("tavern_helper"):
        warnings.append(
            "卡内 tavern_helper 脚本默认仅保存为数据；"
            "开启作者脚本运行时后会在沙箱 iframe 中执行"
        )
    if character.regex_scripts:
        note = "已保留 %d 条酒馆正则脚本，将由 FlyAI 原生正则引擎解释" % len(character.regex_scripts)
        try:
            from ..st import regex_scripts as _st_regex

            invalid = _st_regex.invalid_script_count(character.regex_scripts)
        except Exception:
            invalid = 0
        if invalid:
            note += "（其中 %d 条正则无效，将被跳过）" % invalid
        warnings.append(note)
    if world:
        warnings.append(
            "卡内关联世界书「%s」未自动导入；卡内嵌世界书条目已转换到内容表" % world)
    if character.assets:
        warnings.append(
            "卡内 assets（表情/背景资源）已保留元数据，但暂未同步到 FlyAI 资源库")
    return {"stats": stats, "warnings": warnings}
