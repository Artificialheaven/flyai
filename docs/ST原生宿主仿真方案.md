# ST 原生宿主仿真方案

调研时间：2026-11-23  
扫描对象：
- `F:\PythonProgram\_st_repo\SillyTavern-release`（SillyTavern **1.19.1**）
- `F:\PythonProgram\_th_repo\JS-Slash-Runner-main`（酒馆助手类型与宿主注入逻辑）

## 0. 结论

现在的 `window.DALPhoneRuntime` 覆盖是**卡特定补丁**：只对约会大作战有效，换一张卡、换一个运行时名字仍会失败。  
根因不是某张卡，而是沙箱缺少 ST 的**浏览器原生宿主面**：

1. 全局 `EjsTemplate`（含 `compileTemplate`）
2. 卡片运行时动态 `import()` 的 ST 前端模块
3. ST 前端模块依赖的全局对象与设置快照
4. `/api/backends/chat-completions/generate` 等 ST 服务端接口

通用方案是：**不重写卡的运行时，而是把 ST 宿主面仿真出来**。卡自带的运行时（以及未来任何卡自带的运行时）就能原样跑起来；`DALPhoneRuntime` 覆盖可以最终删除。

已实测：沙箱 srcdoc 的 `location.origin` 是字符串 `"null"`，`baseURI` 继承父页面；用 import map 精确映射 `"null/script.js"` 等键可以成功加载 `data:` 模块。不需要改 CSP，也不需要后端静态路由。

## 0.1 实现状态（2026-11-23）

| 项目 | 状态 | 说明 |
|---|---|---|
| P0 快照 + 10 个 ST 模块 + import map | ✅ | `webapp/src/services/st/native/stNative.ts` |
| `window.EjsTemplate` | ✅ | `native/ejsRuntime.ts`，复用 `ejs.ts` 编译器 |
| `/api/backends/chat-completions/generate|status` | ✅ | 走宿主生成桥，`stream:true` 合成 SSE |
| 宏解析器依赖（Chevrotain/moment/seedrandom/droll） | ✅ | 内置 11.2.0 / 2.30.1 / 3.0.5 / 0.2.1 |
| 作者面板 lodash 全局 `_` | ✅ | 修复原生世界书扫描 `t.compact` 缺失 |
| 原生快照补 `custom_model` / `custom_include_*` | ✅ | 修复 `provider/model unavailable` 与宏源错误 |
| 真实卡运行时 `prepareFromHost` | ✅ | 无头 Chrome + 卡内 4MB 终端脚本，7 条消息请求体生成成功 |
| P1：world-info 12 项设置 / 正则 API / tokenizer 近似 / tags | ✅ | 正则脚本来源仍为空数组，按卡需要再接 |
| P1：settings / extensions / presets / tokenizers HTTP | ✅ | `/api/settings/get`、`/api/extensions/discover` 等 |
| P2：events/templates/chats/characters/group-chats/macros/variables/preset-manager/utils/extensions-shared | ✅ | 只读子集，20 个模块路径 |
| P2：删除 `DALPhoneRuntime` 覆盖 | ⏸ | 保留“原生宿主不可用时”的 gated 回退；实际路径已走原生 |
| SSE 真流式 | ⏸ | 先返回完整文本再合成 SSE，卡内 `stream:false` 场景无影响 |

验证方式：`npm test`（268 个用例）+ 临时探针页 + 真实 Chrome CDP。探针不入库。

## 1. 扫描结果

### 1.1 卡片对 ST 模块的动态 import

扫描全部已导入角色卡：只有《约会大作战P-v1》的 tavern_helper 脚本 2 直接 import ST 模块，共 10 个路径：

| 路径 | 用途 |
|---|---|
| `/script.js` | 聊天状态、上下文、token 上限、角色名、`main_api` |
| `/scripts/world-info.js` | `loadWorldInfo`、世界书扫描设置、`DEFAULT_DEPTH` |
| `/scripts/power-user.js` | `power_user`（persona lorebook、宏引擎、instruct/context/sysprompt、停止串） |
| `/scripts/extensions.js` | `extension_settings`、`extensionPrompts`、`extensionNames`、正则脚本读取 |
| `/scripts/extensions/regex/engine.js` | `getScriptsByType`、`SCRIPT_TYPES` |
| `/scripts/tokenizers.js` | `getTokenCountAsync` |
| `/scripts/tags.js` | `getTagKeyForEntity` |
| `/lib.js` | `lodash`、`yaml` |
| `/scripts/authors-note.js` | `shouldWIAddPrompt` |
| `/scripts/openai.js` | `oai_settings`、`model_list`（独立连接关闭时读取参数快照） |

其他卡只用 CDN 模块（MVU/Zod），不直接依赖 ST 前端模块。

### 1.2 ST 前端模块导出规模（`public/`）

| 模块 | 导出数 | 优先 |
|---|---:|---|
| `script.js` | 249 | P0（子集） |
| `scripts/world-info.js` | 68 | P0（数据 + `loadWorldInfo`） |
| `scripts/openai.js` | 45 | P0（`oai_settings`/`model_list`） |
| `scripts/tags.js` | 33 | P1 |
| `scripts/power-user.js` | 32 | P0（`power_user` 快照） |
| `scripts/extensions.js` | 29 | P0（设置/正则读取） |
| `scripts/utils.js` | 134 | P2（按需） |
| `public/lib.js` | 26 | P0（lodash/yaml） |
| `scripts/tokenizers.js` | 21 | P1 |
| `scripts/extensions/regex/engine.js` | 18 | P1 |
| `scripts/authors-note.js` | 5 | P0 |
| `scripts/events.js` | 2 | P1（`eventSource`/`event_types`） |
| `scripts/templates.js` | 2 | P2（Handlebars 渲染） |
| `scripts/chats.js` / `characters.js` / `group-chats.js` | 24 / 13+ / 41 | P2（按卡需求） |
| `scripts/macros.js` / `variables.js` / `preset-manager.js` | 4 / 19 / — | P1（宏/变量/预设） |

P0 不需要实现全部导出，只需要覆盖“被 import 后实际读取”的属性；其余导出可先给显式诊断桩（访问即抛出“未实现的 ST 接口 X”），便于定位。

### 1.3 全局对象

酒馆助手在 iframe 里合并的宿主全局（`src/iframe/predefine.js`）：

```js
_.pick(window.parent, ['EjsTemplate', 'TavernHelper', 'YAML', 'showdown', 'toastr', 'z'])
```

卡片脚本里实际出现的 `window.*`：`parent`、`top`、`Mvu`、`toastr`、`z`、`CSS`、`MutationObserver`、`addEventListener`、`setTimeout` 等。  
建议宿主面至少提供：`SillyTavern`、`TavernHelper`、`EjsTemplate`、`YAML`、`showdown`、`toastr`、`z`、`_`、`$`/`jQuery`、`Mvu`、`moment`、`DOMPurify`、`localforage`、`Handlebars`、`eventSource`、`event_types`。

### 1.4 EJS 接口

卡片运行时实际使用：

```js
const compiled = await EjsTemplate.compileTemplate(template, { options }, locals)
return await compiled.call(locals, locals)
```

`EjsTemplate` 的完整接口（酒馆助手类型定义）：

| 接口 | 说明 |
|---|---|
| `compileTemplate(code, options?, locals?)` | 返回可调用的编译函数（卡实际使用） |
| `evalTemplate(code, context?, options?)` | 直接求值 |
| `evaltemplate(...)` | 旧大小写别名 |
| `prepareContext(extra?, lastMessageId?)` | 合并全局/聊天/楼层变量 |
| `getSyntaxErrorInfo(code, lines?)` | 语法检查 |
| `allVariables(endMessageId?)` | 变量并集 |
| `getFeatures()` / `setFeatures()` / `resetFeatures()` | 功能开关 |

`Features` 关键字段：`enabled`、`generate_enabled`、`render_enabled`、`preload_worldinfo_enabled`、`with_context_disabled`、`debug_enabled`、`compile_workers`、`sandbox`、`cache_enabled`、`cache_size`、`cache_hasher`。

### 1.5 服务端接口

ST 1.19.1 共 **44 个路由模块 / 267 条路由**。与卡运行时直接相关：

| 模块 | 路由数 | 关键路由 | 建议 |
|---|---:|---|---|
| `/api/backends/chat-completions` | 4 | `POST /generate`、`POST /status`、`POST /bias`、`POST /process` | P0：`generate`/`status` 走 FlyAI 模型桥 |
| `/api/worldinfo` | 5 | `list`、`get`、`edit`、`import`、`delete` | 已有本地实现，补 edit/import |
| `/api/chats` | 13 | `get`、`save`、`rename`、`delete`、`export`、`import` | 已有 get/save/rename/delete |
| `/api/settings` | 6 | `get`、`save`、快照 | P1：`get` 返回快照 |
| `/api/presets` | 3 | `save`、`delete`、`restore` | P1：适配 FlyAI 预设 |
| `/api/tokenizers` | 35 | `encode`、`decode`、`count` | P1：字符/token 近似 |
| `/api/extensions` | 8 | `discover`、`install`、`version` | P1：返回“已安装 Helper/EJS”的合成列表 |
| `/api/users` | 21 | `me`、`login` | 已有 `/api/users/me` 本地桩 |
| `/api/files` | 4 | `sanitize-filename`、`upload`、`verify` | 已有 sanitize |
| `/api/characters` | 13 | `all`、`get`、`create`、`edit` | P2 按需只读 |
| `/api/groups` | 4 | `all`、`create`、`edit`、`delete` | P2 映射房间 |
| `/api/assets`、`/api/images`、`/api/avatars` | 4/4/3 | 资源读写 | P2 |
| `/api/secrets`、`/api/extensions/install` | 8/— | 密钥与安装 | 不实现（安全边界） |

模型后端的其余来源（Claude/Google/Kobold/NovelAI/Horde 等）不追求路径兼容，统一由 FlyAI provider 体系承接。

## 2. 目标架构

```
卡 iframe (srcdoc, opaque origin)
├─ shim（现有 TavernHelper / 变量 / 世界书 / 消息 / fetch 桥）
├─ window.__FLYAI_ST_NATIVE__   ← 会话快照（ST 形状）
├─ window.EjsTemplate           ← EJS 编译器
├─ <script type="importmap">    ← "null/xxx.js" → data: 模块
└─ 卡自带运行时
   ├─ import("/script.js") …    → 读 __FLYAI_ST_NATIVE__
   ├─ fetch("/api/backends/chat-completions/generate")
   │      → shim fetch → 宿主桥 → FlyAI provider
   └─ 世界书/宏/正则/EJS 管线（卡内自带）
```

### 2.1 模块 shim（import map → data: 模块）

每个模块是一个很小的 ES module，从 `globalThis.__FLYAI_ST_NATIVE__` 取数据：

```js
// null/script.js
const host = globalThis.__FLYAI_ST_NATIVE__;
export const main_api = host.mainApi;
export const name2 = host.characterName;
export const chat = host.chat;
export const chat_metadata = host.chatMetadata;
export const characters = host.characters;
export const this_chid = 0;
export function getCurrentChatId() { return host.chatId; }
export function getMaxContextSize() { return host.maxContext; }
export function getMaxContextTokens() { return host.maxContext; }
export function getMaxResponseTokens() { return host.maxResponse; }
```

- `chat`、`chat_metadata` 用**同一对象引用**，卡片原地修改可见；
- `main_api`、`name2` 等标量在 import 时求值；若后续变化，模块内订阅宿主事件更新 `let` 导出；
- 未实现的导出统一由 `stub(name)` 生成：访问/调用时抛出 `ST host interface not implemented: <module>.<name>`，方便按卡补。

P0 覆盖 1.1 节 10 个路径；P2 逐步加入 `events.js`、`templates.js`、`chats.js`、`characters.js`、`group-chats.js`、`macros.js`、`variables.js`、`preset-manager.js`、`utils.js`、`extensions/shared.js`。

### 2.2 原生快照

由宿主在生成 iframe 文档时构造（`stNativeSnapshot`）：

```ts
{
  chatId, characterId, characterName, userName, groupId,
  chat, chatMetadata, characters,
  mainApi: 'openai', maxContext, maxResponse,
  worldInfo: { names, books, settings, defaultDepth },
  powerUser: { ... }, oaiSettings: { ... }, modelList: [...],
  extensions: { names, settings, prompts, regexScripts },
  tokenizers: { model }, tags: { tagMap }
}
```

数据来源：
- 会话/角色/世界书：现有 `st/world`、`session`、`mvu`、`st_extra`；
- 正则：`applyDisplayScripts` 已有脚本列表，转 ST 形状；
- 预设：FlyAI preset 转 ST `chatCompletionSettings`（P1）；
- 设置：给出安全默认值，不读真实密钥。

### 2.3 EJS 运行时

复用 `webapp/src/services/st/ejs.ts`，在沙箱内包装为 `window.EjsTemplate`：

- `compileTemplate` 返回 `async function(context)`，支持 `async/await`、`print`、`locals`、`_with`；
- `prepareContext` 合并 `__FLYAI_ST_NATIVE__` 的全局/聊天/楼层变量；
- `getFeatures` 返回默认 `Features`；
- 变量写入只保留在请求局部，不回写会话（与卡运行时 `templateVariableWrites: request-local-only` 一致）。

### 2.4 HTTP 桥

扩展 `chatApiFetch`（两个 shim 共用）：

| ST 路由 | 处理 |
|---|---|
| `POST /api/backends/chat-completions/generate` | 取 `body.messages`，调用宿主生成桥（作者面板复用 `AUTHOR_MSG_GENERATE`），返回 OpenAI JSON；`stream:true` 时合成 SSE |
| `POST /api/backends/chat-completions/status` | 返回当前模型列表 |
| `POST /api/backends/chat-completions/process`、`/bias` | 返回空成功或 400 明确错误 |
| `GET /api/settings/get` | 返回快照 |
| `GET /api/extensions/*` | 返回合成列表（Helper、EJS 标记为已启用） |
| `/api/worldinfo/*`、`/api/chats/*`、`/api/users/me`、`/api/files/sanitize-filename` | 沿用现有本地实现 |

安全边界不变：
- CSP 保持 `connect-src 'none'`；
- 模型请求统一走宿主桥，不把 Bearer/token 放进 iframe；
- 模块 shim 只从本地 data: 注入，不接受任意 URL；
- extra/metadata/请求体继续限长。

## 3. 实施阶段

### P0：原生路径跑通（约 3–4 人日）

1. 新增 `webapp/src/services/st/native/stNative.ts`
   - `buildStNativeSnapshot()`
   - `buildNativeModules()`（data: 模块源）
   - `buildNativeImportMap()`（`"null/script.js"` 等精确键）
2. 新增 `webapp/src/services/st/native/ejsRuntime.ts`（复用 `ejs.ts`）
3. `authorRuntime.ts` / `cardApp.ts` 注入顺序：
   - shim → `__FLYAI_ST_NATIVE__` → `EjsTemplate` → import map → 卡脚本
4. `chatApiFetch` 增加 `chat-completions/generate|status`
5. 增加开关 `st.native_host_enabled`，默认开；保留 `DALPhoneRuntime` 覆盖做回退
6. 验收：
   - 约会卡作者面板里 `window.DALPhoneRuntime` 为**卡自带**实现；
   - 小手机发送不再报 Helper/EJS、不再报 import 失败；
   - 请求体含正确的消息数组，回复能解析成气泡；
   - 不新增任何直连网络请求。

### P1：语义保真（约 5–8 人日）

- `world-info.js`：扫描设置、递归、深度、位置、`loadWorldInfo` 完整 ST 形状；
- `regex/engine.js`：把会话正则转 `SCRIPT_TYPES`，支持 `getScriptsByType`；
- `power-user.js`：instruct/context/sysprompt、宏开关、停止串；
- `tokenizers.js`：字符/token 近似与 `getTokenCountAsync`；
- `tags.js`：tag map；
- `/api/settings/get`、`/api/presets/*`、`/api/extensions/*`、SSE；
- 未实现导出的诊断桩与日志面板。

### P2：通用化（约 5–10 人日）

- 增加 `events.js`、`templates.js`、`chats.js`、`characters.js`、`group-chats.js`、`macros.js`、`variables.js`、`preset-manager.js`、`utils.js`、`extensions/shared.js`；
- 通过“卡请求了哪个模块/导出”自动记录覆盖率；
- 删除 `DALPhoneRuntime` 覆盖，回归红楼、约会、御主地界等卡；
- 形成“ST Host 兼容矩阵”文档与 CI 冒烟卡。

## 4. 风险与回退

| 风险 | 处理 |
|---|---|
| 原生运行时依赖未文档化字段 | 诊断桩抛出具体模块/导出；按卡迭代补齐 |
| 设置结构不兼容 | 先给安全默认值，不读真实密钥；P1 再对齐 |
| 世界书扫描结果与后端不一致 | 以卡内运行时结果为准，只提供数据；后端不重复扫描 |
| SSE/流式 | P0 先返回非流式 + 合成 SSE；P1 接真实流 |
| 安全 | 不注入 token；模块只走 data:；请求体限长；CSP 不变 |
| 回退 | `st.native_host_enabled=false` 时恢复当前 `DALPhoneRuntime` 覆盖 |

## 5. 文件清单

| 文件 | 动作 |
|---|---|
| `webapp/src/services/st/native/stNative.ts` | 新增：快照 + 模块源 + import map |
| `webapp/src/services/st/native/ejsRuntime.ts` | 新增：`window.EjsTemplate` 源 |
| `webapp/src/services/st/authorRuntime.ts` | 注入快照/模块/EJS；补 `TavernHelper` 世界书名 API |
| `webapp/src/services/st/cardApp.ts` | 同上（消息内应用） |
| `webapp/src/services/st/ejs.ts` | 导出可复用的编译接口 |
| `webapp/src/services/st/cardApp.test.ts` / `authorRuntime.test.ts` | 模块映射、EJS、端点形状测试 |
| `flyai/api/routes_session.py` | 仅在需要时增加 ST chat-completions 适配；优先复用作者生成 |
| `docs/ST原生宿主仿真方案.md` | 本文档 |
