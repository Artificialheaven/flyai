"""APK 发布包发现：扫描 server.web_root 下最新的 FlyAI-x.xx.xxxx.apk。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .config import PROJECT_ROOT

APK_MIME = "application/vnd.android.package-archive"

# 文件名即版本号。大小写不敏感匹配（Windows 部署不区分大小写），
# 但对外始终返回磁盘上的真实文件名。
_APK_NAME_RE = re.compile(r"^FlyAI-(\d+)\.(\d+)\.(\d+)\.apk$", re.IGNORECASE)


@dataclass(frozen=True)
class ApkRelease:
    version: str
    version_code: int
    file_name: str
    size: int


def parse_apk_name(name: str) -> Optional[Tuple[str, int]]:
    """FlyAI-1.27.6134.apk -> ("1.27.6134", 1276134)；非法文件名返回 None。"""
    match = _APK_NAME_RE.match(name)
    if match is None:
        return None
    major, minor, revision = (int(part) for part in match.groups())
    # versionCode = 主*1e6 + 次*1e4 + 修订；超出字段范围会与其他版本撞号
    if minor > 99 or revision > 9999:
        return None
    version = "%d.%d.%d" % (major, minor, revision)
    return version, major * 1_000_000 + minor * 10_000 + revision


def resolve_web_root(configured: str) -> Path:
    """与根目录兜底路由相同的解析规则：相对路径基于项目根。"""
    root = Path((configured or "webroot").strip())
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    return root


def find_latest_apk(web_root: Path) -> Optional[ApkRelease]:
    """非递归扫描 web_root；目录缺失/不可读视为没有发布包。

    跳过符号链接与 0 字节文件：前者与兜底路由的越界检查语义一致，
    后者通常是还没拷贝完的上传文件。
    """
    try:
        entries = list(web_root.iterdir())
    except OSError:
        return None

    best: Optional[ApkRelease] = None
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            size = entry.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        parsed = parse_apk_name(entry.name)
        if parsed is None:
            continue
        version, version_code = parsed
        candidate = ApkRelease(version, version_code, entry.name, size)
        if best is None or (candidate.version_code, candidate.file_name) > (
            best.version_code,
            best.file_name,
        ):
            best = candidate
    return best
