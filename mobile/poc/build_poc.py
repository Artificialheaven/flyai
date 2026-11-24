#!/usr/bin/env python3
"""FlyAI 本地一体化 POC APK 构建（缓存 rootfs + 覆盖源码，无需 WSL/qemu）。

用法（仓库根目录，Windows / Linux 均可）：

    # 第一次：从已有的 POC APK 里播种 rootfs 与 proot，并指定 POC 签名
    .venv/Scripts/python mobile/poc/build_poc.py \
        --source-apk F:\\path\\FlyAI-POC-arm64-v1.5.apk \
        --keystore F:\\path\\poc-release.jks \
        --keystore-properties F:\\path\\keystore.properties

    # 之后改完代码直接重打（约 2-3 分钟）
    .venv/Scripts/python mobile/poc/build_poc.py

    # 只更新 rootfs，不编译 APK
    .venv/Scripts/python mobile/poc/build_poc.py --no-build

缓存目录：
    .poc-cache/base/rootfs.tar.gz    基础 rootfs（不修改）
    .poc-cache/latest/rootfs.tar.gz  叠加当前源码后的 rootfs
    .poc-cache/proot/                Termux PRoot 二进制
    .poc-build/                      Capacitor/Gradle 构建工程（可随时删）

只有 requirements.txt 变化时才需要重建基础 rootfs（WSL+qemu 或 arm64 机器）；
本脚本会检测到并中止，除非显式 --force-rootfs。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

# Windows 控制台默认 cp936，中文日志会乱码；统一切到 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

POC_DIR = Path(__file__).resolve().parent
REPO_ROOT = POC_DIR.parents[1]
CACHE_DIR = REPO_ROOT / ".poc-cache"
BUILD_DIR = REPO_ROOT / ".poc-build"
BASE_DIR = CACHE_DIR / "base"
BASE_ROOTFS = BASE_DIR / "rootfs.tar.gz"
META_FILE = BASE_DIR / "meta.json"
LATEST_DIR = CACHE_DIR / "latest"
LATEST_ROOTFS = LATEST_DIR / "rootfs.tar.gz"
PROOT_DIR = CACHE_DIR / "proot"

PROOT_FILES = ("proot", "loader", "libtalloc.so.2", "libandroid-shmem.so")
DEFAULT_OTA_PUBLIC_KEY = POC_DIR / "ota-public.pem"
DEFAULT_OTA_MANIFEST_URL = (
    "https://github.com/Artificialheaven/flyai/releases/latest/download/poc-update.json"
)


def ota_public_key_b64(path):
    """读取 PEM 公钥，返回 Java 内嵌用的 base64 DER（去掉头尾/换行）。"""
    path = Path(path)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    return "".join(
        line.strip() for line in text.splitlines() if "-----" not in line
    )


def root_build_gradle():
    """本地默认走阿里云镜像；CI 可设 POC_NO_CN_MIRRORS=1 只用官方仓库。"""
    if os.environ.get("POC_NO_CN_MIRRORS"):
        buildscript_repos = "        google()\n        mavenCentral()"
        all_repos = "        google()\n        mavenCentral()"
    else:
        buildscript_repos = (
            "        maven { url 'https://maven.aliyun.com/repository/google' }\n"
            "        maven { url 'https://maven.aliyun.com/repository/central' }\n"
            "        maven { url 'https://maven.aliyun.com/repository/gradle-plugin' }\n"
            "        google()\n"
            "        mavenCentral()"
        )
        all_repos = (
            "        maven { url 'https://maven.aliyun.com/repository/google' }\n"
            "        maven { url 'https://maven.aliyun.com/repository/central' }\n"
            "        google()\n"
            "        mavenCentral()"
        )
    return (
        "// 由 mobile/poc/build_poc.py 生成\n"
        "buildscript {\n"
        "    repositories {\n"
        + buildscript_repos
        + "\n    }\n"
        "    dependencies {\n"
        "        classpath 'com.android.tools.build:gradle:8.7.2'\n"
        "        classpath 'com.google.gms:google-services:4.4.2'\n"
        "    }\n"
        "}\n\n"
        'apply from: "variables.gradle"\n\n'
        "allprojects {\n"
        "    repositories {\n"
        + all_repos
        + "\n    }\n"
        "}\n\n"
        "task clean(type: Delete) {\n"
        "    delete rootProject.buildDir\n"
        "}\n"
    )


def wrapper_properties():
    url = os.environ.get(
        "POC_GRADLE_MIRROR",
        "https://mirrors.cloud.tencent.com/gradle/gradle-8.11.1-all.zip",
    )
    return (
        "distributionBase=GRADLE_USER_HOME\n"
        "distributionPath=wrapper/dists\n"
        "distributionUrl=" + url.replace(":", "\\:") + "\n"
        "networkTimeout=10000\n"
        "validateDistributionUrl=true\n"
        "zipStoreBase=GRADLE_USER_HOME\n"
        "zipStorePath=wrapper/dists\n"
    )


def log(message):
    print("\n== " + message, flush=True)


def warn(message):
    print("警告: " + message, flush=True)


def fail(message):
    print("\n错误: " + message, file=sys.stderr, flush=True)
    raise SystemExit(1)


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def extract_zip_member(zip_path, member_name, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as archive:
        with archive.open(member_name) as src, open(str(dest), "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)


def seed_cache(args):
    log("准备缓存（rootfs / proot）")
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    source_apk = Path(args.source_apk).resolve() if args.source_apk else None
    if source_apk is not None and not source_apk.is_file():
        fail("--source-apk 不存在: %s" % source_apk)

    new_base = not BASE_ROOTFS.is_file()
    if new_base:
        if source_apk is not None:
            print("从 APK 提取 rootfs_payload.bin → %s" % BASE_ROOTFS)
            extract_zip_member(source_apk, "assets/rootfs_payload.bin", BASE_ROOTFS)
        elif args.rootfs:
            rootfs = Path(args.rootfs).resolve()
            if not rootfs.is_file():
                fail("--rootfs 不存在: %s" % rootfs)
            print("复制 rootfs → %s" % BASE_ROOTFS)
            shutil.copyfile(str(rootfs), str(BASE_ROOTFS))
        else:
            fail(
                "缺少基础 rootfs 缓存。第一次请加 --rootfs <rootfs.tar.gz> 或 "
                "--source-apk <FlyAI-POC-*.apk>"
            )
    else:
        print("使用缓存 rootfs: %s" % BASE_ROOTFS)

    if new_base and LATEST_ROOTFS.exists():
        # 新播种的 base 与旧 latest 不对应，避免 --skip-overlay 误用
        LATEST_ROOTFS.unlink()

    PROOT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [name for name in PROOT_FILES if not (PROOT_DIR / name).is_file()]
    if missing:
        proot_dir = Path(args.proot_dir).resolve() if args.proot_dir else None
        for name in missing:
            dest = PROOT_DIR / name
            if source_apk is not None:
                print("从 APK 提取 proot/%s" % name)
                extract_zip_member(source_apk, "assets/proot/" + name, dest)
            elif proot_dir is not None:
                src = proot_dir / name
                if not src.is_file():
                    fail("--proot-dir 缺少 %s" % src)
                shutil.copyfile(str(src), str(dest))
            else:
                fail(
                    "缺少 proot 文件 %s：请加 --source-apk <POC apk> 或 --proot-dir <目录>"
                    % name
                )
    print("proot 就绪: %s" % PROOT_DIR)

    check_requirements(args.force_rootfs)


def check_requirements(force):
    repo_req = (REPO_ROOT / "requirements.txt").read_bytes()
    repo_hash = hashlib.sha256(
        repo_req.replace(b"\r\n", b"\n").strip()
    ).hexdigest()

    meta = {}
    if META_FILE.is_file():
        try:
            meta = json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    base_hash = meta.get("requirements_sha256")
    base_size = BASE_ROOTFS.stat().st_size
    if meta.get("base_size") and meta.get("base_size") != base_size:
        warn("基础 rootfs 文件已变化，重新读取 requirements.txt 校验")
        base_hash = None

    if not base_hash:
        # 第一次播种：从 rootfs 里读出 requirements.txt 做比对
        try:
            with tarfile.open(str(BASE_ROOTFS), "r|gz") as tf:
                for member in tf:
                    if member.name == "./opt/flyai/requirements.txt" and member.isfile():
                        data = tf.extractfile(member).read()
                        base_hash = hashlib.sha256(
                            data.replace(b"\r\n", b"\n").strip()
                        ).hexdigest()
                        break
        except Exception as exc:
            warn("读取 rootfs requirements.txt 失败: %s" % exc)

    if base_hash is None:
        warn("无法确认 rootfs 的依赖版本，跳过 requirements 一致性校验")
        base_hash = repo_hash

    meta.update(
        {
            "requirements_sha256": base_hash,
            "seeded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "base_size": base_size,
        }
    )
    META_FILE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if base_hash != repo_hash:
        message = (
            "requirements.txt 与缓存 rootfs 不一致。\n"
            "  依赖变化必须在 arm64 Linux（或 WSL2 + qemu）上重建 rootfs：\n"
            "    mobile/poc/build-rootfs.sh <工作目录>\n"
            "  然后用新的 rootfs.tar.gz 加 --rootfs 重新播种（先删 .poc-cache/base）。\n"
            "  仅源码变化不需要重建；确认要强行覆盖可加 --force-rootfs（不推荐）。"
        )
        if force:
            warn(message + "\n  已按 --force-rootfs 继续。")
        else:
            fail(message)
    else:
        print("requirements.txt 与缓存 rootfs 一致")


def prepare_overlay(args):
    log("覆盖最新源码到 rootfs")
    static_index = REPO_ROOT / "flyai" / "web" / "static" / "index.html"
    if not static_index.is_file():
        fail(
            "缺少 flyai/web/static/index.html，请先构建前端：\n"
            "  cd webapp && npm run build && npm run sync:static"
        )
    if args.skip_overlay and LATEST_ROOTFS.is_file():
        print("--skip-overlay：复用 %s" % LATEST_ROOTFS)
        return
    sys.path.insert(0, str(POC_DIR))
    import rootfs_overlay

    stats = rootfs_overlay.overlay_rootfs(
        BASE_ROOTFS,
        LATEST_ROOTFS,
        REPO_ROOT,
        compresslevel=args.compresslevel,
        verify=True,
    )
    print(
        "rootfs 已更新: %.1f MB，复制 %d 条，替换 %d 个源码文件，软链 %d 个，耗时 %.1f 秒"
        % (
            stats["bytes"] / 1024.0 / 1024.0,
            stats["copied"],
            stats["replaced"],
            stats.get("symlinks", 0),
            stats["elapsed"],
        )
    )


def link_node_modules(source, dest):
    dest = Path(dest)
    if dest.exists():
        return
    if os.path.lexists(str(dest)):
        try:
            os.rmdir(str(dest))
        except OSError:
            pass
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(source)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("node_modules 目录联接 → %s" % source)
            return
    else:
        try:
            os.symlink(str(source), str(dest), target_is_directory=True)
            print("node_modules 软链 → %s" % source)
            return
        except OSError:
            pass
    print("复制 node_modules（约 72MB，仅首次）")
    shutil.copytree(str(source), str(dest))


def prepare_www(build_mobile):
    src = REPO_ROOT / "flyai" / "web" / "static"
    dest = build_mobile / "www"
    shutil.rmtree(str(dest), ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(src), str(dest / "static"))
    shutil.copyfile(str(src / "index.html"), str(dest / "index.html"))
    login = src / "login.html"
    shutil.copyfile(
        str(login if login.is_file() else src / "index.html"),
        str(dest / "login.html"),
    )
    favicon = src / "favicon.svg"
    if favicon.is_file():
        shutil.copyfile(str(favicon), str(dest / "favicon.svg"))
    config = src / "config.json"
    if config.is_file():
        shutil.copyfile(str(config), str(dest / "config.json"))


def run_cap(build_mobile, cli, action):
    node = shutil.which("node")
    if not node:
        fail("找不到 node，请先安装 Node 18+")
    if not cli.is_file():
        fail("找不到 Capacitor CLI: %s（请先在 mobile/ 执行 npm ci）" % cli)
    print("npx cap %s android" % action)
    subprocess.run([node, str(cli), action, "android"], cwd=str(build_mobile), check=True)


def ensure_mobile_project(args, sdk):
    log("准备 Capacitor / Android 工程")
    build_mobile = BUILD_DIR / "mobile"
    build_mobile.mkdir(parents=True, exist_ok=True)
    source_mobile = REPO_ROOT / "mobile"

    for name in ("package.json", "package-lock.json"):
        src = source_mobile / name
        if src.is_file():
            shutil.copyfile(str(src), str(build_mobile / name))

    shutil.copyfile(
        str(POC_DIR / "android" / "capacitor.config.poc.json"),
        str(build_mobile / "capacitor.config.json"),
    )

    node_modules = source_mobile / "node_modules"
    if not node_modules.is_dir():
        fail("mobile/node_modules 不存在，请先执行：cd mobile && npm ci")
    link_node_modules(node_modules, build_mobile / "node_modules")

    prepare_www(build_mobile)

    cli = node_modules / "@capacitor" / "cli" / "bin" / "capacitor"
    android_dir = build_mobile / "android"
    if not android_dir.is_dir():
        run_cap(build_mobile, cli, "add")
    else:
        package_dir = android_dir / "app" / "src" / "main" / "java" / "top" / "eastcloud" / "flyai" / "poc"
        if not package_dir.is_dir():
            fail("旧构建目录包名不一致，请加 --clean 重新生成 %s" % BUILD_DIR)
    run_cap(build_mobile, cli, "copy")
    return build_mobile


def prepare_signing(args, build_mobile):
    keystore = (
        Path(args.keystore).resolve()
        if args.keystore
        else REPO_ROOT / "mobile" / "poc-release.jks"
    )
    properties = (
        Path(args.keystore_properties).resolve()
        if args.keystore_properties
        else REPO_ROOT / "mobile" / "keystore.properties"
    )
    if not keystore.is_file():
        fail(
            "缺少 POC 签名文件: %s\n"
            "  用 --keystore <poc-release.jks> 指定；首次可用 build-kit 里的签名。" % keystore
        )
    shutil.copyfile(str(keystore), str(build_mobile / "poc-release.jks"))

    if properties.is_file():
        shutil.copyfile(str(properties), str(build_mobile / "keystore.properties"))
        return
    store_password = os.environ.get("FLYAI_STORE_PASSWORD")
    key_password = os.environ.get("FLYAI_KEY_PASSWORD")
    key_alias = os.environ.get("FLYAI_KEY_ALIAS") or "flyai"
    if store_password and key_password:
        lines = [
            "storePassword=" + store_password,
            "keyPassword=" + key_password,
            "keyAlias=" + key_alias,
            "",
        ]
        (build_mobile / "keystore.properties").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        return
    fail(
        "缺少签名口令: 用 --keystore-properties 指定 properties 文件，"
        "或设置 FLYAI_STORE_PASSWORD / FLYAI_KEY_PASSWORD 环境变量"
    )


def apply_patches(args, build_mobile):
    log("应用 POC 补丁")
    android_dir = build_mobile / "android"
    app = android_dir / "app"
    rev = sha256_file(LATEST_ROOTFS)[:16]

    sys.path.insert(0, str(POC_DIR))
    import build_overlay

    code_rev = build_overlay.compute_code_rev(REPO_ROOT)
    public_key = ota_public_key_b64(args.ota_public_key)
    if not public_key:
        warn("未找到 OTA 公钥 %s：本次 APK 会拒绝所有增量包" % args.ota_public_key)

    java_dir = app / "src" / "main" / "java" / "top" / "eastcloud" / "flyai" / "poc"
    java_dir.mkdir(parents=True, exist_ok=True)
    for java_file in sorted((POC_DIR / "android").glob("*.java")):
        text = java_file.read_text(encoding="utf-8")
        text = text.replace("__ROOTFS_REV__", rev)
        text = text.replace("__CODE_REV__", code_rev)
        text = text.replace("__OTA_PUBLIC_KEY__", public_key)
        text = text.replace("__OTA_MANIFEST_URL__", args.ota_manifest_url)
        (java_dir / java_file.name).write_text(text, encoding="utf-8")

    shutil.copyfile(
        str(POC_DIR / "android" / "AndroidManifest.patched.xml"),
        str(app / "src" / "main" / "AndroidManifest.xml"),
    )
    shutil.copyfile(
        str(POC_DIR / "res" / "values" / "styles.patched.xml"),
        str(app / "src" / "main" / "res" / "values" / "styles.xml"),
    )
    file_paths = POC_DIR / "res" / "xml" / "file_paths.patched.xml"
    if file_paths.is_file():
        dest = app / "src" / "main" / "res" / "xml" / "file_paths.xml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(file_paths), str(dest))
    shutil.copyfile(
        str(POC_DIR / "android" / "app-build.gradle.patched"),
        str(app / "build.gradle"),
    )
    (android_dir / "build.gradle").write_text(root_build_gradle(), encoding="utf-8")
    (android_dir / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
        wrapper_properties(), encoding="utf-8"
    )

    gradle_props = android_dir / "gradle.properties"
    lines = []
    if gradle_props.is_file():
        for line in gradle_props.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("android.aapt2FromMavenOverride"):
                continue
            lines.append(line)
    if not any(line.strip() == "android.useAndroidX=true" for line in lines):
        lines.append("android.useAndroidX=true")
    gradle_props.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sdk_line = "sdk.dir=" + str(args.sdk).replace("\\", "/")
    (android_dir / "local.properties").write_text(sdk_line + "\n", encoding="utf-8")

    assets = app / "src" / "main" / "assets"
    proot_assets = assets / "proot"
    proot_assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(LATEST_ROOTFS), str(assets / "rootfs_payload.bin"))
    for name in PROOT_FILES:
        shutil.copyfile(str(PROOT_DIR / name), str(proot_assets / name))

    prepare_signing(args, build_mobile)
    print("rootfs 版本标记: %s" % rev)
    print("代码版本标记: %s" % code_rev)


def pick_java_home():
    def java_major(home):
        exe = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if not exe.is_file():
            return None
        try:
            result = subprocess.run(
                [str(exe), "-version"], capture_output=True, text=True
            )
            text = (result.stderr or "") + (result.stdout or "")
            match = re.search(r'version "(\d+)(?:\.(\d+))?', text)
            if not match:
                return None
            major = int(match.group(1))
            if major == 1 and match.group(2):
                major = int(match.group(2))
            return major
        except Exception:
            return None

    candidates = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]))
    candidates.extend(
        [
            Path("D:/Program/AndroidStudio/jbr"),
            Path("C:/Program Files/Android/Android Studio/jbr"),
        ]
    )
    jvm_root = Path("/usr/lib/jvm")
    if jvm_root.is_dir():
        candidates.extend(sorted(jvm_root.glob("*")))

    fallback = None
    for home in candidates:
        major = java_major(home)
        if major is not None and 17 <= major <= 23:
            if major == 21:
                return home, major
            if fallback is None:
                fallback = (home, major)
    if fallback is not None:
        return fallback

    # 最后尝试 PATH 上的 java（Gradle 会使用它）
    java = shutil.which("java")
    if java:
        try:
            result = subprocess.run([java, "-version"], capture_output=True, text=True)
            text = (result.stderr or "") + (result.stdout or "")
            match = re.search(r'version "(\d+)', text)
            if match and 17 <= int(match.group(1)) <= 23:
                return None, int(match.group(1))
        except Exception:
            pass
    fail("找不到可用的 JDK 17-23（Gradle 8.11 不支持 Java 24+）。请设置 JAVA_HOME 指向 JDK 21。")


def pick_android_sdk():
    candidates = [
        os.environ.get("ANDROID_SDK_ROOT"),
        os.environ.get("ANDROID_HOME"),
        str(Path.home() / "AppData" / "Local" / "Android" / "Sdk"),
        "C:/Android/Sdk",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "platforms").is_dir():
            return path
    fail("找不到 Android SDK（需要 platform 35 / build-tools 35）。请设置 ANDROID_SDK_ROOT。")


def build_apk(args, build_mobile, java_home):
    log("编译 release APK")
    android_dir = build_mobile / "android"
    gradlew = android_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not gradlew.is_file():
        fail("找不到 Gradle wrapper: %s" % gradlew)
    if os.name != "nt":
        try:
            os.chmod(str(gradlew), 0o755)
        except OSError:
            pass

    env = os.environ.copy()
    if java_home is not None:
        env["JAVA_HOME"] = str(java_home)
    else:
        # 回退到 PATH 上的 java 时，清掉可能指向 Java 24+ 的 JAVA_HOME
        env.pop("JAVA_HOME", None)
    env["ANDROID_SDK_ROOT"] = str(args.sdk)
    env["ANDROID_HOME"] = str(args.sdk)

    gradle_args = [
        "-PpocVersionCode=%d" % args.version_code,
        "-PpocVersionName=%s" % args.version_name,
        "assembleRelease",
    ]
    if os.name == "nt":
        command = subprocess.list2cmdline([str(gradlew)] + gradle_args)
        subprocess.run(command, cwd=str(android_dir), env=env, shell=True, check=True)
    else:
        subprocess.run([str(gradlew)] + gradle_args, cwd=str(android_dir), env=env, check=True)

    built = android_dir / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
    if not built.is_file():
        fail("构建结束但找不到 APK: %s" % built)

    if args.out:
        output = Path(args.out).resolve()
    else:
        output = REPO_ROOT / "release" / ("FlyAI-POC-arm64-v%s.apk" % args.version_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(built), str(output))

    digest = sha256_file(output)
    print("\nAPK 已生成")
    print("  路径: %s" % output)
    print("  体积: %.1f MB" % (output.stat().st_size / 1024.0 / 1024.0))
    print("  SHA256: %s" % digest)
    print("  versionCode=%d versionName=%s" % (args.version_code, args.version_name))
    return output


def parse_args():
    parser = argparse.ArgumentParser(description="缓存 rootfs + 覆盖源码，构建 FlyAI POC APK")
    parser.add_argument("--rootfs", help="首次播种用的 rootfs.tar.gz")
    parser.add_argument("--source-apk", help="首次播种用的已构建 POC APK（提取 rootfs 与 proot）")
    parser.add_argument("--proot-dir", help="proot 二进制目录（含 proot/loader/libtalloc.so.2/libandroid-shmem.so）")
    parser.add_argument("--keystore", help="POC 签名 jks（默认 mobile/poc-release.jks）")
    parser.add_argument("--keystore-properties", help="签名口令 properties（默认 mobile/keystore.properties）")
    parser.add_argument("--version-code", type=int, default=6)
    parser.add_argument("--version-name", default="1.5")
    parser.add_argument("--out", help="APK 输出路径（默认 release/FlyAI-POC-arm64-v<版本>.apk）")
    parser.add_argument("--compresslevel", type=int, default=6)
    parser.add_argument("--skip-overlay", action="store_true", help="复用上次覆盖好的 rootfs")
    parser.add_argument("--force-rootfs", action="store_true", help="requirements 不一致时仍继续（不推荐）")
    parser.add_argument("--no-build", action="store_true", help="只生成 rootfs，不编译 APK")
    parser.add_argument("--clean", action="store_true", help="删除 .poc-build 后重新生成 Android 工程")
    parser.add_argument(
        "--ota-public-key",
        default=str(DEFAULT_OTA_PUBLIC_KEY),
        help="OTA 签名公钥 PEM（默认 mobile/poc/ota-public.pem）",
    )
    parser.add_argument(
        "--ota-manifest-url",
        default=DEFAULT_OTA_MANIFEST_URL,
        help="客户端检查更新的清单地址",
    )
    parser.add_argument("--overlay", action="store_true", help="APK 构建后同时生成 OTA overlay/清单")
    parser.add_argument("--overlay-version", type=int, default=0, help="overlay 版本（默认当前时间 YYYYMMDDHHMM）")
    parser.add_argument("--release-tag", default="", help="GitHub Release tag（生成下载 URL）")
    parser.add_argument("--overlay-notes", default="", help="更新说明")
    parser.add_argument("--ota-key", default=str(Path.home() / ".flyai" / "poc-ota-signing.key"))
    return parser.parse_args()


def main():
    args = parse_args()
    if args.clean:
        log("清理构建工程 %s" % BUILD_DIR)
        shutil.rmtree(str(BUILD_DIR), ignore_errors=True)

    seed_cache(args)
    prepare_overlay(args)
    if args.no_build:
        print("\nrootfs 已就绪，未编译 APK：%s" % LATEST_ROOTFS)
        return

    args.sdk = pick_android_sdk()
    java_home, java_major = pick_java_home()
    print("使用 Android SDK: %s" % args.sdk)
    print("使用 JDK: %s (%s)" % (java_home if java_home else "PATH", java_major))

    build_mobile = ensure_mobile_project(args, args.sdk)
    apply_patches(args, build_mobile)
    output = build_apk(args, build_mobile, java_home)
    if args.overlay:
        overlay_version = args.overlay_version or int(time.strftime("%Y%m%d%H%M"))
        tag = args.release_tag or ("poc-v%s-%s" % (args.version_name, overlay_version))
        cmd = [
            sys.executable,
            str(POC_DIR / "build_overlay.py"),
            "--version-code", str(args.version_code),
            "--version-name", args.version_name,
            "--overlay-version", str(overlay_version),
            "--tag", tag,
            "--apk", str(output),
            "--notes", args.overlay_notes,
        ]
        if args.ota_key:
            cmd.extend(["--key", str(args.ota_key)])
        log("生成 OTA overlay / 签名清单")
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
