# FlyAI APK 前端热更新（Web 包 OTA）方案书

> 状态：方案设计，未实施
> 日期：2026-11-18
> 适用分支：`chat-web`
> 相关代码：`mobile/android`、`mobile/scripts/copy-web.js`、`scripts/release.py`、`flyai/api/routes_app.py`、`flyai/updates.py`

---

## 1. 背景与目标

### 1.1 现状

FlyAI 的 Android 客户端是 Capacitor 7 外壳，页面资源全部内嵌在 APK 的 `assets/public` 中，WebView 以 `https://localhost` 加载本地资源。任何前端改动都必须重新编译 APK（约 6.0 MB）、上传 `webroot`、由用户手动下载安装。现有 `FlyUpdaterPlugin` 只解决「整包 APK 替换」，无法只更新前端。

Web 前端每次构建的产物约 4.61 MB（152 个文件），zip 后约 1.88 MB；当前是**单 bundle**（`main.*.js` 约 851 KB + `main.*.css` 约 152 KB），文件名带 contenthash。

### 1.2 目标

1. **前端改版不再发 APK**：服务器发布新的 Web 包后，已安装的 APK 在启动时自动检查并下载，下次启动或用户确认后立即生效。
2. **保留离线可用**：更新包落地到 App 私有目录，断网时照常启动。
3. **失败可回滚**：下载损坏、签名不符、包内容异常、启动即崩，都能自动回到上一版本。
4. **与现有 APK 更新通道共存**：新增原生能力（插件方法）时仍通过整包 APK 升级；前端热更只承载 Web 层改动，并用 `minNativeVersion` 闸防止新前端调用旧原生。
5. **同源、不丢登录态**：更新前后 WebView origin 仍是 `https://localhost`，localStorage 中的 token、主题、草稿等原样保留。
6. **安全**：更新包必须经离线私钥签名，服务端被攻破也无法下发可执行的恶意前端。

### 1.3 非目标

- 不做远程优先加载（`server.url`），不牺牲离线启动。
- 不引入第三方 OTA 服务（Capgo/Appflow）；复用 Capacitor 内建能力自建，避免外部依赖和账号绑定。
- 不在本期实现真正的文件级增量（Phase 2 预留，见 §9.3）。
- 不改变 `config.json` 的 `apiBase` 运行期覆盖机制，不改变现有 APK 更新流程。
- 不支持 iOS（当前只有 Android 壳）。

### 1.4 术语

| 术语 | 含义 |
|---|---|
| Web 包 | `flyai/web/static` 的构建产物（index.html + static/** + config.json 等） |
| OTA | Over-The-Air，本文特指「前端 Web 包热更新」 |
| 基线包 | APK `assets/public` 内置的 Web 包，作为兜底 |
| 已应用包 | 当前 WebView 正在使用的 OTA 包（可能不存在，此时用基线包） |
| 待生效包 | 已下载并校验通过、等待下次启动应用的包 |
| `minNativeVersion` | Web 包要求的最低 APK versionCode |

---

## 2. 可行性结论

**可行，且不需要自研 WebView 资源拦截。** Capacitor 7 已内建「从文件系统目录加载 Web 资源」的能力：

1. `Bridge.loadWebView()` 在创建 WebView 时读取 SharedPreferences：
   - 文件：`mobile/node_modules/@capacitor/android/capacitor/src/main/java/com/getcapacitor/Bridge.java:295-304`
   - 键：`CapWebViewSettings` / `serverBasePath`
   - 若路径存在，调用 `setServerBasePath(path)`。
2. `WebViewLocalServer.hostFiles(basePath)` 把任意 App 私有目录映射到 `https://localhost/...`，与内置 assets 同一 origin、同一 URL 结构：
   - `WebViewLocalServer.java:650-652`、`createHostingDetails()` 的 `!isAsset` 分支（约 684 行）。
3. `Bridge.setServerBasePath()` 立即切换并重新加载 WebView；`WebView` 核心插件已默认注册，JS 也可调用 `Capacitor.Plugins.WebView.setServerBasePath/persistServerBasePath`：
   - `Bridge.java:1423-1426`、`Bridge.java:664-667`、`plugin/WebView.java:14-45`。
4. APK 升级后自动回退内置资源：`Bridge.isNewBinary()` 检测 versionCode/versionName 变化并清空 `serverBasePath`：
   - `Bridge.java:433-461`
5. 主框架加载失败还有 `server.errorPath` 兜底（本方案可作为附加保险，非必需）。

因此 OTA 的「切换」只是一次目录路径持久化，原生风险集中在**下载、校验、解压、回滚**四件事上，工程量可控。

---

## 3. 总体设计

### 3.1 架构图

```
┌────────────────────────────── 服务器 ──────────────────────────────┐
│  webroot/web/                                                      │
│    manifest.json          ← 版本/大小/SHA-256/签名/minNative      │
│    FlyAI-web-<version>.zip← 前端构建产物（约 1.9 MB）              │
│                                                                    │
│  GET /api/app/web-version  ← 返回 manifest（no-store）             │
│  GET /web/FlyAI-web-*.zip  ← 根目录兜底静态文件，支持 Range         │
└────────────────────────────────────────────────────────────────────┘
                                ▲
                    HTTPS（签名保护，服务端不可信也不影响）
                                │
┌────────────────────────── Android APK ─────────────────────────────┐
│ MainActivity.onCreate()                                            │
│   └─ OtaBoot.beforeBridge()          ← 启动前：回滚判定 + 应用待生效包│
│   └─ super.onCreate()                                              │
│        └─ Capacitor Bridge 读取 serverBasePath → 加载 OTA 目录      │
│                                                                    │
│ 前端（当前包）                                                      │
│   └─ webUpdate.check()  → GET manifest → 版本比较 / minNative 闸   │
│   └─ FlyWebUpdater.downloadAndStage()                              │
│        ├─ 下载 zip → 校验 SHA-256 → 校验 ECDSA 签名                 │
│        ├─ 解压到 ota/bundles/<code>.tmp（防 zip-slip/zip 炸弹）     │
│        ├─ 覆盖 config.json（用 APK 内置版本）                       │
│        ├─ 原子改名为 ota/bundles/<code>                            │
│        └─ 写 state.pending                                          │
│   └─ markReady()                    ← 启动成功后复位看门狗          │
│                                                                    │
│ 下次启动：OtaBoot 提升 pending → current，写 serverBasePath        │
└────────────────────────────────────────────────────────────────────┘
```

### 3.2 版本模型

两套版本号完全独立：

| 版本 | 来源 | 用途 | 示例 |
|---|---|---|---|
| APK `versionCode` | `mobile/android/variables.gradle` | 现有整包更新、`minNativeVersion` 比较 | `1356136`（1.35.6136） |
| Web `version_code` | 发布脚本按分钟生成 | OTA 包比较、目录名 | `202611181530`（YYYYMMDDHHMM，Java `long` / JS number，远小于 2^53） |
| Web `version` | 同上格式化 | 展示 | `2026.11.18-1530` |

规则：

- 只有 `manifest.version_code > 当前已应用 version_code` 才下载；相等或更小直接忽略。
- 基线包视为 `version_code = 0`，因此首次发布任意版本都会被应用。
- **服务端回滚** = 用旧代码重新构建并发布一个**更大的** `version_code`（客户端拒绝降级，避免被诱导回退到有漏洞的旧包）。紧急情况下可提供 `allowDowngrade` 调试开关，默认关闭。

### 3.3 生命周期

**A. 首次安装/升级 APK**

1. 新 APK 首次启动，`OtaBoot` 发现 `state.baseApkVersionCode != 当前 versionCode`。
2. 清空 `ota/bundles/*`、`state.json`，删除 `serverBasePath`。
3. Capacitor 自动加载内置基线包；之后按普通流程检查 OTA。

**B. 普通冷启动**

1. `OtaBoot` 检查看门狗：若当前包是 OTA 且上次启动未 `markReady`，累计失败次数；连续失败 ≥2 次则回滚到上一个可用包（没有则回基线）。
2. 若 `state.pending` 存在且目录完整 → 提升为 `current`，`previous = 旧 current`，写 `serverBasePath`。
3. Bridge 加载 `current`（或基线）。
4. 页面挂载后调用 `markReady()`，看门狗计数清零。
5. 冷启动或从后台回前台（距上次检查 >1 小时）触发 `webUpdate.check()`。

**C. 检查到新版本**

1. 版本更高、`minNativeVersion` 满足 → 原生下载并校验。
2. 成功后写 `pending`，toast「新界面已下载，下次启动生效」。
3. `mandatory`（可选字段）为真时弹窗，提供「立即重启生效」按钮。

**D. 立即生效**

`FlyWebUpdater.applyPending()`：

1. 提升 pending → current；
2. 写 `serverBasePath`；
3. `bridge.setServerBasePath(dir)`（内部会 `webView.loadUrl(appUrl)` 重载）。

### 3.4 关键决策摘要

| 决策点 | 选择 | 理由 |
|---|---|---|
| 加载方式 | Capacitor `hostFiles` 私有目录 | 官方能力，origin 不变，零 WebView 拦截代码 |
| 包格式 | 单个 zip | 简单、可整体签名、解压后 CRC 校验 |
| 完整性 | SHA-256 + ECDSA P-256 签名 | 服务端被攻破也无法伪造更新 |
| 生效时机 | 默认下次启动；可选立即重启 | 不打断正在进行的对话 |
| 回滚 | 上一版本 + 启动看门狗 | 覆盖「能下载但跑不起来」的故障 |
| 与 APK 更新关系 | 并存；`minNativeVersion` 闸 | 原生能力仍需整包 |
| 增量 | Phase 1 全量 zip；Phase 2 逐文件 | 先跑通再优化 |
| 开关 | 服务端 `server.web_update.enabled`，缺省 true；Debug 包强制关闭 | 可紧急停发 |

---

## 4. 更新包与清单

### 4.1 服务器目录

```
webroot/
  web/
    manifest.json
    FlyAI-web-2026.11.18-1530.zip
    FlyAI-web-2026.11.20-0921.zip     # 保留最近 3 个，供已取到旧 manifest 的客户端下载
```

`manifest.json` 与 zip 由发布脚本生成，人工不编辑。`manifest.json` 只描述**最新**版本；旧 zip 留作下载兜底。

### 4.2 zip 内容约束

zip 根目录必须直接是 Web 包根（与 `flyai/web/static` 同结构）：

```
index.html
login.html
favicon.svg
NOTICE.txt
static/js/main.<hash>.js
static/css/main.<hash>.css
static/media/...
config.json            （可选；即使存在，客户端也会用 APK 内置版本覆盖）
```

发布脚本打包时校验：

- 条目数 ≤ 512，解压后总大小 ≤ 32 MB，单条目 ≤ 16 MB；
- 路径必须是相对路径，禁止 `..`、绝对路径、反斜杠、符号链接；
- 顶层只允许 `index.html`、`login.html`、`favicon.svg`、`NOTICE.txt`、`config.json`、`static/`；
- 必须包含 `index.html`；
- 拒绝 `.map`、`.pem`、`.key`、`.jks` 等敏感/调试文件；
- zip 内不得包含 `config/config.json`、`data/` 等运行数据。

### 4.3 manifest schema

```json
{
  "schema": 1,
  "version": "2026.11.18-1530",
  "version_code": 202611181530,
  "url": "/web/FlyAI-web-2026.11.18-1530.zip",
  "size": 1936123,
  "sha256": "9f2c...（64 位小写 hex）",
  "min_native_version_code": 1356136,
  "mandatory": false,
  "published_at": "2026-11-18T15:30:00+08:00",
  "notes": "背景透明度、侧栏不透明",
  "signing_key_id": "ota-2026",
  "sig_alg": "SHA256withECDSA",
  "signature": "MEUCIQ...（base64 DER）"
}
```

### 4.4 签名与校验

签名载荷（UTF-8，字段以 `\n` 分隔，末尾也有 `\n`）：

```
flyai-web-v1
<version_code>
<sha256>
<size>
<min_native_version_code>
<signing_key_id>
```

- 算法：ECDSA P-256 + SHA-256（Android `Signature.getInstance("SHA256withECDSA")`，API 23+ 可用）。
- 私钥：开发机 `%USERPROFILE%\.flyai\ota-signing.key`（PEM，绝不入库、绝不放到服务器）。
- 公钥：Base64 DER 内嵌在 `FlyWebUpdaterPlugin.java`；支持按 `signing_key_id` 内置多把公钥以便轮换。
- 校验顺序（原生侧，缺一不可）：
  1. manifest 字段类型/范围校验（`version_code > 0`、`size` 在 1 B~32 MB、hex 长度 64）；
  2. 用内嵌公钥验签；
  3. 下载完成后对 zip 全文件计算 SHA-256 并与 manifest 比较；
  4. 解压时逐条目校验路径与大小（CRC 由 `ZipFile` 读取时校验）。
- **JS 侧传来的 manifest 字段一律视为不可信**，原生必须独立重算签名载荷并验签，不能只信 JS 的「已校验」结论。
- 私钥丢失 = 无法再发更新（只能发新版 APK 换公钥）。发布前备份私钥到离线介质。

### 4.5 `config.json` 规则

Web 包在服务器上是 Web 站点，`config.json` 为 `{"apiBase": ""}`（同源）。APK 必须用绝对地址。

**规则：OTA 解压完成后，原生用 APK `assets/public/config.json` 的内容覆盖新包内的 `config.json`。**

- 保证 OTA 包可直接由服务器站点发布同一份产物，无需为 APK 特制；
- 保证用户通过 localStorage `flyai-runtime-config` 做的运行期覆盖仍旧生效（`runtimeConfig.ts` 的优先级不变）；
- 发布脚本也检查 zip 内若含 `config.json`，其 `apiBase` 必须为空串，否则拒绝发布。

---

## 5. 服务端

### 5.1 新增/修改文件

| 文件 | 改动 |
|---|---|
| `flyai/config.py` | 新增 `server.web_update.enabled`（缺省 `True`）、`server.web_update.keep`（缺省 3） |
| `flyai/updates.py` | 新增 `WebBundle` dataclass 与 `find_web_bundle(web_root)`：读取 `web/manifest.json`，校验 schema、版本号、zip 存在且大小一致；损坏时返回 `None` |
| `flyai/api/routes_app.py` | 新增 `GET /api/app/web-version` |
| `scripts/publish_web.py` | 新脚本：构建 → 打 zip → 计算 sha256 → openssl 签名 → 写 manifest → 落到 `webroot/web/` |
| `scripts/release.py` | 增加 `--publish-web` 选项（或直接调用 `publish_web.py`） |
| `tests/test_web_update.py` | 新增测试 |

不新增 Python 运行时依赖：签名只在发布机上用 `openssl` CLI 完成，服务器只读现成的 manifest。

### 5.2 接口

`GET /api/app/web-version`（公开，登录前后都可访问）：

- 成功：

```json
{
  "enabled": true,
  "version": "2026.11.18-1530",
  "version_code": 202611181530,
  "url": "/web/FlyAI-web-2026.11.18-1530.zip",
  "size": 1936123,
  "sha256": "9f2c...",
  "min_native_version_code": 1356136,
  "mandatory": false,
  "published_at": "2026-11-18T15:30:00+08:00",
  "notes": "背景透明度、侧栏不透明",
  "signing_key_id": "ota-2026",
  "sig_alg": "SHA256withECDSA",
  "signature": "MEUCIQ..."
}
```

- 无发布包或开关关闭：`{"enabled": false}`。
- 响应头：`Cache-Control: no-store`。
- `url` 为相对地址，由客户端按自己的 `apiBase` 拼绝对地址（与 `/api/app/version` 一致，兼容反代/CDN）。
- zip 由现有根目录兜底路由提供（`flyai/app.py:661-692`），`mimetypes` 已识别 `.zip`，并按 `FileResponse` 流式返回；客户端下载时带 `Accept-Encoding: identity`。

### 5.3 发布脚本 `scripts/publish_web.py`

流程：

1. 校验工作区干净（可 `--allow-dirty` 跳过，默认警告）；
2. `cd webapp && npm run build && npm run sync:static`（复用现有构建链）；
3. 以 `flyai/web/static` 为源生成 zip（存储模式为 deflate，排除 `.map`、`config.json` 按规则校验）；
4. 生成 `version`/`version_code`（本地时间 `YYYYMMDDHHMM`，若与已有 manifest 相同则报错，可用 `--version-code` 覆盖）；
5. 计算 `size`、`sha256`；
6. 用 `openssl dgst -sha256 -sign <key>` 对 §4.4 载荷签名；`--key`/环境变量 `FLYAI_OTA_KEY` 指定私钥，缺省 `%USERPROFILE%\.flyai\ota-signing.key`；
7. 写 `manifest.json`，zip 与 manifest 复制到 `webroot/web/`，同时在 `release/` 留一份；
8. 清理超过 `keep` 个的旧 zip（按 manifest 里的版本排序）。

`min_native_version_code` 来源：

- 默认 = 当前 `variables.gradle` 的 `appVersionCode`（保守：本 Web 包只保证在当前 APK 上工作）；
- 可通过 `--min-native 1346135` 下调（纯样式/文案改动，旧 APK 也兼容时）；
- **绝不能**高于当前已发布的 APK 版本，否则所有客户端都拒绝该包。

### 5.4 服务端缓存与压缩（建议同批做）

| 路径 | 头 |
|---|---|
| `/`、`/static/*` | 维持 `no-cache`（现有 ETag 协商） |
| `/web/*.zip` | `public, max-age=31536000, immutable`（URL 带版本） |
| `/api/app/web-version` | `no-store` |

浏览器访问建议加 gzip/brotli 中间件（`starlette.middleware.gzip` 或自建），把 851 KB 的 JS 压到约 250 KB。OTA 走 zip，不依赖服务端压缩。

### 5.5 后端测试

`tests/test_web_update.py`：

1. 无 manifest → `find_web_bundle` 返回 `None`，接口 `{"enabled": false}`；
2. 合法 manifest + zip → 接口字段齐全、`Cache-Control: no-store`；
3. manifest 损坏（JSON 错、缺字段、版本非数字）→ `None`；
4. zip 缺失或大小不符 → `None`；
5. `enabled=false` 配置 → `{"enabled": false}`（即使有 manifest）；
6. 签名存在且 `sig_alg` 正确；用 openssl 验证签名（环境无 openssl 时 skip）；
7. 发布脚本的 zip 内容检查：非法路径、缺 `index.html`、含 `.map`、`config.json.apiBase` 非空 → 拒绝；
8. 与现有 `test_app_version.py` 一致地验证相对 URL 语义。

---

## 6. Android 原生

### 6.1 新增插件 `FlyWebUpdaterPlugin.java`

在 `MainActivity.onCreate()` 中于 `super.onCreate` 之前注册（与现有三个插件同处）。

| 方法 | 入参 | 返回 | 说明 |
|---|---|---|---|
| `getState` | — | `{current:{version,versionCode,source,dir}, pending:{...}|null, previous:{...}|null, bundledConfig, lastRollback}` | 前端读版本状态 |
| `downloadAndStage` | `{url, version, versionCode, size, sha256, signature, sigAlg, signingKeyId, minNativeVersionCode, autoApply}` | `{ok, versionCode}` | 下载→验签→校验→解压→写 pending；`autoApply` 为真时提升并重载 |
| `applyPending` | — | `{ok, applied: {...}}` | 提升 pending，写 `serverBasePath`，`bridge.setServerBasePath` |
| `discardPending` | — | `{ok}` | 删除待生效包 |
| `markReady` | — | `{ok}` | 看门狗复位（App 挂载后调用） |
| `resetToBundled` | — | `{ok}` | 故障排查：清空 OTA 状态并加载内置包 |
| `getBundledConfig` | — | `{json}` | 读 `assets/public/config.json`（调试用） |

事件：`downloadProgress`，`{loaded,total,percent}`，与 `FlyUpdaterPlugin` 相同的 256 KiB / 200 ms 节流。

实现要点：

- 单线程 `ExecutorService`，`AtomicBoolean` 防并发；`handleOnDestroy` 时 `shutdownNow`。
- 下载完全复用 `FlyUpdaterPlugin` 的模式：`HttpURLConnection`、手动跟随重定向（最多 5 跳）、连接 15 s / 读 30 s 超时、`Accept-Encoding: identity`、`.part` 临时文件、失败即删。
- 仅接受 `https`（Debug 允许 `http` 指向模拟器宿主）。
- 下载上限 32 MB，超出直接失败。
- 签名验证在下载完成后、解压开始前；任何失败都不触碰当前版本。

### 6.2 私有目录布局

```
filesDir/ota/
  state.json
  bundles/
    <versionCode>/            # 已解压、可直接 hostFiles 的包
  download/
    <versionCode>.zip.part
  bundles/<versionCode>.tmp/  # 解压中的临时目录（启动时清理）
```

`state.json`（原生原子写入：`.tmp` + `rename`）：

```json
{
  "schema": 1,
  "baseApkVersionCode": 1356136,
  "current": {"version": "2026.11.18-1530", "versionCode": 202611181530},
  "previous": {"version": "2026.11.15-1010", "versionCode": 202611151010},
  "pending": null,
  "boot": {"attempts": 0, "lastVersionCode": 202611181530},
  "lastRollback": null
}
```

`basePath` 直接使用 `new File(context.getFilesDir(), "ota/bundles/" + code).getAbsolutePath()`。

### 6.3 启动钩子 `OtaBoot`

必须**在 `super.onCreate` 之前**执行，因为 Capacitor 在 Bridge 创建时读取 `serverBasePath`：

```java
@Override
public void onCreate(Bundle savedInstanceState) {
    OtaBoot.beforeBridge(this);       // 回滚判定 + 应用 pending + 写 prefs
    registerPlugin(FlySaverPlugin.class);
    registerPlugin(FlyNotifyPlugin.class);
    registerPlugin(FlyUpdaterPlugin.class);
    registerPlugin(FlyWebUpdaterPlugin.class);
    super.onCreate(savedInstanceState);
}
```

`OtaBoot.beforeBridge(context)` 逻辑：

1. 读 `state.json`；不存在则记录 `baseApkVersionCode` 并返回。
2. `baseApkVersionCode != 当前 PackageInfo.versionCode` → 新基线，删除全部 OTA 目录与状态，清空 `serverBasePath`，返回。
3. 清理残留 `*.tmp` 与超期 bundles。
4. 看门狗：
   - 若 `current != null` 且 `boot.attempts >= 2` → 回滚：`current = previous`（无 previous 则用基线），`previous = null`，清 `serverBasePath`，记录 `lastRollback`，不再提升 pending。
   - 否则 `boot.attempts += 1`（仅对 OTA current 计数）。
5. 若 `pending` 存在且目录存在 → `previous = current`、`current = pending`、`pending = null`。
6. 若 `current` 存在 → 写 `serverBasePath` = current 目录；否则删除该 prefs 键。
7. 原子写回 `state.json`。

SharedPreferences 键与 Capacitor 保持一致：

- 文件名：`CapWebViewSettings`
- 键：`serverBasePath`
- 同时维护 Capacitor 的 `lastBinaryVersionCode` / `lastBinaryVersionName` 无需我们处理（Capacitor 自己会写；新 APK 时它会清空 path，但我们的 OTA 状态已在第 2 步清空，不会冲突）。

> 注：`isNewBinary()` 在升级 APK 后首次启动时会把 `serverBasePath` 清空；我们的第 2 步保证 OTA 状态同步清空，二者语义一致（新 APK 内置前端优先）。

### 6.4 下载与解压安全清单

| 检查 | 处理 |
|---|---|
| URL scheme | 只允许 `https`（Debug 可 `http`） |
| 重定向 | 逐跳校验，每跳都必须是 https，最多 5 跳 |
| 大小 | `Content-Length` > 32 MB 或实际下载 > 32 MB → 中止 |
| SHA-256 | 与 manifest 严格相等（小写 hex） |
| 签名 | ECDSA P-256 验签失败 → 删除临时文件 |
| zip 条目路径 | 拒绝绝对路径、`..`、`\`、空段、以 `/` 开头 |
| zip 条目类型 | 只允许普通文件与目录，拒绝符号链接（ZipEntry 无类型时可结合 `ZipFile` 判断） |
| zip 炸弹 | 条目数 ≤ 512、总解压 ≤ 32 MB、单条目 ≤ 16 MB、压缩比 > 1000:1 拒绝 |
| 顶层白名单 | 只允许 §4.2 列出的名字 |
| `config.json` | 解压后强制用 APK 内置内容覆盖 |
| 目标目录 | 只在 `Files.getFilesDir()/ota` 下创建；路径拼接后必须仍以该目录为前缀 |
| 并发 | 同一时间只允许一个下载/应用任务 |
| 降级 | `versionCode <= current` 拒绝（除非显式 `allowDowngrade` 调试） |

### 6.5 原子切换

1. 解压到 `bundles/<code>.tmp/`；
2. 校验必需文件存在、`config.json` 已覆盖；
3. `rename(tmp, bundles/<code>)`（同文件系统内原子）；
4. 更新 `state.pending` 并原子落盘；
5. 只有第 4 步成功后，才允许前端提示「已下载」。

任何一步失败：删除 `.tmp` 与下载 `.part`，当前版本完全不受影响。

### 6.6 回滚看门狗

目的：覆盖「包能下载、能解压，但 JS 一启动就崩」的情况。

- 每次冷启动 `OtaBoot` 对 OTA current 记一次 `attempts`；
- 前端在 `App` 挂载后尽快（首个 `useEffect`，不等待网络）调用 `markReady` → `attempts = 0`；
- 连续 2 次启动都未 `markReady` → 下一次启动自动回退到 `previous`（没有则基线）；
- 回退后不自动重试同一个 `version_code`（写 `lastRollback.code`，check 时跳过该版本）；
- 前端可通过 `getState().lastRollback` 展示一次 toast：「上次更新未通过启动检测，已自动恢复」。

边界：

- 用户在 2 秒内强杀 App 会被计入一次失败；连续两次才回滚，误判概率可接受。
- 若基线包也起不来，回滚到基线后不再做 OTA（避免循环）。

### 6.7 清理与容量

- 只保留 `current` 与 `previous` 两个目录，其余在每次启动与下载成功后删除；
- 一个包约 4.6 MB 解压后，两个约 9 MB，加上临时下载约 2 MB，峰值 < 15 MB；
- 下载目录在 `filesDir`，不占用户可见存储；用户「清除数据」时一并清除（符合预期）。

### 6.8 Debug 构建

- `BuildConfig.DEBUG` 时默认关闭 OTA 检查（避免调试包被远程包覆盖）；
- 可通过 `adb shell setprop` 或设置页隐藏入口开启，便于测试；
- 发布包遇到 `FLYAI_APP_SERVER` 指向的本地地址时仍可更新（自测用）。

---

## 7. 前端接入

### 7.1 新增 `webapp/src/services/flyai/webUpdate.ts`

```ts
export type WebUpdateManifest = {
  enabled: boolean
  version?: string
  versionCode?: number
  url?: string
  size?: number
  sha256?: string
  minNativeVersionCode?: number
  mandatory?: boolean
  notes?: string
  signingKeyId?: string
  sigAlg?: string
  signature?: string
}

export type WebUpdateState = {
  current: { version: string; versionCode: number; source: 'apk' | 'ota' } | null
  pending: { version: string; versionCode: number } | null
  lastRollback: { versionCode: number } | null
}

export const getWebUpdateState = async (): Promise<WebUpdateState | null>
export const checkWebUpdate = async (options?: { interactive?: boolean }): Promise<...>
export const applyWebUpdate = async (): Promise<void>
export const markWebReady = async (): Promise<void>
```

`checkWebUpdate` 流程：

1. `isNativeApp()` 且 `plugin('FlyWebUpdater')` 存在，否则静默返回（覆盖旧 APK / 浏览器）；
2. `getWebUpdateState()`；若 `pending` 已存在 → 直接提示「下次启动生效」，不再下载；
3. `fetch(apiUrl('/api/app/web-version'), { cache: 'no-store' })`，8 s 超时，失败静默；
4. `enabled !== true` → 返回；
5. 版本比较：`manifest.versionCode <= max(current.versionCode, pending?.versionCode ?? 0)` → 返回；
6. 若 `manifest.versionCode === lastRollback.versionCode` → 返回；
7. 原生版本闸：`getNativeAppInfo().build` 解析为 versionCode，小于 `minNativeVersionCode` → 调用现有 APK 更新提示（`checkForAppUpdate`），不下载；
8. 调 `downloadAndStage({...manifest, url: absoluteWebUrl(manifest.url)})`，订阅 `downloadProgress`；
9. 成功：toast；`mandatory` 时弹确认框，确认后 `applyPending`；
10. 失败：静默记录（不打扰用户），下次启动重试；网络错误不提示。

触发时机：

- `App.tsx` 登录态就绪后调用一次（与 `checkForAppUpdate` 并列）；
- `appStateChange` 回到前台且距上次检查 > 1 小时（复用 `services/native.ts` 的监听，或新增轻量监听）；
- 设置页「检查更新」手动触发（`interactive: true`，失败时提示）。

### 7.2 `markReady` 接入

在 `App.tsx` 首次渲染成功后的 effect 中调用（不等登录、不等网络）：

```ts
useEffect(() => {
  void markWebReady()
}, [])
```

若 OTA 包启动失败，这个调用不会发生，看门狗生效。

### 7.3 设置页 UI

在「设置 → 通用」底部或「关于」区域增加：

- 当前版本：`FlyAI 1.35.6136 · 界面 2026.11.18-1530`（无 OTA 时显示「内置」）；
- 按钮「检查更新」→ 手动 `checkWebUpdate({ interactive: true })`；
- 有 pending 时显示：「新界面已下载，下次启动生效」+「立即重启生效」；
- 下载中显示进度（百分比）；
- 失败状态仅手动检查时展示错误文案。

新增 i18n 键（zh/en 对齐）：`webUpdateChecking`、`webUpdateUpToDate`、`webUpdateAvailable`、`webUpdateDownloading`、`webUpdateStaged`、`webUpdateApplyNow`、`webUpdateFailed`、`webUpdateRestartHint`、`webUpdateRolledBack`、`webUpdateRequiresApp`、`webUpdateCurrent`、`webUpdateBundled`。

### 7.4 与现有 APK 更新的协同

- `minNativeVersionCode` 不满足时，优先走现有 `checkForAppUpdate`（下载整包 APK 安装）。
- 两个更新通道互不阻塞：APK 更新提示照旧按 `/api/app/version` 检查。
- 发布规范：**新增原生插件方法/权限 → 必须同时提升 APK 版本并先发 APK，再把 Web 包的 `min_native_version_code` 提到该 APK**。

---

## 8. 安全设计

### 8.1 威胁模型

| 威胁 | 影响 | 对策 |
|---|---|---|
| 服务器被入侵/篡改 Web 包 | 下发恶意 JS | ECDSA 签名 + APK 内置公钥；服务端无签名私钥 |
| 中间人篡改下载 | 同上 | HTTPS + 签名；重定向逐跳校验 |
| 恶意 zip（zip-slip） | 覆盖 App 私有文件 | 路径白名单、`canonicalPath` 前缀校验、拒绝绝对路径/`..` |
| zip 炸弹 | 撑爆存储/OOM | 条目数/总大小/单条目/压缩比上限 |
| 降级攻击 | 回退到有漏洞版本 | 拒绝 `versionCode <= current` |
| 伪造 manifest 诱导下载 | 浪费流量/触发错误 | 版本单调 + 签名覆盖关键字段 |
| 私有目录被其他 App 读取 | 信息泄露 | `filesDir` 仅本 App 可访问（未 root） |

### 8.2 密钥管理

- 私钥只存在发布机，建议离线备份；不提交、不放入 `config/config.json`、不放进源码 zip。
- `scripts/publish_web.py` 检测不到私钥直接失败，绝不生成「无签名」包。
- `docs`/README 只记录公钥指纹，不记录私钥内容。
- 轮换：新 key id 内嵌到新版 APK，同时保留旧公钥一段时间；manifest 带 `signing_key_id`。

### 8.3 不引入的能力

- OTA 包只能包含静态 Web 资源，不能包含 Java/native 代码；
- 不能新增 Android 权限；
- 不能修改插件行为；
- 不执行服务器下发的任意 shell/命令。

---

## 9. 增量与性能

### 9.1 单 bundle 的影响

当前所有前端代码打成一个 `main.*.js`（851 KB），任何一行改动都会改变 hash，导致每次 Web 更新都要传完整 zip（约 1.9 MB）。这仍然远小于 6 MB APK，且只在启动时后台下载，可接受。

### 9.2 Phase 1（本期）：全量 zip

- 传输：约 1.9 MB（zip 内已压缩；JS 在 zip 中约 250–300 KB）；
- 校验/解压：低端机 < 1 s；
- 应用：下次启动无额外耗时（只是换目录）。

### 9.3 Phase 2（可选）：逐文件增量

当网页继续膨胀后再做：

1. manifest 增加 `files: [{path, sha256, size}]`（约 152 条，< 20 KB）；
2. 客户端与当前 bundle 对比，只下载变化的文件到新目录，其余从当前目录复制（`Files.copy`，1.9 MB 本地复制毫秒级）；
3. 逐文件 SHA-256 校验后写 pending；
4. 保留签名覆盖整个 manifest（防止文件列表被篡改）。

前提：先做代码分割，否则「唯一 chunk 变化」= 全量。建议同批做：

```ts
// rspack.config.ts
optimization: {
  splitChunks: {
    chunks: 'all',
    cacheGroups: {
      react: { test: /[\\/]node_modules[\\/](react|react-dom|zustand|scheduler)[\\/]/, name: 'react' },
      markdown: { test: /[\\/]node_modules[\\/](marked|markdown|katex|highlight)[\\/]/, name: 'markdown' },
    },
  },
},
```

再配合 `React.lazy` 拆出设置、Galgame、Pixiv 等重面板。收益：首屏更快、增量更小、浏览器缓存更有效。

### 9.4 下载策略

- 默认允许移动网络（包 ≤ 2 MB），可在设置中加「仅 Wi-Fi」开关（Phase 2）；
- 冷启动检查失败不重试同一次会话；回到前台 1 小时后再检查；
- 下载与聊天请求互不影响（原生线程池）；
- 失败后 24 小时内不做同一版本的重复自动下载（内存 + 本地记录）。

### 9.5 首包体积参考

| 内容 | 大小 |
|---|---|
| APK 整包 | 6.0 MB |
| Web 静态目录 | 4.61 MB |
| Web zip（OTA） | 1.88 MB |
| 其中 JS（zip 内） | ~250–300 KB |
| 其中 CSS（zip 内） | ~20 KB |

---

## 10. 异常与回滚矩阵

| 场景 | 行为 |
|---|---|
| 服务器不可达/超时 | 静默跳过，使用当前包；不影响启动 |
| manifest 损坏/字段非法 | 忽略本次更新 |
| 版本不高于当前 | 忽略 |
| `minNativeVersionCode` 不满足 | 不下载；提示更新 APK（复用现有通道） |
| 下载中断/校验失败 | 删除 `.part`，当前版本不变；本次会话不再重试 |
| SHA-256 不符 | 删除临时包，记录日志，当前版本不变 |
| 签名不符 | 同上，并记录 `signing_key_id` 便于排查 |
| zip 非法（路径/大小/条目） | 拒绝解压，删除 `.tmp` |
| 解压过程中断电 | 启动时清理 `.tmp`，pending 未写，等同失败 |
| 写 pending 后、启动前被清理数据 | 下次检查重新下载 |
| 新包启动即白屏/JS 崩 | 2 次冷启动未 `markReady` → 回滚 previous/基线 |
| 回滚后仍失败 | 回基线并停止 OTA（记录 `lastRollback`，不再自动尝试该版本） |
| APK 升级 | 清除 OTA，用新基线；随后按新 manifest 检查 |
| 用户手动「重置内置页」 | 清空 OTA 状态，加载基线 |

---

## 11. 兼容性

### 11.1 origin 与 localStorage

`hostFiles` 不改变 `appUrl`（仍为 `https://localhost`），因此：

- `localStorage`（token、主题、背景缓存、草稿、会话 UI 状态）全部保留；
- `IndexedDB`/Cache Storage 同源保留；
- Capacitor 插件调用不受影响（bridge 注入逻辑不变）；
- 不需要重新登录、不触发「跨域」类问题。

这是相对「远程优先」方案的核心优势。

### 11.2 多服务器 / 自选后端

- manifest 从客户端当前 `apiBase` 获取；zip URL 也基于同一 `apiBase` 拼接；
- 每台服务器可发布自己的 Web 包，互不干扰；
- 切换 `apiBase` 后首次启动会从新服务器取 manifest，版本号若更低则不会覆盖（安全策略）；管理员可在设置里「重置内置页」后再更新。

### 11.3 旧 APK

- 旧 APK 无 `FlyWebUpdater` 插件：`plugin()` 为 `undefined`，前端所有 OTA 逻辑 no-op；
- 旧 APK 仍走现有整包更新；
- 本方案首个 APK（1.35.x）上线后，后续前端才能热更。

### 11.4 Web 浏览器

- 浏览器不参与 OTA，直接使用服务器最新静态资源；
- `checkWebUpdate` 在非原生环境直接返回。

### 11.5 Python 3.9

服务端只做 manifest 读取与 JSON 返回，无新语法要求；保持现有 3.9.13 约束（无 `match`、无 `TaskGroup`、无 `StrEnum`、无 PEP 604）。

### 11.6 WebView 与 Android 版本

- `minSdk 23` 起支持 `hostFiles`、`SHA256withECDSA`、`java.util.zip`；
- 已内置最低 WebView 版本检查（Capacitor）；
- 签名验证使用 Android 平台 `Signature`，不引入 BouncyCastle。

---

## 12. 发布流程

### 12.1 首次上线（必须发一次 APK）

1. 实现本方案（插件 + 启动钩子 + 前端接入 + 设置页）；
2. 版本号按「新功能 → 第二位 +1」：`1.34.6135` → **`1.35.6136`**（versionCode `1356136`）；
3. 编译、签名、上传 APK 到 `webroot`；
4. 发布第一个 Web 包（内容可与内置一致或更新），`min_native_version_code = 1356136`；
5. 在测试机验证：下载 → 下次启动生效 → 版本号变化；
6. 观察 1–2 天，旧版 APK 用户逐步升级。

### 12.2 日常 Web-only 发布

```bash
.venv/Scripts/python scripts/publish_web.py
# 构建前端 → 生成 zip/manifest → 签名 → 写入 webroot/web/
```

然后按现有方式同步/部署 `webroot`。客户端下次启动或回前台自动检查。

### 12.3 含原生改动的发布

1. 先发新版 APK（含新插件/权限）；
2. 等目标用户升级到新 APK（可用 `min_native_version_code` 强制）；
3. 再发 Web 包，并抬高 `min_native_version_code`；
4. 旧 APK 收到 manifest 后不下载，提示更新 APK。

### 12.4 回滚

- **服务端回滚**：检出旧版本代码 → 用 `--version-code` 生成一个更大的版本号 → 发布；
- 客户端会自动放弃坏包（看门狗）或在下一次检查时接受「新版本号」的旧代码；
- 不提供「降级安装」入口，避免被利用。

### 12.5 运维开关

- `server.web_update.enabled = false`：接口返回 `enabled:false`，客户端停止检查（已下载的 pending 仍会应用，避免半途而废；紧急情况可同时提高版本号发布一个空更新）；
- 删除 `webroot/web/manifest.json`：等同停发。

---

## 13. 测试计划

### 13.1 后端

见 §5.5。全量回归：

```bash
.venv/Scripts/python -m pytest -q
```

已知历史失败：`test_enter_send_logic.py`、`test_galgame_replay_logic.py`（依赖已删除的旧前端文件），与本方案无关。

### 13.2 设备手工矩阵

| # | 场景 | 预期 |
|---|---|---|
| 1 | 新 APK 首次启动（无 OTA） | 使用基线包，设置页显示「内置」 |
| 2 | 发布 v1，冷启动 | 后台下载并提示；当前版本仍为基线 |
| 3 | 再次冷启动 | 加载 v1，设置页版本更新；localStorage 登录态保留 |
| 4 | 立即生效按钮 | WebView 重载后用新包，无需杀进程 |
| 5 | 发布 v2，断网启动 | 不报错，使用 v1 |
| 6 | 发布 v2，恢复网络 | 自动下载，下次启动生效 |
| 7 | 截断 zip | 校验失败，当前版本不变，日志有记录 |
| 8 | 篡改 zip | SHA/签名失败，拒绝 |
| 9 | 篡改 manifest 的 minNative | 签名失败，拒绝 |
| 10 | 发布较低 version_code | 客户端忽略 |
| 11 | 新包 index 引用不存在的 JS | 2 次启动后自动回滚，toast 提示 |
| 12 | OTA 后安装新版 APK | 回到基线，OTA 目录被清理 |
| 13 | 切换 apiBase 到另一台服务器 | 从新服务器检查，不降级 |
| 14 | 老 APK（1.34） | 无 OTA 行为，整包更新照旧 |
| 15 | Debug 包 | 默认不检查 OTA |
| 16 | 清除 App 数据 | 回到基线，重新下载 |
| 17 | 飞行模式 + 冷启动 | 正常进入，无卡顿/白屏 |
| 18 | 下载中切后台/锁屏 | 下载继续或安全中止，无半成品生效 |

设备覆盖：至少一台 Android 8/9（API 26/28）、一台 Android 13+（API 33+）、一台国产 ROM（后台限制严格）。

### 13.3 故障注入工具

- 测试服务器放一个 `manifest.test.json` 指向坏包，用 `?test=1` 手动触发；
- `adb shell run-as top.eastcloud.flyai ls files/ota` 检查目录；
- 设置页隐藏入口：长按版本号 5 次显示「强制检查 / 重置内置页 / 清除 OTA」。

### 13.4 回归

- 聊天、流式输出、断线重连、TTS、图生图、Pixiv、Galgame、后台通知（FlyNotify）、保存文件（FlySaver）在 OTA 包下均需回归；
- 重点验证 Capacitor 插件在 `hostFiles` 模式下可用（插件与加载方式无关，但需实测确认）。

---

## 14. 实施计划

| 阶段 | 内容 | 交付物 | 预估 |
|---|---|---|---|
| P0 | 代码分割 + Web 缓存头/压缩（可选但建议） | `rspack.config.ts`、`flyai/app.py` | 0.5–1 天 |
| P1 | 后端 manifest/接口/发布脚本/签名 | `updates.py`、`routes_app.py`、`publish_web.py`、测试 | 1 天 |
| P2 | 原生插件 + OtaBoot + 看门狗 | `FlyWebUpdaterPlugin.java`、`OtaBoot.java`、`MainActivity.java` | 2–3 天 |
| P3 | 前端接入 + 设置页 + i18n | `webUpdate.ts`、`App.tsx`、Settings UI | 1 天 |
| P4 | 回滚/清理/日志/调试入口 | 与 P2/P3 合并 | 1 天 |
| P5 | 设备矩阵测试 + 首次 APK 发布 | APK `1.35.6136`、首个 Web 包 | 1–2 天 |
| 合计 | | | **6–8 人日**（不含 P0 的 0.5–1 天） |

里程碑：

- M1（P1+P2 后半）：本机制可在模拟器上手动切换目录并加载；
- M2（P2+P3）：端到端下载→生效→回滚闭环；
- M3（P5）：真机矩阵通过，发布 1.35.6136 与首个 Web 包。

---

## 15. 版本号与提交范围

版本号按用户约定：

```gradle
appVersionCode = 1356136
appVersionName = '1.35.6136'
```

提交只在用户明确要求时进行，显式列路径，绝不 `git add -A`。预计涉及：

- 新增：`docs/APK前端热更新方案书.md`、`flyai/api/routes_app.py`（改）、`flyai/updates.py`（改）、`flyai/config.py`（改）、`scripts/publish_web.py`、`tests/test_web_update.py`、`mobile/android/app/src/main/java/top/eastcloud/flyai/{FlyWebUpdaterPlugin,OtaBoot}.java`、`webapp/src/services/flyai/webUpdate.ts`；
- 修改：`mobile/android/.../MainActivity.java`、`webapp/src/app/App.tsx`、`webapp/src/components/settings/*`、`webapp/src/i18n/{zh,en}.ts`、`mobile/android/variables.gradle`（本地，不入库）、重建后的 `flyai/web/static/*`。

排除：`config/config.json`、`data/`、`*.jks`、`keystore.properties`、`webroot/`（除部署产物）、`release/`、`*.apk`、`android-backup-*`、`.smoke-quarantine/`、`node_modules`、`dist`、`mem.md`、`memory.md`、`flyai.txt`、`show/`、`test/`。

---

## 16. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Capacitor 升级改变 `hostFiles`/prefs 语义 | 低 | 高 | 锁定 Capacitor 7；升级时回归 OTA 用例；必要时改用显式 `server.url` 指向 `localhost` 的备用方案 |
| 签名私钥丢失 | 低 | 高 | 离线备份；私钥与公钥指纹记录在安全位置 |
| 国产 ROM 杀死下载线程 | 中 | 中 | 下载快速完成（1.9 MB）；失败下次重试；不使用前台服务 |
| 新包启动失败未触发看门狗（如 bridge 注入失败） | 低 | 高 | 保留「重置内置页」入口；OTA 版本号写入日志，服务端可停发 |
| 单 bundle 导致每次全量下载 | 高 | 低 | 1.9 MB 可接受；Phase 2 做代码分割 + 逐文件 |
| 服务端部署遗漏 `webroot/web/` | 中 | 低 | 接口返回 `enabled:false`，客户端无感 |
| 前端调用旧 APK 不存在的原生方法 | 中 | 高 | `min_native_version_code` 硬闸 + 前端 `plugin()` 存在性检查 |
| 用户长期不重启 App | 中 | 低 | 回前台检查 + 强制更新可提示「立即重启」 |

---

## 17. 待确认事项（建议默认值）

1. **生效时机**：默认「下次冷启动生效」；`mandatory` 包提供「立即重启」。→ 建议采用。
2. **是否做代码分割（P0）**：影响首屏和未来增量；不改变 OTA 机制。→ 建议本期一起做。
3. **是否实现逐文件增量（Phase 2）**：先上线全量，观察包体和更新频率后再定。→ 建议延后。
4. **「仅 Wi-Fi」下载开关**：包 ≤ 2 MB，默认关闭；如流量敏感可加。→ 建议 Phase 2。
5. **签名算法**：ECDSA P-256（`SHA256withECDSA`）。RSA-2048 也可，但密钥/签名更长。→ 建议 ECDSA。
6. **回滚阈值**：连续 2 次启动未 `markReady`。→ 建议采用。

---

## 附录 A：manifest 完整示例

```json
{
  "schema": 1,
  "version": "2026.11.18-1530",
  "version_code": 202611181530,
  "url": "/web/FlyAI-web-2026.11.18-1530.zip",
  "size": 1936123,
  "sha256": "9f2c8a1d9e7b3c4f5a6d8e0b1c2f3a4d5e6f708192a3b4c5d6e7f8091a2b3c4d",
  "min_native_version_code": 1356136,
  "mandatory": false,
  "published_at": "2026-11-18T15:30:00+08:00",
  "notes": "背景透明度、侧栏不透明",
  "signing_key_id": "ota-2026",
  "sig_alg": "SHA256withECDSA",
  "signature": "MEUCIQDx...base64...=="
}
```

## 附录 B：前端 API 约定

```ts
// webapp/src/services/flyai/webUpdate.ts
import { isNativeApp } from '../native.js'

export const isWebUpdateSupported = (): boolean =>
  isNativeApp() && Boolean(window.Capacitor?.Plugins?.FlyWebUpdater)

export const markWebReady = async (): Promise<void> => { /* no-op when unsupported */ }

export const checkWebUpdate = async (options?: {
  interactive?: boolean
}): Promise<'noop' | 'disabled' | 'current' | 'staged' | 'needs-apk' | 'failed'> => {
  // ...
}

export const applyWebUpdate = async (): Promise<void> => { /* reload */ }

export const getWebUpdateState = async (): Promise<WebUpdateState | null> => { /* ... */ }
```

原生事件约定（与现有 `FlyUpdater` 风格一致）：

```ts
FlyWebUpdater.addListener('downloadProgress', ({ loaded, total, percent }) => {})
```

## 附录 C：`state.json` 与 prefs 对照

| 存储 | 键 | 值 |
|---|---|---|
| `files/ota/state.json` | `current` / `previous` / `pending` / `boot` / `baseApkVersionCode` | 见 §6.2 |
| `CapWebViewSettings`（SharedPreferences） | `serverBasePath` | 当前 bundle 的绝对目录；无 OTA 时删除该键 |
| `CapWebViewSettings`（SharedPreferences） | `lastBinaryVersionCode` / `lastBinaryVersionName` | Capacitor 自维护，仅读取，不修改 |

## 附录 D：与「远程优先」方案对比（为何选 B）

| 维度 | 远程优先（server.url） | 本方案（本地 bundle OTA） |
|---|---|---|
| 离线启动 | 不支持 | 支持 |
| 登录态 origin | 变化，需重新登录/迁移 | 不变 |
| 多服务器 | 壳写死一个 URL | 随 `apiBase` |
| 生效速度 | 立即 | 下次启动或立即重启 |
| 实现量 | 0.5–1 天 | 6–8 人日 |
| 安全 | 服务器即代码源 | 离线签名，服务端被攻破也不受影响 |
| 失败兜底 | errorPath 静态页 | 自动回滚上一版本 |

结论：在「保留离线 + 同源 + 可控回滚」的前提下，B 的额外工程投入换来了长期可维护性；且 Capacitor 内建 `hostFiles` 使核心切换逻辑不需要自己写 WebView 拦截，实际风险低于预期。
