#!/usr/bin/env python3
"""FlyAI 一键发布：构建前端 → 编译 APK → 打包源码 zip。

用法（在仓库根目录）：
    .venv/Scripts/python scripts/release.py                 # 全流程
    .venv/Scripts/python scripts/release.py --skip-apk      # 只构建前端 + 源码 zip
    .venv/Scripts/python scripts/release.py --skip-web --skip-apk   # 只打源码 zip

产物统一输出到仓库根目录 release/（已在 .gitignore 中忽略）：
    release/FlyAI-<version>.apk
    release/flyai-src-<version>.zip

版本号以 mobile/android/variables.gradle 的 appVersionName 为准，脚本不写死。
源码 zip = git 已跟踪文件 + mobile/android 源码树（未入库但编译必需），
排除机密（config/config.json、keystore、*.jks 等）与生成物（node_modules、build 等）。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = REPO_ROOT / "webapp"
MOBILE_DIR = REPO_ROOT / "mobile"
ANDROID_DIR = MOBILE_DIR / "android"
VARIABLES_GRADLE = ANDROID_DIR / "variables.gradle"
APK_PATH = ANDROID_DIR / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
KEYSTORE_PROPS = ANDROID_DIR / "keystore.properties"
RELEASE_DIR = REPO_ROOT / "release"

# 任意层级命中的目录名（源码 zip 与 git 跟踪文件都过滤）
EXCLUDED_DIRS = {
    "__pycache__",
    ".venv",
    ".git",
    ".idea",
    "node_modules",
    "dist",
    "build",
    ".gradle",
    ".cxx",
    ".kotlin",
    "capacitor-cordova-android-plugins",
    ".smoke-quarantine",
}

EXCLUDED_FILES = {
    "frpc.toml",
    "keystore.properties",
    "local.properties",
    ".DS_Store",
    "Thumbs.db",
}

EXCLUDED_SUFFIXES = (".pyc", ".apk", ".jks", ".keystore")

# 相对仓库根的前缀（机密、运行数据、生成物、本地备份）
EXCLUDED_PREFIXES = (
    "config/config.json",
    "data/",
    "release/",
    "show/",
    "test/temp/",
    "webroot/",
    "tools/frp/",
    "mobile/android/app/src/main/assets/public/",
)


def log(message):
    print("[release] " + message, flush=True)


def die(message, code=1):
    print("[release] 错误: " + message, file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd, cwd):
    log("$ %s  (cwd=%s)" % (cmd, cwd.relative_to(REPO_ROOT)))
    try:
        subprocess.run(cmd, cwd=str(cwd), shell=True, check=True)
    except FileNotFoundError:
        die("找不到命令：%s（请确认已安装 Node.js 并在 PATH 中）" % cmd)
    except subprocess.CalledProcessError as exc:
        die("命令失败（退出码 %s）：%s" % (exc.returncode, cmd))


def read_version():
    try:
        text = VARIABLES_GRADLE.read_text(encoding="utf-8")
    except OSError as exc:
        die("读取版本号失败：%s: %s" % (VARIABLES_GRADLE, exc))
    match = re.search(r"appVersionName\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        die("无法从 %s 解析 appVersionName" % VARIABLES_GRADLE)
    return match.group(1)


def is_excluded(rel):
    """rel 为仓库根相对路径（posix 分隔）。"""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    name = parts[-1]
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    if name in EXCLUDED_FILES:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    return any(rel.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def git_files():
    """git 已跟踪文件；有未提交改动时只警告（zip 取工作区内容）。"""
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if status.returncode != 0:
        die("git status 失败：%s" % status.stderr.decode("utf-8", "replace").strip())
    if status.stdout.strip():
        log("警告：工作区有未提交改动，源码 zip 将包含当前工作区内容")

    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if listing.returncode != 0:
        die("git ls-files 失败：%s" % listing.stderr.decode("utf-8", "replace").strip())
    files = []
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace")
        if is_excluded(rel):
            continue
        path = REPO_ROOT / rel
        if path.is_file():
            files.append(path)
    return files


def android_files():
    """mobile/android 源码树（该目录未纳入 git，但编译 APK 必需）。"""
    if not ANDROID_DIR.is_dir():
        die("找不到 mobile/android 目录：%s" % ANDROID_DIR)
    files = []
    for root, dirs, names in os.walk(str(ANDROID_DIR)):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in names:
            path = Path(root) / name
            rel = path.relative_to(REPO_ROOT).as_posix()
            if is_excluded(rel):
                continue
            files.append(path)
    return files


def build_web():
    if shutil.which("npm") is None:
        die("未找到 npm，请先安装 Node.js")
    run("npm run build", WEBAPP_DIR)
    run("npm run sync:static", WEBAPP_DIR)


def build_apk(version):
    if shutil.which("npm") is None:
        die("未找到 npm，请先安装 Node.js")
    if not KEYSTORE_PROPS.is_file():
        log("提示：%s 不存在，release 签名可能失败" % KEYSTORE_PROPS.relative_to(REPO_ROOT))
    run("npm run build:release", MOBILE_DIR)
    if not APK_PATH.is_file():
        die("编译完成但未找到 APK：%s" % APK_PATH)
    dest = RELEASE_DIR / ("FlyAI-%s.apk" % version)
    shutil.copy2(str(APK_PATH), str(dest))
    log("APK -> %s（%.1f MB）" % (dest, dest.stat().st_size / 1024 / 1024))
    return dest


def build_source_zip(version):
    entries = {}
    for path in git_files() + android_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if is_excluded(rel):
            continue
        entries[rel] = path

    target = RELEASE_DIR / ("flyai-src-%s.zip" % version)
    raw_size = 0
    with zipfile.ZipFile(str(target), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(entries):
            path = entries[rel]
            try:
                zf.write(str(path), rel)
                raw_size += path.stat().st_size
            except OSError as exc:
                die("写入压缩包失败：%s: %s" % (rel, exc))
    log(
        "源码 zip -> %s（%d 个文件，压缩前 %.1f MB，压缩后 %.1f MB）"
        % (target, len(entries), raw_size / 1024 / 1024, target.stat().st_size / 1024 / 1024)
    )
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description="FlyAI 发布：前端构建 + APK 编译 + 源码 zip")
    parser.add_argument("--skip-web", action="store_true", help="跳过前端构建（webapp）")
    parser.add_argument("--skip-apk", action="store_true", help="跳过 APK 编译（mobile）")
    parser.add_argument("--skip-zip", action="store_true", help="跳过源码 zip 打包")
    args = parser.parse_args(argv)

    version = read_version()
    log("版本号：%s（来自 %s）" % (version, VARIABLES_GRADLE.relative_to(REPO_ROOT)))
    RELEASE_DIR.mkdir(exist_ok=True)

    outputs = []
    if not args.skip_web:
        build_web()
    if not args.skip_apk:
        outputs.append(build_apk(version))
    if not args.skip_zip:
        outputs.append(build_source_zip(version))

    if outputs:
        log("完成，产物：")
        for path in outputs:
            log("  " + str(path))
    else:
        log("完成（所有步骤均已跳过）")


if __name__ == "__main__":
    main()
