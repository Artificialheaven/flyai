# FlyAI · 类酒馆 AI 聊天工具（支持工具调用）

一个本地的 Web 端 AI 聊天工具，类似 SillyTavern（酒馆）。

- 支持接入任意 **OpenAI 兼容协议** 提供商（DeepSeek、Kimi、Qwen、中转站等）及 **Anthropic 原生** 提供商。
- **流式"思考 & 对话"**：模型的思考过程和正式回复分别流式展示。
- **AI 自主工具调用**：模型可通过 function calling 自行调用工具获取信息（如读取文件、查询角色/世界观内容表），多轮循环直到给出最终答复。
- **角色卡 + 世界观**：为角色设定头像/简介/经历/内容表，为世界观设定内容/内容表；AI 扮演角色并通过统一工具 `query_lore` 自行查询内容表。
- **角色扮演对话格式**：默认规范——动作用 `（ ）`、内心活动用 `____…____`、对白用 `「」`；不同类型自动分段并以不同颜色/斜体区分展示。
- **属性跟踪**：可开启后由 AI 在每回合结束时调用工具 `update_attributes` 增减角色属性（好感度、金钱等，可在设置中开关 / 选择是否由 AI 自行提供数值），实时显示在顶栏。
- **亮/暗主题**：扁平化界面，可一键切换。
- **手机端适配**：窄屏下侧栏收为抽屉式菜单，触控友好的现代化界面。
- **多用户 + 管理员**：独立登录页（不登录不能进）；管理员凭据写在 `config/config.json` 的 `auth.admin`（无默认用户），普通用户由管理员创建、不能自助注册；角色卡/世界观/会话按用户隔离，可随时退出切换。
- **导出/导入**：角色卡与世界观可导出为 JSON 文件备份或分享，也可一键导入。
- **插件化扩展**：工具以目录式插件提供，可自行添加（见 `plugins/README.md`）。

> 📖 **详细使用教程**（含如何写出完善的角色卡与世界观）：见 [`docs/使用教程.md`](docs/使用教程.md)。

## 运行

```bash
# 使用项目内 venv（Python 3.9.13）
.venv\Scripts\pip install -r requirements.txt

# 启动
.venv\Scripts\python main.py
# 或
.venv\Scripts\python -m uvicorn flyai.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。

首次启动会自动从 `config/config.example.json` 生成 `config/config.json`。
在页面右侧「⚙️ 设置」中添加/编辑提供商（填好 API Key）、切换激活提供商、配置工具白名单目录。
左侧「🧩 内容」中新建角色卡与世界观；在会话顶栏选择当前会话要扮演的角色 / 采用的世界观。

## 角色扮演与内容查询

- **角色卡**：头像（可上传）、简介、经历、以及一张内容表（标题+内容，AI 主动查询）。
- **世界观**：总体内容 + 一张内容表。
- 会话顶栏下拉选择角色与世界观后，系统提示词会自动注入角色人设与世界观。
- AI 需要细节时可通过 `query_lore(kind='character'|'world', keyword=…)` 工具模糊查询对应内容表（按标题匹配）。
- **内置要求展示**：新建/编辑角色卡或世界观时，「对白格式要求」「工具使用要求」两个文本框会自动填入内置默认（来自 `roleplay.format_instruction` / `roleplay.tool_instruction`），可逐卡覆盖；留空则遵循全局默认。填写的值会注入该系统提示词。
- 弹窗支持点击空白处关闭；若表单有未保存修改会先弹出确认。

## 对话格式与属性跟踪

- **对话格式（可开关）**：在「⚙️ 设置 → 角色扮演」中启用。默认要求 AI 用 `（ ）` 描述动作、用 `____…____` 标记内心、用 `「」` 表示对白，并以空行分段；前端会把对白（彩色）、动作（斜体灰）、内心活动（斜体黄）区分展示。
- **属性跟踪（可开关）**：在设置中定义属性列表（如好感度、金钱，含初始值/上下限）。启用后 AI 在每回合结束时调用工具 `update_attributes` 提交本回合的增减量，顶栏会显示最新数值。可关闭「由 AI 自行提供」使其仅作展示。
- 相关配置位于 `config/config.json` 的 `roleplay` 节点（`format_enabled`、`format_instruction`、`attributes`）。

## 手机端

窄屏（≤860px）时左侧会话列表自动收为抽屉式菜单，点顶栏 ☰ 打开；对话框与表单单列堆叠，适配安全区，触控目标加大。

## 目录结构

```
flyai/
├── main.py                 # 启动入口
├── config/                 # 配置（config.example.json 入库；config.json 含 key 不入库）
├── plugins/                # 插件目录（内置 file_reader + 开发文档）
├── workspace/              # 文件读取默认白名单目录
├── flyai/                  # 应用包
│   ├── app.py              # FastAPI 装配
│   ├── config.py           # 配置模型
│   ├── schemas.py          # 内部统一消息模型
│   ├── providers/          # OpenAI 兼容 / Anthropic 适配器
│   ├── agent/loop.py       # agentic 工具调用循环
│   ├── tools/              # 工具基类 / 注册表 / 插件加载器
│   ├── sessions/           # 会话存储（memory / json）
│   └── web/static/         # 前端单页（无构建）
└── tests/                  # 编解码与安全单测
```

## 测试

```bash
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests/ -q
```

## 换存储后端

`config/config.json` 中 `history.backend`：`memory`（默认，进程内）或 `json`（每个会话一个文件，重启保留）。