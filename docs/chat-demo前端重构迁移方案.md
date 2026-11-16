# FlyAI 前端重构迁移方案（基于 deepseek-ai/chat-demo）

> 分支：`chat-demo`（从 modern @ cfa1525 切出）
> 参考仓库：`F:\PythonProgram\chat-demo`（仓库外同级目录，v0.1.1，MIT）
> 成文日期：2026-11-16
> 范围声明：**不包含 Galgame 模式**（详见第 10 节）

---

## 1. 背景与现状摘要

### 1.1 现有前端（flyai/web/static，30 个文件，全部 Git 跟踪）

| 项 | 现状 |
|---|---|
| 技术栈 | 纯原生 HTML/CSS/JS，无框架、无构建、无 npm 工程 |
| 规模 | index.html 1644 行（100+ 固定 DOM id）、style.css 2770 行、23 个经典脚本共 13585 行 |
| 加载方式 | 顺序 `<script src>` 全局作用域，手工缓存戳 `?v=` |
| 状态 | 单一全局 `state`（app.js:5-65）+ 全局 DOM 缓存 `el`（ui.js:10-281），模块间全局函数直调 |
| 渲染 | 字符串模板 + innerHTML + escapeHtml；**无 Markdown/代码高亮/KaTeX/mermaid**，助手消息纯 textContent（仅 roleplay 正则着色） |
| 网络 | 全 REST（fetchJSON + Bearer）；聊天为 `POST /api/chat` 的 fetch ReadableStream SSE（命名事件 + `id:` 序号），非 EventSource/WS |
| 移动 | 同一份代码响应式（860px 断点），Capacitor 7 整站拷贝进 APK；native.js 桥接状态栏/返回键/下载/通知 |
| 本地存储 | 仅 localStorage（token、偏好、草稿 `flyai-draft-*`、运行标记 `flyai-run-*`）；**无 IndexedDB**，业务数据全在服务端 |

### 1.2 手机端（mobile/）

- `copy-web.js` 把 flyai/web/static 拷成 mobile/www → cap sync → APK；服务器地址构建期写死 `https://fly.eastcloud.top`（FLYAI_APP_SERVER 可替换）
- 原生仅 3 个自研 Java 文件：MainActivity、FlyNotify（后台轮询 run 完成发本地通知）、FlySaver（blob 存系统下载目录）；官方插件 app/local-notifications/status-bar
- **android/ 与 www/ 均被 .gitignore，3 个 Java 文件不在版本库**，动原生代码前必须先纳入版本管理或备份
- 当前 APK 1.21.6126（versionCode 1216126）

### 1.3 参考项目 chat-demo（19k 行，React 18 + TS + Rspack + Zustand + CSS Modules）

经逐文件审计，其价值是**一套质量不错的表现层基础设施**，而非可整包照搬的产品。

**可直接移植（高复用/低风险）**：

- `components/base/`：Button、IconButton、Menu、Modal、Select、Toast、Tooltip、anchored 定位、icons
- 主题 token 机制（theme.css 的 `--dsw-*` 三层 token：static 色板 → alias 语义 → specific 场景）、useTheme（light/dark/system）
- viewport.ts：visualViewport 键盘适配、防整页滚动
- Markdown 渲染链：mdast/micromark（GFM + math）+ MarkdownAstRenderer + CodeBlock(Prism 懒加载) + MathFormula(KaTeX) + MermaidDiagram + 流式节流 hook
- `services/api/sse.ts`：通用 WHATWG SSE decoder（与 DeepSeek 协议无关，可直接用）
- 消息分支模型 branches.ts（对应现有的编辑/重生成/版本切换）、drafts 草稿、MessageList 滚动锚定
- recorder.ts 的 WAV 录音思路、ZIP 读写工具（fflate）

**不可直接搬（高耦合，必须重写）**：

- store.ts（1094 行单助手状态机）、api/client.ts + parseEvent.ts + reply.ts + view.ts（DeepSeek/Anthropic 专用协议、citation 角标、Files API）
- IndexedDB schema（库名 chat-demo v11）与历史归档 v3——FlyAI 数据在服务端，**这套本地持久化整体不需要**
- 固定 MODEL/SYSTEM_PROMPT、ttsVoice 固定音色、i18n（FlyAI 纯中文，不搬）
- Rspack 配置中的 `__API_ORIGIN__`、`.js` 扩展名 alias、手写且滞后的 `.module.css.d.ts`

**chat-demo 没有、需新建的 FlyAI 领域能力**：角色卡/世界书、群聊多角色编排、生图/Pixiv、AI 浏览器工具卡、多 provider 配置、记忆/压缩、市场、MCP/长跑/proactor 管理面、账号权限等级、Capacitor 原生桥。

---

## 2. 目标架构与技术选型

| 维度 | 选型 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript（strict） | 与参考项目一致，移植成本最低 |
| 构建 | Rspack 2 + SWC + Lightning CSS | 同参考；dev server 代理 `/api` 到本地后端 |
| 包管理 | **npm**（不引入 pnpm） | 本机现有 npm 10.7；chat-demo 的 pnpm-lock 不沿用，新工程自行锁版本 |
| 状态 | Zustand，**按领域拆多个 slice/store** | 拒绝 1094 行单 store；服务端数据不引 react-query，手写 fetch 层贴合现有 fetchJSON 语义 |
| 样式 | CSS Modules + 全局 design token（CSS 变量） | 同参考；token 重做为 FlyAI 品牌体系 |
| 路由 | **自研轻量 hash 路由**（#/login、#/chat/:sid） | 后端无 SPA history fallback、Capacitor 以 https://localhost 加载本地文件，hash 路由零后端改动、最稳 |
| 本地存储 | localStorage（偏好/草稿/token/运行标记） | 维持现状语义；不引入 IndexedDB |
| 测试 | Vitest + React Testing Library；Playwright E2E（复用仓库现有 Chromium 设施） | 现前端零测试，重写期必须补上关键路径保护 |
| Node | v20.15.1 已验证可用（Rspack 仅非致命警告，2026-11-16）；若后续变硬失败则**升 22 LTS（已授权）** | P0 已闭环 |
| 运行期配置 | 产物根目录 `config.json`（fetch 加载，不参与构建哈希）+ localStorage 覆盖 | 见第 8 节 |

**明确不迁移的 chat-demo 部件**：IndexedDB/归档 schema、fflate 归档流程（FlyAI 已有 `/api/sessions/export-zip`、`import-zip`）、i18n 框架、DeepSeek Messages 主聊天协议、Files API 附件。

**保留迁移但走 DeepSeek 直连的能力（已确认）**：语音输入 ASR、Smart Search（含 SearchPanel/SourceCard/citation 角标），两项由前端直连 DeepSeek API，不经过 FlyAI 后端，详见第 8 节。

---

## 3. 新工程目录与产物衔接

### 3.1 目录（仓库根新建 `webapp/`，与 flyai/、mobile/ 平级）

```
webapp/
├── package.json  rspack.config.ts  tsconfig.json
├── public/
│   └── config.json          # 运行期配置（apiBase/deepseek），原样拷入 dist 根，不参与哈希
├── src/
│   ├── app/                 # 应用壳、hash router、theme、viewport、global.css
│   ├── base/                # 移植自 chat-demo 的基础组件（Button/Modal/Menu/Select/Toast/Tooltip/anchored）
│   ├── services/
│   │   ├── api/client.ts    # fetchJSON 等价物：apiUrl/assetUrl/Bearer/apiBase 注入
│   │   ├── api/sse.ts       # 移植 chat-demo 通用 SSE decoder
│   │   ├── api/deepseek.ts  # DeepSeek 直连：ASR 转写、Smart Search（独立 Bearer key）
│   │   ├── chat/stream.ts   # FlyAI 事件 adapter + 重连/回放/运行标记
│   │   ├── upload.ts        # /api/upload + 图片压缩（重写 images.js 压缩规则）
│   │   └── native.ts        # Capacitor 桥（重写 native.js）
│   ├── stores/              # auth/session/chat/room/lore/config/market/preset/memory/loop/proactor/mcp/ui
│   ├── features/            # 按业务域组织（见第 6 节映射表），admin/ 为管理后台（第 12 节）
│   ├── components/          # 跨 feature 复用件（Markdown 渲染链、ImageViewer、Cropper、AudioCard…）
│   └── types/               # 领域模型（第 4 节）
└── dist/                    # 构建产物（gitignore）
```

### 3.2 与后端/手机端的衔接（链路改动最小化）

```
webapp/src --rspack build--> webapp/dist --同步脚本--> flyai/web/static
（FastAPI：/static 挂载 + / 返回 index.html，均不改）
mobile/scripts/copy-web.js 源目录仍是 flyai/web/static，链路不动
```

- Rspack 开 `publicPath:'auto'` + contenthash 文件名，替代手工 `?v=` 戳；index.html 维持 no-cache 响应头（app.py:563 已有）
- 登录页并入 SPA 的 `#/login`，取消独立 login.html 双入口（Capacitor 入口统一 index.html）
- 替换 web/static 的时机：**P1 完成、最小聊天闭环可用后**才整体切换；P0-P1 期间新旧共存，新站只跑 dev server
- 旧站靠 Git 历史回退（main 分支始终保留旧站），不做运行时开关

---

## 4. 领域模型（先定模型，再搬 UI）

chat-demo 的 `UserMessage/AssistantMessage` 二分模型不满足 FlyAI，新建：

```ts
// 会话：单聊 / 房间（群聊）
interface Session {
  id: string; kind: 'single' | 'room';
  title: string; pinned?: boolean; mode: 'normal' | 'galgame'; // galgame 仅历史降级
  characterIds: string[]; worldId?: string; userPersonaId?: string;
  threads: Thread[]; updatedAt: number;
}

// 消息：发言者可以是用户或房间内任一角色成员
interface ChatMessage {
  id: string; threadId: string;
  author: { kind: 'user' } | { kind: 'character'; memberId: string; name: string; avatar?: string };
  status: 'pending' | 'streaming' | 'done' | 'stopped' | 'error';
  blocks: MessageBlock[];
  versions?: ChatMessage[][];        // 分支：移植 branches.ts 的树模型
  createdAt: number;
}

// 消息块联合类型——替代 chat-demo 封闭的 ReplyBlock
type MessageBlock =
  | { kind: 'text'; text: string }
  | { kind: 'thinking'; text: string; secs?: number }
  | { kind: 'recall'; text: string }                    // recall_notice 回忆卡
  | { kind: 'tool-call'; id: string; name: string; args: unknown; state: 'running'|'done'|'error' }
  | { kind: 'tool-result'; callId: string; content: string; ok: boolean }
  | { kind: 'image-generated'; images: ImageRef[] }    // 生图/Pixiv 候选
  | { kind: 'tts-audio'; url: string; duration?: number }
  | { kind: 'attributes'; data: Record<string, unknown> };
```

SSE 事件到块的映射（stream.ts 的 adapter）：

| FlyAI 事件 | 处理 |
|---|---|
| message_start / speaker / speaker_skip | 群聊发言者切换、建消息容器 |
| thinking_delta | 追加 thinking 块 |
| content_delta / content_replace | 追加/替换 text 块 |
| tool_calls / tool_start / tool_end | tool-call / tool-result 块（重写现有工具卡） |
| recall_notice | recall 块 |
| attributes | attributes 块 |
| error / done（含 terminal:false） | 收尾；群聊单成员完成不整轮结束的语义保留 |

---

## 5. 必须原样保留的可靠性契约（P1 验收红线）

重写 UI 不能丢掉 chat-stream.js 的断线语义，以下逐条转译为 stream.ts 测试用例：

1. `X-Run-Id` 响应头取 run id；AbortController + `POST /api/chat/runs/{id}/cancel` 双路停止
2. 断线指数退避重连，`GET /api/chat/runs/{id}/events?after=seq` 按序号回放
3. 启动时 `GET /api/chat/active-run?session_id=` 发现活动运行 + localStorage `flyai-run-{uid}` 标记恢复
4. 刷新自动回放续读、停止保存半截、后台任务不被页面生命周期杀掉
5. 群聊 done 帧 `terminal:false` 的多成员轮次语义
6. 发送失败回填草稿（现有 ui.js 草稿/失败逻辑）

---

## 6. 旧脚本 → 新模块迁移映射表

| 旧文件（行数） | 新位置 | 迁移方式 |
|---|---|---|
| app.js (223) | stores/app.ts + services/api/client.ts | 重写：state 拆 slice，保留 apiUrl/assetUrl/fetchJSON/APP_SERVER 语义 |
| auth.js (273) + login.html | features/auth/ | 重写为 LoginView + authStore + 用户管理（管理员） |
| ui.js (1062) | base/ + app/layout/ | 拆解：Toast/Modal/Sheet/Drawer/SettingsStack 组件化；主题、草稿、textarea 自适应、长按转 hooks |
| sessions.js (488) | features/sessions/ | 重写：SessionList/SessionItem/搜索/批量删除/500ms 长按 hook |
| rooms.js (1425) | features/rooms/ | 最重一块：线程 tabs、成员、persona、新建对话、更多菜单、群聊弹窗、记忆弹窗 |
| chat-history.js (846) | features/chat/ | 重写为 MessageList + User/AssistantMessage + Thinking/Recall/Tool 卡；roleplay 着色改组件；编辑/重生成/分页/滚动 |
| chat-stream.js (855) | services/chat/stream.ts | **重写但逐条保契约**（第 5 节）；SSE 解码用 chat-demo sse.ts |
| images.js (447) | features/images/ | 压缩规则原样保留（4 张/1568 边/.82/8MB）；Pixiv 候选墙、ImageViewer、信息面板重写 |
| audio.js (157) | components/AudioCard.tsx | 重写：[语音已生成] 标记解析 + 互斥播放/进度/下载（接 NativeSaver） |
| compress.js (385) | features/compress/ | 重写：压缩弹窗、LCS diff（237-259 行算法移植为纯函数并加测试） |
| lore.js (421) | features/lore/ | 重写：角色根/变体、世界书、导入导出、tavern 检测 |
| editor.js (456) | features/lore/editor/ | 重写：角色/世界表单、AI 属性生成 |
| presets.js (369) | features/presets/ | IIFE 拆成 store + 组件 |
| memory-manager.js (364) | features/memory/ | 重写：stats/sources/ledger/recall-logs/删除 |
| loop.js (288) | features/loop/ | 重写：5s 轮询、审批 decide、stop/interrupt |
| proactor.js (597) | features/proactor/ | 重写：心情时间线、self-memory、commitments、jobs、polls |
| mcp.js (406) | features/mcp/ | 重写：server CRUD/测试/toggle、prompts、watchers |
| cropper.js (199) | components/Cropper/ | 指针/双指/滚轮逻辑移植为 TS hook，512px 输出不变 |
| native.js (230) | services/native.ts | 重写为 TS：NativeBar/NativeSaver/NativeNotify + 返回键状态机（第 9 节） |
| market.js (521) | features/market/ | 重写：列表/详情/安装/发布表单 |
| settings.js (1300) | features/settings/ | 最大管理面：/api/config 与 /api/my/settings 两套；移动端全屏页栈保留 |
| galgame.js (2055) | — | **不迁移**（第 10 节） |
| midi_player.js (218) + vendor/* (32MB) | — | **不迁移**，随 Galgame 排除 |

---

## 7. 主题与样式映射

- 保留 FlyAI 品牌资产：`--accent #6c8cff`、角色扮演三色（`--speech/--action/--thought`）、聊天背景图+透明度、字体缩放、深/浅双主题（默认深色）
- 建立 chat-demo token → FlyAI token 映射层：`--dsw-alias-*` 统一改名 `--fly-*`，base 组件移植时只改 token 名，不改组件结构
- 新增 chat-demo 已有的 system 主题（跟随系统）选项
- 断点沿用 FlyAI 的 **860px**（不用 chat-demo 的 768px），避免现有移动布局回归；440px 窄屏规则参考引入
- z-index 层级重新规划（chat-demo：SearchPanel 700 / 遮罩 799 / 抽屉 800 / Menu 900 / Modal 1000 / Select 1050 / Toast 2000），纳入 ImageViewer、Market 全屏、设置栈

---

## 8. 网络层设计

### 8.1 运行期配置 config.json（已确认）

产物根目录放 `config.json`（Rspack Copy 插件原样拷贝，不带哈希），应用启动第一步 fetch：

```json
{
  "apiBase": "https://fly.eastcloud.top",
  "deepseek": { "baseUrl": "https://api.deepseek.com", "apiKey": "" }
}
```

- 优先级：**localStorage 用户覆盖 > config.json 出厂值 > 构建期默认值**；设置页提供「服务器地址」「DeepSeek 密钥」编辑项，写入 localStorage
- Web 部署：改 config.json 即可换后端，免重新构建；替代 copy-web.js 的 FLYAI_APP_SERVER 字符串替换
- Capacitor：config.json 随 www 打包；应用内设置页改地址存 localStorage；局域网 http 地址需 P5 同步加 `networkSecurityConfig`/`usesCleartextTraffic`（现 Manifest 无此项）
- 加载失败（文件 404）降级到构建期默认值，不阻塞启动

### 8.2 FlyAI 后端通道

- `services/api/client.ts`：统一 `apiUrl()`（apiBase 前缀 + /api）、`authHeaders()`（Bearer flyai-token）、401 跳登录、错误归一化；约 110 个端点按域封装为各 feature 的 api 模块，不生成巨型 api 文件
- `sse.ts` 直接移植 chat-demo 通用 decoder；`stream.ts` 实现第 4-5 节的 FlyAI adapter
- 轮询保留：loop 5s、proactor 各自轮询；不引入全局轮询
- 附件：重写 upload（/api/upload multipart），chat-demo 的 Files API/serverId/补传机制整体弃用

### 8.3 DeepSeek 直连通道（ASR + Smart Search，已确认保留）

`services/api/deepseek.ts` 独立于 FlyAI client，读取 config.json/localStorage 的 deepseek 配置：

- **语音输入 ASR**：移植 chat-demo `recorder.ts`（getUserMedia 16kHz 单声道 PCM + 手写 WAV 头，120s 上限）与 `speech.ts` 的 `POST {baseUrl}/v1/tools/asr`（multipart WAV，8MiB 上限）；转写文本插入输入框光标处。注意仅安全上下文可用（https 与 Capacitor https://localhost 满足，局域网 http 会禁用并提示）
- **Smart Search**：移植输入区搜索开关、`search/` 全套组件（SearchPanel/SourceCard/HoverCard/CitationLink）与 Markdown 的 `[citation:N]` 节点；前端直连 DeepSeek Messages API 的 `deepseek_web_search`/`deepseek_read_url` server tools，取得带引用回答后在右侧面板展示来源，回答支持「发送到当前会话」（作为用户消息进入 /api/chat 主流程），**不侵入 FlyAI 聊天协议**
- 密钥安全：DeepSeek key 存 localStorage（Capacitor 沙箱内可接受；Web 端需防 XSS，全站坚持 React 转义、禁 innerHTML）；未配置 key 时两个入口显示禁用态并引导去设置页填写

---

## 9. 移动端 / Capacitor 衔接

1. 同一份 React 代码响应式，不做独立手机工程；mobile/www 的来源改为 webapp 构建产物（copy-web.js 的 SRC 改指 webapp/dist 或同步后的 web/static），**废弃 FLYAI_APP_SERVER 字符串替换，改为 www 根目录 config.json 出厂配置 + 应用内设置页覆盖**
2. **先把 3 个 Java 文件纳入 Git**（android/ 当前整目录 gitignore，建议收窄 ignore 规则，保留源码、忽略 build/.gradle/local.properties/keystore.properties）；若支持局域网 http 地址，新增 res/xml/network_security_config.xml 并在 Manifest 引用
3. native.ts 重写四座桥：
   - NativeBar：StatusBar overlay + 深浅色随主题
   - NativeSaver：blob→dataURL→FlySaver（图片/音频/导出下载依赖）
   - NativeNotify：LocalNotifications 仅用于申请权限，FlyNotify 后台轮询 run 完成通知，逻辑不变
   - 返回键状态机：按新路由/浮层重写优先级（Modal → ImageViewer → Market → Menu/提及 → Drawer → 房间二级视图 → 再按退出），移除 Galgame/backlog 分支
4. 移动交互全部保留为设计约束：76vw 抽屉、bottom sheet、设置全屏页栈（270ms）、hover:none 常显操作、500ms 长按、viewport-fit=cover + 全套 safe-area、正文 16px 防缩放
5. visualViewport 键盘方案移植 viewport.ts，替换现有零散适配
6. APK 版本按现有规则（新功能第二位 +1）：重构首发版 **1.22.xxxxx**，签名密钥 flyai-release.jks 不变

---

## 10. Galgame 排除与历史数据降级

- 不迁移：galgame.js、midi_player.js、vendor 音色库（约 32MB，新包直接瘦身）、#galgame-view、Galgame 设置页、5 个 /api/galgame/* 前端调用、native 返回栈 Galgame 分支、5 个 galgame-* localStorage 键
- 后端 routes_galgame.py 与 mode=galgame 历史会话数据**保留不动**
- 历史 Galgame 会话降级策略（**已确认**）：侧栏正常列出，点击进入**只读文本视图**（复用普通消息列表渲染台词文本，无立绘/BGM/存档/选项），顶部提示「Galgame 模式已在新版下线」；后续是否补充功能再行研究，本期不做
- 注意 style.css 中 `#app.gv-app-hidden` 被 Market 全屏复用，删除时改为通用全屏类，勿连带破坏 Market

---

## 11. 分批实施计划（每批可独立提交、可验收、不破坏 main）

### P0 工程地基
Rspack+TS+npm 工程跑通（验证 Node 20 兼容性）；theme token 映射层；base 组件移植；hash router 骨架；dev server 代理 /api；client.ts + 登录闭环；产物可放入 web/static 由 FastAPI 打开。
**出口**：浏览器走真实后端完成登录，进入空壳主布局，深/浅主题切换正常。

> **P0 完成登记（2026-11-16，分支 chat-demo，未提交）**
> 出口准则已由 `webapp/smoke_p0.py`（系统 Chrome + Playwright）实测通过，截图存 `show/p0-smoke/`：
> 错误密码 401 内联报错 → 真实管理员登录（令牌写 flyai-token 等旧键）→ 空壳主布局 → 浅/深/跟随系统三态切换（body.light 与 data-fly-dark-theme 正确）→ 刷新经 GET /me 恢复会话 → 登出令牌吊销回登录页，全程零 JS 错误。
> - 工程：React 18.3.1 + TS strict + Rspack 2.0.2 + Zustand 4.4.7，`npm run type` 零错误、`npm run build` 产物完整（config.json/NOTICE.txt/favicon.svg 落产物根）。
> - Node 结论：v20.15.1 下 build/dev/E2E 全部成功，Rspack 仅打印非致命的 unsupported 版本提示（退出码 0）。**暂不升级**；若后续 Rspack 补丁将其变为硬失败再升 22 LTS（授权仍有效）。
> - 移植清单：base 八组件（Button/IconButton/Tooltip/Menu/Modal/Select/Toast/icons/anchored）已机械改名 `--dsw-/--ds-`→`--fly-`、断点 768→860px、剥离 i18n（Toast/Modal 改纯中文）；主题色换 FlyAI 蓝 #6c8cff，暗色基底 #1b1b22 家族。
> - 运行期配置：`public/config.json` 默认 apiBase 为空串（同源；开发走 8090→8000 代理，部署走 FastAPI 同源），localStorage 键 `flyai-runtime-config` 可覆盖；移动端在 P5 打包时写入正式地址。
> - dev 启动：后端 `.venv/Scripts/python main.py`（:8000）+ webapp `npm run dev`（:8090）。

### P1 核心聊天闭环（替代切换的门槛）
authStore/sessionStore/chatStore；会话侧栏（列表/搜索/新建/重命名/置顶/删除/批量）；MessageList + Markdown 渲染链（GFM/代码高亮/KaTeX/mermaid/流式节流）；stream.ts 全契约（第 5 节六条红线 + Vitest 测试）；输入区（textarea 自适应/草稿/失败回填/停止/深度思考/附件图片）；历史分页、消息编辑/重生成/版本分支；@提及基础；**语音输入 ASR（DeepSeek 直连，recorder + 转写填入）**；**Galgame 历史会话只读兜底视图（最小版，防止切换后点击无响应）**。
**出口**：单会话聊天全功能可用，断线重连 E2E 通过；此后切换 web/static 为新产物。

### P2 房间与角色
新建对话弹窗；角色卡/世界书 CRUD、fork/发布/导入导出（PNG/JSON/tavern）；编辑器 + AI 属性生成；房间线程 tabs、成员/persona、群聊多 speaker 编排、shuffle、群聊@提及；房间记忆弹窗。
**出口**：群聊多 AI 轮次与现有后端行为一致。

### P3 富内容能力
图片上传压缩/生图模型/SD 引擎/Pixiv 候选墙/ImageViewer 信息面板；TTS 语音气泡；上下文压缩（LCS diff 加单测）；记忆管理；预设管理；Market 市场；**Smart Search（DeepSeek 直连：搜索开关 + SearchPanel/来源卡/citation 角标 + 一键发送到会话）**。
**出口**：现站图片/语音/压缩/市场路径全部有对应入口且回归通过；DeepSeek 两项直连能力真机/浏览器实测。

### P4 管理面
设置中心（管理员 /api/config 全字段 + 用户 /api/my/settings，含移动端全屏页栈）；用户管理与权限等级；MCP 管理；长跑任务；proactor 心情/主动行为；插件与工具目录；Cropper 头像裁剪。
**出口**：settings.js 的 1300 行表单功能逐项对账无遗漏。

### P5 移动端收口
native.ts 四桥；返回键状态机；safe-area/抽屉/sheet/长按/visualViewport；FlyNotify 后台通知实测；copy-web 改造与 APK 打包；Java 源码入库；（可选）运行期服务器地址。
**出口**：APK 1.22 真机回归：登录/聊天/通知/下载/返回键/键盘/离线重连。

### P6 旧站下线与收尾
Galgame 只读视图文案与对账；旧静态文件从 web/static 移除（Git 历史留存）；Playwright 关键路径 E2E 入 CI；对照本方案第 6 节做全量功能对账；更新使用教程文档。
**出口**：main 合并，旧站文件零残留引用。

### P7 管理后台（DeepSeek 开放平台风格，详见第 12 节）
后端用量统计端点先行（my/admin 两个维度）；AdminLayout（左导航 + 卡片 + 自绘 SVG 图表）；全员可用的个人用量信息页（含剩余额度）；管理员专属的用户与权限、模型配置、系统配置、市场审核、工具生态、任务监控模块；移动响应式（汉堡抽屉 + 单列卡片）。
**出口**：普通用户能在 #/admin 查看本人用量与剩余额度；管理员能完成全部管理操作，越权入口对普通用户完全隐藏。

---

## 12. 管理后台（新增，DeepSeek 开放平台风格）

设计稿：`show/QQ_1794788000980.png`（桌面端）、`show/QQ_1794788071471.png`（移动端单列）、`show/QQ_1794788090908.png`（移动端抽屉）。

### 12.1 形态与路由（2026-11-16 修订：全员可进）

- 同一 SPA 内的 `#/admin/*` 路由，**所有登录用户均可进入**，默认落地「用量信息」看自己的用量与剩余额度；管理类菜单按现有权限等级（0-255）守卫，不达标完全隐藏
- 导航分两组：**个人组**（用量信息，全员可见）+ **管理组**（用户/模型/系统/市场/工具/任务，仅管理员可见）；Capacitor 手机端同样可进入个人用量页
- AdminLayout 照截图：桌面左侧固定窄导航（品牌区 + 导航分组 + 底部用户卡），右侧页面标题 + 灰色说明 + 圆角卡片网格；移动端顶栏汉堡 + 全屏抽屉导航，统计卡片单列堆叠、筛选器折行
- 复用 base 组件与 `--fly-*` token，不引入第二套样式体系；黑色主按钮、浅灰卡片底与截图对齐

### 12.2 菜单模块（截图信息架构 → FlyAI 现有 API 映射）

| 后台菜单 | 可见性 | 现有能力/端点 | 缺口 |
|---|---|---|---|
| 用量信息 | **全员** | `/api/my/quota`（仅对话次数额度） | **需新增后端统计**：普通用户查自己（按日聚合自己的请求数、token 数、剩余额度）；管理员可按用户/Key/模型筛选全局。卡片（剩余额度/区间用量/请求次数/Tokens）+ 时间维度筛选 + 柱状图 + 导出。截图的充值/余额/账单属计费体系，FlyAI 没有，替换为「额度/用量」语义 |
| 用户管理 | 管理员 | `/api/auth/users` CRUD、改密、权限等级 | 无 |
| 模型配置 | 管理员 | `/api/models/providers`、`/api/models/images`、share 共享 | 无 |
| 系统配置 | 管理员 | `/api/config` 管理员全字段（从 settings.js 1300 行迁出） | 无 |
| 市场管理 | 管理员 | `/api/market/entries` 列表/刷新/删除/审核 | 无 |
| 工具生态 | 管理员 | `/api/agentdb`（MCP/watcher/prompts）、`/api/plugins`、`/api/tools` | 无 |
| 任务监控 | 管理员 | `/api/chat/loop/*`、`/api/proactor/*` | 无 |

### 12.3 图表与导出

- 截图只有柱状图，**零依赖自绘 SVG**（与 chat-demo ReadingBars 的自绘思路一致），不引入图表库；将来需要折线/饼图再评估 recharts
- 用量导出优先前端拼 CSV（纯前端下载，NativeSaver 兜底），避免再开后端导出端点

### 12.4 后端前置工作（P7 开工前）

- 核查 chat usage 帧的 token 数据当前是否落库；设计按日聚合存储（新表或按需扫描），再暴露统计端点
- 统计端点分两个维度：`/api/my/usage*`（全员，强制限定本人数据）与 `/api/admin/usage*`（管理员，可按用户/模型筛选）；剩余额度复用 `/api/my/quota`
- 管理类端点复用现有 admin 权限校验，不新增认证机制

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| 1.8 万行旧逻辑重写，工期长、易漏 | 严格按 P0-P6 分批，每批出口可验；P1 前不删旧站；第 6 节映射表作为对账清单 |
| SSE 重连/回放语义丢失 | 第 5 节红线逐条转 Vitest + 真后端 E2E，不过不进 P2 |
| innerHTML/escapeHtml 惯性带入 React | 代码审查禁 dangerouslySetInnerHTML（Markdown AST 渲染器除外，其本身不拼 HTML） |
| chat-demo 手写 .module.css.d.ts 滞后 | 不移植这些 d.ts，统一用 env.d.ts 泛型声明或构建期生成 |
| Rspack/Node 版本 | P0 首日验证 Node 20.15.1；不行升 22 LTS；build 脚本禁用 `rm -rf`（Windows 不兼容），改跨平台写法 |
| 原生代码无版本管理 | P5 第一步收窄 mobile/.gitignore，Java/Manifest/res 入库 |
| CSS token 改名波及面大 | 映射层集中在 theme.css，组件只引用 --fly-*，禁止硬编码色值回潮 |
| 群聊/生图等新块冲击渲染器 | MessageBlock 联合类型先行（第 4 节），渲染器按 kind 扩展，不重走 chat-demo 封闭 union 老路 |
| 后端无 SPA fallback | 全程 hash 路由，零后端路由改动 |
| DeepSeek key 存 localStorage 的 XSS 风险 | 全站禁 innerHTML/dangerouslySetInnerHTML（Markdown AST 渲染器除外）；key 只存本机不入库；Capacitor 沙箱内风险可控 |
| 管理后台用量统计缺后端数据源 | P7 先核查 usage 落库情况，统计端点列为后台前置任务；前端卡片/图表先用 mock 并行开发 |
| 局域网 http 服务器地址 | config.json 支持 http 基址；Android 侧补 networkSecurityConfig 放行明文，Web 端提示非安全上下文会禁用 ASR |

---

## 14. 决策记录（2026-11-16 已确认）

1. **Galgame 历史会话**：只读文本视图降级，功能补充以后再研究
2. **运行期配置**：采用产物根目录 `config.json`（apiBase + deepseek 配置），localStorage 可覆盖，替代构建期字符串替换
3. **ASR 与 Smart Search**：保留 chat-demo 两项能力，前端直连 DeepSeek API（设置页配 key）；ASR 随 P1 输入区落地，Smart Search 随 P3 落地
4. **Node**：v20.15.1 先实测，Rspack 2 不兼容即升级 22 LTS（已授权）
5. **管理后台**：新增 P7 批次，按 `show/QQ_*.png` 的 DeepSeek 开放平台风格实现；**全员可进入**，个人用量页看自己的用量与剩余额度，管理模块仅管理员可见；用量统计需先补后端端点（my/admin 两个维度）

留待实施时细化的小项：用量统计的数据落库方案、后台入口在侧栏的具体位置。
