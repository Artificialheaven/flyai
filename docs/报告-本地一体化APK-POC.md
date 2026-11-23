# 报告：本地一体化 APK POC（内置 Python 服务端）

- 日期：2026-11-24
- 分支：`local-app-poc`
- 产物：`FlyAI-POC-arm64.apk`（106MB，包名 `top.eastcloud.flyai.poc`，独立自签）
- 说明：本报告记录已完成、已验证与未验证项，避免把“能编译”误当成“真机跑通”

## 1. 方案选择

| 方案 | 结论 |
|---|---|
| Chaquopy（嵌入 CPython） | 放弃：`pydantic-core`/`uvloop` 等需要针对 Android bionic 的轮子，生态不全，改造面大 |
| PRoot + 完整 Linux rootfs | **采用**：直接复用 manylinux aarch64 轮子，业务代码零改动 |
| 继续纯客户端 | 保留为正式形态，POC 只做技术验证 |

## 2. 架构

```
APK (top.eastcloud.flyai.poc, targetSdk 28)
├─ assets/rootfs_payload.bin   101MB gzip（解包后 ≈350MB）
├─ assets/proot/{proot,loader,libtalloc,libandroid-shmem}  Termux 构建的 bionic 版
└─ MainActivity(WebView) + BootService(前台服务)
     └─ PRoot: proot --link2symlink -0 -r rootfs -b /dev -b /proc -b /sys
                -w /opt/flyai /bin/sh /opt/flyai/start.sh
          └─ /opt/flyai/.venv/bin/python main.py → 127.0.0.1:8000
```

- rootfs：Ubuntu 24.04 base（arm64）+ Python 3.12 + `requirements.txt` 全量依赖
  （fastapi 0.115.6 / pydantic 2.10.4 / uvicorn 0.34.0 / uvloop / Pillow …）
- 数据：`/opt/flyai/data` 位于应用私有目录，卸载即清除
- 首启解包用自研 `TarGzExtractor`（支持 ustar/pax/longname/软硬链），不依赖 toybox
- targetSdk 28 的原因：Android 10+ 对 targetSdk≥29 的应用禁止从数据目录 `execve`，
  PRoot 依赖该能力（与 Termux 一致）

## 3. 构建环境（本机 aarch64）

| 组件 | 版本/路径 |
|---|---|
| JDK | Temurin 21.0.13（`/workspace/tools/jdk21`） |
| Android SDK | platform 35 / build-tools 35.0.0（`/workspace/android-sdk`） |
| Gradle | 8.11.1（阿里云镜像分发） |
| Maven | 阿里云 google/public 镜像 |
| aapt2 | Google 仅发布 x86_64 → QEMU 用户态模拟（`aapt2-wrapper.sh` + `qemu-x86_64` + amd64 rootfs） |

产物签名：`apksigner` 验证 v1+v2 通过，证书 `CN=FlyAI POC`。

## 4. 已验证

- rootfs 在 chroot 中完整启动：`GET /` → 200，依赖版本正确
- **APK 内资源还原**：从 APK 取出 `assets/rootfs_payload.bin`，sha256 与构建用
  `rootfs.tar.gz` 完全一致；用 Java 解包器还原后再次启动服务 → 200
- 解包耗时：主机 1.8s（手机预计数十秒）
- APK 清单：包名 `top.eastcloud.flyai.poc`、`minSdk 23`、`targetSdk 28`、
  `usesCleartextTraffic=true`、前台服务与权限齐全
- WebView 加入文件选择回调（角色卡上传）、返回键、常亮

## 5. 未验证（必须真机确认）

1. **PRoot 在真机的执行**：bionic proot 依赖 `PROOT_LOADER`、SELinux 与 targetSdk 28
   的 exec 放行；本环境无 Android 设备，无法实测。
2. 首次启动解包 350MB 的耗时与存储占用（需 ~500MB 空闲）。
3. WebView 对本地 SSE 长连接、IndexedDB、文件上传的实际表现。
4. 后台生成在厂商省电策略下的存活率。
5. 下载类能力（导出卡/文件）未接 `DownloadListener`。

## 6. 安装与回退

- 下载：`/static/poc/FlyAI-POC-arm64.apk`（随本机服务提供）
- `adb install -r FlyAI-POC-arm64.apk`，或手机浏览器直接安装
- 与正式包包名不同，可共存；卸载即清理 rootfs 与数据
- 若真机 PROot 被拦截，回退方向：改用 Termux 环境验证、或引入 Chaquopy 精简依赖

## 7. 复现

见 `mobile/poc/README.md`：`fetch-proot.sh` → `build-rootfs.sh` → `npx cap add android`
→ `apply-android.sh` → `./gradlew assembleRelease`。
