<p align="center">
  <img src="docs/assets/logo.png" width="150" alt="FlyAI">
</p>

<h1 align="center">FlyAI</h1>

<p align="center">
  本地运行的类酒馆（SillyTavern）AI 聊天工具<br>
  角色扮演 · 工具调用 · 全屏视觉小说 · Android 客户端
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9.13-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115.6-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Android-Capacitor%207-3DDC84?logo=android&logoColor=white" alt="Android">
</p>

<p align="center">
  <a href="docs/使用教程.md"><b>📖 详细使用教程</b></a> · 角色卡、世界观与完整玩法
</p>

---

FlyAI 是一个**本地优先**的 Web 端 AI 角色扮演聊天工具，灵感来自 SillyTavern（酒馆）。后端为单进程 FastAPI 服务，前端是零构建的静态单页应用，数据与素材全部保存在本机；同一个前端也能通过 Capacitor 打包成 Android 应用。

## 功能一览

**🗣 对话与模型**
- 支持任意 **OpenAI 兼容协议** 提供商（DeepSeek、Kimi、Qwen、中转站等）以及 **Anthropic 原生** 提供商。
- 模型思考过程与正式回复**分别流式展示**。
- **断线重连**：生成在后台任务中进行，事件带序号全量缓存；回答中刷新页面会自动打开会话并回放续读，网络抖动时指数退避自动重连，点「停止」已生成的半截回复照样保存。
- **AI 自主工具调用**：通过 function calling 自行调用工具获取信息，多轮循环直到给出最终答复。

**🎭 角色扮演**
- 角色卡（头像 / 简介 / 经历 / 对话范例 / 内容表）+ 世界观 + 常驻条目。
- AI 通过统一工具 `query_lore` 按需查询设定；支持关键词与向量混合检索。
- 可开关的对话格式规范与属性跟踪。
- 对话压缩：AI 把历史归纳进设定，**先 diff 确认、后写入**。

**🎨 生图与语音**
- 云端图生图 `edit_image`（Seedream），支持单张与最多 14 张参考图。
- 本地 Stable Diffusion WebUI（Anything V5），免 `--api` 直连 Gradio。
- 豆包语音大模型 TTS，音频以独立语音条展示，角色卡可绑定音色。

**🌐 工具与联网**
- Playwright **AI 浏览器**：像人一样逐级打开、点击、填表浏览网页，逐请求 SSRF 防护。
- `web_search` + `web_fetch`：结果列表、正文转 markdown、长页面分页读取，拥有独立轮数预算。
- 目录式插件扩展（内置 `file_reader`、`current_time`）。

**🎮 Galgame 模式**
- 全屏视觉小说：AI 绘制横版背景与全身立绘（绿幕抠像），自动合成语音、作曲 BGM，提供选项分支、剧情回放与存档点。

**🖥 会话与界面**
- 亮 / 暗主题、Enter 发送开关、全屏看图器、消息撤回与重新编辑、会话搜索 / 置顶 / 重命名。
- 对话可导出为自包含 `.zip` 迁移包，角色卡与世界观支持 JSON 导入导出。
- 手机端自适应抽屉布局，另有 Capacitor Android 客户端。

## 快速开始

### 环境要求

- **Python 3.9**（项目在 3.9.13 上开发，代码保持 3.9 兼容）
- Windows / macOS / Linux 均可；本地 SD、AI 浏览器等为可选能力

### 启动

```bash
# Windows（使用项目内 venv）
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py

# macOS / Linux
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

也可以直接用 uvicorn：

```bash
python -m uvicorn flyai.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。

### 首次配置

首次启动会自动从 `config/config.example.json` 生成 `config/config.json`（含密钥，不入库）。

1. 在 `config/config.json` 的 `auth.admin` 中配置管理员账号；普通用户由管理员创建，不开放自助注册。
2. 登录后进入右侧「⚙️ 设置」，添加 / 编辑提供商并填写 API Key，切换激活提供商，按需配置工具白名单目录。
3. 左侧「🧩 内容」中新建角色卡与世界观；在会话顶栏选择当前会话要扮演的角色与采用的世界观。

> 📖 如何写出完善的角色卡、对话范例与世界书：见 [`docs/使用教程.md`](docs/使用教程.md)。

## 详细功能

### 角色扮演与内容查询

- **角色卡**：头像（可上传）、简介、经历、**对话范例 mes_example**，以及一张内容表（标题 + 内容，由 AI 主动查询）。
- **对话范例（few-shot）**：填 3~5 轮范例对白，轮间单独一行写 `<START>`，可用 `{{char}}` / `{{user}}` 占位。注入时会明确告知 AI「范例剧情并未真实发生，仅供模仿语气」，避免范例污染剧情——这是维持原创角色语气的关键手段。
- **常驻条目（pinned）**：内容表条目勾选「常驻」后无条件注入系统提示词，等同 SillyTavern 世界书的 Constant，防止 AI 不调用 `query_lore` 时关键设定形同虚设。
- **世界观**：总体内容 + 内容表（同样支持常驻条目）。会话顶栏选择后，人设与世界观自动注入系统提示词。
- **内容查询**：AI 通过 `query_lore(kind='character'|'world', keyword=…)` 查询内容表。标题 + 正文多词打分（标题精确 / 子串加权）；启用语义嵌入时先做带相似度门槛（默认 0.55）的向量检索，不达标自动回退关键词；结果有上限（默认 8 条）并排序；表向量按内容签名缓存。
- **注入预算**：「⚙️ 设置 → 角色扮演」可给简介 / 经历 / 世界观 / 对话范例 / 常驻条目分别设字符预算，**默认全部为 0 = 不限**。超预算时按整段、整轮丢弃（绝不截半轮），末尾标「…（下略）」。
- **内置要求展示**：编辑角色卡或世界观时，「对白格式要求」「工具使用要求」会自动填入全局默认（来自 `roleplay.format_instruction` / `roleplay.tool_instruction`），支持逐卡覆盖，留空则遵循全局。
- 弹窗支持点击空白处关闭；表单有未保存修改时先确认。

### 对话格式与属性跟踪

- **对话格式（可开关）**：默认要求动作用 `（ ）`、内心活动用 `_…_`（下划线外侧不贴字母数字）、对白用 `「」`、空行分段。前端会把对白（彩色）、动作（斜体灰）、内心活动（彩色斜体）区分展示。
- **属性跟踪（可开关）**：在设置中定义属性列表（好感度、金钱等，含初始值与上下限）。启用后 AI 在每回合结束调用 `update_attributes` 提交增减量，顶栏实时显示最新数值；关闭「由 AI 自行提供」后仅作展示。
- 相关配置位于 `config/config.json` 的 `roleplay` 节点（`format_enabled`、`format_instruction`、`attributes`、5 个 `*_chars` 注入预算）；语义检索参数位于 `embeddings` 节点（`score_threshold` 默认 0.55、`keyword_limit` 默认 8）。

### 对话压缩（先 diff 确认，后写入）

- 顶栏「☁ 压缩」让 AI 把对话历史归纳进角色卡 / 世界观。**只生成提案**：弹窗展示逐项 diff（新增 / 更新 / 未变折叠，未变条目数与常驻数可见），确认前不写任何数据。
- 点「✓ 确认写入」后写入**当前会话副本**（角色库主卡不受影响）；若提案生成后设定又被改过（返回 409），提案作废需重新压缩。
- 内容表**按标题合并**：AI 只需给出要更新的标题，新设定用新标题追加；漏列的条目原样保留，常驻标记只增不减。
- 写入后才能下载压缩包；单 AI 会话还可把角色卡发布回角色库；细改可在写入后用会话副本编辑器完成。

### 云端图生图（单张 / 多张参考图）

`edit_image` 以对话中已有图片为参考调用 Seedream：

- **单张参考**：改风格、换装、改表情动作、局部修改、画面延展；省略 `image` 时自动取最近一张。
- **多张参考（最多 14 张）**：换装（图1人物 + 图2服装）、把人物放进指定场景、融合特征等。`image` 传路径数组，或省略路径并给 `image_count`，自动从历史收集最近 N 张（用户上传图与生成图都算）。
- AI 在 prompt 中用「图1」「图2」逐张说明用途，图片顺序与编号严格一致；历史不足指定张数会直接报错，不静默少图。

### 本地 Stable Diffusion（Anything V5）

- 管理员在「⚙️ 设置 → Stable Diffusion」启用并确认 WebUI 地址（默认 `http://127.0.0.1:7860`）。
- 对话输入区出现「SD」按钮：点亮走本地 SD，熄灭走云端生图模型；选择会持久化保存。
- 点亮时由**内置提示词 AI** 生成全部内容：英文 danbooru 标签、负向提示词，以及宽高 / 步数 / CFG / 采样器与调度器 / 种子等参数；缺失或越界参数自动回落到设置中的兜底默认。
- 经 Gradio API（`/config` + `/api/predict` + `/file=`）调用，WebUI 无需加 `--api`；更换 WebUI 版本导致 `fn_index` 变化时可在设置中修改。

### 语音合成（豆包语音大模型 · 朗读）

用户说「朗读一下 / 用语音说」时，AI 自动调用 `text_to_speech`，音频在消息内以**独立语音条**展示：播放 / 暂停、点击进度跳转、时长、下载；页面内多条语音互斥播放。

- 在火山引擎控制台开通「豆包语音合成」，在「⚙️ 设置 → 语音合成」填写 API Key。
- 默认资源版本 `seed-tts-2.0`（只能调用 2.0 音色），默认音色 `S_BcqE5org2`；音色授权以控制台为准。
- **音色清单**：管理员在设置中维护「显示名 + 音色 ID」，作为角色卡下拉绑定的数据源（清单不强制校验，默认音色允许填写清单外 ID）。
- **角色卡绑定音色**：编辑角色卡时可选一个绑定音色，未绑定则用全局默认。解析顺序 = AI 显式指定 → 角色卡绑定 → 默认音色（AI 可在对话中临时换音色）。
- **权限等级**：可设置语音合成最低等级（0 = 不限制，0–255；管理员恒可用）。等级不足的用户仍能看到工具，但调用会被拒绝并收到中文提示。
- 音频格式默认 mp3；`ogg_opus` 存为 `.ogg`；`pcm` 在服务端自动封装为 `.wav`。代理仅支持 HTTP（`http://host:port`，走 CONNECT），留空为直连。
- 保存配置后**立即生效**，无需重启。依赖 `websockets`（已在 `requirements.txt`）。

### 联网搜索与分页抓取

- **`web_search(query)`** 返回编号的结果列表（标题 / URL / 摘要），AI 自行挑选相关链接。默认 **Chromium 引擎**：Bing 优先走 **RSS 数据接口**（`&format=rss`，httpx 直连干净 XML、不启动浏览器），RSS 失败自动回退真实 Chromium 渲染（需 Playwright）；也可整体改用 httpx 直连。搜索引擎支持 **Bing**（默认）与**百度**（仅 Chromium，自动解包 `link?url=` 跳转链，遇验证码回退 Bing）。
- **`web_fetch(url)`** 抓取单页并整理为 markdown。长页面**分页读取、不丢后半部分**：每次返回约 `max_page_chars`（默认 6000）字，头部标明区间与总字数；继续读取用相同 URL 加 `offset=6000`、`offset=12000`…。完整正文在服务端短缓存 10 分钟，翻页不重复下载；`offset` 超出总长度会明确报错。
- **联网工具轮数独立**：`web_search` / `web_fetch` 拥有专属轮数预算（默认 15，设置中可调 1–50），与通用工具轮数（8）分开计数、互不占用；用满后 AI 仍可正常给出最终回答，只有再次发起同类调用时才拦截。

### AI 浏览器（Playwright，逐级进入网页）

可选功能，AI 通过一组共享浏览器会话的工具像人一样操作真实浏览器：

- **`browser_open(url)`**：打开页面，返回快照（标题 / URL + 编号元素 + 正文）。
- **`browser_click(target)`**：点击编号对应的链接 / 按钮，进入内部页面后返回新快照；`target=_blank` 自动切换新标签。
- **`browser_type(target, text, submit=true)`**：向输入框填字并可直接回车提交（搜索 → 结果页 → 点详情的标准流程）。
- **`browser_back()` / `browser_get_text()` / `browser_close()`**：后退、只取当前页正文、关闭会话。

会话按「用户 + 聊天会话」隔离复用，隐身上下文不共享 cookie；空闲自动回收标签页，动作数有上限。安全上对**页面与 JS 发出的每一个请求**做 SSRF 拦截（内网 / 本机地址拒绝），并自动屏蔽图片、字体等无用资源加速渲染。

安装（Windows / Ubuntu 均可，无头模式无需桌面环境）：

```bash
pip install -r requirements-browser.txt
playwright install chromium
# Ubuntu 另需系统库：
sudo playwright install-deps chromium
```

在「⚙️ 设置」中勾选启用，保存后立即生效（浏览器在首次使用时自动启动）。

### Galgame 模式（全屏视觉小说）

新建会话时勾选「🎮 Galgame 模式」，必须且只能选择一个角色卡（一期不支持群聊）。进入后为全屏视觉小说界面：

- **场景与立绘**：AI 自动绘制横版场景背景与全身立绘；立绘按纯绿幕（#00FF00）生成，前端 canvas 实时抠像并 despill 去绿边后叠加到背景。**第二张起立绘强制以历史立绘为参考**（AI 自选最接近的一张，未指定时自动补当前画面立绘），保证人物长相一致。立绘与角色卡绑定、跨会话复用（`uploads/<uid>/galgame/sprites/<cid>/`），背景按会话存放（`galgame/backgrounds/<sid>/`）；AI 切换前先查资产清单，有合适的直接切换。
- **演出**：底部对话框显示角色名与内容——对白去引号、白色；动作蓝、内心紫斜体、叙述浅色。整段语音**全部合成完成后才开始演出**（期间显示「角色思考中…」），随后每句语音与打字机文字同时出现，句末停住等点击。等待时点击跳过等待，打字中点击立即补全，再点进入下一句。
- **语音**：对白在流式阶段即开始合成（沿用角色卡音色与权限门槛），右下角喇叭可开关，状态持久化，默认开启。已合成语音保存在会话内，刷新或重入后直接播放，不再重新合成。
- **BGM**：首个场景完成后，AI 依场景氛围创作 MIDI，在浏览器本地用 FluidSynth（GeneralUser GS）合成循环播放；台词响起自动压低、结束恢复；场景变化优先复用已有曲目。右下角 🎵 可开关。首次播放需下载约 32MB 音色库，保存于浏览器 Cache API 并申请持久化授权，只下载一次；浏览器不支持或下载失败时静默降级为无音乐。
- **分支与输入**：每段叙事结束后 AI 给出 2–4 个选项（另附「✎ 自己输入」）；AI 未给选项时可自由输入下一段。
- **对白回看（Backlog）**：📜 按钮或鼠标滚轮上滑调出全屏清单，按时间列出全部台词、动作叙述、玩家自由输入与已选选项；超时未答的选项标注「600 秒未作答」，点击台词可重听语音，点空白处 / Esc 返回。
- **剧情重放**：回看清单顶部可「🔁 从头开始回放」，已作答与超时节点都能「🔁 从此处重放」；本地回放不调用 AI、不改剧情，遇到选项只展示当时选中的一个，超时 / 出错节点显示当时状态并可点击越过。「重放中」提示条可最小化为左下角小圆标。
- **自动播放**：▶ / ⏩ 开启后每句演完停顿约 1 秒进入下一句（语音未播完会等待），遇到选项自动停下，状态持久化。
- **存档点**：⚙️ 内可命名保存当前位置，清单带场景缩略图、一点即回；回档前确认是否先另存当前位置；每会话最多 30 个，超出淘汰最旧。
- **可靠性与续读**：每段结束、选项弹出前自动保存全部对话与场景；其它设备 / 标签页的演出可自动发现并静默接上（409 不再报「已有进行中的回答」）；服务在等待选项时重启也能从保存的工具调用恢复选项，不产生重复消息；历史中中断失联的工具调用会在下次发言时自动补占位修复，避免服务商 400。左上角 ⚙ →「返回主界面」可随时退出，运行在后台继续，重入时恢复场景。

限制：仅单角色；生图走云端模型（同 `edit_image`，暂不支持本地 SD）；绿幕效果取决于模型出图质量，作曲质量取决于模型（以调性 / 音阶 / 节拍对齐规则约束）。可在「⚙️ 设置 → Galgame 模式」调整最大工具轮数（默认 40）。

### 会话、素材与导出

- **全屏看图器**：点击对话任意图片放大，可下载，并按生图方式查看提示词（SD 展示正 / 负向标签与采样参数；云端展示完整提示词）。
- **角色卡 / 世界观**可导出为 JSON 备份或分享，也可一键导入。
- **对话导出为自包含 `.zip` 迁移包**：对话文本连同引用到的背景、立绘、MIDI、TTS 语音一起打包；导入到其他账号 / 服务器时自动释放素材并改写 URL，不再丢图丢声音。
- **消息撤回与重新编辑**：可撤回整轮消息（含群聊），历史中留下墓碑行，重新编辑时回填原内容。

### 多用户与权限

- **独立登录页**：不登录不能进入。登录签发随机 Bearer 令牌，7 天有效、可吊销，服务端持久化到 `data/` 下，重启 / 刷新不掉登录态；连续失败 5 次锁定 5 分钟。
- **管理员**：凭据写在 `config/config.json` 的 `auth.admin`（没有内置默认账号）。普通用户只能由管理员创建；管理员重置密码会立即踢出该用户的全部会话。
- **数据隔离**：角色卡、世界观、会话均按用户隔离。
- **权限等级**：用户等级 0–255，管理员最高。模型可自建或选用管理员共享的模型；共享模型可设「共享最低等级」，等级不足则完全不可见。TTS 也可单独设最低等级（见上）。

### 手机端与 Android 客户端

- 窄屏（≤ 860px）时左侧会话列表收为抽屉式菜单，点顶栏 ☰ 打开；对话框与表单单列堆叠，适配安全区，触控目标加大。
- `mobile/` 提供 Capacitor Android 外壳，应用 ID 为 `top.eastcloud.flyai`，默认连接 `https://fly.eastcloud.top`。
- 本地联调：启动本地服务后执行 `FLYAI_APP_SERVER=http://10.0.2.2:8000 npm run copy-web`，只替换拷贝产物中的服务器地址，不改源码。
- 构建：

  ```bash
  cd mobile
  npm install
  npm run sync         # 复制前端静态资源并 cap sync android
  npm run build:debug  # 输出 android/app/build/outputs/apk/debug/app-debug.apk
  npm run build:release
  ```

  Release 签名读取 `mobile/android/keystore.properties`（本地文件，不入库）；生成密钥需先设置 `FLYAI_KEYSTORE_PASS` 环境变量，见 `mobile/scripts/make-keystore.sh`。

## 目录结构

```
flyai/
├── main.py                 # 启动入口
├── config/                 # 配置（config.example.json 入库；config.json 含密钥不入库）
├── plugins/                # 插件目录（内置 file_reader、current_time + 开发文档）
├── workspace/              # 文件读取默认白名单目录
├── docs/                   # 使用教程与 README 资源
├── mobile/                 # Capacitor Android 外壳（前端页面内置）
├── flyai/                  # 应用包
│   ├── app.py              # FastAPI 装配
│   ├── config.py           # 配置模型
│   ├── schemas.py          # 内部统一消息模型
│   ├── providers/          # OpenAI 兼容 / Anthropic 适配器
│   ├── agent/loop.py       # agentic 工具调用循环
│   ├── tools/              # 工具基类 / 注册表 / 插件加载器
│   ├── sd/                 # 本地 Stable Diffusion 引擎（提示词 AI + Gradio 客户端）
│   ├── sessions/           # 会话存储（memory / json）
│   └── web/static/         # 前端单页（无构建）
└── tests/                  # 编解码、安全与端到端测试
```

## 开发与测试

```bash
# 安装开发依赖并运行测试
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest tests/ -q
```

### 换存储后端

`config/config.json` 中 `history.backend`：

- `memory`：默认，进程内存储，重启丢失。
- `json`：每个会话一个文件，重启保留。

### 插件扩展

工具以目录式插件提供，可自行添加，详见 [`plugins/README.md`](plugins/README.md)。