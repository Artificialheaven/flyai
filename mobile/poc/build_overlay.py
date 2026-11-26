#!/usr/bin/env python3
"""生成 FlyAI POC 的代码增量包（rootfs overlay）与签名清单。

不依赖 APK/rootfs 编译：只读取仓库源码和已缓存的 base rootfs 里的
requirements.txt，因此断网构建机上也能在几秒内出包。

产物（默认 release/）：
    poc-overlay-<version>.tar.gz        # 只含 opt/flyai 代码/前端
    poc-update.json                     # 签名清单（GitHub latest/download 用）
    FlyAI-POC-update-<version>.zip      # 可选：手动导入包（清单+payload）

用法：
    .venv/Scripts/python mobile/poc/build_overlay.py \
        --version-code 6 --version-name 1.5 \
        --overlay-version 202611241530 \
        --tag poc-v1.5.1-202611241530 \
        --apk release/FlyAI-POC-arm64-v1.5.apk \
        --key "$USERPROFILE/.flyai/poc-ota-signing.key"
"""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

POC_DIR = Path(__file__).resolve().parent
REPO_ROOT = POC_DIR.parents[1]
DEFAULT_BASE = REPO_ROOT / ".poc-cache" / "base" / "rootfs.tar.gz"
DEFAULT_KEY = Path.home() / ".flyai" / "poc-ota-signing.key"
DEFAULT_PUBLIC_KEY = POC_DIR / "ota-public.pem"
DEFAULT_MANIFEST_URL = (
    "https://github.com/Artificialheaven/flyai/releases/latest/download/poc-update.json"
)

sys.path.insert(0, str(POC_DIR))
import rootfs_overlay  # noqa: E402


def warn(message):
    print("警告: " + message, flush=True)


def fail(message):
    print("\n错误: " + message, file=sys.stderr, flush=True)
    raise SystemExit(1)


def log(message):
    print("\n== " + message, flush=True)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def normalized_requirements(data):
    if data is None:
        return b""
    return data.replace(b"\r\n", b"\n").strip()


def requirements_sha256(data):
    return sha256_bytes(normalized_requirements(data))


def compute_code_rev(repo_root):
    """代码树稳定哈希：路径 + 权限 + 文件内容；不含 mtime/uid/gid。"""
    entries = []
    for name, path, data, mode in rootfs_overlay._iter_source_entries(Path(repo_root)):
        if path is not None:
            data = rootfs_overlay._read_source(path)
        if data is None:
            digest = b"d"
        else:
            digest = b"f" + hashlib.sha256(data).digest()
        entries.append((name, int(mode), digest))
    tree = hashlib.sha256()
    for name, mode, digest in sorted(entries, key=lambda item: item[0]):
        tree.update(name.encode("utf-8"))
        tree.update(b"\0")
        tree.update(("%o" % mode).encode("ascii"))
        tree.update(b"\0")
        tree.update(digest)
        tree.update(b"\n")
    return tree.hexdigest()[:16]


def write_payload(repo_root, out_path, compresslevel=6):
    """确定性 tar.gz：mtime=0、gzip 头不写文件名、条目顺序固定。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    with open(str(tmp), "wb") as raw:
        with gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", compresslevel=compresslevel, mtime=0
        ) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as dst:
                for name, path, data, mode in rootfs_overlay._iter_source_entries(
                    Path(repo_root)
                ):
                    if path is not None:
                        data = rootfs_overlay._read_source(path)
                    if data is None:
                        rootfs_overlay._add_dir(dst, name)
                    else:
                        rootfs_overlay._add_bytes(dst, name, data, mode)
    os.replace(str(tmp), str(out_path))
    return out_path


def read_base_requirements(base_rootfs):
    try:
        data = rootfs_overlay.read_tar_member(
            base_rootfs, "./opt/flyai/requirements.txt"
        )
    except Exception as exc:
        warn("读取 base requirements 失败: %s" % exc)
        return None
    if data is None:
        # rootfs 里没有 requirements.txt（或成员名不一致）：按未知处理，
        # 不能当成空文件，否则会误判为依赖变更而禁用 overlay。
        warn("base rootfs 中找不到 opt/flyai/requirements.txt")
        return None
    return normalized_requirements(data)


def canonical_payload(manifest):
    overlay = manifest.get("overlay") or {}
    full = manifest.get("full") or {}
    lines = [
        "flyai-poc-overlay-v1",
        str(manifest["version_code"]),
        str(manifest["overlay_version"]),
        str(manifest["code_rev"]),
        str(manifest["requirements_sha256"]),
        str(manifest["min_app_version_code"]),
        str(overlay.get("sha256") or "-"),
        str(overlay.get("size") or 0),
        str(full.get("sha256") or "-"),
        str(full.get("size") or 0),
        str(full.get("version_code") or 0),
        str(manifest["signing_key_id"]),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def sign_manifest(manifest, key_path):
    payload = canonical_payload(manifest)
    key_path = Path(key_path)
    if not key_path.is_file():
        fail(
            "找不到 OTA 私钥: %s\n"
            "  生成：openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 "
            "-out %s" % (key_path, key_path)
        )
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "manifest.txt"
        sig_file = Path(tmp) / "manifest.sig"
        data_file.write_bytes(payload)
        try:
            subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-sign",
                    str(key_path),
                    "-out",
                    str(sig_file),
                    str(data_file),
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError:
            fail("找不到 openssl；请安装 OpenSSL 或使用 --no-sign 仅调试")
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace")
            fail("签名失败: %s" % detail.strip())
        return base64.b64encode(sig_file.read_bytes()).decode("ascii")


def load_full_from_manifest(path, version_code):
    """从旧版（带完整 APK 的）清单里取出 full 段，供纯 overlay 清单复用。

    旧版本客户端（例如 1.17）看到 full 会先升级到同版本的完整 APK，
    装好后再检测 overlay，避免「只有 overlay、老版本永远升不上来」。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("读取 full 清单失败: %s" % exc)
    full = data.get("full")
    if not isinstance(full, dict):
        warn("清单里没有 full 段，忽略: %s" % path)
        return None
    try:
        code = int(full.get("version_code") or 0)
    except (TypeError, ValueError):
        code = 0
    if code != int(version_code):
        warn(
            "清单 full.version_code=%s 与本次 %s 不一致，忽略: %s"
            % (code, version_code, path)
        )
        return None
    url = str(full.get("url") or "").strip()
    sha = str(full.get("sha256") or "").strip()
    if not url or not sha:
        warn("清单 full 段缺少 url/sha256，忽略: %s" % path)
        return None
    return {
        "url": url,
        "size": int(full.get("size") or 0),
        "sha256": sha,
        "version": str(full.get("version") or data.get("version") or ""),
        "version_code": code,
    }


def apk_info(path, version_name, version_code, repo_url, tag):
    path = Path(path)
    if not path.is_file():
        fail("找不到 APK: %s" % path)
    name = path.name
    url = repo_url.rstrip("/") + "/releases/download/" + tag + "/" + name
    return {
        "url": url,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "version": version_name,
        "version_code": int(version_code),
    }


def build_manifest(args):
    overlay_path = Path(args.out_dir) / ("poc-overlay-%s.tar.gz" % args.version_name)
    write_payload(REPO_ROOT, overlay_path, compresslevel=args.compresslevel)
    overlay_size = overlay_path.stat().st_size
    overlay_sha = sha256_file(overlay_path)

    base_req = read_base_requirements(Path(args.base_rootfs))
    repo_req = normalized_requirements((REPO_ROOT / "requirements.txt").read_bytes())
    repo_req_hash = requirements_sha256(repo_req)
    base_req_hash = requirements_sha256(base_req) if base_req is not None else repo_req_hash
    overlay_allowed = base_req is None or base_req_hash == repo_req_hash
    if not overlay_allowed:
        warn(
            "requirements.txt 与 base rootfs 不一致，清单将只提供全量 APK："
            "base=%s repo=%s" % (base_req_hash[:12], repo_req_hash[:12])
        )

    code_rev = compute_code_rev(REPO_ROOT)
    overlay_url = (
        args.repo_url.rstrip("/")
        + "/releases/download/"
        + args.tag
        + "/"
        + overlay_path.name
    ) if args.repo_url and args.tag else ""
    manifest = {
        "schema": 1,
        "channel": "poc",
        "version": args.version_name,
        "version_code": int(args.version_code),
        "overlay_version": int(args.overlay_version),
        "code_rev": code_rev,
        "requirements_sha256": repo_req_hash if overlay_allowed else base_req_hash,
        "min_app_version_code": int(args.min_app_version),
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "notes": args.notes or "",
        "overlay": (
            {
                "url": overlay_url,
                "size": overlay_size,
                "sha256": overlay_sha,
            }
            if overlay_allowed
            else None
        ),
        "full": None,
        "signing_key_id": args.key_id,
        "sig_alg": "SHA256withRSA",
        "signature": "",
    }
    if args.apk:
        manifest["full"] = apk_info(
            args.apk, args.version_name, args.version_code, args.repo_url, args.tag
        )
    elif getattr(args, "full_manifest", None):
        manifest["full"] = load_full_from_manifest(
            args.full_manifest, args.version_code
        )
    if not args.no_sign:
        manifest["signature"] = sign_manifest(manifest, Path(args.key))
    return manifest, overlay_path


def write_manual_zip(out_dir, version_name, manifest, overlay_path):
    target = Path(out_dir) / ("FlyAI-POC-update-%s.zip" % version_name)
    tmp = Path(str(target) + ".tmp")
    with zipfile.ZipFile(str(tmp), "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.write(str(overlay_path), "payload.tar.gz", compress_type=zipfile.ZIP_STORED)
    os.replace(str(tmp), str(target))
    return target


def parse_args():
    parser = argparse.ArgumentParser(description="生成 POC 代码 overlay + 签名清单")
    parser.add_argument("--base-rootfs", default=str(DEFAULT_BASE), help="base rootfs.tar.gz")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "release"))
    parser.add_argument("--version-code", type=int, default=6)
    parser.add_argument("--version-name", default="1.5")
    parser.add_argument("--overlay-version", type=int, required=True)
    parser.add_argument("--min-app-version", type=int, default=None)
    parser.add_argument("--tag", default="", help="GitHub Release tag（用于拼 URL）")
    parser.add_argument(
        "--repo-url", default="https://github.com/Artificialheaven/flyai"
    )
    parser.add_argument("--apk", help="同版本完整 APK（填入 full 字段）")
    parser.add_argument(
        "--full-manifest",
        help="旧版带 APK 的 poc-update.json；纯 overlay 构建时复用其 full 段",
    )
    parser.add_argument("--notes", default="")
    parser.add_argument("--key", default=str(DEFAULT_KEY), help="OTA RSA 私钥（PEM）")
    parser.add_argument("--key-id", default="poc-ota-2026")
    parser.add_argument("--no-sign", action="store_true", help="仅调试：生成空签名")
    parser.add_argument("--no-manual-zip", action="store_true")
    parser.add_argument("--compresslevel", type=int, default=6)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.min_app_version is None:
        args.min_app_version = args.version_code
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log("生成 overlay")
    manifest, overlay_path = build_manifest(args)
    manifest_path = out_dir / "poc-update.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("overlay: %s (%.2f MiB)" % (overlay_path, overlay_path.stat().st_size / 1048576))
    print("code_rev: %s" % manifest["code_rev"])
    print("manifest: %s" % manifest_path)
    if not args.no_manual_zip:
        zip_path = write_manual_zip(out_dir, args.version_name, manifest, overlay_path)
        print("手动导入包: %s" % zip_path)
    if args.no_sign:
        warn("--no-sign：清单未签名，客户端会拒绝；仅用于调试")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
