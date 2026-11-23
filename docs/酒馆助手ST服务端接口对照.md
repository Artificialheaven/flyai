# 酒馆助手 / ST 服务端接口对照

调研时间：2026-11-23  
上游：`N0VI028/JS-Slash-Runner` 4.12.2  
样本：本地 8 张含作者脚本的卡（重点《红楼风月录》《约会大作战P-v1》）  
FlyAI：`/openapi.json`，168 个路径

> 完整的 ST 服务端清单见 `docs/SillyTavern服务端接口清单.md`（44 个模块、267 条路由）。
> 本文只保留与 TavernHelper/两张卡直接相关的 24 个接口。

## 统计口径

从两个来源收集 ST 接口：

1. TavernHelper 源码里的 `fetch('/api/...')`；
2. 8 张卡的作者脚本里出现的 `/api/...` 字面量。

去重后共 **24 个**与酒馆助手/卡相关的接口。

| 状态 | 数量 | 含义 |
|---|---:|---|
| ✅ 直接兼容 | 0 | 路径、请求体、响应体都与 ST 一致 |
| 🟡 有近似接口 | 10 | FlyAI 有等价能力，但路径/字段不同，需要适配层 |
| 🔧 作者运行时 shim | 4 | 后端没有该接口，由 `authorRuntime.ts` 在 iframe 内模拟 |
| ⛔ 没有等价实现 | 10 | FlyAI 没有对应能力 |

## 明细

| ST 接口 | 用途 / 调用方 | FlyAI 现状 | 状态 | 说明 |
|---|---|---|---|---|
| `/api/backends/chat-completions/generate` | TH `generateRaw`、约会大作战手机生成 | `POST /api/chat`（SSE 主聊天） | 🔧 shim | 作者运行时把请求转成静默生成，返回 OpenAI 形状 JSON；不写聊天楼层 |
| `/api/backends/chat-completions/status` | TH `getModelList` / 模型状态 | `GET /api/models/providers` | 🟡 | 字段不同，需要适配 |
| `/api/characters/create` | TH `createCharacter` | `POST /api/characters` | 🟡 | FlyAI 用 JSON 创建，ST 用表单/头像 id |
| `/api/characters/edit` | TH `updateCharacterWith`、世界书绑定 | `PUT /api/characters/{cid}` | 🟡 | 字段集不同；不处理 ST 的 `chid`/头像文件语义 |
| `/api/characters/import` | TH `importRawCharacter` | `POST /api/characters/import`、`/import-card` | 🟡 | 路径同名，但请求体是 FlyAI 信封或 multipart，不是 ST 的原始表单 |
| `/api/characters/export` | TH 导出角色 | `GET /api/characters/{cid}/export` | 🟡 | 导出格式不同 |
| `/api/chats/get` | TH `getChatHistoryBrief/Detail`、约会大作战手机回读 | `GET /api/sessions/{sid}` | 🟡 | 会话结构不同；没有 `ch_name/file_name/avatar_url` 参数 |
| `/api/chats/import` | TH `importRawChat` | `POST /api/sessions/import`、`/import-chatbox`、`/import-zip` | 🟡 | 导入格式不同 |
| `/api/chats/group/get` | TH 群聊历史 | `GET /api/rooms/{rid}/...` | ⛔ | 没有同形状接口 |
| `/api/files/upload` | TH `uploadTavernFile` | `POST /api/upload` | ⛔ | 只接图片；没有 `users/files/` 通用文件区 |
| `/api/files/delete` | TH `deleteTavernFile` | — | ⛔ | 无 |
| `/api/files/sanitize-filename` | 约会大作战聊天改名校验 | — | ⛔ | 无；FlyAI 文件名规则在服务端内部 |
| `/api/extensions/install` | TH `installExtension` | `POST /api/market/entries/{mid}/install` | ⛔ | FlyAI 是插件/市场条目，不是 ST 扩展格式 |
| `/api/extensions/update` | TH `updateExtension` | — | ⛔ | 无 |
| `/api/extensions/version` | TH `getExtensionStatus` | — | ⛔ | 无 |
| `/api/extensions/delete` | TH `uninstallExtension` | — | ⛔ | 无 |
| `/api/settings/get` | TH 兼容层读取酒馆设置 | `GET /api/my/settings`、`/api/config` | 🟡 | 字段不同 |
| `/api/avatars/upload` | TH persona 头像 | `POST /api/upload` | 🟡 | 只返回 URL，没有 ST 的头像路径语义 |
| `/api/avatars/delete` | TH persona 删除头像 | — | ⛔ | 无 |
| `/api/worldinfo/get` | 红楼/约会大作战读世界书 | 无后端接口；作者运行时本地副本 | 🔧 shim | 返回当前角色世界书快照 |
| `/api/worldinfo/edit` | 红楼/约会大作战写世界书 | `POST /api/sessions/{sid}/st/world` | 🔧 shim | 未绑定世界书时返回 400 `world-not-bound` |
| `/api/worldinfo/delete` | 卡内删除世界书 | 作者运行时本地副本 | 🔧 shim | 只删本地副本，不通知宿主 |
| `/api/worldinfo/list` | 列出世界书 | — | ⛔ | 之前漏列；世界书管理面板需要 |
| `/api/worldinfo/import` | 导入世界书 | — | ⛔ | 之前漏列；ST 1.19.1 实际有该路由 |
| `/api/users/me` | 约会大作战读取账号 handle | `GET /api/auth/me` | 🟡 | 字段/鉴权方式不同；没有 `accountStorage` |

## 两张卡的实际阻塞

### 《红楼风月录》

- `/api/worldinfo/get` 可读；
- `/api/worldinfo/edit` 写回依赖 `/api/sessions/{sid}/st/world`，当前会话未绑定世界书时 400；
- 其余不直接依赖 ST 服务端接口。

### 《约会大作战P-v1》

- `/api/worldinfo/get` 可读，但写回同样受 `world-not-bound` 影响；
- `/api/chats/get`：手机配置保存后的回读校验会失败；
- `/api/users/me`：账号 handle 读取失败（`accountStorage` 也缺失）；
- `/api/files/sanitize-filename`：聊天改名校验失败；
- `/api/backends/chat-completions/generate`：shim 能返回文本，但 `context.generate('normal', ...)` 不会写主聊天楼层；
- 外部图片 `fetch(url)` 受 CSP `connect-src 'none'` 限制。

## 作者运行时 API 补齐计划

按你对全量对照的标记，排除：

- audio（8）
- extension（8）
- file（3）
- import_raw（5）
- script（1）

排除 25 项后，剩余 **112 项**；当前已有 27 项，待补 **85 项**。建议按批：

### Batch A：直接解卡（约 12 项）

- `setChatMessages` / `deleteChatMessages` / `rotateChatMessages`
- `generate` 全局入口
- `initializeGlobal`
- `errorCatched` / `getMessageId`
- 全局/helper 双向暴露补齐
- 事件 API 返回值、常量值、`eventMakeFirst/Last` 顺序
- `injectPrompts` 返回 `{uninject}`

### Batch B：世界书 / 角色 / 展示消息（约 20 项）

- `getWorldbook` / `replaceWorldbook` / `updateWorldbookWith` 数组语义
- `createWorldbook` / `createOrReplaceWorldbook` / `deleteWorldbook`
- `createWorldbookEntries` / `deleteWorldbookEntries`
- `getCharacterNames` / `getCharacterIds` / `getCurrentCharacterName`
- `getCharData` / `getCharAvatarPath` / `getChatHistoryBrief`
- `formatAsDisplayedMessage` / `retrieveDisplayedMessage` / `refreshOneMessage`

### Batch C：预设 / persona / 正则（约 30 项）

- preset 14 项（读写、加载、重命名）
- persona 11 项
- tavern_regex 5 项

### Batch D：ST 服务端适配层（非 TavernHelper API）

- `/api/chats/get` → 会话详情适配
- `/api/users/me` → `/api/auth/me` 适配
- `/api/files/sanitize-filename` → 宿主文件名规则
- `accountStorage` → 用户级键值存储
- `/st/world`：未绑定世界书时从角色卡世界书派生副本，而不是 400
- `context.generate('normal', ...)`：接入主聊天生成
- 外部图片/网络请求：按现有 `net-proxy` 策略放行或代理

## 建议

先做 **Batch A + `/st/world` 兜底**。这两块改动小，直接决定红楼和约会大作战能不能跑通主流程；Batch B/C 再按卡的实际使用逐步补。
