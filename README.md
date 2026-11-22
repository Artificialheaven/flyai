<p align="center">
  <img src="docs/assets/logo.png" width="150" alt="FlyAI">
</p>

<h1 align="center">FlyAI</h1>

<p align="center">
  可自托管的类酒馆（SillyTavern）AI 角色扮演与多 AI 群聊平台<br>
  角色扮演 · 酒馆卡兼容 · 工具调用 · 全屏视觉小说 · Android 客户端
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white" alt="React">
  <img src="https://img.shields.io/badge/Android-Capacitor%207-3DDC84?logo=android&logoColor=white" alt="Android">
</p>

<p align="center">
  <a href="docs/使用教程.md"><b>📖 使用教程</b></a> ·
  <a href="docs/酒馆卡兼容矩阵.md">🃏 酒馆卡兼容矩阵</a> ·
  <a href="docs/酒馆EJS提示词模板兼容方案.md">🧩 EJS 模板方案</a>
</p>

---

FlyAI 是一个**可自托管、数据本地优先**的 AI 角色扮演聊天平台。后端是单进程 FastAPI 服务，前端为 React 18 + TypeScript 单页应用（Rspack 构建），对话记录、角色卡、素材与配置全部保存在本机；同一套前端也可以通过 Capacitor 打包成 Android 应用。

项目同时兼容大量 SillyTavern（酒馆）生态格式：角色卡、世界书、OpenAI 预设、正则脚本、TavernHelper 作者脚本与 EJS 提示词模板。

## 主要能力

**🗣 对话与模型**
- 支持任意 **OpenAI 兼容协议**（DeepSeek、Kimi、Qwen、中转站等）以及 **Anthropic 原生**提供商；模型可由用户自建，也可由管理员共享（支持共享最低等级）。
- 思考过程与正式回复分别流式展示；思考模型截断时自动保留已生成正文。
- **断线重连**：生成在后台任务中运行，SSE 事件带全局序号并缓存；刷新页面、切换设备或网络抖动后可自动发现并续接运行，半截回复与待选项不丢失。
- **AI 自主工具调用**：function calling 多轮循环，按上下文自行编排工具，直到给出最终回答。

**🎭 角色扮演与酒馆兼容**
- 角色卡（头像 / 简介 / 经历 / 对话范例 / 内容表）、世界观、常驻条目；支持关键词 + 向量混合检索，AI 通过 `query_lore` 按需查询。
- 多 AI 群聊房间：参与者座位、多个私聊窗口、群聊 fan-out、每 AI 独立折叠卡片与发言自评。
- 酒馆卡导入：世界书、@depth / 概率 / 角色触发、OpenAI 预设、正则脚本（含 `extensions.regex_scripts`）、MVU 变量、卡内状态面板。
- **卡片沙箱**：卡内整页 HTML+JS 在独立 iframe 中运行；卡内图片与网络请求可经服务端受控代理（`st_image` / `st_net`），带 SSRF 防护与限流。
- **TavernHelper 作者脚本**：角色卡内的脚本树在沙箱 iframe 中执行，服务端只负责抽取、限额与下发，绝不执行卡内 JS。
- **EJS 提示词模板（方案 B）**：客户端执行 EJS，服务端负责权限、原文哈希、ops 校验与原子落盘；输出侧执行需单独授权。
- 对话压缩：AI 将历史归纳进设定，**先 diff 确认、后写入**，只写会话副本，不污染根卡。

**🌐 工具与联网**
- `web_search` + `web_fetch`：结果列表、正文转 markdown、长页面 offset 分页读取（正文短缓存），拥有独立轮数预算。
- Playwright **AI 浏览器**：有状态地逐级打开、点击、填表浏览真实网页，路由级 SSRF 拦截。
- **长期记忆 / 剧情台账 / 心情主动系统**：剧情状态自动注入，AI 可维护长期记忆并主动发起行为。
- **长跑模式 / 自定义工具 / MCP**：无轮限循环与安全点打断；AI 编写的 Python 工具在隔离子进程执行；标准库实现的 MCP 客户端与外部触发。

**🎨 生图与语音**
- 云端图生图 `edit_image`（Seedream），支持单张与最多 14 张参考图；Pixiv 搜图。
- 本地 Stable Diffusion WebUI（Gradio 接口，免 `--api`）。
- 豆包语音大模型 TTS：独立语音条、角色卡绑定音色、流式合成与结果缓存、按权限等级门控。

**🎮 Galgame 模式**
- 全屏视觉小说：AI 自动绘制横版背景与绿幕立绘（前端实时抠像），按场景作曲（MIDI + FluidSynth WASM），对白逐句语音同步，支持选项分支、剧情回放、存档点与自动播放。

**👥 多用户与运营**
- 独立登录、管理员创建用户、邮箱验证码注册 / 找回、邀请码开关。
- 权限等级 0–255：工具、模型、TTS 等均可设最低等级，不达标完全不可见。
- 用量统计、计费批次、CDK 兑换、爱发电自动发卡、启动公告、APK 更新检查。
- 对话可导出为自包含 `.zip` 迁移包，角色卡 / 世界观支持 JSON 导入导出；支持消息撤回、分支、草稿与重新编辑。

**💻 前端与客户端**
- 亮 / 暗主题、桌面与手机自适应布局、全屏看图器、消息搜索 / 置顶 / 重命名、Token 用量展示。
- Capacitor Android 外壳（应用 ID `top.eastcloud.flyai`），支持本地通知、文件保存与 APK 自更新。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.9、FastAPI、Pydantic 2、httpx、WebSocket / SSE、Playwright（可选） |
| 前端 | React 18、TypeScript、Rspack、Vitest；表现层参考 deepseek-ai/chat-demo |
| 存储 | 用户目录下的 JSON（会话、内容库、配置） |
| 语音 / 音乐 | 豆包语音大模型 WebSocket 二进制协议、MIDI 渲染、FluidSynth WASM |
| 移动端 | Capacitor 7（Android）、Gradle |

> 代码保持 **Python 3.9** 兼容：不使用 `match`、`TaskGroup`、`StrEnum` 等 3.10+ 语法。

## 目录结构

```
flyai/
├── main.py                 # 启动入口
├── config/                 # config.example.json 入库；config.json 含密钥不入库
├── plugins/                # 目录式插件（file_reader、current_time 等）
├── flyai/                  # Python 应用包
│   ├── app.py              # FastAPI 装配
│   ├── config.py           # 配置模型
│   ├── api/                # 各业务路由
│   ├── agent/              # agentic 循环、长跑模式、运行管理
│   ├── agentdb/            # 执行期工具授权与运行记录
│   ├── proactor/           # 心情状态机与主动行为
│   ├── mcp_client/         # 自研 stdlib MCP 客户端
│   ├── sandbox/            # AI 文件沙箱
│   ├── st/                 # 酒馆卡兼容（MVU / 正则 / 作者脚本 / 面板）
│   ├── template/           # EJS 提示词模板（方案 B）
│   ├── providers/          # OpenAI 兼容 / Anthropic 适配
│   ├── tools/              # 工具注册表与内置工具
│   ├── lore/  sessions/  memory/  market/  preset/  tts/  galgame/  sd/  browser/
│   └── web/static/         # 前端构建产物（由 webapp 生成并入库）
├── webapp/                 # React 18 + TS + Rspack 前端源码
├── mobile/                 # Capacitor Android 外壳与构建脚本
├── deploy/                 # nginx 反向代理与静态资源长缓存配置
├── docs/                   # 使用教程与设计文档
├── scripts/release.py      # 一键构建前端 / APK / 源码包
└── tests/                  # 后端测试（前端测试在 webapp）
```

## 快速开始

### 环境要求

- **Python 3.9**（开发环境为 3.9.13）
- Windows / macOS / Linux 均可
- 可选：Node.js 18+（仅在修改前端、构建 APK 时需要）
- 可选：Playwright Chromium（AI 浏览器）、本地 SD WebUI、豆包语音密钥

### 启动后端

```bash
# Windows
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

浏览器打开 <http://127.0.0.1:8000>。仓库内已包含构建好的前端产物，**只运行后端无需安装 Node.js**。

也可以直接用 uvicorn：

```bash
python -m uvicorn flyai.app:app --host 127.0.0.1 --port 8000
```

### 首次配置

首次启动会从 `config/config.example.json` 自动生成 `config/config.json`（含密钥，已被 `.gitignore` 忽略）。

1. 在 `config/config.json` 的 `auth.admin` 中配置管理员账号（没有内置默认账号）；普通用户默认由管理员创建。
2. 登录后在「设置」中添加提供商并填写 API Key，选择激活提供商。
3. 在「内容」中新建角色卡与世界观，然后在会话顶栏选择本会话要使用的角色与世界观。

> 📖 角色卡、对话范例、世界书与完整玩法见 [`docs/使用教程.md`](docs/使用教程.md)。

### 前端开发

```bash
cd webapp
npm ci
npm run dev      # 开发服务器
npm run type     # TypeScript 类型检查
npm test         # Vitest
npm run deploy   # 生产构建并同步到 flyai/web/static
```

`npm run deploy` 会整体替换 `flyai/web/static`，旧的哈希资源不会保留（回退靠 Git 历史）。

### 运行测试

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests/ -q
```

### 可选：AI 浏览器

```bash
pip install -r requirements-browser.txt
playwright install chromium
# Ubuntu 另需：sudo playwright install-deps chromium
```

安装后在「设置」中启用，保存即生效。

## 部署

- 反向代理与静态资源长缓存参考 [`deploy/nginx.conf`](deploy/nginx.conf)（宝塔 / nginx）。
- `python scripts/release.py` 会依次构建前端、尝试编译 APK，并将源码打包到 `release/`；可按需加 `--skip-apk` / `--skip-web`。
- 生产环境建议把 `config/config.json`、`data/`、`mobile/android/` 与签名密钥排除在公开仓库之外。

## Android 客户端

应用外壳位于 `mobile/`，应用 ID 为 `top.eastcloud.flyai`，默认连接 `https://fly.eastcloud.top`。

```bash
cd mobile
npm ci
npm run sync          # 复制前端静态资源并 cap sync android
npm run build:debug   # android/app/build/outputs/apk/debug/app-debug.apk
npm run build:release # 需本地 keystore.properties 与签名密钥
```

> ⚠️ **APK 构建依赖未入库的本地文件。** `mobile/android/`（Gradle 工程、自研原生插件、Manifest 定制）与 `flyai-release.jks`、`keystore.properties` 按设计不入库，因此**全新 clone 无法直接编译出 APK**，只能构建 Web 端。请在保留完整 `mobile/android/` 的机器上构建；签名密钥需单独备份，丢失后无法覆盖升级老用户。

本地联调可用 `FLYAI_APP_SERVER=http://10.0.2.2:8000 npm run copy-web` 临时替换拷贝产物中的服务器地址。

## 安全说明

- 仓库中**不包含**任何真实 API Key、管理员密码或签名密钥；`config/config.json`、`mobile/android/`、`*.jks` 均被忽略。
- 服务端不执行角色卡内的 JavaScript：卡内应用与作者脚本运行在独立沙箱 iframe 中。
- 联网工具与卡内网络代理均带 SSRF 防护（内网 / 本机地址拦截、DNS 重绑定与重定向逐跳校验）。
- 工具、模型与语音能力支持按用户等级 0–255 门控；管理员权限最高。

## 文档

- [使用教程](docs/使用教程.md)
- [酒馆卡兼容矩阵](docs/酒馆卡兼容矩阵.md)
- [酒馆 EJS 提示词模板兼容方案](docs/酒馆EJS提示词模板兼容方案.md)
- [卡内只读数据桥方案](docs/卡内只读数据桥方案.md)
- [Tide 能力移植技术书](docs/Tide能力移植技术书.md)
- [APK 前端热更新方案书](docs/APK前端热更新方案书.md)
- [插件开发说明](plugins/README.md)

## 致谢

- 前端表现层参考并移植自 [deepseek-ai/chat-demo](https://github.com/deepseek-ai/chat-demo)（MIT）。
- 角色卡、世界书、预设与正则等格式兼容 [SillyTavern](https://github.com/SillyTavern/SillyTavern) 生态。
