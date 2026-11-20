# FlyAI 新前端（chat-demo 底座）适配现状与缺口清单

> 分支：`chat-web`（工作区，未提交）
> 成文日期：2026-11-16
> 比对基准：
> - 新前端：`webapp/src`（chat-demo React 底座 + FlyAI 适配层）
> - 上一版 React 前端：`git show 90a8a13:webapp/src`（P3a 迁移版本）
> - 旧站点：`mobile/android/app/build/intermediates/assets/debug/mergeDebugAssets/public/`
>   （legacy 原生 JS 站，含 `lore.js`/`rooms.js`/`market.js`/`memory-manager.js`/`presets.js`/`proactor.js`/`mcp.js`/`loop.js`/`settings.js`/`editor.js`）
> - 后端：`flyai/api/routes_*.py`
>
> 范围：本文只盘点「已有后端能力在新 UI 中的适配状态」，不包含实现方案与代码变更。

---

## 0. 结论摘要

1. 新 UI 已把 **登录 + 单会话流式聊天** 这条主线接入 FlyAI 后端，表现层质量高于旧站，但 FlyAI 的领域面板基本都还没迁入。
2. 新前端实际调用的后端接口只有 11 个（见 §2.3），而后端仍注册着 15 个路由模块、200+ 个接口（见附录 A）。**大量功能属于「后端在、前端没入口」**，不是后端缺失。
3. 存在一批**静默丢功能**的问题：UI 元素或解析函数已经存在，但动作是空实现、数据被过滤、辅助函数无人调用。这类问题比「明显没有页面」更危险，容易在验收时被误判为已适配（见 §5）。
4. 旧站的功能面远大于 P3a React 版：市场、记忆管理、预设、proactor、MCP、长跑、用户管理只在 legacy 站存在过，**从未迁到 React**；本次 chat-demo 迁移又在此基础上把 P3a 已有的角色/世界书、群聊管理、Pixiv 候选墙、语音气泡、断线重连、撤回等一并丢掉了。
5. 移动端打包链路（`mobile/scripts/copy-web.js` → `flyai/web/static`）仍指向旧构建产物；新 UI 尚未产出可进 APK 的静态包，原生通知/下载桥也未随新前端验证。

---

## 1. 判定口径

| 标记 | 含义 |
|---|---|
| ✅ 已适配 | 新 UI 有入口，动作真正请求后端并正确回显 |
| 🟡 部分适配 | 只读、只显示、或只覆盖正常路径；关键动作缺失 |
| 🟢 有壳无逻辑 | 组件/按钮/服务函数存在，但点击无效、无调用方或数据被丢弃 |
| ⬜ 未适配 | 新 UI 完全无入口 |

后端列：`✅` 表示接口已注册且可直接复用；`—` 表示原本就是纯前端逻辑或尚无接口。

---

## 2. 已适配部分

### 2.1 功能清单

| 功能 | 新 UI 位置 | 状态 | 说明 |
|---|---|---|---|
| 登录 / 登出 / 当前用户 | `services/flyai/auth.ts`、`components/auth/LoginView.tsx` | ✅ | Bearer Token 存 localStorage |
| 会话列表 / 详情 | `services/flyai/sessions.ts`、`services/chat/store.ts` | ✅ | 列表轻量字段、详情含 room/thread |
| 新建会话 | `sendMessage` 内先 `POST /api/sessions` | ✅ | 仅创建普通自由对话，无角色/房间参数 |
| 重命名 / 单条删除 | `components/sider/SessionItem.tsx` | ✅ | 走 `PATCH`/`DELETE /sessions/{sid}` |
| 流式聊天 | `services/flyai/chat.ts`、`services/flyai/events.ts` | ✅ | `POST /api/chat` fetch 流 + 命名事件解析 |
| 思考流 / 工具卡片 / 正文替换 | `services/chat/view.ts`、`components/messages/*` | ✅ | thinking 合并、工具行、`content_replace` |
| 召回提示（recall_notice） | `components/messages/RecallNotice.tsx` | ✅ | 仅「记忆召回」提示，不是消息撤回 |
| 消息编辑 / 重新生成 | `store.ts` 的 `editMessage`/`regenerateReply` | 🟡 | 普通 dm 可用；群聊直接禁用 |
| 消息版本切换 UI | `components/messages/BranchSwitcher.tsx` | 🟢 | 组件在，`selectMessageVersion` 是空函数 |
| 附件 / 图片上传 | `services/attachments.ts`、`ChatInput` | ✅ | base64 随 `/api/chat` 发送 |
| 生成图展示 / 全屏看图器 | `GeneratedImages.tsx`、`ImageViewer.tsx` | ✅ | 图片与生图参数可见 |
| 语音输入 ASR | `components/chatInput/useVoiceInput.ts` | ✅ | 直连 DeepSeek，Key 存本地 |
| 朗读 | `components/messages/ReadAloudButton.tsx` | ✅ | 走 chat-demo 的 `/tts`（DeepSeek），**非**豆包 TTS 工具 |
| 深度思考开关 | `ChatInput` → `deep_thinking` | ✅ | 全局开关，非会话级 |
| 联网开关 | `ChatInput` → `disabled_tools` | 🟡 | 只能整体启停 `web_search`/`web_fetch`，不能按工具配置 |
| 群聊历史回看 | `AssistantMessage` 的 speaker 行 | 🟡 | 已存群聊可看/可发，不能管理 |
| Galgame 历史只读 | `SessionView.readOnly` | ✅ | 按既定决策不恢复交互模式 |
| 草稿 / 失败回填 | `services/chat/drafts.ts` | ✅ | 草稿随会话保存 |
| 主题 / 语言 / 移动布局 | `app/*`、`components/sider/*` | ✅ | chat-demo 原有能力 |
| 设置弹窗 | `components/settings/SettingsDialog.tsx` | 🟡 | 仅主题/语言/朗读音色/位置/API Key/清空会话 |

### 2.2 仍可工作的后端能力（被新 UI 复用）

- `/api/auth/login|logout|me`
- `/api/sessions`（列表/创建/详情/改名/删除/批量删除/清空）
- `/api/sessions/{sid}/messages/{mid}`（撤回整轮）
- `/api/chat`、`/api/chat/runs/{id}/cancel`
- `/api/chat/active-run`、`/api/chat/runs/{id}`（已封装但未接入 UI）

### 2.3 新 UI 实际请求的接口全集

```
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/chat
GET  /api/chat/active-run?session_id=...
GET  /api/chat/runs/{id}
POST /api/chat/runs/{id}/cancel
GET  /api/sessions
POST /api/sessions
GET  /api/sessions/{sid}
PATCH /api/sessions/{sid}
DELETE /api/sessions/{sid}
POST /api/sessions/batch-delete
POST /api/sessions/{sid}/clear
DELETE /api/sessions/{sid}/messages/{mid}
```

> 注：`batch-delete`、`messages/{mid}`、`active-run`、`runs/{id}` 虽然已封装，但 UI 中没有调用方（见 §5）。

---

## 3. 缺口清单（按功能域）

优先级定义：

- **P0**：影响已上线行为的正确性，或用户一用就能感知的缺失
- **P1**：日常高频核心能力
- **P2**：进阶/重度功能
- **P3**：管理面与长尾能力

### 3.1 会话与消息

| # | 功能 | 旧站是否有 | 后端 | 新 UI | 优先级 | 备注 |
|---|---|---|---|---|---|---|
| 3.1.1 | 置顶 / 取消置顶 | 有 | ✅ `PATCH /sessions/{sid}` body `pinned` | ⬜ | P1 | `SessionSummary.pinned` 只读展示，无操作入口 |
| 3.1.2 | 会话内容搜索（标题/消息全文） | 有（「搜索对话内容…」） | — 纯前端过滤 | ⬜ | P1 | 新 `SearchPanel` 是联网搜索引用面板，不是会话搜索 |
| 3.1.3 | 撤回这一轮（墓碑） | 有 | ✅ `DELETE /sessions/{sid}/messages/{mid}` | ⬜ | P0 | `deleteMessage()` 已封装但无调用；`mapMessage` 直接跳过 `tombstone` |
| 3.1.4 | 撤回后「重新编辑」回填 | 有 | ✅ 墓碑 payload 含原文/图片 | ⬜ | P1 | 依赖 3.1.3 |
| 3.1.5 | 消息删除（非撤回语义） | 有 | 🟡 同上接口即撤回整轮 | ⬜ | P2 | 若需要纯删除需先确认后端语义 |
| 3.1.6 | 批量删除（多选） | 有 | ✅ `POST /sessions/batch-delete` | 🟢 | P2 | `batchDeleteSessions()` 无人调用 |
| 3.1.7 | Token 用量 / 上下文水位 | 有 | ✅ `SessionDetail.token_usage`、`max_tokens` | ⬜ | P2 | 类型已有，UI 未显示 |
| 3.1.8 | 导出会话（md/json） | 有 | ✅ `GET /sessions/{sid}/export` | ⬜ | P2 | 前端另有 chat-demo 本地导出服务，也未接入口 |
| 3.1.9 | 导出 ZIP（含附件） | 有 | ✅ `GET /sessions/{sid}/export-zip` | ⬜ | P2 | |
| 3.1.10 | 导入会话 / ZIP / Chatbox | 有 | ✅ `POST /sessions/import`、`import-zip`、`import-chatbox` | ⬜ | P2 | |
| 3.1.11 | 压缩对话 / 压缩另存 | 有（长跑相关） | ✅ `compress`、`compress/apply`、`compress/save-as` | ⬜ | P3 | |
| 3.1.12 | 存档点 | Galgame 有 | ✅ `galgame/savepoints*` | ⬜ | P3 | 与 Galgame 决策绑定 |
| 3.1.13 | 断线重连 / 刷新续流 | P3a 有 | ✅ `active-run` + `runs/{id}` 事件回放 | 🟢 | P0 | `discoverActiveRun`/`probeRun` 已实现，全项目无调用 |
| 3.1.14 | 继续生成 | P3a 有 | ✅ 重新生成接口可覆盖 | 🟢 | P3 | `continueReply` 空函数 |
| 3.1.15 | 上传重试 | P3a 有 | — | 🟢 | P3 | `retryUpload` 空函数 |
| 3.1.16 | 历史消息折叠 | P3a 有 | — | ⬜ | P3 | chat-demo 原有滚动模型未做折叠 |
| 3.1.17 | 消息版本切换（分支） | P3a 有 | ✅ `PATCH /api/rooms/{rid}/threads/{tid}/turns/{anchor}`（仅 dm） | 🟢 | P2 | 切换动作空实现 |

### 3.2 角色卡 / 世界观 / 内容库

| # | 功能 | 旧站 / P3a | 后端 | 新 UI | 优先级 |
|---|---|---|---|---|---|
| 3.2.1 | 角色卡列表、新建根卡/副本 | 均有 | ✅ `/api/characters*` | ⬜ | P1 |
| 3.2.2 | 角色编辑器（简介/经历/首条消息/示例/系统提示/后置指令） | 均有 | ✅（同上） | ⬜ | P1 |
| 3.2.3 | 角色卡导入（PNG/JSON/文本生成） | 均有 | ✅ `characters/import`、`import-card`、`gen/character-from-text` | ⬜ | P1 |
| 3.2.4 | 角色卡导出 / 克隆 / 发布 / 取消发布 | 均有 | ✅ `fork`、`export`、`publish`、`unpublish` | ⬜ | P2 |
| 3.2.5 | 角色属性表（定义 + 当前值） | 均有 | ✅ `gen/attributes` | ⬜ | P2 |
| 3.2.6 | 世界观列表 / 编辑器 / 世界书条目 | 均有 | ✅ `/api/worlds*`、`import-worldbook` | ⬜ | P1 |
| 3.2.7 | 会话绑定/切换角色与世界 | 均有 | ✅ `PATCH /sessions/{sid}` | ⬜ | P1 | 当前新会话只能是自由对话 |
| 3.2.8 | 会话专属角色副本编辑/导出/入库 | 均有 | ✅ `/sessions/{sid}/character*` | ⬜ | P2 |
| 3.2.9 | 角色音色绑定 | 旧站有 | ✅ 字段在 `Character.voice` | ⬜ | P2 | 与 TTS 气泡一起做 |

### 3.3 群聊 / 多线程

| # | 功能 | 旧站 / P3a | 后端 | 新 UI | 优先级 |
|---|---|---|---|---|---|
| 3.3.1 | 新建房间（单角色扮演 / 多 AI） | 均有 | ✅ `POST /api/rooms` | ⬜ | P1 |
| 3.3.2 | 成员增删 / 换角 / 自由对话位 | 均有 | ✅ `/rooms/{rid}/members*` | ⬜ | P2 |
| 3.3.3 | 群组线程（group） | 均有 | ✅ `/rooms/{rid}/groups` | 🟡 | P2 | 只能回看已存在群组 |
| 3.3.4 | 线程切换标签 / 重命名 / 删除 | 均有 | ✅ `/rooms/{rid}/threads/{tid}` | ⬜ | P2 |
| 3.3.5 | @提及成员 | P3a 有 | — 前端语法 | ⬜ | P2 |
| 3.3.6 | 房间级记忆与属性（参与者记忆） | 均有 | ✅ | ⬜ | P2 |
| 3.3.7 | 房间记忆快照 | 旧站有 | ✅ `rooms/{rid}/memory-snapshot` | ⬜ | P3 |
| 3.3.8 | 群聊内编辑 / 重新生成 | 均有 | ✅ | ⬜ | P2 | `store.ts` 对 `threadKind==='group'` 直接 return |
| 3.3.9 | 会话角色副本发布 | 均有 | ✅ `rooms/{rid}/members/{mid}/character/publish` | ⬜ | P3 |

### 3.4 记忆 / 主动系统（proactor）

| # | 功能 | 旧站 | 后端 | 新 UI | 优先级 |
|---|---|---|---|---|---|
| 3.4.1 | 记忆管理（来源/条目/删除/检索） | 有 | ✅ `/api/my/memory/stats|sources|ledger|{id}` | ⬜ | P2 |
| 3.4.2 | 召回日志 | 有 | ✅ `recall-logs` | ⬜ | P3 |
| 3.4.3 | 自我记忆（新建/修改/替代/删除/轨迹） | 有 | ✅ `/api/proactor/self-memory*` | ⬜ | P2 |
| 3.4.4 | 心情系统 / 时间线 / 清除 | 有 | ✅ `mood/*` | ⬜ | P3 |
| 3.4.5 | 承诺登记 / 关闭 / 取消 | 有 | ✅ `commitments*` | ⬜ | P3 |
| 3.4.6 | 定时作业 / 投票 / 主动行为开关 | 有 | ✅ `jobs*`、`polls*`、`proactive/*` | ⬜ | P3 |
| 3.4.7 | 打断审批 / 审批申请 | 有 | ✅ `/chat/loop/requests*`、`interrupt` | ⬜ | P3 |
| 3.4.8 | 外部触发端点 | 有 | ✅ `/api/ext/trigger`、`loop/hub/pending` | ⬜ | P3 |
| 3.4.9 | Watcher 监测进程 | 有 | ✅ `/api/agentdb/watchers*` | ⬜ | P3 |
| 3.4.10 | 提示词分区编辑 | 有 | ✅ `/api/agentdb/prompts*` | ⬜ | P3 |

### 3.5 预设 / 工具 / MCP / 长跑 / 浏览器

| # | 功能 | 旧站 | 后端 | 新 UI | 优先级 |
|---|---|---|---|---|---|
| 3.5.1 | 模型预设 CRUD / 启停 | 有 | ✅ `/api/presets*` | ⬜ | P2 |
| 3.5.2 | 预设克隆 / 导出 / 导入 / 酒馆导入 | 有 | ✅ `clone`、`export`、`import` | ⬜ | P2 |
| 3.5.3 | 从市场复制预设 | 有 | ✅ `market/entries/{mid}/install` | ⬜ | P3 |
| 3.5.4 | 工具总开关 / 按工具禁用 | 有 | ✅ `disabled_tools`（聊天请求体） | 🟡 | P2 | 仅联网一对开关 |
| 3.5.5 | 插件 / 工具清单 | 有 | ✅ `/api/plugins`、`/api/tools` | ⬜ | P3 |
| 3.5.6 | MCP server 登记/编辑/测试/启停 | 有 | ✅ `/api/agentdb/mcp/servers*` | ⬜ | P2 |
| 3.5.7 | AI 浏览器（Playwright 工具组）开关 | 有 | 🟡 通过 `disabled_tools` 传 `browser_*` | ⬜ | P2 |
| 3.5.8 | P 站搜图开关 | 有 | 🟡 通过 `disabled_tools` 传工具名 | ⬜ | P2 | 见 §3.7 |
| 3.5.9 | 长跑模式（轮次/预算/申请） | 有 | ✅ `/api/chat/loop/*` | ⬜ | P3 |
| 3.5.10 | 搜索引擎配置 | 有 | ✅ 配置在 `config` | ⬜ | P3 |

### 3.6 市场 / 模型 / 计费 / 管理后台

| # | 功能 | 旧站 | 后端 | 新 UI | 优先级 |
|---|---|---|---|---|---|
| 3.6.1 | 市场浏览 / 搜索 / 安装 / 刷新 | 有 | ✅ `/api/market/*` | ⬜ | P2 |
| 3.6.2 | 发布角色卡/世界观/预设/模型 | 有 | ✅ `market/publish` | ⬜ | P3 |
| 3.6.3 | 我的发布 / 编辑发布信息 | 有 | ✅ | ⬜ | P3 |
| 3.6.4 | 自建 LLM provider / 激活 / 探活 | 有 | ✅ `/api/config*` | ⬜ | P2 |
| 3.6.5 | 共享 LLM 模型 / 自建模型 | 有 | ✅ `/api/models/providers*` | ⬜ | P2 |
| 3.6.6 | 生图模型（含共享） | 有 | ✅ `/api/models/images*` | ⬜ | P2 |
| 3.6.7 | 用量与剩余配额 | 有 | ✅ `/api/my/quota` | ⬜ | P1 | 用户明确要求「每个人可看自己的用量」 |
| 3.6.8 | 用户管理（创建/改密/删除） | 有 | ✅ `/api/auth/users*` | ⬜ | P2 |
| 3.6.9 | 权限等级（0-255，admin=256） | 有 | ✅ | ⬜ | P2 |
| 3.6.10 | 个人设置（`my/settings`） | 有 | ✅ | ⬜ | P3 |
| 3.6.11 | 功能开关持久化（工具/浏览器/搜图） | 有 | ✅ `/api/config` | ⬜ | P2 |
| 3.6.12 | QQ 风格管理后台整体界面 | 有（legacy） | 后端基本齐 | ⬜ | P3 | `show/QQ_*.png` 风格稿未落到 React |

### 3.7 语音 / 搜图 / 生图 / Galgame

| # | 功能 | 旧站 / P3a | 后端 | 新 UI | 优先级 |
|---|---|---|---|---|---|
| 3.7.1 | 豆包 TTS 语音气泡（`text_to_speech` 工具结果） | P3a 有 `AudioCard` | ✅ 工具输出含音频标记 | ⬜ | P2 | 现在只有朗读按钮，**不是**同一功能 |
| 3.7.2 | 音色清单 / 角色绑定音色 / AI 临时覆盖 | 有 | ✅ 角色 `voice` 字段 + `/api/galgame/tts` 合成 | ⬜ | P2 | 音色清单旧站由前端维护 |
| 3.7.3 | Pixiv 候选墙 → 视觉筛选 → 原图回填 | P3a 有 | ✅ 工具在 | 🟢 | P2 | `markers.ts` 的 `parsePixivCands`/`buildPixivReveal` 无组件消费 |
| 3.7.4 | 生图输出网格 / 参数面板 | P3a 有 | ✅ | ✅ | — | 已适配 |
| 3.7.5 | SD 引擎配置界面 | P3a 有 | ✅ 配置在 `/api/config` | ⬜ | P2 | 生成本身由 AI 工具触发 |
| 3.7.6 | Galgame 交互（选择/存档/重开） | 旧站有 | ✅ `/api/galgame/*` | ⬜ | P3 | 既定决策：只读历史，暂不做 |

### 3.8 交付与运行

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 3.8.1 | 新 UI 构建产物进 `flyai/web/static` | ⬜ | `mobile/scripts/copy-web.js` 仍从该目录拷包 |
| 3.8.2 | APK 打包新 UI | ⬜ | 现有 APK 1.22.6127 早于本次 chat-demo 改造；需重新 `build` → `copy-web` → `cap sync` |
| 3.8.3 | 原生桥（通知/下载/状态栏）回归 | ⬜ | `FlyNotify` 依赖 run 轮询，新 UI 的 run 生命周期需核对 |
| 3.8.4 | 运行期配置 `config.json` 兜底 | ✅ | `services/flyai/runtimeConfig.ts` 已实现三级优先级 |
| 3.8.5 | localStorage 兼容旧站 Key | 🟡 | 新 UI 使用 `chat-demo:*`；旧站 `flyai-*` 的草稿/运行标记未迁移 |

---

## 4. 旧站模块 → 新 UI 覆盖映射

| legacy 模块 | 主要能力 | 新 UI 状态 |
|---|---|---|
| `auth.js` | 登录/注册/令牌 | ✅ 基本覆盖 |
| `app.js` / `ui.js` | 页面骨架、面板切换、用户管理、全局设置 | ⬜ 仅保留会话主界面 |
| `chat-stream.js` | SSE 流、工具/思考渲染 | ✅ 重写后覆盖 |
| `chat-history.js` | 历史渲染、撤回、重新编辑、重新生成、token 着色 | 🟡 历史/重生成有；撤回/重编辑无 |
| `editor.js` | 角色/世界书/副本编辑器、属性 | ⬜ |
| `lore.js` | 角色卡/世界观库、导入导出、发布 | ⬜ |
| `rooms.js` | 多 AI 房间、成员、线程、副本 | 🟡 仅回看已有群聊 |
| `memory-manager.js` | 记忆管理、召回日志 | ⬜ |
| `presets.js` | 模型预设、酒馆导入 | ⬜ |
| `market.js` | 市场发布/安装 | ⬜ |
| `mcp.js` | MCP server 管理 | ⬜ |
| `proactor.js` | 心情/自我记忆/承诺/主动 | ⬜ |
| `loop.js` | 长跑任务、压缩、审批 | ⬜ |
| `settings.js` | provider/模型/工具开关/音色/管理员配置 | 🟡 仅 API Key + 两个聊天开关 |
| `native.js` | Capacitor 桥 | ⬜ 未随新前端验证 |

---

## 5. 「有壳无逻辑 / 死代码」清单（验收重点）

这些问题在页面上看不出缺失，但功能实际不生效：

| 位置 | 问题 | 影响 |
|---|---|---|
| `webapp/src/services/chat/store.ts:1315` | `selectMessageVersion` 是空函数 | 消息版本切换按钮点击无反应 |
| `store.ts:1318` | `continueReply` 空实现 | 「继续生成」不可用 |
| `store.ts:1321` | `retryUpload` 空实现 | 附件重试不可用 |
| `store.ts:460` | `retrySaveSessions` 空实现 | 本地存储失败无重试 |
| `store.ts:246` 附近 | `mapMessage` 跳过 `tombstone`/`hidden` | 撤回后的墓碑行永远不显示 |
| `store.ts:350` | `pickThread` 只取第一个 dm 或第一个线程 | 多线程会话只显示一条线程 |
| `store.ts:1249`、`1285` | 群聊线程直接禁止编辑/重生成 | 群聊半残 |
| `services/flyai/chat.ts:74`、`83` | `discoverActiveRun`、`probeRun` 无调用方 | 刷新/断网后续流不恢复 |
| `services/flyai/sessions.ts:36` | `batchDeleteSessions` 无调用方 | 批量删除不可用 |
| `services/flyai/sessions.ts:53` | `deleteMessage` 无调用方 | 撤回整轮不可用 |
| `services/chat/markers.ts:78-97` | `extractGeneratedAudio`/`extractAudioMeta` 无消费 | 豆包语音气泡不出现 |
| `services/chat/markers.ts:110+` | `parsePixivCands`/`buildPixivReveal` 无消费 | Pixiv 候选墙不出现 |
| `services/chat/history/*` | 导出/导入/zip 全套服务 | 数据管理无 UI 入口 |
| `services/flyai/types.ts:143` | `TokenUsage` 仅类型 | 用量不显示 |
| `services/flyai/sessions.ts:6` | `pinned` 仅类型 | 不能置顶 |
| `components/settings/SettingsDialog.tsx` | 仍是 chat-demo 的通用设置 | 没有 FlyAI 的 provider/模型/工具/用户配置 |

---

## 6. 建议落地批次

按「用户日常感知 → 内容生产 → 重度功能 → 管理面」排序：

| 批次 | 内容 | 覆盖条目 | 预估 |
|---|---|---|---|
| **A. 会话体验补全** | 置顶、会话搜索、撤回+重新编辑、批量删除、token 用量、断线重连、导出/导入、压缩存档点 | 3.1 全部 P0/P1/P2 | M |
| **B. 角色与世界观** | 角色卡/世界书列表与编辑器、导入导出、会话绑定、副本编辑、属性生成 | 3.2 P1/P2 | L |
| **C. 群聊管理** | 新建房间、成员/换角、线程标签、@提及、群聊内编辑/重生成 | 3.3 P1/P2 | L |
| **D. 设置中心 + 管理后台** | provider/模型/生图模型、工具开关、用量/配额、用户管理、权限等级、QQ 风格后台 | 3.5 部分、3.6 P1/P2 | L |
| **E. 记忆 / proactor / MCP / 长跑** | 记忆管理、自我记忆、心情/承诺/作业/投票、watcher、提示词分区、长跑与审批 | 3.4、3.5.5-3.5.9 | XL |
| **F. 语音与搜图** | 豆包 TTS 气泡 + 音色绑定、Pixiv 候选墙、SD 配置 | 3.7 P2 | M |
| **G. 交付** | 构建进 static、APK、原生桥回归 | 3.8 | S–M |

> A 与 B 是使用频率最高的两项；C 之后偏内容生产与管理。D 中的「用量/配额」是用户明确要求过的最小管理能力，可单独提前。

---

## 7. 验收口径建议

每批交付时至少覆盖：

1. **正常路径**：新 UI 有入口 → 请求后端 → 刷新后回读一致。
2. **空/异常路径**：无权限、配额不足、长会话压缩失败、群聊中不存在的成员。
3. **跨端**：桌面 1440px 与移动 390px 各走一遍；移动端软键盘不遮挡输入框。
4. **历史兼容**：旧会话（含 galgame、群聊、墓碑、版本分支）在新 UI 打开不崩、不丢数据。
5. **断线**：聊天中刷新页面/断网 10 秒/切后台，恢复后能看到完整或半截回复（A 批次后）。
6. **不回归**：思考标题、工具轮合并、群聊发言者切分（近期修复的 `store.ts` 合并逻辑）。

---

## 附录 A：后端路由模块与前缀

| 模块 | 前缀 | 功能域 |
|---|---|---|
| `routes_agentdb` | `/api/agentdb` | MCP server、watcher、提示词分区 |
| `routes_auth` | `/api/auth` | 登录、用户管理 |
| `routes_chat` | `/api` | 聊天流、run、房间记忆快照 |
| `routes_config` | `/api` | provider/config、`my/settings`、`my/quota`、共享模型 |
| `routes_ext` | 无前缀（`/api/ext/*` 等） | 外部触发 |
| `routes_galgame` | `/api/galgame` | 交互/存档/TTS |
| `routes_gen` | `/api` | 文本生角色、属性生成 |
| `routes_loop` | `/api/chat/loop` | 长跑任务、审批、打断 |
| `routes_lore` | `/api` | 角色卡、世界观、导入、上传 |
| `routes_market` | `/api/market` | 市场发布/安装 |
| `routes_memory` | `/api/my/memory` | 记忆、召回日志 |
| `routes_plugins` | `/api` | 插件、工具清单 |
| `routes_preset` | `/api/presets` | 模型预设 |
| `routes_proactor` | `/api/proactor` | 心情、自我记忆、承诺、作业、投票、主动 |
| `routes_room` | `/api` | 房间、成员、群组、线程 |
| `routes_session` | `/api/sessions` | 会话、消息、导出导入、压缩、副本 |

## 附录 B：本次盘点依据的关键文件

- 新 UI：`webapp/src/app/App.tsx`、`services/chat/store.ts`、`services/flyai/*`、`components/settings/*`
- 后端：`flyai/app.py:585-600`、`flyai/api/routes_*.py`
- 旧站：`mobile/android/.../public/static/js/{lore,rooms,editor,market,memory-manager,presets,proactor,mcp,loop,settings,chat-history}.js`
- 上一版 React：`git show 90a8a13:webapp/src`