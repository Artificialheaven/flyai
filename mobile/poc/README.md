# FlyAI 本地一体化 APK（POC）

在设备本地运行完整 FlyAI 服务端：APK 内置 Debian 用户态（Python 3.12 + 依赖），
用 PRoot 在非 root 环境启动 FastAPI，WebView 连接 `http://127.0.0.1:8000`。
不依赖远程服务器，也不使用 Chaquopy（避免 pydantic-core 等原生轮子的 Android 适配问题）。

- 包名：`top.eastcloud.flyai.poc`（与正式包隔离，可共存）
- 签名：独立自签证书（`CN=FlyAI POC`），仅供侧载测试
- 目标 SDK：28（Android 10+ 对 targetSdk≥29 的应用禁止从数据目录 `execve`，PRoot 依赖该能力）
- 体积：APK ≈ 106MB；首次启动解包 ≈ 350MB

## 架构

```
MainActivity (WebView)
  └─ 前台服务 BootService
       ├─ 首次启动：解包 assets/rootfs_payload.bin（tar.gz，约 350MB）
       ├─ 解包 assets/proot/{proot,loader,libtalloc,libandroid-shmem} 到 filesDir
       └─ PRoot 启动 rootfs：
            proot --link2symlink -0 -r rootfs -b /dev -b /proc -b /sys \
                  -w /opt/flyai /bin/sh /opt/flyai/start.sh
                └─ .venv/bin/python main.py  → 127.0.0.1:8000
```

- `PROOT_LOADER` / `PROOT_TMP_DIR` / `LD_LIBRARY_PATH` 指向 `filesDir/proot`
- rootfs 里 `/opt/flyai` 是完整仓库（已剔除 tests/docs/node_modules 等）
- 数据目录 `/opt/flyai/data` 位于应用私有存储，卸载即清除

## 增量更新

代码/前端更新走 GitHub Release 的 rootfs overlay（约 2.7 MB），不重装 APK；
依赖或 native 变化时回退全量 APK。设计见 `docs/POC增量更新方案书.md`。

OTA 签名：

- 私钥默认 `%USERPROFILE%\.flyai\poc-ota-signing.key`（不入库）；
- 公钥 `mobile/poc/ota-public.pem` 随 APK 构建注入；
- CI 用 Secret `POC_OTA_SIGNING_KEY`（私钥 PEM 的 base64）签名；
- 重新生成密钥后必须同步更新公钥并重打 APK。

## 构建机架构说明

- **APK 编译**不需要 ARM：x86_64 上 aapt2 是原生二进制，反而更简单；
  只有 rootfs（要装 arm64 的 Python/依赖）需要 arm64 环境。
- 三种生成 rootfs 的方式：
  1. aarch64 Linux + chroot：`build-rootfs.sh`（最快）
  2. 任意架构 + Docker（推荐 x86_64）：`build-rootfs-docker.sh`
  3. GitHub Actions 公共 arm64 runner（`runs-on: ubuntu-24.04-arm`），产物上传 artifact

## 本地缓存构建（推荐，Windows 也可，无需 WSL/qemu）

前提：已有一份 `rootfs.tar.gz`（或任意已构建的 `FlyAI-POC-*.apk`）、POC 签名
`poc-release.jks` 与口令 properties。首次把文件喂给脚本播种，之后改代码直接重打：

```bash
# 首次：从现成 APK 提取 rootfs/proot，并指定签名
.venv/Scripts/python mobile/poc/build_poc.py \
  --source-apk /path/FlyAI-POC-arm64-v1.5.apk \
  --keystore /path/poc-release.jks \
  --keystore-properties /path/keystore.properties

# 之后（约 2-3 分钟出包）
.venv/Scripts/python mobile/poc/build_poc.py

# 只更新 rootfs，不编译 APK
.venv/Scripts/python mobile/poc/build_poc.py --no-build
```

工作原理：

- `.poc-cache/base/rootfs.tar.gz` 只读基底；`rootfs_overlay.py` 流式重写 tar，
  只替换 `opt/flyai/{flyai,plugins}`、`main.py`、`requirements.txt`、`start.sh`
  与 `config/config.example.json`，保留 `.venv`、`config`、`data`、符号链接、
  硬链接和权限。**不要在 Windows 上解包再重打**，会丢 600+ 个软链。
- `.poc-build/` 是可复用的 Capacitor/Gradle 工程，删除后会自动重建。
- APK 里的 `rootfs_payload.bin` 不含用户 `data/` 与 `config/config.json`；
  `BootService` 按注入的 `ROOTFS_REV` 判断版本，升级时只替换代码目录，
  保留聊天记录与 API Key。首次安装仍会完整解包约 350MB。
- 只有 `requirements.txt` 变化才需要重建 rootfs（WSL2 + qemu 或 arm64 机器），
  脚本会检测并中止；确认可用 `--force-rootfs`（不推荐）。

## GitHub Actions

`.github/workflows/poc-apk.yml` 可在 GitHub 上出包（手动触发或推送 `poc-*` tag）：

- runner 用 `ubuntu-24.04`（26.04 暂无 Python 3.9 构建包）；base rootfs 走
  Actions cache（key = `requirements.txt` 哈希）。
- 缓存命中时只做源码覆盖 + APK 打包（约 3–5 分钟）；未命中时用
  Docker + `tonistiigi/binfmt` 在 x86_64 上构建 arm64 rootfs（约 30–50 分钟）。
  不用宿主 chroot：CI 上 `/dev`、DNS 与 binfmt 都不稳定。
- APK 作为 workflow artifact 上传，保留 7 天。

需要在仓库 Secrets 中配置：

| Secret | 内容 |
|---|---|
| `POC_KEYSTORE_B64` | `poc-release.jks` 的 base64（不要换行输出问题） |
| `POC_STORE_PASSWORD` | keystore 口令 |
| `POC_KEY_PASSWORD` | key 口令 |
| `POC_KEY_ALIAS` | 别名，默认 `flyai` |

生成 base64：

```bash
# Linux / macOS
base64 -w0 mobile/poc-release.jks
# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("mobile\poc-release.jks"))
```

## 构建（aarch64 Linux）

> 该流程用于**重新生成基础 rootfs**（依赖变化时）。日常打 APK 直接用上面的
> `build_poc.py` 即可，它在 Linux/Windows 上都能从缓存 rootfs 出包。
> `apply-android.sh` 已由 `build_poc.py` 取代，仅保留给旧流程。

前置：JDK 21、Android SDK（platform 35 / build-tools 35）、Node 18+、`mobile/` 已 `npm ci`。

```bash
# 1) 生成 rootfs（脚本内使用 chroot + apt + pip，首次约 10 分钟）
mobile/poc/build-rootfs.sh /workspace/poc

# 2) 生成 Android 工程并应用 POC 补丁
cd mobile
npx cap add android
mobile/poc/apply-android.sh          # 包名/权限/服务/Java 源码/资源

# 3) 构建签名包
cd android && ./gradlew assembleRelease
```

### aapt2 注意（arm64 构建机）

Google 的 `aapt2` 只有 x86_64 版本。脚本用 QEMU 用户态模拟：

```properties
# android/gradle.properties
android.aapt2FromMavenOverride=/workspace/poc/aapt2-bin/aapt2
```

`aapt2-bin/aapt2` 是一个包装脚本，内容见 `mobile/poc/aapt2-wrapper.sh`，
依赖 `qemu-x86_64` 与 x86_64 基础 rootfs（`/workspace/poc/x86root`）。

## 已验证

- rootfs 在 chroot 中可启动完整服务：`GET /` 返回 200（Python 3.12 / FastAPI 0.115.6 / pydantic 2.10.4）
- Java 版 tar.gz 解包器：APK 内资源 → 解包耗时约 1.8s（主机），解包结果为可用 rootfs
- APK 签名（v1+v2）、包名、targetSdk、资源清单均已核对
- `assets/rootfs_payload.bin` 与构建用 `rootfs.tar.gz` sha256 完全一致

## 未验证 / 已知限制

- **未在真机运行**：当前环境没有 Android 设备/可用模拟器，PRoot 的 bionic 二进制
  与 SELinux/exec 行为只能按 Termux 的已验证方案推断
- 下载类功能（导出角色卡、保存文件）未接 `DownloadListener`
- 未集成 Capacitor 原生插件（通知/状态栏定制），仅基础 WebView
- 首次启动解包 + Python 冷启动预计 10–60 秒（取决于闪存）
- 目标 SDK 28 无法上架 Google Play，仅适合侧载
- 后台长时间生成受 Android 省电策略影响，POC 仅靠前台服务保活

## 安装

```
adb install -r FlyAI-POC-arm64.apk
```

或手机浏览器打开下载地址直接安装（需允许“安装未知来源应用”）。
首次启动后按网页提示注册管理员账号，数据全部保存在设备本地。
