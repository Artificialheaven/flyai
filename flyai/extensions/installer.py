"""扩展 zip 安装：安全解压、manifest 校验、静态扫描、原子替换。"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

from .manifest import parse_manifest
from .models import CompatReport, ExtensionManifest, InstallRecord, ManifestError
from .scanner import is_loadable, scan_extension
from .store import ExtensionStore, _atomic_json


class InstallError(ValueError):
    """安装失败（路由层转 400/409）。"""


@dataclass
class InstallLimits:
    max_zip_bytes: int = 20 * 1024 * 1024
    max_total_bytes: int = 20 * 1024 * 1024
    max_files: int = 500
    max_file_bytes: int = 5 * 1024 * 1024


@dataclass
class InstallResult:
    manifest: ExtensionManifest
    report: CompatReport
    record: InstallRecord
    warnings: list


def _safe_member_name(name: str) -> str:
    raw = str(name or "")
    if not raw:
        raise InstallError("压缩包包含空文件名")
    if "\\" in raw:
        raise InstallError("压缩包文件名不能包含反斜杠")
    if raw.startswith("/") or raw.startswith("~"):
        raise InstallError("压缩包文件名不能是绝对路径")
    if ":" in raw:
        raise InstallError("压缩包文件名不能包含盘符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise InstallError("压缩包文件名含控制字符")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise InstallError("压缩包文件名不能包含 ..")
    if not parts:
        raise InstallError("压缩包包含非法路径")
    return "/".join(parts)


def _is_symlink_or_special(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode in (0o120000, 0o020000, 0o060000, 0o010000, 0o140000)


def _find_manifest_root(base: Path) -> Path:
    if (base / "manifest.json").is_file():
        return base
    candidates = [
        p for p in base.glob("*/manifest.json")
        if p.is_file()
    ]
    if len(candidates) == 1:
        return candidates[0].parent
    deeper = [p for p in base.glob("*/*/manifest.json") if p.is_file()]
    if len(deeper) == 1:
        return deeper[0].parent
    raise InstallError("压缩包内找不到唯一的 manifest.json")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def install_zip(
    store: ExtensionStore,
    data: bytes,
    fallback_name: str = "",
    limits: Optional[InstallLimits] = None,
    update: bool = False,
) -> InstallResult:
    limits = limits or InstallLimits()
    if not data:
        raise InstallError("上传内容为空")
    if len(data) > limits.max_zip_bytes:
        raise InstallError("压缩包超过 %dMB" % (limits.max_zip_bytes // (1024 * 1024)))
    if not zipfile.is_zipfile(__import__("io").BytesIO(data)):
        raise InstallError("不是有效的 zip 文件")

    work = store.tmp / ("install-%s" % uuid.uuid4().hex)
    files_root = work / "files"
    files_root.mkdir(parents=True, exist_ok=True)
    try:
        total = 0
        count = 0
        seen: set = set()
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if _is_symlink_or_special(info):
                    raise InstallError("扩展包不能包含符号链接或设备文件")
                rel = _safe_member_name(info.filename)
                key = rel.casefold()
                if key in seen:
                    raise InstallError("压缩包包含重复路径：%s" % rel)
                seen.add(key)
                if info.file_size > limits.max_file_bytes:
                    raise InstallError("单文件超过 5MB：%s" % rel)
                count += 1
                if count > limits.max_files:
                    raise InstallError("文件数超过 %d" % limits.max_files)
                total += info.file_size
                if total > limits.max_total_bytes:
                    raise InstallError("解压后超过 %dMB" % (limits.max_total_bytes // (1024 * 1024)))
                target = files_root.joinpath(*PurePosixPath(rel).parts)
                resolved = target.resolve()
                if files_root.resolve() not in resolved.parents and resolved != files_root.resolve():
                    raise InstallError("压缩包路径逃逸：%s" % rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 16)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > limits.max_file_bytes:
                            raise InstallError("单文件解压超过 5MB：%s" % rel)
                        dst.write(chunk)

        base = _find_manifest_root(files_root)
        manifest_raw = base / "manifest.json"
        import json
        try:
            manifest_data = json.loads(manifest_raw.read_text(encoding="utf-8"))
        except Exception as exc:
            raise InstallError("manifest.json 不是合法 JSON：%s" % exc)

        fallback = fallback_name or (base.name if base != files_root else "")
        try:
            manifest = parse_manifest(manifest_data, fallback_name=fallback)
        except ManifestError as exc:
            raise InstallError(str(exc))

        exists = store.install_dir(manifest.name).is_dir()
        if update and not exists:
            raise InstallError("扩展未安装，无法更新")
        if exists and not update:
            raise InstallError("扩展 %s 已安装" % manifest.name)

        report = scan_extension(base, manifest)
        warnings = []
        if report.missing_modules:
            warnings.append("缺少宿主模块：%s" % "、".join(report.missing_modules[:6]))
        if report.missing_symbols:
            sample = ["%s.%s" % (m["module"], m["symbol"]) for m in report.missing_symbols[:6]]
            warnings.append("缺少导出：%s" % "、".join(sample))
        if not is_loadable(manifest, report):
            warnings.append("兼容等级 %s：默认不会加载" % report.level)

        stage = work / "stage"
        stage.mkdir(parents=True, exist_ok=True)
        target_files = stage / "files"
        if base == files_root:
            shutil.move(str(files_root), str(target_files))
        else:
            shutil.move(str(base), str(target_files))
        _atomic_json(stage / "manifest.json", manifest.to_dict())
        _atomic_json(stage / "compat-report.json", report.to_dict())

        final_dir = store.install_dir(manifest.name)
        backup = work / "backup"
        if final_dir.exists():
            shutil.move(str(final_dir), str(backup))
        try:
            shutil.move(str(stage), str(final_dir))
        except Exception:
            if backup.exists():
                shutil.move(str(backup), str(final_dir))
            raise

        record = InstallRecord(
            name=manifest.name,
            display_name=manifest.display_name,
            version=manifest.version,
            sha256=_sha256(data),
            installed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            source="zip",
            compat=report.level,
            manifest=manifest.to_dict(),
        )
        store.upsert_record(record)
        return InstallResult(manifest=manifest, report=report, record=record, warnings=warnings)
    finally:
        shutil.rmtree(work, ignore_errors=True)
