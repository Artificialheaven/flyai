# FlyAI 本地一体化 POC 增量更新方案书

> 状态：核心已实现（`feature/poc-incremental-update`），待真机 OTA 联调
> 日期：2026-11-24
> 适用分支：`feature/poc-incremental-update`（从 `local-app-poc` 切出）
> 适用包名：`top.eastcloud.flyai.poc`
> 相关代码：`mobile/poc/build_poc.py`、`mobile/poc/build_overlay.py`、`mobile/poc/rootfs_overlay.py`、`mobile/poc/android/{BootService,OtaState,OtaManifest,UpdateService,MainActivity}.java`、`webapp/src/services/pocUpdate.ts`、`.github/workflows/poc-apk.yml`

## 0. 实现进度

已在 `feature/poc-incremental-update` 落地核心闭环：

- **构建/签名**：`mobile/poc/build_overlay.py` 生成确定性代码 overlay、`poc-update.json`、RSA 签名和手动导入 zip；`build_poc.py` 注入 `ROOTFS_REV`、`CODE_REV`、OTA 公钥和清单 URL。
- **应用/回滚**：`BootService` + `OtaState` 支持 `state.json` 迁移、pending overlay、代码备份、requirements/APK 版本闸门、服务健康检查、`markReady` 看门狗和自动回滚。
- **下载/全量**：`UpdateService` 拉清单、验签、下载 overlay/完整 APK、进度通知；完整 APK 通过 FileProvider 调系统安装器。
- **前端**：设置 → 通用新增“本地更新”行；启动后 `markReady` + 延迟检查；`pocUpdate.ts` 经带 token 的 `FlyAIPoc` 桥调用原生，卡片 iframe 拿不到 token。
- **发布**：GitHub Actions 构建 APK 后生成并签署 overlay，创建 Release 上传 APK/overlay/清单。

尚未完成/待真机验证：

- 真机 OTA（下载 → 重启应用 → 数据保留 → 回滚）全流程联调；
- 手动导入 zip 的 UI（构建已产出，客户端暂未接导入入口）；
- APK 二进制差分包（仍按 §12 的 P2 预留）；
- CI Secret `POC_OTA_SIGNING_KEY` 与公钥的部署确认。

> 本次 APK 含 native `UpdateService`/返回键改动，必须先完整安装一次；之后 Python/前端更新才能走约 2.7 MB 的 overlay。OTA 私钥默认在 `%USERPROFILE%\.flyai\poc-ota-signing.key`，公钥已放到 `mobile/poc/ota-public.pem`；私钥不要入库，CI 用 Secret 注入。

---

## 1. 背景与目标

### 1.1 现状

POC APK 是一个自包含的 Android 安装包：

- APK 内 `assets/rootfs_payload.bin` 是完整的 Ubuntu arm64 rootfs（Python 3.12 + `.venv` + FlyAI 源码 + 前端静态资源）；
- 首次启动由 `BootService` 解包到 `filesDir/rootfs`；
- 用 PRoot 启动 `127.0.0.1:8000` 的 FastAPI；
- WebView 加载本地服务。

当前升级逻辑是：

1. `build_poc.py` 把整份 rootfs 压成 `rootfs_payload.bin`，并注入 `ROOTFS_REV`；
2. `BootService` 发现设备上的 `.installed` 与 APK 里的 `ROOTFS_REV` 不一致时，删除 `opt/flyai/{flyai,plugins}` 等代码目录；
3. 然后把 **整份 105 MB 的 rootfs payload** 重新解压到设备（保留 `.venv`、`config`、`data`）。

问题：

- 即使只改一行 Python 或一个前端文件，也要重新下载/安装约 105 MB 的 APK；
- 即使只更新代码，也要在设备上重新解压整份 rootfs；
- 没有更新检查、下载、校验、回滚机制，只能手动侧载。

### 1.2 目标

1. **代码/前端更新不再重装 APK**：通过 GitHub Release 发布约 2.7 MB 的代码增量包，客户端检测、下载、校验后在本地覆盖更新；
2. **需要全量时仍可更新 APK**：依赖（`requirements.txt`）或 native 壳变化时，客户端可以下载并调起系统安装器安装完整 APK；
3. **保留数据**：更新不触碰 `.venv`、`config/config.json`、`data/`，聊天记录、API Key、模型配置保持不变；
4. **可回滚**：下载损坏、签名不符、服务起不来、前端启动失败，都能恢复上一版本；
5. **安全**：更新包和清单必须签名，客户端只接受持有更新私钥的一方发布的包；
6. **可离线/可手动**：网络不可用时不影响启动；支持手动导入更新包作为 GitHub 的兜底。

### 1.3 非目标

- 不做真正的 APK 二进制差分包（bsdiff）——本期全量 APK 作为兜底；差分包留作后续可选项（见 §12）；
- 不做逐文件增量——本期使用“全量代码 overlay”（约 2.7 MB），后续可优化到只传变化文件（约 0.6 MB）；
- 不更新系统库、`.venv`、PRoot、native Java 代码——这些必须走完整 APK；
- 不做 iOS、不做远程优先加载、不改变现有 Python 服务端业务逻辑。

### 1.4 术语

| 术语 | 含义 |
|---|---|
| 基础包 / base | APK 内置的完整 rootfs（`rootfs_payload.bin`），由 `ROOTFS_REV` 标识 |
| 代码增量包 / overlay | 只含 `opt/flyai` 代码、插件和前端静态资源的 tar.gz（约 2.7 MB） |
| 代码版本 / `code_rev` | 代码树的稳定哈希，用于判断当前设备上的代码是否已是最新 |
| 全量包 | 完整 POC APK（约 105 MB），用于依赖或 native 壳变化 |
| 待生效包 / pending | 已下载、校验通过、等待下次启动应用的 overlay |
| 回滚备份 / backup | 应用 overlay 前保存的旧代码目录 |
| `requirements_sha256` | base rootfs 中 `requirements.txt` 的哈希；overlay 只能应用到依赖一致的 base |

---

## 2. 可行性结论与实测数据

### 2.1 结论

**方案可行，且不需要 Android 安装器参与日常代码更新。**

`BootService` 已经把 rootfs 解包到 App 私有目录，Python 代码和前端都是普通文件，App 有写权限。因此“运行时覆盖代码”天然可行；需要补的是版本状态、下载校验、原子替换和回滚。

### 2.2 实测数据（2026-11-24，当前分支）

| 项目 | 大小 |
|---|---:|
| 完整 POC APK | 104.9 MiB |
| 其中 `assets/rootfs_payload.bin` | 100.5 MiB（已 gzip；APK 内只再压 1%） |
| 其中 `assets/public`（Capacitor 前端副本，POC 实际不读） | 2.2 MiB（压缩后） |
| 仅代码+前端 overlay（`flyai`、`plugins`、`main.py`、`requirements.txt`、`start.sh`、`config.example`） | **2.7 MiB**（原始 7.5 MiB） |
| 仅 Python 代码（不含前端） | 1.2 MiB |
| 只改前端（当前提交） | 628 KiB |
| 同工具链新旧 rootfs payload 的 bsdiff | 2.05 MiB |
| 整 APK 二进制差分（估算） | 2.5–3 MiB |

> 说明：拿 `tester/FlyAI-POC-arm64-v1.5.apk` 做 bsdiff 得到 99.79 MiB，是因为两边 gzip 实现不同，压缩流从第 9 个字节就分叉，不能作为增量基线。用同一个 `rootfs_overlay.py` 和同一个 base rootfs 生成的新旧 payload，差异只在最后约 2.3 MB，bsdiff 为 2.05 MiB。

### 2.3 直接收益

- 日常前端/Python 更新：**105 MB → 2.7 MB**，不需要重新安装 APK；
- 设备解包量：**350 MB（全量）→ 7.5 MB（overlay）**；
- 更新后只重启本地 Python 服务，不重启 Android 安装流程；
- 聊天记录、API Key、配置全程保留。

---

## 3. 总体设计

### 3.1 架构图

```
┌──────────────────────── GitHub Releases ────────────────────────┐
│  poc-update.json                 ← 签名清单（最新版本）          │
│  poc-overlay-<version>.tar.gz    ← 代码增量包（约 2.7 MB）       │
│  FlyAI-POC-arm64-v<version>.apk  ← 全量包（约 105 MB）           │
│  FlyAI-POC-update-<version>.zip  ← 可选：手动导入包              │
└────────────────────────────────┬────────────────────────────────┘
                                 │ HTTPS
                                 ▼
┌──────────────────────── POC APK（native） ──────────────────────┐
│ UpdateService（前台服务）                                        │
│  - 拉清单 → 验签 → 比较版本                                      │
│  - 下载 overlay → 校验 SHA-256 → 解出 payload.tar.gz → 写 pending│
│  - 下载全量 APK → 校验 → FileProvider → 系统安装器               │
│                                                                  │
│ BootService（每次启动）                                          │
│  - 读 state.json（base/code/requirements/版本）                  │
│  - 新 APK → 全量解包；有 pending → 备份代码 → 解 overlay          │
│  - 启动 Python → 健康检查 → 等前端 markReady                     │
│  - 失败 → 回滚 backup / 回退到 APK 内置代码                      │
│                                                                  │
│ MainActivity（WebView + 更新消息桥）                             │
└────────────────────────────────┬────────────────────────────────┘
                                 ▼
                   127.0.0.1:8000（Python/FastAPI + 前端）
```

### 3.2 两条更新通道

| 通道 | 触发条件 | 产物 | 是否安装 APK | 典型大小 |
|---|---|---|---|---|
| 代码 overlay | `requirements.txt` 不变、native 壳兼容 | `poc-overlay-*.tar.gz` | 否 | 2.7 MiB |
| 全量 APK | 依赖变化、native 变化、overlay 不兼容、用户手动选择 | `FlyAI-POC-arm64-v*.apk` | 是 | 105 MiB |

判定顺序：

1. 清单里有 `overlay`，且 `requirements_sha256` 与设备 base 一致，且 `min_app_version_code <= 当前 versionCode <= manifest.version_code`，且 `overlay_version > 已应用版本` → 提供 overlay；
2. 否则，清单里有 `full`，且 `full.version_code > 当前 versionCode` → 提供全量 APK；
3. 否则 → 已是最新。

> `当前 versionCode <= manifest.version_code` 这一条用于防止降级：如果用户已经安装了比清单更新的 APK，不能再把旧代码 overlay 盖上去；此时应等针对新 APK 的清单。

### 3.3 版本模型

| 版本 | 来源 | 用途 |
|---|---|---|
| APK `versionCode` | `build_poc.py --version-code`（默认 6） | Android 安装、全量包比较、`min_app_version_code` |
| APK `versionName` | `build_poc.py --version-name`（默认 1.5） | 展示 |
| `overlay_version` | 发布时间 `YYYYMMDDHHMM` 或 CI run number | overlay 单调递增比较、防降级 |
| `code_rev` | 代码树稳定哈希（路径+权限+文件 SHA-256） | 判断设备代码是否已是最新 |
| `ROOTFS_REV` | 完整 rootfs payload 的 SHA-256 前 16 位 | 判断 APK 基础包是否变化 |
| `requirements_sha256` | base rootfs 中 `requirements.txt` 的 SHA-256 | overlay 与 `.venv` 兼容性闸门 |

规则：

- 只有 `overlay_version > state.overlayVersion` 才应用；
- overlay 只在 `min_app_version_code <= 当前 versionCode <= manifest.version_code` 时应用；用户已装更新 APK 时，旧 overlay 不得覆盖新代码；
- `code_rev == state.codeRev` 时直接跳过（即使 `overlay_version` 更大，说明内容相同）；
- `full.version_code <= 当前 versionCode` 的全量包不提示（Android 也不会接受降级）；
- 服务端“回滚”= 用旧代码发布一个 **更大的** `overlay_version`，客户端不提供降级入口（调试开关除外）。

### 3.4 关键决策摘要

| 决策点 | 选择 | 理由 |
|---|---|---|
| 更新源 | GitHub Releases | 无自建服务器、版本与仓库绑定、可直接 `latest/download` |
| overlay 粒度 | 全量代码（2.7 MB） | 实现简单；前端 bundle 变化本来就会全量，先跑通 |
| 清单校验 | RSA-2048 + SHA-256 签名 | Java 原生支持，密钥管理简单 |
| 下载/校验 | native `UpdateService` | 不依赖 Python 加密库；可在服务未启动时工作；可调系统安装器 |
| 应用时机 | 默认下次冷启动；支持“立即重启” | 不打断正在进行的对话 |
| 回滚 | 代码备份 + 服务健康检查 + 前端 `markReady` 看门狗 | 覆盖“起不来”和“起来了但前端白屏” |
| 全量兜底 | 清单带 `full`，native 下载 + 系统安装器 | 满足依赖/native 变化 |
| 手动导入 | 可选 Phase 2 | GitHub 不可达/离线时可用 |

---

## 4. 更新包与清单

### 4.1 Release 产物

每次发布至少包含：

```
poc-update.json                    # 最新版本签名清单（稳定文件名）
poc-overlay-<version>.tar.gz       # 代码增量包（约 2.7 MB）
FlyAI-POC-arm64-v<version>.apk     # 全量 APK（约 105 MB）
```

可选：

```
FlyAI-POC-update-<version>.zip     # 手动导入包：manifest.json + payload.tar.gz
```

`poc-update.json` 使用稳定文件名，客户端固定请求：

```
https://github.com/Artificialheaven/flyai/releases/latest/download/poc-update.json
```

这样不需要 GitHub API、不受 API 速率限制；清单里的 overlay/APK 使用该 Release 的版本化绝对 URL。

### 4.2 overlay tar.gz 内容

根路径与 rootfs 一致，只允许以下条目：

```
./opt/flyai/flyai/**                 # Python 包 + web/static 前端
./opt/flyai/plugins/**               # 插件
./opt/flyai/main.py
./opt/flyai/requirements.txt         # 必须与 base 完全一致
./opt/flyai/start.sh
./opt/flyai/config/config.example.json
```

禁止包含：

- `./opt/flyai/data/**`
- `./opt/flyai/config/config.json`
- `./opt/flyai/.venv/**`
- `./usr/**`、`./etc/**`、`./bin/**` 等系统路径
- 符号链接、硬链接、设备文件、绝对路径、`..`

构建脚本负责白名单；客户端解包时再次校验。

### 4.3 `poc-update.json` schema

```json
{
  "schema": 1,
  "channel": "poc",
  "version": "1.5.1",
  "version_code": 7,
  "overlay_version": 202611241530,
  "code_rev": "9f2c8a1d9e7b3c4f",
  "requirements_sha256": "50288f7cb03b1273...64位hex",
  "min_app_version_code": 7,
  "published_at": "2026-11-24T15:30:00+08:00",
  "notes": "设置页 GitHub 入口、透明背景、返回键接管",
  "overlay": {
    "url": "https://github.com/Artificialheaven/flyai/releases/download/poc-v1.5.1-202611241530/poc-overlay-1.5.1.tar.gz",
    "size": 2831155,
    "sha256": "…64位hex…"
  },
  "full": {
    "url": "https://github.com/Artificialheaven/flyai/releases/download/poc-v1.5.1-202611241530/FlyAI-POC-arm64-v1.5.1.apk",
    "size": 110047508,
    "sha256": "…64位hex…",
    "version": "1.5.1",
    "version_code": 7
  },
  "signing_key_id": "poc-ota-2026",
  "sig_alg": "SHA256withRSA",
  "signature": "base64…"
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `version_code` | 本清单对应的最高 APK 版本；overlay 只应用到 `min_app_version_code <= 设备版本 <= 本字段` |
| `overlay_version` | 单调递增的增量版本；用于比较和防降级 |
| `code_rev` | 增量包代码树哈希；与设备 `state.codeRev` 比较 |
| `requirements_sha256` | 必须与设备 base 的 `requirements.txt` 一致，否则只能全量 |
| `min_app_version_code` | overlay 依赖的最低 APK versionCode；不满足则走 `full` |
| `overlay` | 可为 `null`（只发全量）；`size`/`sha256` 直接对应下载的 `poc-overlay-*.tar.gz` |
| `full` | 可为 `null`（只发代码更新、不提供全量）；客户端不满足 overlay 时提示手动安装 |
| `signature` | 对 §4.4 的规范字符串做 RSA-2048/SHA-256 签名 |

> OTA 下载的是原始 `poc-overlay-*.tar.gz`，不再套一层 zip；`overlay.size`/`sha256` 就是它的字节数和哈希。可选的手动导入包 `FlyAI-POC-update-*.zip` 内含 `manifest.json` + `payload.tar.gz`，其完整性由清单签名 + payload 哈希保证，不依赖 zip 自身哈希。

### 4.4 签名载荷

签名不是对整段 JSON 做 canonicalization，而是对固定顺序的 UTF-8 文本签名：

```
flyai-poc-overlay-v1
<version_code>
<overlay_version>
<code_rev>
<requirements_sha256>
<min_app_version_code>
<overlay.sha256 或 ->
<overlay.size 或 0>
<full.sha256 或 ->
<full.size 或 0>
<full.version_code 或 0>
<signing_key_id>
```

每行以 `\n` 分隔，末尾也带 `\n`。`overlay`/`full` 为 `null` 时对应字段填 `-` / `0`。

签名算法：`SHA256withRSA`，签名结果 Base64 放入 `signature`。

### 4.5 密钥管理

- 生成一次性 OTA 密钥（与 APK 签名密钥分离，便于轮换）：
  ```bash
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out poc-ota-signing.key
  openssl rsa -in poc-ota-signing.key -pubout -out poc-ota-public.pem
  ```
- 私钥：
  - 只存在发布机 `%USERPROFILE%\.flyai\poc-ota-signing.key`；
  - CI 放 GitHub Secret `POC_OTA_SIGNING_KEY`（base64 后的 PEM）；
  - 绝不入库、不放进 APK、不写入日志。
- 公钥：
  - 以 Base64 DER 内嵌到 `BootService` / `UpdateService`；
  - `signing_key_id` 支持多把公钥，便于轮换；
  - 公钥指纹记录在本文档和 README，不记录私钥。
- 私钥丢失：只能发一个新 APK 内置新公钥；旧客户端仍可走全量 APK 更新，但无法验证新的 overlay。

> 也可以复用 POC APK 签名密钥，用 App 自身证书验签；但需要从 JKS 导出私钥来签任意清单，工具链更绕。本期推荐独立 OTA 密钥。

---

## 5. 客户端更新流程

### 5.1 状态机

```
IDLE
  │ 启动 3s 后 / 设置页点击 / 回前台超过 24h
  ▼
CHECKING ──网络失败/404/无更新──> IDLE
  │ 发现 overlay（兼容）
  ▼
DOWNLOADING_OVERLAY ──失败──> FAILED（保留当前版本）
  │ 校验通过
  ▼
STAGED（pending 已写入）
  │ 下次冷启动 / 用户点“立即重启”
  ▼
APPLYING（BootService 停服务 → 备份 → 解包）
  │ 服务健康检查 + 前端 markReady
  ▼
READY（删除备份）
```

全量分支：

```
CHECKING → 发现 full → DOWNLOADING_APK → 校验 → 系统安装器 → App 被杀
  → 用户重开 → BootService 全量解包 → READY
```

### 5.2 检查

- 触发：
  - App 启动、服务就绪、前端挂载后延迟 3 秒检查一次；
  - 从后台回前台且距上次检查 > 24 小时；
  - 设置页“检查更新”手动触发。
- 请求：
  - `GET https://github.com/Artificialheaven/flyai/releases/latest/download/poc-update.json`
  - `Cache-Control: no-cache`，连接 15s / 读取 30s 超时，最多 5 次 HTTPS 重定向；
  - `Accept-Encoding: identity`。
- 校验顺序（原生侧）：
  1. JSON 大小 ≤ 64 KiB；
  2. 字段类型/范围校验；
  3. 用内嵌公钥按 §4.4 重算并验签；
  4. 版本比较。
- **JS 侧传来的任何字段都不可信**；更新服务只使用自己拉取并验签的清单。

### 5.3 下载 overlay

- 只下载清单里 `overlay.url`，不接受 JS 传入任意 URL；
- 大小上限 20 MiB；`Content-Length` 与实际读取都要检查；
- 下载到 `filesDir/update/download/overlay.tar.gz.part`；
- 完成后计算 SHA-256，与 `overlay.sha256` 比较；
- 通过后移动到 `filesDir/update/pending/payload.tar.gz`；
- 同时把清单写入 `filesDir/update/pending/manifest.json`，并写 `ready` 标记；
- 进度通过前台通知和 App 内进度条展示（256 KiB / 200 ms 节流）。

### 5.4 应用 overlay

`BootService.boot()` 在启动 Python 前执行：

1. 读取 `pending/manifest.json`，再次验签（防止下载后被篡改）；
2. 校验 `requirements_sha256 == state.requirementsSha256`；
3. 校验 `min_app_version_code <= 当前 versionCode <= manifest.version_code`；
4. 校验 `overlay_version > state.overlayVersion` 且 `code_rev != state.codeRev`；
5. 读取 `pending/payload.tar.gz`，校验大小/哈希；
6. 备份旧代码，保留相对路径以便原样恢复：
   ```
   filesDir/update/backup/<timestamp>/opt/flyai/
     flyai/ plugins/ main.py requirements.txt start.sh
     config/config.example.json
   ```
   使用 `rename`（同一文件系统，原子且快）；
7. 写 `state.applying = {codeRev, overlayVersion, backupDir}`；
8. 删除 `opt/flyai/{flyai,plugins}`、`main.py`、`requirements.txt`、`start.sh`、`config/config.example.json`；
9. 用 `TarGzExtractor` 解压 `payload.tar.gz` 到 rootfs；
10. 校验关键文件存在（`main.py`、`flyai/app.py`、`start.sh`）；
11. 启动 Python 服务；
12. 健康检查：最多 60 秒内能连上 `127.0.0.1:8000`；
13. 成功 → 更新 `state.codeRev/overlayVersion`，清除 `applying`；等待前端 `markReady`；
14. `markReady` 后删除 backup 和 pending；
15. 任一步失败 → 停止服务，删除新代码，恢复 backup，恢复旧 state，重启旧服务，记录 `lastRollback`。

“立即重启”路径：

1. 前端调用 `applyPocOverlay()`；
2. 原生 `stopService(BootService)`，`onDestroy` 杀掉 Python 进程；必要时 `destroyForcibly` 并等待端口 8000 释放；
3. `startService(BootService)`，`onStartCommand` 重新执行 `boot()`，在启动 Python 前应用 pending；
4. 原生显示“正在更新”遮罩，轮询 `127.0.0.1:8000`；
5. 端口就绪后 `webView.clearCache(true)`，再 `reload()`，前端重新 `markReady()`，避免旧 `index.html` 缓存。

### 5.5 前端 `markReady` 看门狗

服务能启动不代表前端能正常渲染。增加：

- 原生在每次冷启动、当前代码是 overlay 时，`state.boot.attempts += 1`；
- Web 前端在 `App.tsx` 首次挂载的 effect 中调用 `window.FlyAIUpdate.markReady()`；
- `markReady` 将 `boot.attempts = 0`，并允许删除备份；
- 连续 2 次冷启动未收到 `markReady` → 回滚到 backup（没有 backup 则重新解包 APK 内置代码）；
- 记录 `lastRollback.codeRev`，本次会话不再自动重试同一个坏包。

这样覆盖：

- 新代码语法错误/导入失败 → 服务起不来 → 健康检查回滚；
- 服务能起但前端 JS 白屏/崩溃 → `markReady` 不触发 → 二次启动回滚。

### 5.6 全量 APK 更新

当清单只有 `full`，或 overlay 不兼容时：

1. 显示全量更新提示（版本、105 MB、需要重启安装）；
2. 检查存储空间（建议 ≥ 250 MiB 可用）；
3. 下载到 `filesDir/update/apk/FlyAI-POC-arm64-v<version>.apk`；
4. 校验 `sha256`、`version_code > 当前`；
5. 通过 `FileProvider` 调系统安装器：
   ```java
   Intent intent = new Intent(Intent.ACTION_VIEW);
   intent.setDataAndType(uri, "application/vnd.android.package-archive");
   intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
           | Intent.FLAG_ACTIVITY_NEW_TASK);
   intent.setClipData(ClipData.newRawUri(name, uri));
   startActivity(intent);
   ```
6. 若未授予“安装未知应用”，先打开 `Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES`；
7. 安装后 App 被杀，用户重开；新 APK 的 `ROOTFS_REV` 与 `state.baseRev` 不同 → 全量解包，清除 pending/backup，`codeRev = APK 内置 CODE_REV`。

Manifest 需要新增：

```xml
<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
```

`file_paths.xml` 增加：

```xml
<files-path name="update" path="update/" />
```

### 5.7 手动导入（可选）

设置页增加“导入更新包”：

- 用户选择 `FlyAI-POC-update-<version>.zip`（内含 `manifest.json` + `payload.tar.gz`）；
- 原生读取 zip 内 manifest，验签、校验 payload 哈希；
- 后续流程与自动下载一致；
- 适用于 GitHub 不可达、离线测试、内网分发。

---

## 6. 版本状态与启动逻辑

### 6.1 `state.json`

把现在的 `.installed` 单字符串升级为 JSON（保留旧文件做迁移）：

```json
{
  "schema": 1,
  "baseRev": "481d22ac9f2c8a1d",
  "codeRev": "9f2c8a1d9e7b3c4f",
  "overlayVersion": 202611241530,
  "requirementsSha256": "50288f7cb03b1273...",
  "appVersionCode": 6,
  "applying": null,
  "boot": { "attempts": 0, "lastCodeRev": "9f2c8a1d9e7b3c4f" },
  "lastRollback": null,
  "lastCheckAt": 0
}
```

| 字段 | 说明 |
|---|---|
| `baseRev` | 当前 APK 内置 rootfs payload 的 `ROOTFS_REV` |
| `codeRev` | 当前设备上的代码版本；全量解包后 = APK 注入的 `CODE_REV` |
| `overlayVersion` | 已应用的 overlay 版本；全量解包后为 0 |
| `requirementsSha256` | 当前 base 的 `requirements.txt` 哈希 |
| `appVersionCode` | 上次全量安装的 APK versionCode |
| `applying` | 应用 overlay 的日志；崩溃恢复用 |
| `boot.attempts` | 前端 `markReady` 看门狗计数 |
| `lastRollback` | 最近一次回滚，避免自动重试坏包 |

### 6.2 启动流程

```
installProot()
readState()（没有则从 .installed 迁移）
if (fresh || state.baseRev != ROOTFS_REV):
    fullExtract()                       # 删除代码目录 + 解整份 payload
    state.baseRev = ROOTFS_REV
    state.codeRev = CODE_REV            # APK 注入的代码哈希
    state.overlayVersion = 0
    state.requirementsSha256 = sha256(rootfs/opt/flyai/requirements.txt)
    state.appVersionCode = currentVersionCode
    clear pending/backup
else if (pending exists):
    applyOverlayOrRollback()
prepareDns()
startServer()
waitForPort(8000, 60s)
if (overlay active): state.boot.attempts += 1
if (boot.attempts >= 2): rollback()
```

### 6.3 `ROOTFS_REV` 与 `CODE_REV`

`build_poc.py` 向 `BootService.java` 注入两个常量：

```java
private static final String ROOTFS_REV = "__ROOTFS_REV__";  // 完整 payload 哈希
private static final String CODE_REV = "__CODE_REV__";      // 代码树哈希
```

- `ROOTFS_REV` 继续表示“基础包是否变化”；
- `CODE_REV` 表示 APK 内置代码的版本；
- overlay 清单的 `code_rev` 使用同一套哈希算法；
- 新 APK 全量解包后 `state.codeRev = CODE_REV`，如果它正好等于最新清单的 `code_rev`，客户端不会重复下载同一个 overlay。

代码哈希算法（构建脚本与 Python 共用）：

1. 遍历 overlay 白名单内的文件；
2. 排序，逐行写入 `相对路径\0权限\0文件SHA256\n`；
3. 对整段文本做 SHA-256，取前 16 位作为 `code_rev`。

不包含 mtime/uid/gid，保证不同机器/时间构建结果一致。

### 6.4 旧版本迁移

首次运行新 `BootService` 时：

- 若 `state.json` 不存在、`.installed` 存在：
  - `baseRev = .installed`；
  - `codeRev = ""`（未知，视为 base）；
  - `overlayVersion = 0`；
  - `requirementsSha256 = sha256(已安装的 requirements.txt)`；
  - `appVersionCode = 当前`；
  - 写 `state.json`。
- 若当前 APK 的 `ROOTFS_REV` 与 `baseRev` 不同 → 走一次全量解包（本次返回键/MainActivity 改动就属于这种情况）。

---

## 7. 发布与构建

### 7.1 本地构建

```bash
# 只打 APK（原有行为）
.venv/Scripts/python mobile/poc/build_poc.py

# 打 APK + 生成 overlay/清单（本地不签名发布时可用 --no-sign）
.venv/Scripts/python mobile/poc/build_poc.py \
  --overlay \
  --overlay-version 202611241530 \
  --release-tag poc-v1.5.1-202611241530 \
  --ota-key "$USERPROFILE/.flyai/poc-ota-signing.key"

# 只生成 overlay（不编译 APK）
.venv/Scripts/python mobile/poc/build_overlay.py \
  --version-code 6 --version-name 1.5 \
  --overlay-version 202611241530 \
  --tag poc-v1.5.1-202611241530 \
  --key "$USERPROFILE/.flyai/poc-ota-signing.key"
```

产物：

```
release/
  FlyAI-POC-arm64-v1.5.1.apk
  poc-overlay-1.5.1.tar.gz
  poc-update.json
  FlyAI-POC-update-1.5.1.zip      # 可选手动导入包
```

### 7.2 构建脚本改造

新增 `mobile/poc/build_overlay.py`：

1. 从仓库读取 overlay 白名单文件（复用 `rootfs_overlay._iter_source_entries` 的规则）；
2. 生成确定性 `payload.tar.gz`（mtime=0，路径排序，排除 `__pycache__`/`.pyc`）；
3. 计算 `code_rev`、`payload` 的 size/sha256；
4. 读取 `.poc-cache/base/rootfs.tar.gz` 中的 `requirements.txt` 哈希；
   - 如果与当前仓库 `requirements.txt` 不一致 → 标记 `overlay = null`，只发全量；
5. 生成 `poc-update.json`，按 §4.4 签名；
6. 生成可选的手动导入 zip（`manifest.json` + `payload.tar.gz`）；
7. 如果提供 `--apk`，把 APK 的 size/sha256/version 填入 `full`。

`build_poc.py` 改造：

- 注入 `__CODE_REV__`；
- `--overlay` 时在 APK 构建完成后调用 `build_overlay.py`；
- `--release-tag`/`--repo-url` 用于生成清单里的绝对 URL；
- 无 `--ota-key` 时只生成未签名清单（仅调试，不用于发布）。

### 7.3 GitHub Actions

现有 `.github/workflows/poc-apk.yml` 增加发布 job：

```yaml
permissions:
  contents: write

env:
  POC_VERSION_CODE: ${{ inputs.version_code || '6' }}
  POC_VERSION_NAME: ${{ inputs.version_name || '1.5' }}
  POC_OVERLAY_VERSION: ${{ github.run_number }}   # 或时间戳
  POC_TAG: poc-v${{ inputs.version_name || '1.5' }}-${{ github.run_number }}

steps:
  # 1) 现有 APK 构建
  # 2) 生成 overlay + 清单 + 签名
  - name: Build overlay and manifest
    env:
      POC_OTA_SIGNING_KEY: ${{ secrets.POC_OTA_SIGNING_KEY }}
    run: |
      python mobile/poc/build_poc.py \
        --version-code "$POC_VERSION_CODE" \
        --version-name "$POC_VERSION_NAME" \
        --overlay \
        --overlay-version "$POC_OVERLAY_VERSION" \
        --release-tag "$POC_TAG" \
        --repo-url "https://github.com/${{ github.repository }}" \
        --ota-key-from-env POC_OTA_SIGNING_KEY

  # 3) 创建 Release 并上传
  - name: Publish release
    uses: softprops/action-gh-release@v2
    with:
      tag_name: ${{ env.POC_TAG }}
      name: FlyAI POC ${{ env.POC_VERSION_NAME }}
      make_latest: true
      files: |
        release/FlyAI-POC-arm64-v*.apk
        release/poc-overlay-*.tar.gz
        release/poc-update.json
        release/FlyAI-POC-update-*.zip
```

要点：

- `poc-update.json` 是稳定文件名，客户端固定请求 `releases/latest/download/poc-update.json`；
- overlay/APK 使用该 Release 的版本化 URL；
- 发布前必须验证 `poc-update.json` 签名、overlay SHA-256、APK SHA-256；
- Release 资产保留最近 N 个（建议 5 个），避免 105 MB × 多个版本占满存储；
- 如果没有 `POC_OTA_SIGNING_KEY`，CI 直接失败，绝不发布未签名清单。

### 7.4 版本号规则

- **仅 Python/前端改动**：`versionCode`/`versionName` 不变，只递增 `overlay_version`；
- **依赖变化 / native Java 变化**：`versionCode` +1（按现有 APK 版本规则），发布全量 APK；overlay 可同时发布给已升级到新 APK 的用户；
- **本次返回键改动**：`MainActivity` 变了，属于 native 变化 → 需要先装一次全量 APK；之后的代码/前端更新可用 overlay。

---

## 8. 安全设计

### 8.1 威胁模型

| 威胁 | 影响 | 对策 |
|---|---|---|
| 更新源被篡改 | 下发恶意 Python 代码 | 清单 RSA 签名；公钥内嵌 APK |
| 中间人替换下载 | 同上 | HTTPS + 逐跳校验 + SHA-256 |
| 清单被篡改（改 URL/哈希） | 下载恶意包 | 签名覆盖 URL 相关哈希、版本、requirements |
| 降级攻击 | 回退到有漏洞版本 | `overlay_version`/`versionCode` 单调 |
| 恶意 tar（路径穿越） | 覆盖私有文件/系统路径 | 白名单 + 解包路径前缀校验 + 拒绝链接 |
| tar 炸弹 | 撑爆存储/OOM | 条目数/总大小/单条目/压缩比上限 |
| 卡片 iframe 调更新桥 | 触发下载/安装 | WebMessageListener 限制主 origin；方法不接受任意 URL/路径 |
| 依赖不匹配 | Python 导入失败 | `requirements_sha256` 闸门，失败回退全量 |
| 数据迁移不可逆 | 回滚代码后数据不兼容 | 迁移保持向后兼容；高风险更新前可选手动备份 data |

### 8.2 校验清单

原生侧在应用前必须全部通过：

1. 清单 JSON ≤ 64 KiB，字段类型/范围合法；
2. RSA 签名验证通过；
3. overlay 大小 ≤ 20 MiB，SHA-256 匹配；
4. requirements 哈希匹配；
5. `min_app_version_code <= 当前 versionCode <= manifest.version_code`；
6. `overlay_version` 单调递增；
7. tar 条目只允许 `./opt/flyai/` 白名单路径；
8. 拒绝绝对路径、`..`、反斜杠、符号链接/硬链接；
9. 解压后关键文件存在；
10. 解压后总大小/条目数限制。

任何一步失败：删除临时文件/待生效包，当前版本不变，记录日志。

### 8.3 WebView 消息桥

POC 的 WebView 加载 `http://127.0.0.1:8000`，卡片 iframe 是 sandbox 的 `srcdoc`。更新桥不能暴露给任意 frame：

- 优先使用 `WebViewCompat.addWebMessageListener`，`allowedOriginRules = {"http://127.0.0.1:8000"}`；
- 如果不使用 AndroidX WebKit，则用 `addJavascriptInterface` + 每次页面加载注入随机 token，原生校验 token；卡片 iframe 无法读取主页面闭包中的 token；
- 桥方法不接受 URL/文件路径参数：
  - `check()`、`downloadOverlay()`、`downloadFull()`、`apply()`、`cancel()`、`markReady()` 全部无参数；
  - 更新服务只使用自己验签的清单；
- 手动导入由原生文件选择器完成，JS 不能传路径。

### 8.4 数据边界

overlay 只覆盖代码：

- 不写 `data/`、`config/config.json`、`.venv`、`usr/`、`etc/`；
- 应用前备份代码目录，失败可回滚；
- 全量 APK 更新保留 App 私有数据（同包名/同签名）。

---

## 9. 前端接入

### 9.1 检测 POC 环境

原生页面加载完成后注入：

```js
window.__flyaiPocUpdate = { supported: true, versionCode, versionName, codeRev };
```

Web 端只在 `window.__flyaiPocUpdate?.supported === true` 时显示更新入口；普通浏览器/正式 APK 不显示。

### 9.2 `pocUpdate.ts`

新增 `webapp/src/services/pocUpdate.ts`：

```ts
export type PocUpdateState = {
  current: { versionCode: number; versionName: string; codeRev: string };
  pending: { version: string; overlayVersion: number; size: number } | null;
  status: 'idle' | 'checking' | 'downloading' | 'staged' | 'applying' | 'failed';
  progress: { loaded: number; total: number; percent: number } | null;
  error?: string;
};

export const isPocUpdateSupported = (): boolean => ...
export const getPocUpdateState = async (): Promise<PocUpdateState | null>
export const checkPocUpdate = async (): Promise<void>
export const downloadPocOverlay = async (): Promise<void>
export const downloadPocFullApk = async (): Promise<void>
export const applyPocOverlay = async (): Promise<void>
export const markPocReady = async (): Promise<void>
export const onPocUpdateEvent = (cb: (event: PocUpdateEvent) => void): () => void
```

- `checkPocUpdate` 触发原生 `UpdateService`；
- 下载进度/结果通过原生 `evaluateJavascript("window.__flyaiUpdateEvent(...)")` 回传；
- `applyPocOverlay` 触发 `BootService` 重启；WebView 会短暂断开，原生显示“正在更新”遮罩，服务就绪后重新加载；
- `markPocReady` 在 `App.tsx` 首次挂载的 effect 中调用。

### 9.3 设置页 UI

设置 → 通用设置最下方（GitHub 入口附近）增加“本地更新”：

- 当前：`本地服务 1.5 (6) · 代码 2026.11.24-1530`；
- 按钮：`检查更新`；
- 有新版本：
  - 代码增量：`新版本 1.5.1 · 2.7 MB` → `下载并更新`；
  - 全量：`需要完整安装包 · 105 MB` → `下载完整包`；
- 已下载：`更新已下载，重启 App 生效` + `立即重启`；
- 下载中：进度条/百分比 + `取消`；
- 失败：错误文案 + `重试`；
- 全量更新前提示：会关闭 App 并打开系统安装器。

新增 i18n 键（zh/en 对齐）：

`pocUpdateTitle`、`pocUpdateCurrent`、`pocUpdateCheck`、`pocUpdateChecking`、`pocUpdateLatest`、`pocUpdateAvailable`、`pocUpdateDownload`、`pocUpdateDownloading`、`pocUpdateStaged`、`pocUpdateApplyNow`、`pocUpdateRestartHint`、`pocUpdateFullRequired`、`pocUpdateFullDownload`、`pocUpdateFailed`、`pocUpdateCancel`、`pocUpdateRolledBack`、`pocUpdateSize`。

### 9.4 自动检查策略

- 启动后 3 秒检查一次（不阻塞首屏）；
- 发现 overlay 且兼容：默认自动下载并 stage（2.7 MB），toast 提示“新版本已下载，下次启动生效”；
- 发现全量：只提示，不自动下载（105 MB，需用户确认）；
- 手动检查失败时展示错误；自动检查失败静默；
- 同一版本下载失败后 24 小时内不自动重试。

---

## 10. 异常与回滚矩阵

| 场景 | 行为 |
|---|---|
| GitHub 不可达/超时 | 静默跳过，使用当前版本 |
| 清单 404/字段非法/验签失败 | 忽略本次更新 |
| 没有新版本 | 不提示 |
| overlay 不兼容（requirements/native） | 不下载；提示全量 APK |
| 下载中断 | 删除 `.part`；当前版本不变；可重试 |
| SHA-256 不匹配 | 删除下载；记录日志；当前版本不变 |
| 签名不匹配 | 删除下载；记录日志；当前版本不变 |
| tar 路径非法/炸弹 | 拒绝解包；删除 pending |
| 应用时断电/被杀 | 启动时读 `state.applying`，恢复 backup |
| 新代码服务起不来 | 60s 健康检查失败 → 恢复 backup → 重启旧服务 |
| 服务起来但前端白屏 | 连续 2 次未 `markReady` → 回滚 backup |
| 回滚后仍失败 | 重新解包 APK 内置代码（最后兜底） |
| 回滚后同版本再次检查 | `lastRollback.codeRev` 跳过，不自动重试 |
| 安装全量 APK | App 被杀；重开后 `ROOTFS_REV` 变化 → 全量解包；数据保留 |
| 用户清除 App 数据 | state/pending/backup 清空；重新解包；重新检查 |
| 存储不足 | 下载/应用前检查，提示所需空间 |
| 安装未知应用未授权 | 打开系统设置页；授权后重试 |

---

## 11. 测试计划

### 11.1 构建/工具测试

- overlay 生成两次，SHA-256 完全一致（确定性）；
- `code_rev` 与 `build_poc.py` 注入值一致；
- 清单签名可用公钥验证；篡改任一字段验签失败；
- 非法 tar（`../`、绝对路径、符号链接、超大文件）被构建脚本和客户端双重拒绝；
- requirements 不一致时只生成 `full`，不生成 `overlay`。

### 11.2 主机侧模拟

1. 解包 base rootfs 到临时目录；
2. 写入假 `config/config.json`、`data/chat.db`；
3. 应用 overlay；
4. 验证：
   - 代码/前端文件已替换；
   - `data/`、`config/config.json`、`.venv/` 未变化；
   - 旧代码备份存在；
   - 回滚后文件恢复。

### 11.3 真机矩阵

| # | 场景 | 预期 |
|---|---|---|
| 1 | 全新安装 | 解包 base，服务启动，显示当前版本 |
| 2 | 发布 overlay，冷启动 | 自动/手动下载，下次启动应用，数据保留 |
| 3 | 立即重启 | 服务重启，WebView 重载，新版本生效 |
| 4 | overlay requirements 不匹配 | 拒绝，提示下载全量 APK |
| 5 | 篡改清单签名 | 拒绝，当前版本不变 |
| 6 | 下载被截断 | 校验失败，不产生 pending |
| 7 | 新代码 import 错误 | 健康检查失败，自动回滚 |
| 8 | 新前端白屏 | 2 次启动后回滚，toast 提示 |
| 9 | 全量 APK 更新 | 系统安装器，数据保留，baseRev 更新 |
| 10 | GitHub 断网/404 | 无报错，正常启动 |
| 11 | 应用过程中强杀 | 下次启动恢复 backup |
| 12 | 清除数据 | 回到 base，重新检查 |
| 13 | Android 8/9/13/14 国产 ROM | 下载、安装、后台限制均正常 |
| 14 | 低存储（< 25 MiB） | 应用前拒绝并提示 |

### 11.4 CI 测试

- 构建 APK + overlay + 清单；
- 验证清单签名、overlay SHA-256、APK SHA-256；
- 上传 Release 资产；
- 用 `gh release view` 校验资产齐全。

---

## 12. 后续可选：APK 二进制差分包

如果 native 壳/依赖变化频繁，或希望全量更新也只传几 MB，可增加 bsdiff：

- 用同一工具链构建新旧 APK；
- `bsdiff` 生成补丁（实测 payload 部分约 2.05 MiB，整包预计 2.5–3 MiB）；
- 客户端复制自身 `sourceDir` APK → 应用补丁 → 校验目标 SHA-256 → 系统安装器；
- 需要设备端 bspatch（纯 Java 或小 native 库），并保证补丁与已安装 APK 的 `base_sha256` 完全匹配；
- 版本不匹配时回退全量 APK；
- 补丁同样要签名/校验，防止下载到错误补丁。

本期不实现，作为 P2 优化。

---

## 13. 实施计划

| 阶段 | 内容 | 交付物 | 预估 |
|---|---|---|---|
| P0 | 构建侧：overlay 生成、`code_rev`、清单、签名 | `build_overlay.py`、`build_poc.py`、文档 | 1–1.5 天 |
| P1 | 原生状态与启动：`state.json`、迁移、全量/增量分支、备份/回滚 | `BootService.java`、`TarGzExtractor` 校验 | 2–3 天 |
| P2 | 下载与更新服务：清单拉取、验签、下载、stage、全量 APK 安装 | `UpdateService.java`、`AndroidManifest`、`file_paths.xml` | 2–3 天 |
| P3 | Web 接入：消息桥、`pocUpdate.ts`、设置页、i18n、`markReady` | Web 前端 | 1.5–2 天 |
| P4 | CI/Release：GitHub Actions、签名 Secret、资产上传 | workflow | 0.5–1 天 |
| P5 | 测试与真机验证 | 测试记录、修复 | 1.5–2 天 |
| 合计 | | | **8–12 人日** |

里程碑：

- M1：本地生成 overlay + 清单 + 签名，主机侧可解包验证；
- M2：设备上“下载 → 下次启动应用 → 数据保留”闭环；
- M3：回滚/看门狗/全量 APK 兜底闭环；
- M4：GitHub Release 自动发布，真机矩阵通过。

---

## 14. 风险与待确认

### 14.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| GitHub 在部分网络不可达/慢 | 中 | 中 | 支持手动导入包；后续加镜像/manifest 自定义 URL |
| 更新私钥丢失 | 低 | 高 | 离线备份 + GitHub Secret；丢失后发新 APK |
| 依赖变化但未发全量 | 中 | 高 | 构建脚本强制校验 requirements；客户端二次校验 |
| native 改动被 overlay 误覆盖 | 低 | 高 | `min_app_version_code` 闸；native 改动必须升 versionCode |
| 数据迁移不可逆 | 中 | 中 | 迁移保持向后兼容；高风险更新前备份 data |
| 国产 ROM 杀下载/安装 | 中 | 中 | 前台服务 + 通知；下载包小；失败重试 |
| 全量 APK 105 MB 下载失败 | 中 | 低 | 断点/重试；提示 Wi-Fi；可手动侧载 |
| overlay 体积随前端增长 | 低 | 低 | 后续逐文件增量（Phase 2） |
| WebView 消息桥被卡片滥用 | 低 | 高 | 限制 origin/token；方法不接受 URL/路径 |

### 14.2 待确认（建议默认值）

1. **自动下载策略**：overlay 自动下载（2.7 MB），全量只提示。→ 建议采用。
2. **签名密钥**：独立 RSA-2048 OTA 密钥，还是复用 POC APK 密钥。→ 建议独立密钥。
3. **手动导入**：是否纳入 P1。→ 建议作为 GitHub 不可达的兜底，P1.5。
4. **更新检查频率**：启动 + 回前台 24h。→ 建议采用。
5. **数据备份**：是否在 overlay 前备份 `data/`。→ 默认不备份（可能很大），高风险迁移单独处理。
6. **Release 保留数量**：保留最近 5 个。→ 建议采用。
7. **全量 APK 是否做差分包**：本期不做。→ 建议 P2。

---

## 15. 附录

### 15.1 目录布局

```
filesDir/
  rootfs/                         # 完整 rootfs
    opt/flyai/                    # 代码/配置/数据
      flyai/                      # overlay 覆盖
      plugins/
      main.py
      requirements.txt
      start.sh
      config/config.json          # 用户配置，永不覆盖
      data/                       # 聊天记录等，永不覆盖
      .venv/                      # 依赖，永不覆盖
    .flyai-state.json             # 新状态文件（替代 .installed）
  update/
    download/                     # .part 临时文件
    pending/
      manifest.json
      payload.tar.gz
      ready
    backup/<timestamp>/           # 应用 overlay 前的旧代码
    apk/                          # 全量 APK 下载
```

### 15.2 清单示例（只发全量）

```json
{
  "schema": 1,
  "channel": "poc",
  "version": "1.6.0",
  "version_code": 7,
  "overlay_version": 0,
  "code_rev": "",
  "requirements_sha256": "…",
  "min_app_version_code": 7,
  "published_at": "2026-11-24T16:00:00+08:00",
  "notes": "新增 Python 依赖，需完整安装包",
  "overlay": null,
  "full": {
    "url": "https://github.com/Artificialheaven/flyai/releases/download/poc-v1.6.0-7/FlyAI-POC-arm64-v1.6.0.apk",
    "size": 110047508,
    "sha256": "…",
    "version": "1.6.0",
    "version_code": 7
  },
  "signing_key_id": "poc-ota-2026",
  "sig_alg": "SHA256withRSA",
  "signature": "…"
}
```

### 15.3 官方更新 URL

```
https://github.com/Artificialheaven/flyai/releases/latest/download/poc-update.json
```

如果以后需要镜像，客户端可在设置里允许覆盖 manifest URL；镜像必须提供自己的签名清单（同一公钥），不能只镜像 ZIP。

### 15.4 与正式版 Web 热更新的关系

正式版 APK 的前端热更新方案见 `docs/APK前端热更新方案书.md`；本方案针对 **本地一体化 POC**，更新范围是 rootfs 内的 Python + 前端，不依赖 Capacitor 的 `hostFiles`，也不使用 `server.url`。两者可以共享签名/清单/回滚的设计经验，但代码路径不同。
