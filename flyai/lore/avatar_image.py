"""角色卡 JSON 的头像内嵌：导出时把本地头像转成 base64，导入时校验后落盘。

只处理 FlyAI 自己上传目录下的头像（`/uploads/<uid>/<file>`）。图片类型以文件
魔数为准，不信任扩展名或 JSON 里的声明。任何失败都返回 None —— 头像只是附属
信息，不能让导出/导入整体失败。
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..ids import InvalidName, safe_child

MAX_AVATAR_BYTES = 8 * 1024 * 1024

# 魔数 → (扩展名, media_type)
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)
_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_UPLOAD_RE = re.compile(r"^/uploads/([^/]+)/([^/]+)$")
# base64 最多膨胀 4/3，再留一点余量
_MAX_B64_LEN = (MAX_AVATAR_BYTES // 3 + 1) * 4 + 16


def sniff_image(data: bytes) -> Optional[Tuple[str, str]]:
    """按魔数判定图片类型；支持 PNG/JPEG/GIF/WebP。"""
    if not data:
        return None
    for magic, ext, media in _SIGNATURES:
        if data.startswith(magic):
            return ext, media
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _uploads_root(upload_dir: Path) -> Path:
    """upload_dir 形如 <data>/uploads/<uid>，其父目录即上传根。"""
    return Path(upload_dir).resolve().parent


def embed_avatar(upload_dir: Optional[Path], avatar_url: str) -> Optional[Dict[str, str]]:
    """把 `/uploads/<uid>/<file>` 指向的本地头像读成 base64；失败返回 None。"""
    if upload_dir is None:
        return None
    match = _UPLOAD_RE.match((avatar_url or "").strip())
    if not match:
        return None
    uid, fname = match.group(1), match.group(2)
    try:
        user_dir = safe_child(_uploads_root(upload_dir), uid)
        path = safe_child(user_dir, fname)
    except InvalidName:
        return None
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size <= 0 or size > MAX_AVATAR_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    kind = sniff_image(raw)
    if kind is None:
        return None
    return {"media_type": kind[1], "data": base64.b64encode(raw).decode("ascii")}


def materialize_avatar(
    upload_dir: Optional[Path], uid: str, blob: Any
) -> Optional[str]:
    """校验并落盘内嵌头像；成功返回新的 `/uploads/<uid>/<file>`，失败返回 None。"""
    if upload_dir is None or not isinstance(blob, dict):
        return None
    media = blob.get("media_type")
    data = blob.get("data")
    if media not in _MEDIA_TYPES or not isinstance(data, str):
        return None
    data = data.strip()
    if not data or len(data) > _MAX_B64_LEN:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_AVATAR_BYTES:
        return None
    kind = sniff_image(raw)
    if kind is None:
        return None
    ext = kind[0]  # 声明类型与魔数不一致时以魔数为准
    uid = str(uid or "")
    try:
        user_dir = safe_child(_uploads_root(upload_dir), uid)
        user_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.{ext}"
        dest = safe_child(user_dir, fname)
        dest.write_bytes(raw)
    except (InvalidName, OSError):
        return None
    return f"/uploads/{uid}/{fname}"
