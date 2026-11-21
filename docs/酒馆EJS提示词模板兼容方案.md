# 酒馆 EJS 提示词模板兼容方案（ST-Prompt-Template）

> **状态：方案 B（客户端执行器）已确认，待实施（2026-11-21）；方案 A（服务端 quickjs）保留为参考与备选**
> **参考对象：[zonde306/ST-Prompt-Template](https://github.com/zonde306/ST-Prompt-Template)（AGPL-3.0）**
> **结论：语义可兼容。执行层改为客户端沙箱执行 + ops 回传，服务端只负责校验与生效；主动轮与记忆快照明确不执行模板**
> **前置条件：管理员功能开关 + 用户个人开关双开，默认关闭；不改变现有卡片 JS 沙箱策略**
> **信任模型：模板代码只在本机 null-origin 沙箱中执行，拿不到宿主凭据与会话数据；客户端回传的只是“渲染结果 + 受限操作”，服务端逐项校验后才生效**
> 本文只描述通用技术能力，不包含任何真实卡片叙事正文、用户变量数据或密钥。

---

## 0. 结论摘要

> **2026-11-21 更新：** 交付方案已改为第 19 节「方案 B：客户端执行器」。
> 第 1–18 节（含第 4–15 节的执行层、服务端链路与分阶段计划）描述的是方案 A
> （服务端 quickjs），保留为技术参考与备选实现，**不按该方案实施**。
> 两套方案共享同一套 ops 语义，后续如需服务端 fallback 可直接复用第 19 节的协议。

ST-Prompt-Template 是酒馆的 EJS 提示词模板扩展：在提示词发给模型之前执行 `<% %>`
里的 JavaScript，先把世界书、预设、角色卡、聊天消息里的模板渲染成文本，再交给模型；
模型回复到达后再执行一次消息里的 `<% %>`，用来更新变量、渲染状态栏。

FlyAI 能吃下这套**语义**，但不能"把扩展搬过来"：

1. **运行环境不同。** 扩展跑在浏览器里，直接拿得到 `window`、SillyTavern 的
   `SillyTavern` 全局与 IndexedDB。FlyAI 的后端是 Python，提示词在服务端组装，前端
   又明确规定"卡片代码不进主窗口执行"（现有 `CardAppFrame` 只在 null-origin
   iframe 里跑卡内应用）。要兼容 EJS，需要一个独立执行层：方案 A 在服务端新增受限
   JS 运行时；方案 B 改为客户端 null-origin 沙箱 + ops 回传（§19）。
2. **数据模型不同。** 扩展依赖酒馆世界书的 `uid`、标题（memo）、`position`、
   `order`、`probability` 与本扩展自有的 `[GENERATE:*]` / `[InitialVariables]` /
   `@@` 装饰器约定。FlyAI 的 `LoreEntry` 没有 `uid`/`memo` 字段，标题直接兼作
   触发词；预设块也没有扩展可回写的标识。需要补字段和导入映射。
3. **组装链路不同。** FlyAI 目前只在 `flyai/agent/loop.py::build_request`
   （`flyai/agent/loop.py:418-481`）做一次最终组装，主动轮（proactor）与"记忆快照"
   路径又绕开了这条链路；扩展则假定所有提示词都经过酒馆自身的 prompt 构造器。
   要做就得先把"唯一出站组装点"坐实。

建议路线（方案 A，保留参考；方案 B 的实际阶段见 §19.9）：

| 阶段 | 内容 | 风险 | 价值 |
|---|---|---|---|
| **P1 提示词侧** | 服务端 EJS 引擎 + 变量作用域 + 世界书 `[GENERATE]` 注入 + 装饰器子集 + 预设/角色/世界书模板渲染 | 中 | 让"变量驱动的提示词"跑起来，覆盖大多数卡的核心用法 |
| **P2 输出侧** | 助手消息落库时执行 `<% %>`（只执行一次）、`[RENDER]` 注入、消息级变量 | 高（执行模型生成的代码） | 状态栏、好感度等"回复即更新"的用法 |
| **P3 深度兼容** | `@INJECT` 精确插桩、主动轮/快照统一组装、更多宿主 API | 中 | 与现有 ST 卡的高保真兼容 |

**不建议做的事：**

- 不把 AGPL 源码复制或翻译进仓库。按公开文档描述的语义做**净室实现**，行为和
  命名可以兼容，代码独立编写。
- 不在前端主窗口执行任何卡片/消息里的 JS。方案 B 使用独立 null-origin 沙箱执行器
  （§19.7），同样不进入主窗口。
- 不默认开启。输出侧等于"执行模型写出来的代码"，必须用户显式同意。

---

## 1. 参考对象与行为基线

### 1.1 扩展在酒馆中的工作方式

按官方文档（`docs/features.md`、`docs/reference.md`）：

1. 酒馆构造完整提示词（预设、世界书、角色定义、聊天消息合并完毕）。
2. 扩展遍历提示词里的每个 `<% %>` 语句块，用 EJS 执行，替换为执行结果。
3. 渲染后的提示词发给 LLM。
4. LLM 回复到达、消息落楼后，扩展再执行消息正文里可见的 `<% %>`，把结果写回
   展示内容；代码块里的 `<% %>` 不执行（与代码高亮冲突）。
5. 提示词里要隐藏消息中的 `<% %>`，需要靠一条 `promptOnly` 正则
   （`/<%.*?%>/g`）在组装时剔除，扩展本身不改原始消息。

扩展可作用于：**世界书、预设提示词、角色相关字段、聊天消息**。

### 1.2 语法与能力面

| 类别 | 语法/能力 | 说明 |
|---|---|---|
| EJS 语句 | `<% ... %>` | 执行 JS，无输出 |
| 输出 | `<%= ... %>` / `<%- ... %>` | 前者在消息渲染时 HTML 转义，后者原样输出；提示词侧两者等价 |
| 注释/转义 | `<%# ... %>`、`<#escape-ejs>...</#escape-ejs>` | 转义区内 `<%` 变成字面量 |
| 变量 | `variables.x`、`getvar/setvar/incvar/decvar` | 作用域：`global`、`local`（按聊天）、`message`（按楼层）、`cache`（渲染期）、`initial`（初始变量） |
| 世界书标记 | 标题前缀 `[GENERATE:BEFORE/AFTER]`、`[GENERATE:{idx}:...]`、`[GENERATE:REGEX:...]`、`[RENDER:...]`、`[InitialVariables]` | 控制条目内容注入到提示词/输出的位置 |
| 装饰器 | 条目内容首行 `@@activate`、`@@generate_before`、`@@if`、`@@preprocessing`、`@@preload`、`@@private`、`@@message_formatting` 等 | 改变条目激活/预处理行为 |
| 注入 | `@INJECT pos=...,role=...`、`target=...,index=...,at=...`、`regex=...` | 按绝对位置/目标角色/正则匹配插入整条消息 |
| 宿主 API | `getvar/setvar/getwi/getchar/getpreset/getChatMessage/injectPrompt/activewi/activateRegex/evalTemplate/prepareContext` 等 | 约五十个函数，覆盖变量、内容读取、激活、注入 |
| 依赖库 | `_`（lodash）、`faker` 等 | 扩展内置 |

### 1.3 许可与合规边界

上游为 **AGPL-3.0**。FlyAI 若复制其源码，整个项目在分发时需要按 AGPL 开源，
且网络服务用户有权获取源码。本项目不做这件事。可接受的边界：

- 读公开文档、公开示例，提炼**行为约定**（标记名字、参数含义、作用域语义）→ 可。
- 复制/改写其 `.js` 实现、直接打包其 dist → 不可。
- 自行实现的模块只标注"兼容 ST-Prompt-Template 文档语义"，不引入其代码或
  LICENSE 文件。

---

## 2. FlyAI 现状与差距

### 2.1 现有提示词组装链路

```
routes_chat.py
  ├─ render_preset()            flyai/preset/apply.py:61    预设块 → main/tail/depth，内部已展开酒馆宏
  ├─ 世界书选择/触发             flyai/lore/query.py          pinned + keys + probability → system/depth
  ├─ 角色字段（system_prompt / PHI / intro / mes_example …）
  └─ run_agent_loop()
        └─ build_request()      flyai/agent/loop.py:418-481
              1. _actor_view / keep_turns / 去墓碑
              2. ST 正则 prompt 侧      flyai/st/regex_scripts.py::apply_to_messages
              3. 剥离 MVU 更新块        flyai/st/mvu.py::strip_update_blocks
              4. 上下文截断
              5. 插入 system_prompt
              6. 插入 depth 块（预设/世界书 @depth）
              7. 追加 PHI
              8. 追加 LIVE_CTX（心情/时钟/自我记忆等）
              9. 追加工具 nudge / 循环通知
             10. 追加 assistant prefill（必须是最后一条）
```

相关事实：

- **宏**：`flyai/tavern.py` 在 `render_preset` / 世界书注入时展开
  `{{char}}`、`{{user}}` 等；`{{getvar}}` 目前展开为空，`{{setvar}}` 不产生文本
  （本项目尚无变量系统）。
- **快照路径**：`routes_chat.py:2509` 的 `_snapshot_transcript` 与
  `POST /rooms/{rid}/memory-snapshot` 只拼一段文本给记忆模型，不经过
  `build_request`，也不应用世界书/预设。
- **主动轮**：`flyai/proactor/runner.py::fire`（约 65 行起）自己构造 brief 与
  提示，不经过 `build_request`。
- **会话持久化**：`flyai/sessions/json_store.py` 的 `session_from_dict` /
  `session_to_dict` 使用**显式字段表**（80-109、112-138 行），新增字段必须两边
  同步，否则存档点恢复会丢数据。
- **消息模型**：`flyai/sessions/models.py` 的 `Message` 现有 `content`、
  `actor_id`、`tool_calls` 等字段，没有"原始内容/渲染内容"之分。

### 2.2 能力差距矩阵

| 能力 | ST-Prompt-Template | FlyAI 现状 | 差距 |
|---|---|---|---|
| 提示词内 JS 执行 | 浏览器内 EJS | 无 JS 运行时 | **硬缺口**：方案 B 用客户端沙箱执行器补（§19） |
| 变量系统 | global/local/message/cache/initial | 仅 MVU 的 `stat_data`，面向卡片 UI | 需独立变量树与持久化 |
| 世界书标题标记 | 原生支持 | 标题只作触发词；无 memo/uid | 需补字段与导入映射 |
| 条目装饰器 | 15+ 个 `@@` 装饰器 | 无 | 需解析器 + 子集实现 |
| 消息内模板渲染 | 前端展示时执行 | 展示层只跑显示正则；流式节流会重复渲染 | 改为**落库时执行一次** |
| 注入 `@INJECT` | 支持三种插入模式 | 无 | P3 |
| 世界书 `getwi` | 支持读任意条目 | 可查条目但未暴露给模板 | 需 API 适配 |
| 预加载/递归激活 | 支持 `@@preload`、`activewi` | 世界书有触发与递归相关逻辑 | 需映射 |
| 依赖库 `_`/`faker` | 内置 | 无 | 不做（或后续按需） |

### 2.3 为什么不能直接复用现成的前端执行路径

> 方案 B 使用“新建独立模板 runner”的方式解决了本节的问题；本节反对的是直接复用
> `CardAppFrame` 或展示层，不是反对客户端执行。详见 §19.2/§19.7。

有两条现成的"执行 JS"路径，都不适合：

1. **卡片沙箱 iframe（`CardAppFrame`）**：null-origin、`connect-src 'none'`、父窗口
   遮蔽，专门给卡内 UI 应用用。它拿不到聊天上下文，且只在用户打开卡片应用时存在，
   与"每次请求都要执行提示词模板"的生命周期不符。
2. **前端展示层**：`webapp/src/components/markdown/Markdown.tsx:60-64` 只做显示
   正则的字符串替换，不执行代码；流式渲染有节流（`useThrottleMarkdownRender`），
   同一段内容会被多次重渲染，若在这里执行 `<% setvar() %>` 会产生重复副作用。

结论：**执行层放服务端**，与现有"卡片 UI 代码不进主窗口"的策略不冲突。

---

## 3. 目标与非目标

### 3.1 目标

- 支持把 `<% %>` 模板写进：预设块、世界书条目、角色卡文本字段（系统级提示词、PHI、
  简介、对话范例等）、聊天消息。
- 提示词侧：在发给模型前统一渲染，支持条件、循环、变量读写、读取世界书/角色/预设/
  聊天消息。
- 输出侧（P2）：助手消息最终落库时执行一次模板，支持变量更新与显示改写；原始内容
  保留，供编辑/重生成/提示词侧隐藏使用。
- 变量：`global`（跨会话、按用户）、`local`（按会话）、`message`（按消息）、
  `cache`（单次渲染内）、`initial`（世界书初始值）。
- 与现有 ST 正则、MVU、酒馆宏、LIVE_CTX、PHI、prefill 的顺序**明确且可测**。
- 默认关闭；管理员可设全局开关与最低权限等级；普通用户可自行开启。

### 3.2 非目标（至少 P1 不做）

- 不执行卡内 UI 应用代码；不改变 `CardAppFrame` 沙箱。
- 不提供网络、文件、进程、环境变量、宿主凭据访问。
- 不实现 EJS 的 `include`、异步/定时器、ES module 导入。
- 不内置 lodash/faker；不做完整的酒馆宏二次展开。
- 不做卡片包内任意 HTML 的渲染（输出侧 HTML 仍走现有 sanitizer）。
- 不兼容 `@@iframe` 的样式隔离语义；如需展示型 HTML，走现有卡片沙箱能力。

---

## 4. 总体架构

### 4.1 模块划分

新增 `flyai/template/`：

| 模块 | 职责 |
|---|---|
| `engine.py` | quickjs 运行时封装：创建/复用上下文、注入宿主 API、超时/内存/输出上限、错误包装 |
| `syntax.py` | EJS 词法扫描：识别 `<% %>`、`<%= %>`、`<%- %>`、`<%# %>`、`-%>`、`<%_`、`<#escape-ejs>`；把模板拆成"文本段 + 代码段" |
| `scopes.py` | 五类作用域树；变量路径读写、类型校验、深拷贝、脏标记 |
| `api.py` | 宿主函数实现与导出表；所有函数同步、有参数校验、有返回值大小上限 |
| `directives.py` | 世界书标题标记解析（`[GENERATE:*]`、`[RENDER:*]`、`[InitialVariables]`）、`@@` 装饰器解析与执行（`@@if`、`@@private` 等） |
| `render.py` | 对外的两个入口：`render_prompt_text()`、`render_output_text()`；负责上下文生命周期与错误策略 |
| `inject.py` | `@INJECT` 解析、排序、插桩（P3） |
| `store.py` | 用户级 `global` 变量的读写与原子落盘 |

### 4.2 请求级上下文

每次生成创建一个 `TemplateContext`，在整个请求内共享：

```
TemplateContext
 ├─ engine        : quickjs 运行时（请求结束释放）
 ├─ user_id / session_id
 ├─ scopes        : global / local / message / cache / initial
 ├─ 只读数据视图   : char / user / group / world / preset / messages
 ├─ 写入缓冲区     : 变量变更、激活请求、注入请求
 └─ 限额计数       : 已执行单元格数、累计耗时、累计输出字节
```

生命周期：

1. `routes_chat` 进入生成前创建 `TemplateContext`。
2. 世界书选择阶段：解析标题标记，把 `[GENERATE:*]` 条目放入独立候选列表，不按普通
   条目注入。
3. `loop.build_request` 最终组装时调用 `render_prompt_text`，按**消息顺序**逐条
   执行模板；随后处理 `[GENERATE]` 条目的注入。
4. 请求结束：把脏变量写回 `Session` / 用户级存储；捕获激活/注入请求并执行。
5. 任何阶段出错：默认把该模板段替换为空字符串并记一条警告；不中断生成（可配置为
   严格模式，见 §12）。

### 4.3 与现有处理的顺序

最终顺序（P1）：

```
消息副本（_actor_view / 去墓碑）
  → ST prompt 正则（现有）
  → 剥离 MVU 更新块（现有）
  → 截断（现有）
  → 插入 system_prompt / depth 块 / PHI / LIVE_CTX / nudge
  → 【新增】EJS 提示词渲染（按消息顺序）
  → 【新增】[GENERATE:*] 条目求值与注入
  → assistant prefill（保持最后一条）
```

选择"最后统一执行"而不是在 `render_preset`/`lore` 各做一次，原因：

- 只有一个执行点，顺序确定、易测试；变量写入顺序与模型看到的顺序一致。
- 世界书、预设、角色字段都已经在消息数组里，无需把上下文穿进每个渲染函数。
- `[GENERATE:AFTER]` 需要知道最终消息数组，只有在组装后才有准确位置。

代价与处理：

- **宏顺序**：现有宏在 `render_preset` 阶段已展开，EJS 产出的文本里若再写
  `{{char}}` 不会二次展开。P1 明确不支持"EJS 输出中的宏"；需要时在模板里直接拼
  变量或调用 `getchar()`。文档中注明。
- **正则顺序**：prompt 侧正则先于 EJS。这与"正则用来隐藏模板代码"的用法兼容
  （先剔除，再执行其余）。输出侧另有一套顺序，见 §10。

### 4.4 主动轮与快照

> **方案 B 结论：两条路径都不执行模板，且不做代码改动。** 主动轮/定时作业/提醒/watcher
> 与记忆快照保持现状；挂载含模板的卡或预设时，不应启用主动轮（使用约束，见 §19.1）。
> 以下为方案 A 的设想，保留供参考。

- **主动轮**（`proactor/runner.py::fire`）：P1 不改其提示词构造，但它在调用模型前
  若复用 `build_request` 则应自动获得模板能力。若暂时不复用，则在 P3 统一。
  文档与设置面板需标明："主动轮是否执行模板"取决于是否走统一组装点。
- **记忆快照**（`_snapshot_transcript`）：**不执行模板**。快照给记忆模型的是历史
  原文摘要，执行模板会产生副作用与额外开销；保持现状，并在代码注释中写明这是有意
  绕开。

---

## 5. 执行层

### 5.1 运行时选型

| 方案 | 结论 | 原因 |
|---|---|---|
| **quickjs（PyPI `quickjs`）** | **采用（P1）** | 纯 C 实现、进程内、可设时间/内存/栈上限；有 cp39 Windows x64 / Linux x86_64 / macOS x86_64 wheel |
| mini-racer（V8） | 否 | 体积大，Python 3.9 wheel 可用性差 |
| 内嵌 Node 子进程 | 否 | 部署复杂，多一个进程与 IPC 攻击面 |
| 自写 JS 解释器 | 否 | 工作量与兼容性不可接受 |
| 不用 JS，只做条件/变量 DSL | 备选 | 兼容性差，现有卡大面积不可用；如运行时不可用可作为降级模式 |

依赖声明：

```
quickjs==1.19.4; platform_machine == "AMD64" or platform_machine == "x86_64"
```

- 采用**可选依赖**：导入失败时功能自动不可用，设置页给出明确错误，不影响主流程启动。
- 不支持 ARM64 服务器（无官方 wheel）。需要时要么从源码构建 quickjs，要么只提供
  降级 DSL。
- 该包上游已归档，但接口稳定；封装在 `engine.py` 内，后续可替换。

### 5.2 运行参数

| 项 | 默认值 | 说明 |
|---|---|---|
| 单单元格执行时限 | 250 ms | `set_time_limit`；超时抛错并按错误策略处理 |
| 单请求累计时限 | 2 s | 超限后剩余模板直接跳过并告警 |
| 内存上限 | 16 MB | `set_memory_limit` |
| 栈上限 | 1 MB | `set_max_stack_size` |
| 单单元格输出 | 256 KB | 超出截断并告警 |
| 单请求输出总量 | 2 MB | 超出截断 |
| 单元格数量 | 500/请求 | 超出后剩余不执行 |
| 异步/定时器 | 不提供 | 所有宿主 API 同步；不调用 `execute_pending_job` |
| 网络/文件 | 不提供 | 运行时无 `fetch`/`XMLHttpRequest`/`require`/`import` |
| 宿主对象 | 只注入白名单函数与只读快照 | 不注入 Python 对象、不暴露 `os`/`sys`/数据库句柄 |

每次请求新建 runtime/context，**不跨请求复用全局对象**；变量通过 JSON 快照进出，
避免请求间串状态。

### 5.3 错误处理

EJS 执行错误分三档：

| 档位 | 行为 | 适用 |
|---|---|---|
| `silent`（默认） | 该单元格输出空串；记录 `warning` 日志（含会话 id、来源标题、错误类型，不含堆栈给模型） | 普通用户 |
| `inline` | 输出一段固定短标记（如 `[模板错误]`），不含异常细节 | 用户自选 |
| `debug` | 输出错误类型与行号，管理员/开发者可见 | 管理员排障 |

错误**不得**导致：整轮失败、泄露 Python 堆栈、把宿主路径/配置写进提示词、重复执行
同一副作用。

### 5.4 编译缓存

- 单元格字符串 → 编译后的 JS 函数缓存（LRU 512 条），只缓存**无装饰器、无变量写入**
  的纯模板；带 `setvar`/`injectPrompt` 等副作用的模板禁用缓存。
- 缓存键包含模板文本哈希与引擎版本；进程重启即失效，不落盘。

### 5.5 平台支持

| 平台 | P1 | 说明 |
|---|---|---|
| Windows x64（当前开发机） | 支持 | wheel 可用 |
| Linux x86_64 | 支持 | wheel 可用 |
| macOS x86_64 | 支持 | wheel 可用 |
| Linux/Windows ARM64 | 不支持 | 无 wheel；功能显示"当前平台不可用" |
| 浏览器/Android WebView | 不适用 | 执行在服务端，APK 只是壳 |

---

## 6. 模板语法实现

### 6.1 EJS 子集

P1 支持：

| 标签 | 行为 |
|---|---|
| `<% code %>` | 执行语句 |
| `<%= expr %>` | 输出，提示词侧与 `<%-` 相同；输出侧 HTML 转义 |
| `<%- expr %>` | 输出原文 |
| `<%# comment %>` | 丢弃 |
| `<%_ code _%>` / `-%>` | 控制空白裁剪（按 EJS 语义实现常用部分） |
| `<#escape-ejs> ... <#/escape-ejs>` | 区间内 `<%`/`%>` 不解析 |
| `%%>` 转义 | 输出字面 `%>` |

P1 不支持：

- `include()` / 布局 / 文件名相对路径。
- 异步模板、`await`、Promise 返回值（宿主 API 同步返回）。
- 自定义分隔符 `delimiter`。
- `with` 语义差异导致的顶层变量泄漏（按 EJS 默认 `with(locals)` 行为实现，但关闭
  `strict`；模板内未声明变量读写失败是常见卡内写法，需保持兼容）。

实现方式：`syntax.py` 先把模板拆成片段，再拼成一段 JS 函数体：

```js
(function (locals) {
  with (locals) {
    var __out = [];
    __out.push("text");
    code;
    __out.push(String(expr));
    return __out.join("");
  }
})
```

编码 JSON 字符串时注意 `</script>`、U+2028/U+2029，避免破坏运行时。

### 6.2 词法扫描要点

- 按 `<%` 找起始，按 `%>` 找结束；`<%%` 与 `%%>` 是转义。
- `<#escape-ejs>` 用栈匹配，未闭合时按到文本末尾处理并告警。
- 代码块内允许字符串里出现 `%>` 吗？EJS 官方扫描器不支持（会提前结束）；保持同样
  行为，文档提示避免在字符串中写 `%>`。
- 空代码块（`<%%>`、`<%=%>`）按空输出处理，不报错。

### 6.3 与酒馆宏的共存

- 现有 `{{char}}`、`{{user}}`、`{{random}}` 等宏仍在 `render_preset`/世界书注入
  阶段展开；模板代码里写 `{{char}}` 会先被宏展开，再进入 JS 字符串，容易产生语法
  错误。推荐模板里用 `getchar().name` / `char` 只读变量。
- EJS 输出中的 `{{...}}` 不再二次展开（P1 限制）。
- `{{getvar}}` 是否映射到新变量系统：P1 **不映射**，避免改变现有提示词行为；
  提供 `getvar()` 模板函数替代。P2 可评估映射。

---

## 7. 变量作用域

### 7.1 五类作用域

| 作用域 | 生命周期 | 存储位置 | 读写 API |
|---|---|---|---|
| `global` | 同一用户跨会话 | `data/template_vars/<uid>.json`（§7.2） | `getvar(name,{scope:'global'})` 等 |
| `local` | 当前会话 | `Session.template_vars` | 默认作用域为 `local` |
| `message` | 当前消息 | `Message.template_vars` | `getvar(name,{scope:'message'})`；输出侧默认 `message` |
| `cache` | 单次请求 | 内存，请求结束丢弃 | `getvar(name,{scope:'cache'})` |
| `initial` | 世界书 `[InitialVariables]` 写入，持久同 `message`/`local` | 取决于写入目标 | 只读默认值来源 |

模板同时可访问只读对象（不是变量作用域的写入目标）：

- `variables`：当前作用域视图（兼容 `<%- variables.foo %>`）。
- `char` / `user` / `group`：角色名、简介等只读字段。
- `chat`：消息数组摘要（id、role、content、index），只读。
- `matched_message` / `matched_message_index` / `matched_message_role`：
  `[GENERATE:REGEX]` 命中时可用。

### 7.2 持久化模型

**会话级（local / message）**：

```jsonc
// Session 新增字段
"template_vars": {
  "local": { "affection": 60, "flags": { "met": true } },
  "message": {
    "<message_id>": { "status": "normal" }
  }
}
```

`Message` 新增：

```jsonc
{
  "raw_content": "",           // 仅当原文含模板且已被渲染时非空
  "template_vars": {},         // 该消息的 message 作用域
  "template_rendered": false   // 标记是否已执行过输出侧模板
}
```

**用户级（global）**：独立文件 `data/template_vars/<uid>.json`，结构：

```json
{ "version": 1, "vars": { "...": "..." }, "updated_at": "..." }
```

- 原子写：临时文件 + `os.replace`；每用户一把内存锁。
- 大小上限：默认 1 MB/用户，超出拒绝写入并提示清理。
- 不放进 `config/config.json`，不进 git，不导出到公开分享的卡片数据。

### 7.3 读写语义

| 函数 | 语义 | 校验 |
|---|---|---|
| `getvar(name, opts)` | 读取；缺失时返回 `opts.defaults`（可为对象/字符串/数字） | `name` 为字符串；路径深度 ≤ 8 |
| `setvar(name, value)` | 写入当前作用域；返回写入值 | 值必须能 JSON 序列化；单值 ≤ 64 KB |
| `incvar(name, delta=1)` | 数字自增，缺失按 0 处理 | 结果必须是有限数 |
| `decvar(name, delta=1)` | 数字自减 | 同上 |
| `getMessageVar(id, name)` | 读指定消息变量 | 只能读当前会话 |
| `setMessageVar(id, name, value)` | 写指定消息变量 | 不得写用户消息（P1）；见 §12 |
| `unsetvar(name)` | 删除 | — |
| `hasvar(name)` | 存在性 | — |

变量名规则：允许 `a-zA-Z0-9_.-`，点号表示嵌套路径；禁止 `__proto__`、`constructor`、
`prototype`，禁止空段。

### 7.4 并发与原子性

- 同一会话的生成请求由现有会话级锁串行化（若没有，需在 `routes_chat` 生成入口加
  per-session 锁），避免两个请求同时写 `local`。
- 请求内写入先进缓冲区，**请求成功结束后**统一落盘；请求失败/中断则按配置决定
  是否丢弃（默认丢弃，避免"半截副作用"）。
- 输出侧执行发生在助手消息落库之前，与消息内容在同一次写盘内提交（同一 JSON 文件
  `os.replace`），保证"消息存在 ⇒ 变量已保存"。

---

## 8. 宿主 API 面

### 8.1 变量

`getvar`、`setvar`、`incvar`、`decvar`、`unsetvar`、`hasvar`、`getMessageVar`、
`setMessageVar`、`getGlobalVar`、`setGlobalVar`（后两个是 `getvar`/`setvar` +
`scope:'global'` 的语法糖）。

### 8.2 内容读取

| API | 返回 | 限制 |
|---|---|---|
| `getchar(name?)` | `{ name, description, personality, scenario, first_mes, mes_example, system_prompt, post_history_instructions, ... }` | 只读；只返回当前会话可访问的角色 |
| `getuser()` | `{ name, ... }` | 不含账号、权限、密钥 |
| `getgroup()` | 群成员摘要 | 只读 |
| `getwi(keywordOrTitle)` | 世界书条目原文（可选 `{raw:true}` 跳过变量展开） | 只读；单条 ≤ 64 KB |
| `getpreset(name?)` | 预设块摘要（name/content/role/position/order） | 只读；不返回 params 中的 api key |
| `getChatMessage(index, role?)` | 单条消息 `{ index, role, content, name }` | 只读当前会话；只能访问最近 N 条（默认 100） |
| `getChatMessages(start?, end?, role?)` | 消息数组 | 同上，总量 ≤ 256 KB |
| `substituteParams(text)` | 按当前角色/用户名展开宏 | 仅白名单宏 |
| `getContext()` | 上下文摘要（角色名、消息数、模型名） | 不含密钥、内部路径 |

### 8.3 激活与注入

| API | 行为 | 限制 |
|---|---|---|
| `activewi(title, {position})` | 请求激活指定世界书条目，进入本轮注入队列 | 只能激活当前会话可用世界书；单轮 ≤ 8 条；不递归 |
| `activateRegex(name)` | 标记某条正则在本轮启用 | 只能启用会话已绑定的正则 |
| `injectPrompt(text, {role, position, depth})` | 请求插桩一段文本 | P1 只支持 `role` + `position:'before'/'after'/'depth'`；单轮 ≤ 8 条、总长 ≤ 32 KB |
| `evalTemplate(text)` | 在当前上下文内执行另一段模板 | 递归深度 ≤ 3；计入总限额 |

`activewi` 与 `injectPrompt` 的请求在 `build_request` 求值阶段收集，注入前重新做长度
与数量校验，失败则丢弃并告警。

### 8.4 明确不提供

- `fetch`、`XMLHttpRequest`、`WebSocket`、`importScripts`、`require`、`import()`。
- 文件、目录、环境变量、进程、数据库、Cookie、localStorage。
- 用户 token、API key、管理员设置、其他用户数据。
- `SillyTavern` 全局对象与扩展事件总线。
- DOM/window/document（运行时不是浏览器环境）。

### 8.5 参数与返回值约定

- 所有宿主函数对 JS 层是同步函数，参数做类型校验；错误以 `Error` 抛出，由错误策略
  统一处理。
- 返回的字符串有长度上限；对象先 JSON 序列化再进运行时，超大对象抛错。
- 不返回 Python 对象引用；每次调用都是值拷贝。

---

## 9. 提示词侧链路（P1）

> 本节描述方案 A 的服务端执行点。方案 B 的挂接位置相同（现有插入之后、prefill 之前），
> 但改为提取单元格 → 客户端执行 → commit 拼回，见 §19.4。

### 9.1 执行点

在 `build_request` 完成现有插入后、追加 prefill 前执行：

```
for msg in msgs:
    if 含 "<%" 且 msg 不属于 prefill:
        msg.content = render_prompt_text(msg.content, tctx, source=msg.origin)
```

- 只处理字符串内容；工具调用参数不做模板处理（P1）。
- 每条消息执行前记录来源（预设块名/世界书标题/角色字段/消息 id），用于错误日志与
  调试面板。
- 执行顺序即消息顺序：system_prompt 在前，历史在后，PHI/LIVE_CTX 在尾部。
- `cache` 作用域在同一请求内跨消息共享；`local` 写入立即在后续消息中可见。

### 9.2 世界书标题标记

`LoreEntry` 新增两个可选字段（默认空，不改变现有 JSON）：

| 字段 | 含义 |
|---|---|
| `st_uid` | 酒馆世界书条目的 `uid`（导入时保留） |
| `memo` | 酒馆的标题/注释（`comment`）；`title` 继续用于触发与显示 |

标记解析顺序：

1. 优先看 `memo`，为空再看 `title` 的方括号前缀。
2. 识别 `[GENERATE:BEFORE]`、`[GENERATE:AFTER]`、`[GENERATE:{idx}:BEFORE/AFTER]`、
   `[GENERATE:REGEX:...]`、`[RENDER:*]`、`[InitialVariables]`。
3. 标记本身不作为触发词（现有"keys 为空则用 title 当触发词"的逻辑需在标记存在时
   跳过标记部分）。
4. `[GENERATE:BEFORE]` 条目按酒馆语义视为常驻（蓝灯）；`[GENERATE:AFTER]` 允许绿
   灯触发。

选择阶段的分流：

- `[GENERATE:*]` 条目**不**进入普通 `system`/`depth` 注入，进入 `generate_before` /
  `generate_after` / `generate_index` / `generate_regex` 四个候选桶。
- `[RENDER:*]` 条目只进输出侧候选桶（P2）；P1 识别但忽略。
- `[InitialVariables]` 在进入生成前处理（§9.4）。

`build_request` 末尾：

```
渲染完 msgs 后：
  1. generate_before：逐条执行模板 → 作为 system 消息插到 msgs 最前（顺序按 order）
  2. generate_index：执行模板 → 插到指定下标前后（下标以渲染后的 msgs 为准）
  3. generate_regex：对每条消息内容做不区分大小写匹配，命中则执行该条目模板并插到
     命中消息之前；匹配在本轮已渲染文本上进行
  4. generate_after：逐条执行模板 → 追加到 msgs 末尾（prefill 之前）
```

约束：

- 所有注入内容长度计入总量上限（§5.2）。
- 注入的消息也带来源标记，重复注入需去重（同一 `st_uid` 单轮只注入一次）。
- `generate_before/after` 条目自身的模板只能访问只读数据 + 变量，不能再触发
  `[GENERATE]`（防递归）。
- `{idx}` 越界时丢弃并告警。

### 9.3 装饰器

P1 实现：

| 装饰器 | 行为 |
|---|---|
| `@@activate` | 视为常驻条目（等价 `pinned=True`），但仅本卡生效，不改写存储 |
| `@@dont_activate` | 禁止任何触发；`activewi` 也忽略 |
| `@@generate_before` / `@@generate_after` | 等价对应标记 |
| `@@initial_variables` | 等价 `[InitialVariables]` |
| `@@if <expr>` | 在条目注入前求值；`false` 则跳过该条目 |
| `@@private` | 条目内容包一层 IIFE 作用域，避免重复声明 |
| `@@always_enabled` | 特殊条目强制参与（与 `@@dont_activate` 冲突时后者优先） |
| `@@preload` / `@@only_preload` / `@@dont_preload` | P1 只做解析与标记，预加载阶段在 P1 末尾按简化规则执行：`[InitialVariables]` 必执行，其余 `@@preload` 在会话首次生成前执行一次 |
| `@@preprocessing` | 在条目**触发判断之前**执行模板，用结果参与关键词匹配；P1 支持，执行失败则按普通条目处理 |
| `@@message_formatting` / `@@iframe` | P2/不做（输出侧） |

装饰器解析规则：

- 必须是条目内容**首行开始**的连续行，每行一个，多个装饰器之间不能有空行。
- 名称与参数以第一个空格分隔。
- 未识别的 `@@xxx` 行丢弃（与上游一致），但要记 debug 日志。
- `@@@` 前缀转义，保留为普通文本。

### 9.4 `[InitialVariables]`

- 条目内容先按 JSON 解析，失败再按 YAML 解析（项目已有 YAML 依赖则复用；否则只支持
  JSON，YAML 明确报"不支持"）。
- 必须是对象；否则报错并跳过。
- 写入目标：`initial` 作用域，并在会话**首次**注入 `local`（不覆盖已有同名变量）。
- 每次会话创建/导入时执行一次；条目修改后按"修改时间更新则重新合并缺失键"处理，
  已在会话中存在且被用户改过的键不覆盖。
- 不写入用户级 `global`，避免一张卡污染所有会话。

### 9.5 与工具调用、长跑、多角色的交互

- 工具轮：每轮都会重新 `build_request`，模板会**每轮执行一次**。变量写入可能重复
  （例如 `incvar`）。P1 提供设置项"模板每轮执行 / 每回合只执行一次"：
  - 每轮：与 ST 语义接近，但副作用重复；
  - 每回合：缓存首轮渲染结果，工具轮复用（推荐默认，副作用安全）。
- 长跑压缩：压缩会改写 `history`，下一轮重新渲染模板。依赖消息计数的模板可能变化，
  属于预期行为。
- 群聊：`variables` 按会话共享，不按角色隔离；需要区分时用变量名前缀（如
  `alice_affection`）。`getchar()` 默认取当前发言角色，可传名字指定。

### 9.6 主动轮与快照

- 主动轮若走 `build_request`，自动执行模板；若仍走独立提示构造，则 P3 统一。
- 记忆快照不执行（§4.4）。
- 现有 `_snapshot_transcript` 生成的历史文本里可能含 `<% %>` 原文；快照模型会看到
  代码。P1 在快照拼装时用与输出侧相同的剥离规则剔除模板代码（纯字符串处理，不执行）。

---

## 10. 输出侧渲染（P2）

> 本节描述方案 A 的服务端落库执行。方案 B 的执行时机相同（消息最终确定、保存前），
> 但由客户端沙箱执行一次并回传，见 §19.3；§10.1 反对的是“在流式展示层执行”，
> 方案 B 同样遵守“只执行一次、与落库原子提交”。

### 10.1 为什么不能在展示层执行

- 流式渲染会多次重渲染同一段 Markdown（`useThrottleMarkdownRender` 节流），在此执行
  `setvar` 会重复累加。
- 前端主窗口执行卡片/消息代码违反现有安全策略。
- 变量必须与消息落库原子提交，否则刷新/断线重连后状态不一致。

### 10.2 落库时执行模型

在助手消息**最终确定并保存**的位置（生成结束、用户编辑保存、重生成完成）执行：

```
raw = message.content
if 含 "<%" and 输出侧开关开启:
    rendered = render_output_text(raw, tctx, message=message)
    message.raw_content = raw
    message.content = rendered
    message.template_rendered = true
```

- 流式期间**不执行**；流式界面显示原文（或按用户设置隐藏代码），结束后一次性执行。
- 编辑：用户编辑的是 `raw_content`（若存在），保存时重新执行；执行副作用按"编辑一次
  执行一次"计。
- 重生成：旧消息不重新执行；新消息正常执行。
- 输出侧执行失败：保留原文，`template_rendered=false`，并在消息旁显示可展开错误
  （管理员/开启调试时含行号）。

### 10.3 展示与提示词的分离

| 场景 | 使用字段 | 是否执行模板 |
|---|---|---|
| 前端展示 | `content`（已渲染） | 否 |
| 提示词历史 | `raw_content` 或 `content`，二选一 | 否 |
| 用户编辑 | `raw_content`（存在时） | 保存时执行 |
| 正则重算/导出 | `raw_content` 保留原文 | 否 |

提示词侧组装时，内置一条模板隐藏规则（等价上游的 `promptOnly` 正则）：

- 若消息有 `raw_content` 且 `template_rendered`，历史中使用 `raw_content` 并剔除
  `<% ... %>`；
- 若没有 `raw_content`，直接使用 `content`（大概率不含模板）。

该规则**不可关闭**，避免模板代码进入上下文造成模型模仿。

### 10.4 `[RENDER:BEFORE]` / `[RENDER:AFTER]`

- 在输出侧执行阶段：先执行消息自身模板，再执行命中条目的模板，按
  `BEFORE` → 消息 → `AFTER` 拼接，最后整体执行一次？**不整体二次执行**（避免重复
  副作用）。顺序：
  1. 渲染条目模板（可读消息变量，写入变量）；
  2. 渲染消息模板；
  3. 拼接并保存。
- `[RENDER:*]` 条目内容不进入提示词（与上游一致）。
- `@@message_formatting`：输出作为 HTML 片段；仍经过现有 sanitizer，不允许脚本/事件
  属性/外链资源。
- `@@iframe`：不支持；文档说明用卡片沙箱代替。

### 10.5 消息级变量

- 输出侧默认作用域为 `message`；`setvar` 未指定 scope 时写当前消息变量。
- 提示词侧读取历史消息变量：`getMessageVar(id, name)`；也支持在消息上下文中直接
  用 `variables` 读当前消息的合并视图（message 覆盖 local）。
- 消息删除时同步清理 `Session.template_vars.message[id]`。

### 10.6 安全闸门

输出侧执行模型生成的代码，是整个方案风险最高的部分。硬性要求：

1. 管理员功能开关为 `off` 时，代码路径根本不存在（服务端不注册该钩子）。
2. 用户首次开启输出侧时弹窗确认，文案说明"回复内容中的代码将在服务器沙箱内执行，
   只能读写本会话变量"。
3. 执行限额低于提示词侧（单单元格 150 ms、内存 8 MB、输出 128 KB）。
4. 输出侧模板**禁止** `injectPrompt`、`activewi`、`evalTemplate`，只允许变量读写与
   只读查询。
5. 所有错误与写入行为进审计日志（用户、会话、消息 id、写入键名与旧/新值摘要，值内容
   不入日志）。
6. 输出侧仅对当前用户的请求生效；群聊中其他成员的消息不执行该用户的模板钩子。

---

## 11. `@INJECT`（P3）

### 11.1 语法

世界书条目内容以 `@INJECT` 开头，参数以逗号分隔：

```
@INJECT pos=1,role=system
@INJECT target=user,index=-1,at=after,role=system
@INJECT regex="^User.*",at=before,role=system
```

- `pos` 从 1 开始，`0` 视为第一条，负数从末尾数。
- `target` 按角色定位，`index` 支持负数。
- `regex` 为不区分大小写的 JS 正则；参数可带单/双引号或不带。
- 支持世界书的 `probability`；排序规则：位置优先级 → 条目 `order` → 类型
  `pos > target > regex`。
- 条目内容其余部分为插入消息内容，执行模板后插入。

### 11.2 实现方式

在 `build_request` 渲染完成、`[GENERATE]` 处理完毕之后：

1. 收集所有命中的 `@INJECT` 条目（触发规则与世界书一致）。
2. 为每条计算插入点（以当前 `msgs` 为基准）。
3. 按"从后往前"插入，避免下标漂移。
4. 每条内容单独执行模板，可访问 `matched_message` 等上下文。
5. 校验：单轮 ≤ 16 条、总长 ≤ 64 KB、角色仅 `system/user/assistant`、不得插到
   prefill 之后。

### 11.3 与 `[GENERATE]` 的优先级

执行顺序：

```
普通模板渲染
  → [GENERATE:BEFORE/AFTER/{idx}/REGEX]
  → @INJECT
  → prefill
```

`@INJECT` 的插入点以 `[GENERATE]` 处理后的数组为准；文档中明确"混合使用时结果依赖
执行顺序"，并建议卡片作者二选一。

### 11.4 P3 仍不做

- 不执行 `@INJECT` 中的任意 HTML 渲染指令。
- 不支持扩展私有的隐藏参数（如非文档化字段）。
- 不支持跨世界书递归注入（可能形成环）。

---

## 12. 权限、开关与治理

### 12.1 分层开关

| 层 | 开关 | 默认 | 说明 |
|---|---|---|---|
| 管理员 | `features.prompt_template`：`off` / `on` | `off` | 关闭时后端不加载引擎、不解析标记，零开销 |
| 管理员 | 最低权限等级 `prompt_template_min_level` | 0 | 用户等级低于该值完全看不到入口 |
| 管理员 | 输出侧允许：`allow_output_side` | `false` | 单独授权；即使功能总开关为 on |
| 用户 | `template_enabled` | `false` | 个人是否启用提示词侧 |
| 用户 | `template_output_enabled` | `false` | 个人是否启用输出侧（需管理员允许） |
| 用户 | 错误显示：`silent` / `inline` / `debug` | `silent` | debug 仅管理员 |
| 用户 | 执行频率：每轮 / 每回合 | 每回合 | 见 §9.5 |

开关读取方式沿用现有个人设置模式（`UserSettings` 布尔字段 + `OVERRIDE_FIELDS`），
管理员功能开关沿用 `FeaturesPanel` 现有结构。

### 12.2 按卡信任提示

- 导入含 `<% %>` 的卡片/预设/世界书时，界面标注"此内容包含可执行模板"；不自动
  开启执行。
- 首次在某个会话里遇到模板时，若用户已开启功能则不重复确认；若未开启则照常当普通
  文本显示。
- 输出侧开启后，每条执行过模板的消息在详情里可看到"已执行模板"标记。

### 12.3 审计与可观测性

- 日志字段：`event=template_prompt|template_output`、`user_hash`、`session_id`、
  `source`、`duration_ms`、`vars_written`（键名列表）、`error_type`。
- 不记录变量值、消息正文、卡片正文。
- 管理员面板增加统计：近 24h 执行次数、失败率、平均耗时、超时次数。

### 12.4 资源与滥用

- 每用户每分钟模板执行请求数上限（默认 60），超限直接跳过模板。
- 单请求总限额见 §5.2；连续超时的用户可被管理员临时禁用该功能。
- 变量文件大小上限 1 MB/用户；写入失败返回可读错误，不中断生成。

---

## 13. 安全威胁模型

| 威胁 | 场景 | 缓解 |
|---|---|---|
| 任意代码执行逃逸 | 模板尝试访问宿主对象/执行系统命令 | quickjs 无网络/文件 API；只注入白名单函数；不传 Python 对象；每次请求新运行时 |
| 死循环/CPU 耗尽 | `while(true){}`、递归展开 | 单单元格时限 + 请求累计时限 + 单元格数量上限 |
| 内存炸弹 | 超大数组/字符串 | `set_memory_limit` + 输出上限 |
| 数据外带 | 模板把变量编码进 URL/图片外链 | 运行时无网络；输出文本作为提示词，不渲染为可请求资源 |
| 提示词注入升级 | 模型输出里写 `<% setvar(...) %>` 改写状态 | 输出侧默认关闭；开启后限额、禁止注入类 API、写入审计 |
| 越权读 | `getwi`/`getchar` 读其他用户私有数据 | 只读视图按当前用户与会话过滤；不暴露文件路径与 id 枚举 |
| 变量污染 | 写入 `__proto__`/`constructor` | 路径白名单校验；宿主桥只拷贝 JSON 值 |
| 副作用重复 | 流式重渲染/工具轮重复执行 | 输出侧只在落库时执行一次；提示词侧默认每回合一次 |
| 配置泄露 | 错误信息带出堆栈/路径 | 错误分级；默认 silent，不把堆栈写入提示词 |
| AGPL 污染 | 复制上游代码 | 净室实现；只参考文档语义；不打包上游 dist |

---

## 14. 兼容性矩阵

> 本节是方案 A（服务端 quickjs）的矩阵。方案 B 的路径覆盖、能力矩阵与两方案对比
> 见 §19.10。

| ST-Prompt-Template 能力 | P1 | P2 | P3 | 说明 |
|---|---|---|---|---|
| `<% %>` / `<%= %>` / `<%- %>` | ✅ | ✅ | ✅ | 提示词侧先落地 |
| `<#escape-ejs>` | ✅ | ✅ | ✅ | |
| 变量 global/local/message/cache/initial | ✅（message 仅会话内） | ✅ | ✅ | |
| `variables.x` 读取 | ✅ | ✅ | ✅ | 当前作用域视图 |
| `getvar/setvar/incvar/decvar` | ✅ | ✅ | ✅ | |
| `getchar/getuser/getgroup` | ✅ | ✅ | ✅ | 只读摘要 |
| `getwi` | ✅ | ✅ | ✅ | |
| `getpreset` | ✅ | ✅ | ✅ | 不含 params 敏感项 |
| `getChatMessage(s)` | ✅ | ✅ | ✅ | 最近 N 条 |
| `substituteParams` | ✅ | ✅ | ✅ | 白名单宏 |
| `evalTemplate` | ✅ | ✅ | ✅ | 递归深度 ≤ 3 |
| `activewi` | ✅ | ✅ | ✅ | 单轮 ≤ 8 |
| `activateRegex` | ✅ | ✅ | ✅ | 仅已绑定正则 |
| `injectPrompt` | ✅（简化） | ✅ | ✅ | 输出侧禁止 |
| `[GENERATE:BEFORE/AFTER]` | ✅ | ✅ | ✅ | |
| `[GENERATE:{idx}:*]` | ✅ | ✅ | ✅ | |
| `[GENERATE:REGEX:*]` | ✅ | ✅ | ✅ | 在渲染后文本上匹配 |
| `[InitialVariables]`（JSON） | ✅ | ✅ | ✅ | YAML 视依赖情况 |
| `@@if` / `@@private` / `@@activate` / `@@dont_activate` | ✅ | ✅ | ✅ | |
| `@@preprocessing` | ✅ | ✅ | ✅ | |
| `@@preload` 系列 | 简化 | ✅ | ✅ | 预加载阶段做最小实现 |
| 输出侧消息模板 | — | ✅ | ✅ | 落库时执行一次 |
| `[RENDER:BEFORE/AFTER]` | 识别 | ✅ | ✅ | |
| `@@message_formatting` | — | ✅（过 sanitizer） | ✅ | |
| `@INJECT` | — | — | ✅ | |
| `@@iframe` | ❌ | ❌ | ❌ | 用卡片沙箱替代 |
| lodash / faker | ❌ | ❌ | 评估 | 体积与许可另行评估 |
| EJS include / 异步 | ❌ | ❌ | ❌ | |
| 代码块内不执行 | ✅ | ✅ | ✅ | 与上游一致 |

---

## 15. 分阶段实施计划

> 本节为方案 A 的实施计划。实际交付按 §19.9 的 P0–P3 执行；其中 P1/P2 的顺序
> （先输出侧、后提示词侧）与本节相反，且不需要 quickjs 依赖。

### P1：提示词侧（建议先做）

**范围**

1. `flyai/template/` 骨架：`engine/syntax/scopes/api/render`。
2. 依赖：`quickjs` 可选依赖 + 平台探测 + 设置页不可用提示。
3. `TemplateContext` 接入 `routes_chat` → `run_agent_loop` → `build_request`。
4. 变量：`local`/`cache`/`initial`（会话字段）与 `global`（用户文件）。
5. `LoreEntry.memo/st_uid`、导入映射、标题标记解析。
6. `[GENERATE:BEFORE/AFTER/{idx}/REGEX]` 注入。
7. 装饰器：`@@if`、`@@private`、`@@activate`、`@@dont_activate`、
   `@@generate_*`、`@@preprocessing`、`@@initial_variables`。
8. `[InitialVariables]` JSON 解析与应用。
9. 管理员/用户开关、最低权限等级、错误档位。
10. 测试与文档。

**验收标准**

- 一张只含 `<% getvar/setvar/if %>` 的测试卡能按预期改变提示词；
- `[GENERATE:BEFORE/AFTER]` 注入位置与顺序符合 §9.2；
- 变量在后续轮次可见，会话重开后仍在；
- 死循环模板在 250 ms 内被终止，生成继续进行；
- 关闭开关后，请求与输出与现状逐字节一致（回归测试）；
- Windows x64 与 Linux x86_64 测试通过；无 quickjs 的环境明确降级。

**预计测试量**：45–60 条（引擎/语法/作用域/注入/开关/错误/回归）。

### P2：输出侧

**范围**

1. 消息落库钩子（生成结束/编辑保存/重生成）。
2. `raw_content` / `template_vars` / `template_rendered` 字段与会话序列化。
3. 输出侧变量写入与审计；禁止注入类 API。
4. `[RENDER:*]` 注入与 `@@message_formatting`（经 sanitizer）。
5. 提示词侧模板隐藏规则（内置，不可关）。
6. 用户确认弹窗、消息标记、设置项。

**验收标准**

- 回复中的模板只执行一次；刷新、断线重连、重新打开会话不重复执行；
- 变量与消息同事务保存；
- 关闭时消息内容与现状一致；
- 输出侧注入类 API 调用被拒绝且有审计；
- 模板错误不破坏消息展示，原文可恢复。

**预计测试量**：35–45 条。

### P3：深度兼容

**范围**

1. `@INJECT` 三种模式与排序。
2. `@@preload`/`@@only_preload`/`@@dont_preload` 完整语义与预加载阶段。
3. `activewi`/`activateRegex`/`injectPrompt`/`evalTemplate` 的完整校验与递归限制。
4. 主动轮统一走 `build_request`（或抽出共享 builder）。
5. 调试面板：按请求查看执行来源、耗时、写入键名。
6. `[GENERATE:REGEX]` 在预处理文本上的细节对齐。

**验收标准**

- 公开示例卡的模板用法可运行（不含依赖 lodash/faker 的样本）；
- `@INJECT` 插入点与上游文档示例一致；
- 主动轮与普通轮提示词结构一致；
- 全部模板路径可关闭且零副作用。

**预计测试量**：30–40 条。

---

## 16. 风险与备选方案

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| quickjs wheel 缺失/平台不支持 | 中 | 功能不可用 | 可选依赖 + 启动探测 + 明确提示；备选降级 DSL |
| quickjs 项目已归档 | 中 | 长期维护 | 封装隔离；后续可换其他嵌入式引擎 |
| 执行模型输出代码的安全争议 | 高 | 用户信任 | 默认关闭、单独授权、严格限额、审计 |
| 模板写变量导致提示词不可复现 | 中 | 调试困难 | 来源标记、每回合一次、调试面板 |
| 与 ST 卡细节不兼容（宏顺序、装饰器边界） | 高 | 兼容性口碑 | 明确兼容矩阵；按需补，不追求 100% |
| 变量膨胀/文件损坏 | 低 | 数据问题 | 大小上限、原子写、备份旧文件 |
| 性能开销 | 中 | 首字延迟 | 只处理含 `<%` 的文本；编译缓存；限额 |
| AGPL 合规争议 | 低 | 法律 | 净室实现，保留设计记录，不复制源码 |

**备选 ①（最小可用）**：只做 `<% if %>` / `<%= variables.x %>` / `getvar/setvar`
的受限 DSL，不引入 JS 引擎。优点是零新依赖、安全面小；缺点是无法运行现有卡。可作为
quickjs 不可用时的降级模式，而不是主线。

**备选 ②（不做输出侧）**：只做提示词侧。风险大幅下降，但状态栏/好感度类卡只能
在下一轮提示词里体现，无法即时更新变量。若用户对"执行模型输出代码"有顾虑，这是推荐
的止步点。

**备选 ③（独立子进程）**：Node 子进程执行。隔离更彻底，但部署、IPC、超时杀进程
复杂度显著上升，P1 不采用。

---

## 17. 未决问题

> 以下为方案 A 的未决问题。方案 B 的问题见 §19.11；其中 ARM64、quickjs 维护、
> 服务端执行模型代码等条目在方案 B 中不再存在。

1. **输出侧要不要做？** 这是唯一会执行模型生成代码的部分，风险与价值都最高。
   建议先 P1，观察使用反馈后再决定 P2。
2. **YAML 初始变量**是否必须？项目若已有 YAML 依赖可顺手支持，否则 P1 只支持 JSON。
3. **全局变量范围**：按用户跨所有会话，还是按用户 + 世界书隔离？前者符合上游，
   后者更防串味。建议 P1 按用户全局，文档标注可后续加命名空间。
4. **ARM64 服务器**是否需要支持？若部署目标是 ARM 小主机，需要提前评估源码编译
   quickjs 的成本。
5. **模板执行频率默认值**：每回合一次（副作用安全）还是每轮一次（贴近上游）？
   建议默认每回合，设置里可改。
6. **是否把 `{{getvar}}` 宏映射到新变量系统**？P1 不做，避免改变现有行为；P2 再评估。
7. **卡片导入 UI**是否需要在导入时就提示"包含可执行模板"？建议需要，但涉及前端
   导入流程改动。

---

## 18. 附录

### 附录 A：标记语法速查

```
[GENERATE:BEFORE]                 内容注入提示词开头（常驻）
[GENERATE:AFTER]                  内容注入提示词末尾（可触发）
[GENERATE:{idx}:BEFORE/AFTER]     注入到第 idx 条消息前/后（idx 从 0 起）
[GENERATE:REGEX:pattern]          消息匹配 pattern 时注入到该消息前
[RENDER:BEFORE/AFTER]             输出侧注入（P2，不进提示词）
[InitialVariables]                条目内容作为初始变量（JSON/YAML 对象）

@@activate / @@dont_activate
@@generate_before / @@generate_after
@@render_before / @@render_after
@@initial_variables / @@always_enabled
@@if <expr> / @@private
@@preload / @@only_preload / @@dont_preload
@@preprocessing
@@message_formatting
@@iframe                           不支持
@@@xxx                            转义，保留为普通文本

@INJECT pos=N,role=...
@INJECT target=role,index=N,at=before|after,role=...
@INJECT regex=pattern,at=before|after,role=...
```

### 附录 B：项目内落点清单

> 本表为方案 A 的服务端落点。方案 B 的落点见 §19.12：执行器在 `webapp/`，
> 服务端新增模板会话/校验/落库接口，不新增 JS 引擎依赖。

| 位置 | 改动 |
|---|---|
| `flyai/template/`（新增） | 引擎、语法、作用域、API、指令、渲染、存储 |
| `flyai/agent/loop.py:418-481` | 在最终组装处插入模板渲染与 `[GENERATE]` 处理 |
| `flyai/api/routes_chat.py` | 创建 `TemplateContext`；助手消息落库钩子（P2） |
| `flyai/lore/models.py` | `LoreEntry` 增加 `memo`、`st_uid` |
| `flyai/lore/query.py` | 标题标记分流；`initial_variables` 读取 |
| `flyai/preset/models.py` | 可选：块级 `identifier` 已存在，模板来源标记沿用 |
| `flyai/sessions/models.py` | `Session.template_vars`；`Message.raw_content/template_vars/template_rendered` |
| `flyai/sessions/json_store.py` | 显式序列化两处同步新增字段 |
| `flyai/user.py` | `template_enabled`、`template_output_enabled`、错误档位、频率 |
| `flyai/api/routes_config.py` | 个人设置读写新增字段 |
| `flyai/proactor/runner.py` | P3 统一组装；P1 文档标注不生效 |
| `flyai/web/static`（构建产物） | 前端设置项与标记（源在 `webapp/`） |
| `webapp/src/components/settings/...` | 用户设置与管理员功能面板 |
| `webapp/src/components/chat/...` | "已执行模板"标记、错误展开、确认弹窗 |
| `requirements.txt` | `quickjs` 带平台环境标记，可选 |

### 附录 C：测试清单

**引擎与语法**

- 文本/语句/输出/注释/空白裁剪/转义区/转义 `%%>`；
- 未闭合标签、空标签、字符串中的 `%>`；
- 输出类型转换（数字、布尔、对象、null、undefined）；
- 超时、内存、输出上限、单元格数量上限；
- 错误三档输出与日志脱敏。

**作用域**

- 五类作用域读写与覆盖顺序；
- 嵌套路径、非法路径（`__proto__`）、超深路径；
- 请求失败丢弃写入；原子落盘；并发写锁；
- global 跨会话可见、local 不跨会话、message 随消息删除清理；
- `[InitialVariables]` 合并不覆盖已修改键。

**链路**

- 模板在正则/MVU 剥离之后、prefill 之前执行；
- 工具轮每轮 vs 每回合；
- `[GENERATE]` 四种标记的插入位置、顺序、去重、越界；
- `@@if` 假值排除、`@@private` 重复声明、`@@@@` 转义；
- `[InitialVariables]` JSON 非法、非对象；
- 快照路径剔除模板代码且不执行。

**输出侧（P2）**

- 只执行一次；刷新/重连不重复；编辑重执行；重生成只影响新消息；
- `raw_content` 与提示词隐藏规则；
- 注入类 API 被拒绝；审计字段正确；
- 消息删除清理变量。

**安全**

- 无 `fetch`/`require`/`process`/`window`；
- 读不到密钥、配置、其他用户数据、文件路径；
- 提示词错误不泄露堆栈；
- 关闭开关的逐字节回归。

### 附录 D：与现有特性的边界

| 现有特性 | 与模板的关系 |
|---|---|
| 酒馆正则（prompt/display） | prompt 侧先于模板；display 侧仍在渲染层，不执行模板 |
| MVU `stat_data` | 独立状态机；模板变量不自动同步到 MVU。需要同步时由模板显式写 MVU 兼容变量（P2 评估） |
| 卡片 JS 沙箱 | 完全隔离；模板不读写卡片 UI 状态 |
| WATCHER / 主动轮 / 记忆快照 | 方案 B 明确不执行模板、不改代码；挂载含模板的卡/预设时不应启用主动轮 |
| 导出/导入 v2 | 新字段随会话导出；不含用户级 global 变量 |
| 权限等级 | 功能入口按最低等级隐藏；执行时再次校验（防直连 API） |

---

## 19. 方案 B：客户端执行器（确认采用）

> 确认时间：2026-11-21。
> 与方案 A 的根本区别：**执行在客户端沙箱，生效在服务端**。服务端不引入任何 JS 运行时，
> `requirements.txt` 不新增依赖；客户端也不获得任何直接写数据库/变量的能力，只能回传
> 受校验的“渲染结果 + 操作日志（ops）”。

### 19.1 范围与明确排除

| 路径 | 是否执行模板 | 执行方 | 说明 |
|---|---|---|---|
| 交互式单聊请求 | ✅ | 发起请求的客户端 | 用户发消息、群聊里的用户请求 |
| 重生成 | ✅ | 发起请求的客户端 | 新消息执行一次，旧消息不重跑 |
| 编辑保存 | ✅ | 编辑的客户端 | 编辑 `raw_content`，保存时执行一次 |
| 工具循环内的多轮模型调用 | ✅（每回合一次） | 首次执行后复用结果 | 后续工具轮不重复执行，避免变量累加 |
| 主动轮 / 定时作业 / 提醒 / watcher | ❌ | — | **不做代码处理**。挂载含模板的卡或预设时不应启用主动轮，作为使用约束写进设置说明 |
| 记忆快照 `POST /rooms/{rid}/memory-snapshot` | ❌ | — | **保持现状**，继续使用 `_snapshot_transcript` 的原始文本，不接 `build_request`、不新增渲染 |
| 记忆提炼 / 主动轮读记忆簿 | ❌ | — | 同记忆快照，默认行为即可 |
| 会话导出 / 导入 / 备份 | ❌ | — | 保留 `raw_content` 与变量快照，不执行 |
| 断线重连 / 刷新 / 切换会话 | ❌ | — | 展示已保存的渲染结果；不重复执行 |

结论：

- 方案 B 只服务“用户在场”的交互式请求；没有客户端时一律不执行模板，不做补发、不排队。
- 主动轮与记忆快照两条路径**不改代码、不加开关、不加守卫**。模板代码若出现在这类
  提示词里，按现有规则当普通文本处理；文档和设置页只做使用提示。
- 因此方案 B 不需要服务端 quickjs，也不存在 ARM64 wheel、服务端执行模型生成代码、
  后台任务无执行器等问题。

### 19.2 核心模型：Cell + Ops

不要在客户端重新实现提示词组装。服务端仍负责世界书选择、预设、正则、截断、PHI、
LIVE_CTX 与最终顺序；客户端只对“含模板的文本单元格”求值。

```
服务端组装骨架
  → 提取含 "<%" 的单元格（cell_id + 文本 + 来源 + 只读上下文 + 变量快照 + 版本号）
  → prepare 下发给客户端
  → 客户端在 null-origin 沙箱中执行：
       文本渲染结果写入 cells
       setvar/injectPrompt/activewi 等只记 ops（不直接改任何数据）
  → commit 回传 cells + ops + errors + 版本号 + 幂等 id
  → 服务端校验 ops → 应用变量/注入 → 拼回骨架 → 调用模型
```

设计要点：

1. **服务端拥有组装权。** 客户端不能提交整段提示词，只能提交“服务端发来的 cell 的渲染
   结果”；未知 cell_id 丢弃。
2. **客户端拥有纯计算权。** 所有宿主 API 都是桩：读变量读本地镜像，写变量写镜像并记
   op；`injectPrompt/activewi` 只记录，不产生实际注入。
3. **服务端拥有生效权。** ops 逐项校验作用域、路径、条数、长度、权限与版本；通过后按
   顺序落到会话/用户存储。
4. **协议与引擎解耦。** 后续若需要服务端 fallback，quickjs 只需实现同一套 API 与 ops，
   客户端与服务端可共享上层组装与校验逻辑。

### 19.3 输出侧（P1，先做）

执行时机：生成结束、消息最终确定并保存前；编辑保存时；重生成完成时。

流程：

1. 客户端拿到待保存的助手消息 `raw_content`；
2. 在沙箱中执行一次模板（输出侧禁止 `injectPrompt/activewi/evalTemplate`）；
3. 回传 `{ message_id, raw_hash, content, ops, errors }`；
4. 服务端校验消息 hash 与 ops，与消息在同一次写盘内保存：
   - `raw_content`：原文；
   - `content`：渲染后内容（展示用）；
   - `template_rendered = true`；
   - `template_vars`：该消息的 message 作用域变量；
   - 会话级 `local/global` 变量按 ops 更新。
5. 流式期间不执行，界面继续显示原文或按设置隐藏代码块。

无客户端时的处理：

- 生成在后台完成、客户端在保存前关闭：服务端先保存 `raw_content`，`template_rendered=false`；
- 下次打开会话时，客户端执行并 commit，服务端补齐渲染结果与变量；
- 补执行之前，提示词历史使用 `raw_content` 并剥离 `<% %>`（纯字符串处理），不把代码发给模型；
- 代价：这段时间内该消息产生的变量尚未生效。这是方案 B 接受的取舍，需在文档与 UI 中说明。

幂等与一致性：

- 幂等键：`message_id + raw_hash + render_id`；重复 commit 只接受第一次；
- 编辑：用户编辑的是 `raw_content`；保存一次执行一次；
- 重生成：旧消息不重跑，新消息正常执行；
- 消息删除：同步清理 `template_vars.message[id]`。

### 19.4 提示词侧（P2）

执行时机：服务端完成现有插入（system_prompt、预设块、世界书、PHI、LIVE_CTX、截断）
之后、追加 assistant prefill 之前。

两阶段握手：

1. **prepare**
   - 扫描最终消息数组，收集所有含 `<%` 且不属于 prefill 的单元格；
   - 无模板：直接走现有生成路径，零额外开销；
   - 有模板：创建 `prepare_id`（有效期建议 10 秒），返回单元格、只读上下文、
     变量快照、`context_version` 与限额。
2. **客户端执行**
   - 按消息顺序逐单元格执行；`cache` 作用域在本次请求内共享；
   - `local` 写入立即在后续单元格可见（写本地镜像）；
   - 输出长度、单元格数量、总耗时受客户端与服务端双重限额。
3. **commit**
   - 回传 `prepare_id + context_version + cells + ops + errors + nonce`；
   - 服务端校验后拼回骨架、应用 ops，再调用模型；
   - commit 超时/失败：中止本次生成并提示重试；绝不回退成“把 `<% %>` 原文发给模型”。
4. **工具循环**
   - 同一用户回合的多次模型调用只执行一次模板；后续轮复用已渲染单元格；
   - 工具结果消息默认不含模板；若含，按新单元格单独执行一次。

并发与版本：

- prepare 到 commit 期间对会话变量做版本检查（CAS）；版本变化则拒绝 commit 并要求重试；
- 同一会话的生成请求由现有会话锁串行化；模板阶段不长时间占用生成锁；
- 多标签页/多设备：只有发起请求的客户端参与；其他端在结果落盘后同步。

### 19.5 回传协议（概念）

统一使用 JSON；字段名以实现为准，语义如下：

| 字段 | 方向 | 说明 |
|---|---|---|
| `prepare_id` | 服务端 → 客户端 | 一次渲染会话的标识，短期有效 |
| `context_version` | 双向 | 变量/会话版本，用于 CAS |
| `cells` | 服务端 → 客户端 | `cell_id`、原文、来源（预设块/世界书/角色字段/消息）、角色、位置 |
| `context` | 服务端 → 客户端 | 只读数据：char/user/group 摘要、世界书/预设查询结果、最近 N 条消息、变量快照 |
| `limits` | 服务端 → 客户端 | 单元格数、总输出、单单元格输出、总耗时 |
| `cells_result` | 客户端 → 服务端 | `cell_id → 渲染文本`（服务端按 id 拼回，不接受整段提示词） |
| `ops` | 客户端 → 服务端 | 见下表 |
| `errors` | 客户端 → 服务端 | 错误类型与 cell_id；不含堆栈 |
| `nonce / render_id` | 客户端 → 服务端 | 幂等与防重放 |
| `stats` | 客户端 → 服务端 | 单元格数、耗时、沙箱版本，用于审计 |

ops 类型：

| op | 参数 | 限制 |
|---|---|---|
| `vars.set` | scope、path、value | JSON 值；单值 ≤ 64KB；路径白名单 |
| `vars.unset` | scope、path | — |
| `vars.inc` / `vars.dec` | scope、path、delta | 结果必须是有限数 |
| `inject` | text、role、position、depth | 提示词侧：单轮 ≤ 8 条、总长 ≤ 32KB；输出侧禁止 |
| `activate.world` | 条目标识、position | 仅当前会话可用世界书；单轮 ≤ 8 条 |
| `activate.regex` | 正则标识 | 仅会话已绑定正则 |
| `message.render` | 输出侧：渲染后内容 | 必须携带 `raw_hash`；输出侧专用 |

服务端只接受白名单 op；未知、越权、超限、版本不符的 op 一律丢弃并记审计，不中断生成
（输出侧渲染失败可保留原文）。

### 19.6 变量、并发与持久化

- 作用域与方案 A 一致：global / local / message / cache / initial；
- 服务端仍是唯一持久化方；客户端只拿快照、只回传变更；
- 请求内变量写入先进入 commit 缓冲区，校验通过后落盘；请求失败/中断默认丢弃；
- 输出侧：消息与变量在同一 JSON 写盘内提交，保证“消息存在 ⇒ 变量已保存”；
- 变量文件大小上限（global 1MB/用户）、单值上限、路径深度上限沿用方案 A 的数值；
- 消息新增字段：`raw_content`、`template_vars`、`template_rendered`，会话新增
  `template_vars.local/message`；`json_store` 显式字段表两处同步。

### 19.7 沙箱与安全

执行器：

- 新增独立“模板 runner” iframe，不复用 `CardAppFrame`；
- `sandbox="allow-scripts"`、null-origin（无 `allow-same-origin`）、无 DOM 需求；
- 父窗口遮蔽 `parent/top`，执行器拿不到 token、cookie、localStorage、IndexedDB；
- CSP：`connect-src 'none'`、`img-src data:`、`frame-src 'none'`、`form-action 'none'`，
  禁止 `fetch/XMLHttpRequest/WebSocket/sendBeacon/importScripts/import()`；
- 每次请求新建执行上下文，执行完销毁；不跨请求复用全局对象。

限额：

| 项 | 建议值 |
|---|---|
| 单单元格执行时限 | 100–250ms（超时终止执行器） |
| 单请求累计时限 | 2s |
| 单元格数量 | 500/请求 |
| 单单元格输出 | 256KB |
| 单请求输出总量 | 2MB |
| 输出侧 | 更严格：单单元格 150ms、输出 128KB、禁止注入类 op |

服务端校验：

- ops 白名单、变量路径、作用域、长度/条数/权限、`context_version`、幂等键；
- 不信任客户端返回的变量最终值以外的任何数据；不接受客户端提交完整提示词；
- 关闭开关时后端不注册相关接口/钩子，行为与现状逐字节一致。

审计与治理：

- 记录 `event=template_prompt|template_output`、用户哈希、会话 id、来源、耗时、
  写入键名列表、错误类型；不记录变量值与正文；
- 管理端统计执行次数、失败率、超时次数；用户级频率上限（默认 60 次/分钟）；
- 分层开关：管理员功能总开关（默认 off）、最低权限等级；用户提示词侧/输出侧开关
  （默认 off）；首次开启输出侧需确认弹窗；
- 导入含 `<% %>` 的卡/预设/世界书时提示“包含可执行模板”，不自动开启。

### 19.8 主流程时序

交互式请求：

```
用户发送
  → 服务端常规组装（世界书/预设/正则/截断/PHI）
  → 扫描模板单元格
      ├─ 无模板：直接调用模型（现有路径）
      └─ 有模板：prepare → 客户端沙箱执行 → commit 校验
  → 拼回骨架 → 调用模型（流式）
  → 生成结束
  → 输出侧：客户端执行消息模板 → commit 渲染结果 + ops
  → 服务端原子落盘（消息 + 变量）
```

主动轮 / 记忆快照：

```
调度器 / 快照请求
  → 现有路径，不扫描、不执行、不新增分支
  → 模板代码按普通文本处理（使用约束：此类场景不应挂载含模板的卡/预设）
```

### 19.9 分阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 协议与沙箱** | ops 协议、prepare/commit/render 端点、null-origin runner、EJS 子集、服务端校验、限额、审计、分层开关 | 沙箱逃逸测试全部失败；关闭开关时提示词逐字节不变；超时不阻塞生成 |
| **P1 输出侧** | 生成结束/编辑保存执行一次；`raw_content/template_vars/template_rendered`；原子落盘；无客户端补渲染；幂等 | 刷新/重连/重生成不重复执行；变量与消息同事务；原文可恢复 |
| **P2 提示词侧** | prepare/commit 握手；单元格渲染；只读上下文；变量 CAS；工具轮复用 | 含 `<%>` 的测试卡能改变提示词；commit 超时中止；两标签页并发只有一个生效 |
| **P3（按需）** | 服务端结构能力：`[GENERATE:*]`、`@@` 装饰器、`@INJECT`、`[InitialVariables]` 的解析与注入；纯文本处理，不需要 JS 引擎 | 公开示例卡的模板用法可运行；结构指令与方案 A 行为一致 |

P3 之前不评估 lodash/faker；如确有需要，单独评估体积与许可，不随 P0–P2 引入。

### 19.10 兼容性矩阵

**路径覆盖**

| 能力 / 路径 | ST-Prompt-Template | 方案 B P1 | 方案 B P2 | 方案 B P3 | 说明 |
|---|---|---|---|---|---|
| 提示词 `<% %>` | ✅ | — | ✅ | ✅ | 客户端执行，服务端拼回 |
| `<%= %>` / `<%- %>` | ✅ | — | ✅ | ✅ | 提示词侧等价 |
| `<#escape-ejs>` / 注释 | ✅ | — | ✅ | ✅ | 纯语法 |
| global/local/message/cache/initial | ✅ | 部分（message/global） | ✅ | ✅ | 客户端镜像 + 服务端落地 |
| `getvar/setvar/incvar/decvar` | ✅ | ✅ | ✅ | ✅ | ops 回传 |
| `getchar/getuser/getgroup/getwi/getpreset` | ✅ | — | ✅ | ✅ | 只读上下文随 prepare 下发 |
| `getChatMessage(s)` | ✅ | — | ✅ | ✅ | 最近 N 条，限量 |
| `substituteParams` | ✅ | — | ✅ | ✅ | 白名单宏 |
| `injectPrompt` / `activewi` / `activateRegex` | ✅ | ❌（输出侧禁止） | ✅ | ✅ | 校验后由服务端生效 |
| `evalTemplate` | ✅ | ❌ | ✅ | ✅ | 递归深度 ≤ 3 |
| 输出侧消息模板 | ✅ | ✅ | ✅ | ✅ | 落库前执行一次 |
| `[RENDER:BEFORE/AFTER]` | ✅ | — | ✅ | ✅ | 输出侧注入 |
| `[GENERATE:*]` | ✅ | — | — | ✅ | 服务端结构解析 |
| `@@` 装饰器 | ✅ | — | — | ✅ | 服务端结构解析 |
| `@INJECT` | ✅ | — | — | ✅ | 服务端结构解析 |
| `[InitialVariables]` | ✅ | — | — | ✅ | 服务端解析 JSON/YAML |
| `@@iframe` | — | ❌ | ❌ | ❌ | 用卡片沙箱替代 |
| lodash / faker | ✅ | ❌ | ❌ | 评估 | 不默认引入 |
| EJS include / 异步 / 定时器 | ✅ | ❌ | ❌ | ❌ | 不支持 |
| 主动轮 / 定时作业 / 提醒 / watcher | ✅（浏览器常驻） | ❌ | ❌ | ❌ | 明确排除，不改代码 |
| 记忆快照 | ✅（浏览器常驻） | ❌ | ❌ | ❌ | 保持现状，不执行 |
| 后台/离线生成 | ✅（浏览器常驻） | ❌ | ❌ | ❌ | 无客户端即不执行 |

**方案 B 与方案 A 的对比**

| 维度 | 方案 B（客户端） | 方案 A（服务端 quickjs） |
|---|---|---|
| 服务端 JS 依赖 | 无，`requirements.txt` 不变 | `quickjs` 可选依赖，ARM64 无 wheel |
| 执行位置 | 本机 null-origin 沙箱 | 服务端受限运行时 |
| 模型生成代码的执行风险 | 只在用户本机沙箱 | 集中在服务器，需单独授权 |
| 提示词侧延迟 | 多一次 prepare/commit 往返（仅含模板时） | 进程内执行 |
| 后台/主动轮/快照 | 不支持（已排除） | 可支持 |
| 多端一致性 | 发起端执行 + 版本 CAS | 服务端统一执行 |
| 实现重点 | 协议、沙箱、幂等、并发、校验 | 引擎、语法、作用域、链路 |

### 19.11 未决问题（方案 B）

1. 输出侧“无客户端补渲染”期间变量延迟生效，是否可接受？是否需要在 UI 明示？
2. 提示词侧 commit 超时的默认值（建议 10s）与用户可调范围。
3. P3 的结构能力是否进入首个发布，还是先只做 `<% %>` + 变量。
4. 是否需要把 lodash/faker 的一个最小子集打进执行器（体积、许可）。
5. 全局变量是否按用户全局，还是按“用户 + 世界书/卡”命名空间隔离。
6. 导入 UI 的模板提示文案与首次确认流程。
7. 沙箱 runner 在 Android WebView 上的 CSP/沙箱行为需要真机验证。

### 19.12 项目内落点清单（方案 B）

| 位置 | 改动 |
|---|---|
| `webapp/src/services/st/templateRunner.ts`（新增） | 沙箱 iframe 生命周期、消息协议、超时与限额 |
| `webapp/src/services/st/ejs.ts`（新增） | EJS 子集编译（文本/语句/输出/注释/转义） |
| `webapp/src/services/st/templateOps.ts`（新增） | 宿主 API 桩、变量镜像、ops 记录与序列化 |
| `webapp/src/services/chat/*` | 发送前 prepare/commit；生成结束的输出侧提交；补渲染 |
| `webapp/src/components/settings/*` | 用户开关、错误档位、首次确认、模板标记 |
| `flyai/api/routes_template.py`（新增） | prepare/commit/render 接口、ops 校验、限额、审计 |
| `flyai/template/validate.py`（新增） | 作用域/路径/长度/权限校验、幂等与版本检查 |
| `flyai/api/routes_chat.py` | 提示词侧挂接点；输出侧落盘钩子 |
| `flyai/sessions/models.py` / `json_store.py` | `raw_content`、`template_vars`、`template_rendered` 与会话变量字段 |
| `flyai/user.py` / `routes_config.py` | 分层开关、最低等级、错误档位、频率 |
| `flyai/proactor/runner.py` | **不改**（明确排除） |
| `flyai/api/routes_chat.py::_snapshot_transcript` / `memory_snapshot` | **不改**（保持现状） |
| `requirements.txt` | **不变**（不新增 quickjs） |

### 19.13 测试清单（方案 B）

**协议与校验**

- 未知 `prepare_id` / 过期 / 版本不符 / 重复 nonce 全部拒绝；
- 未知 `cell_id`、重复 cell、缺失 cell 的处理；
- ops 越权（scope、路径、条数、长度、输出侧注入类）全部丢弃并审计；
- 关闭开关时请求与现状逐字节一致。

**沙箱安全**

- 执行器内 `fetch/XHR/WebSocket/sendBeacon` 全部失败；
- 拿不到 parent/top、localStorage、cookie、token、IndexedDB；
- 死循环、内存炸弹、超大输出被终止且不阻塞生成；
- `postMessage` 伪造来源被忽略。

**输出侧**

- 只执行一次；刷新/重连/重生成不重复；
- 编辑 `raw_content` 后重新执行；
- 消息与变量同事务保存；删除消息清理 message 变量；
- 客户端在保存前关闭：保存原文并标记，下次打开补渲染；
- 补渲染前提示词使用原文且剥离模板代码。

**提示词侧**

- prepare 无模板时不产生额外请求；
- commit 超时中止生成，不回退发送模板源码；
- 工具循环只执行一次；
- 两标签页并发 commit 只有一个成功；
- 变量在后续消息中可见、会话重开后仍在。

**结构能力（P3）**

- `[GENERATE]` 位置/顺序/越界；`@@` 装饰器；`@INJECT` 插入点；
- `[InitialVariables]` 非法 JSON/非对象；
- 与现有酒馆宏、ST 正则、MVU 剥离顺序一致。

**回归**

- 主动轮、提醒、记忆快照路径不执行模板、代码无新增分支；
- 导出/导入不丢 `raw_content` 与变量；
- Android WebView 真机沙箱与 CSP 行为。
