#!/usr/bin/env python3
"""把当前仓库源码覆盖进已缓存的 arm64 rootfs.tar.gz。

不执行任何 arm64 程序：整包流式读写，opt/flyai 以外的条目（符号链接、
硬链接、uid/gid、权限、基础镜像文件）原样保留，因此 Windows 上也能跑，
不需要 WSL / qemu。

处理规则：
  * 替换  opt/flyai/{flyai,plugins}、main.py、requirements.txt、start.sh、
          config/config.example.json
  * 丢弃  opt/flyai/data（用户数据）、opt/flyai/config/config.json（用户配置）、
          .installed（安装标记）
  * 保留  .venv、config.example.json 以外的基础镜像内容、webroot 等

这样产出的 rootfs_payload.bin 可以直接在已有安装上覆盖解包：代码更新，
数据与配置不被覆盖。依赖变化（requirements.txt）需要重建 rootfs，本脚本
会通过 --check-requirements 提示。
"""

import argparse
import gzip
import hashlib
import io
import os
import sys
import tarfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

REPLACE_PATHS = (
    "./opt/flyai/main.py",
    "./opt/flyai/requirements.txt",
    "./opt/flyai/start.sh",
    "./opt/flyai/flyai",
    "./opt/flyai/plugins",
    "./opt/flyai/config/config.example.json",
)

DROP_PATHS = (
    "./opt/flyai/data",
    "./opt/flyai/config/config.json",
    "./opt/flyai/.installed",
    "./opt/flyai/F:",
)

SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache"}
SKIP_FILE_SUFFIXES = (".pyc", ".pyo")
TEXT_SUFFIXES = {
    ".py", ".txt", ".json", ".md", ".html", ".css", ".js", ".svg",
    ".yaml", ".yml", ".toml", ".sh",
}

START_SH = (
    b"#!/bin/sh\n"
    b"cd /opt/flyai || exit 1\n"
    b"export HOME=/root\n"
    b"export PYTHONUNBUFFERED=1\n"
    b"exec .venv/bin/python main.py\n"
)


def normalize_requirements(data):
    if data is None:
        return b""
    return data.replace(b"\r\n", b"\n").strip()


def requirements_sha256(data):
    return hashlib.sha256(normalize_requirements(data)).hexdigest()


def _normalize_member_name(name):
    # GNU tar 产物是 "./opt/..."，docker export 是 "opt/..."，统一去掉前缀
    return name[2:] if name.startswith("./") else name


def read_tar_member(tar_path, member_name):
    """流式读取 tar.gz 中的单个文件；找不到返回 None。"""
    target = _normalize_member_name(member_name)
    with tarfile.open(str(tar_path), "r|gz") as tf:
        for member in tf:
            if _normalize_member_name(member.name) == target and member.isfile():
                fileobj = tf.extractfile(member)
                if fileobj is not None:
                    return fileobj.read()
    return None


def _path_matches(name, path):
    return name == path or name.startswith(path + "/")


def _should_skip(name):
    for path in REPLACE_PATHS + DROP_PATHS:
        if _path_matches(name, path):
            return True
    return False


def _new_tarinfo(name, mode, is_dir=False, mtime=0):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    # 固定 mtime：源码内容不变时 overlay 结果逐字节一致，
    # 设备上的 ROOTFS_REV 才不会每次构建都变。
    info.mtime = int(mtime)
    if is_dir:
        info.type = tarfile.DIRTYPE
        info.size = 0
    return info


def _add_dir(tf, name, mtime=0):
    tf.addfile(_new_tarinfo(name.rstrip("/") + "/", 0o755, True, mtime))


def _add_bytes(tf, name, data, mode, mtime=0):
    info = _new_tarinfo(name, mode, False, mtime)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


def _read_source(path):
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return data


def _iter_source_entries(repo_root):
    """产出 (归档名, 源文件 Path 或 None, 直接数据或 None, 权限)。"""
    simple_files = (
        ("main.py", "./opt/flyai/main.py", 0o644),
        ("requirements.txt", "./opt/flyai/requirements.txt", 0o644),
        ("config/config.example.json", "./opt/flyai/config/config.example.json", 0o644),
    )
    for rel, archive_name, mode in simple_files:
        path = repo_root / rel
        if path.is_file():
            yield archive_name, path, None, mode

    for rel_root in ("flyai", "plugins"):
        root = repo_root / rel_root
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIR_NAMES)
            rel_dir = os.path.relpath(dirpath, str(repo_root)).replace(os.sep, "/")
            yield "./opt/flyai/" + rel_dir + "/", None, None, 0o755
            for filename in sorted(filenames):
                if filename.endswith(SKIP_FILE_SUFFIXES) or filename == ".DS_Store":
                    continue
                path = Path(dirpath) / filename
                rel = os.path.relpath(str(path), str(repo_root)).replace(os.sep, "/")
                mode = 0o755 if filename.endswith(".sh") else 0o644
                yield "./opt/flyai/" + rel, path, None, mode

    yield "./opt/flyai/start.sh", None, START_SH, 0o755


def overlay_rootfs(base_tar, out_tar, repo_root, compresslevel=6, verify=True):
    """生成新的 rootfs.tar.gz，返回统计信息。"""
    base_tar = Path(base_tar)
    out_tar = Path(out_tar)
    repo_root = Path(repo_root)
    if not base_tar.is_file():
        raise FileNotFoundError("找不到基础 rootfs: %s" % base_tar)
    if not (repo_root / "main.py").is_file():
        raise FileNotFoundError("repo_root 不像 FlyAI 仓库: %s" % repo_root)

    out_tar.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_tar) + ".tmp")
    started = time.time()
    copied = 0
    replaced = 0

    with open(str(tmp), "wb") as raw:
        # filename=""：不要把输出文件名写进 gzip 头，保证结果可复现
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as dst:
                with tarfile.open(str(base_tar), "r|gz") as src:
                    for member in src:
                        if _should_skip(member.name):
                            continue
                        data = src.extractfile(member) if member.isreg() else None
                        dst.addfile(member, data)
                        copied += 1

                for archive_name, path, data, mode in _iter_source_entries(repo_root):
                    if path is not None:
                        data = _read_source(path)
                    if data is None:
                        _add_dir(dst, archive_name)
                    else:
                        _add_bytes(dst, archive_name, data, mode)
                    replaced += 1

    os.replace(str(tmp), str(out_tar))
    elapsed = time.time() - started

    stats = {
        "copied": copied,
        "replaced": replaced,
        "bytes": out_tar.stat().st_size,
        "elapsed": elapsed,
        "symlinks": 0,
        "entries": 0,
    }
    if verify:
        with tarfile.open(str(out_tar), "r:gz") as tf:
            members = tf.getmembers()
        names = set(m.name for m in members)
        if "./opt/flyai/main.py" not in names:
            raise RuntimeError("新 rootfs 缺少 opt/flyai/main.py")
        stats["entries"] = len(members)
        stats["symlinks"] = sum(1 for m in members if m.issym())
        stats["hardlinks"] = sum(1 for m in members if m.islnk())
    return stats


def main():
    parser = argparse.ArgumentParser(description="把仓库源码覆盖进缓存的 arm64 rootfs")
    parser.add_argument("--base", required=True, help="基础 rootfs.tar.gz")
    parser.add_argument("--out", required=True, help="输出 rootfs.tar.gz")
    parser.add_argument(
        "--repo",
        default=str(Path(__file__).resolve().parents[2]),
        help="仓库根目录（默认自动推断）",
    )
    parser.add_argument("--compresslevel", type=int, default=6, help="gzip 压缩级别（1-9）")
    parser.add_argument("--no-verify", action="store_true", help="跳过输出完整性校验")
    parser.add_argument(
        "--check-requirements",
        action="store_true",
        help="仅检查 base 的 requirements.txt 是否与仓库一致，不一致返回 2",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo)
    if args.check_requirements:
        base_req = read_tar_member(args.base, "./opt/flyai/requirements.txt")
        repo_req = (repo_root / "requirements.txt").read_bytes()
        if normalize_requirements(base_req) != normalize_requirements(repo_req):
            print("requirements.txt 与 rootfs 不一致：需要重建 rootfs（WSL+qemu 或 arm64 机器）")
            return 2
        print("requirements.txt 一致")
        return 0

    stats = overlay_rootfs(
        args.base,
        args.out,
        repo_root,
        compresslevel=args.compresslevel,
        verify=not args.no_verify,
    )
    print(
        "rootfs 已生成：%s\n"
        "  复制条目 %d，替换源码 %d，符号链接 %d，条目总数 %d\n"
        "  体积 %.1f MB，耗时 %.1f 秒"
        % (
            args.out,
            stats["copied"],
            stats["replaced"],
            stats.get("symlinks", 0),
            stats.get("entries", 0),
            stats["bytes"] / 1024.0 / 1024.0,
            stats["elapsed"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
