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

## 构建（aarch64 Linux）

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
