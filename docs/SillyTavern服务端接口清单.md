# SillyTavern 服务端接口清单与 FlyAI 对照

调研时间：2026-11-23  
上游：`SillyTavern/SillyTavern` 1.19.1（release 源码包）  
FlyAI：`/openapi.json`，168 个路径

## 总览

- ST 服务端共 **44 个路由模块、267 条路由声明**（不含静态资源与 Webpack 中间件）。
- FlyAI 有 168 个路径，但 **0 条与 ST 路径/协议直接兼容**；按模块存在近似能力。
- 与 TavernHelper/角色卡直接相关的是其中约 20 个模块，下面单独列出。

## 模块总览

| ST 模块 | 路由数 | FlyAI 现状 | 判断 | 说明 |
|---|---:|---|---|---|
| `/api/tokenizers` | 35 | — | ⛔ | 无 |
| `/api/users` | 21 | `/api/auth/*` | 🟡 | 登录/账号形状不同；没有 `/api/users/me` |
| `/api/openai` | 16 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/characters` | 13 | `/api/characters` CRUD/import/export/fork | 🟡 | 路径和字段不同，需要适配 |
| `/api/chats` | 13 | `/api/sessions` CRUD/import/export | 🟡 | 会话结构不同，没有 `ch_name/file_name/avatar_url` |
| `/api/horde` | 11 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/image-metadata` | 10 | — | ⛔ | 无 |
| `/api/sd` | 10 | FlyAI 自有生图模型接口 | ⛔ | 不兼容 ST SD 接口 |
| `/api/extensions` | 8 | `/api/plugins`、市场条目 | ⛔ | 不是 ST 扩展格式 |
| `/api/search` | 8 | 联网搜索走 AI 工具，不是 HTTP 接口 | ⛔ | 无同形状接口 |
| `/api/secrets` | 8 | — | ⛔ | 无 |
| `/api/translate` | 8 | — | ⛔ | 无 |
| `/api/google` | 7 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/vector` | 7 | — | ⛔ | 无 |
| `/api/openrouter` | 6 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/settings` | 6 | `/api/my/settings`、`/api/config` | 🟡 | 字段不同，没有快照接口 |
| `/api/backgrounds` | 5 | `/api/my/settings/background` | 🟡 | 只有个人背景设置 |
| `/api/worldinfo` | 5 | 作者运行时本地副本 + `/api/sessions/{sid}/st/world` | 🔧 | 写回未绑定世界书时 400 |
| `/api/assets` | 4 | — | ⛔ | 没有角色资产下载接口 |
| `/api/backends/chat-completions` | 4 | `/api/chat` + 作者运行时 shim | 🔧 | `generate` 可用，`status/process/bias` 未适配 |
| `/api/backends/kobold` | 4 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/data-maid` | 4 | — | ⛔ | 无 |
| `/api/files` | 4 | — | ⛔ | 只有 `/api/upload` 图片上传，没有通用文件区 |
| `/api/groups` | 4 | `/api/rooms` | 🟡 | 群聊模型不同 |
| `/api/images` | 4 | `/api/upload`、`/api/st/img-proxy` | 🟡 | 缺列表/删除/文件夹 |
| `/api/novelai` | 4 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/sprites` | 4 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/avatars` | 3 | `/api/upload` | 🟡 | 只有上传，没有删除/读取路径 |
| `/api/backends/text-completions` | 3 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/backups` | 3 | — | ⛔ | 无 |
| `/api/presets` | 3 | `/api/presets` | 🟡 | 只有列表/导入/开关，缺 save/restore |
| `/api/stats` | 3 | `/api/my/usage` | 🟡 | 统计口径不同 |
| `/api/azure` | 2 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/content` | 2 | — | ⛔ | 无 |
| `/api/extra/classify` | 2 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/nanogpt` | 2 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/quick-replies` | 2 | — | ⛔ | 无 |
| `/api/speech` | 2 | `/api/speech/tts`、`/api/speech/asr` | 🟡 | 能力有，路径不同 |
| `/api/themes` | 2 | — | ⛔ | 无 |
| `/api/anthropic` | 1 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/extra/caption` | 1 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/minimax` | 1 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |
| `/api/moving-ui` | 1 | — | ⛔ | 无 |
| `/api/volcengine` | 1 | FlyAI 自有 provider 体系 | ⛔ | 不追求 ST 路径兼容 |

## 与卡/TavernHelper 最相关的模块

### `/api/characters`（13 条，🟡）

FlyAI：`/api/characters` CRUD/import/export/fork。路径和字段不同，需要适配

路由：POST `/all`、POST `/chats`、POST `/create`、POST `/delete`、POST `/duplicate`、POST `/edit`、POST `/edit-attribute`、POST `/edit-avatar`、POST `/export`、POST `/get`、POST `/import`、POST `/merge-attributes`、POST `/rename`

### `/api/chats`（13 条，🟡）

FlyAI：`/api/sessions` CRUD/import/export。会话结构不同，没有 `ch_name/file_name/avatar_url`

路由：POST `/delete`、POST `/export`、POST `/get`、POST `/group/delete`、POST `/group/get`、POST `/group/import`、POST `/group/info`、POST `/group/save`、POST `/import`、POST `/recent`、POST `/rename`、POST `/save`、POST `/search`

### `/api/worldinfo`（5 条，🔧）

FlyAI：作者运行时本地副本 + `/api/sessions/{sid}/st/world`。写回未绑定世界书时 400

路由：POST `/delete`、POST `/edit`、POST `/get`、POST `/import`、POST `/list`

### `/api/files`（4 条，⛔）

FlyAI：—。只有 `/api/upload` 图片上传，没有通用文件区

路由：POST `/delete`、POST `/sanitize-filename`、POST `/upload`、POST `/verify`

### `/api/avatars`（3 条，🟡）

FlyAI：`/api/upload`。只有上传，没有删除/读取路径

路由：POST `/delete`、POST `/get`、POST `/upload`

### `/api/settings`（6 条，🟡）

FlyAI：`/api/my/settings`、`/api/config`。字段不同，没有快照接口

路由：POST `/get`、POST `/get-snapshots`、POST `/load-snapshot`、POST `/make-snapshot`、POST `/restore-snapshot`、POST `/save`

### `/api/extensions`（8 条，⛔）

FlyAI：`/api/plugins`、市场条目。不是 ST 扩展格式

路由：POST `/branches`、POST `/delete`、GET `/discover`、POST `/install`、POST `/move`、POST `/switch`、POST `/update`、POST `/version`

### `/api/backends/chat-completions`（4 条，🔧）

FlyAI：`/api/chat` + 作者运行时 shim。`generate` 可用，`status/process/bias` 未适配

路由：POST `/bias`、POST `/generate`、POST `/process`、POST `/status`

### `/api/users`（21 条，🟡）

FlyAI：`/api/auth/*`。登录/账号形状不同；没有 `/api/users/me`

路由：POST `/backup`、POST `/change-avatar`、POST `/change-name`、POST `/change-password`、POST `/create`、POST `/delete`、POST `/demote`、POST `/disable`、POST `/enable`、POST `/get`、POST `/list`、POST `/login`、POST `/logout`、GET `/me`、POST `/promote`、POST `/recover-step1`、POST `/recover-step2`、POST `/reset-settings`、POST `/reset-step1`、POST `/reset-step2`、POST `/slugify`

### `/api/groups`（4 条，🟡）

FlyAI：`/api/rooms`。群聊模型不同

路由：POST `/all`、POST `/create`、POST `/delete`、POST `/edit`

### `/api/presets`（3 条，🟡）

FlyAI：`/api/presets`。只有列表/导入/开关，缺 save/restore

路由：POST `/delete`、POST `/restore`、POST `/save`

### `/api/assets`（4 条，⛔）

FlyAI：—。没有角色资产下载接口

路由：POST `/character`、POST `/delete`、POST `/download`、POST `/get`

### `/api/images`（4 条，🟡）

FlyAI：`/api/upload`、`/api/st/img-proxy`。缺列表/删除/文件夹

路由：POST `/delete`、POST `/folders`、POST `/list/:folder?`、POST `/upload`

### `/api/backgrounds`（5 条，🟡）

FlyAI：`/api/my/settings/background`。只有个人背景设置

路由：POST `/all`、POST `/delete`、POST `/folders`、POST `/rename`、POST `/upload`

### `/api/themes`（2 条，⛔）

FlyAI：—。无

路由：POST `/delete`、POST `/save`

### `/api/tokenizers`（35 条，⛔）

FlyAI：—。无

路由：POST `/claude/decode`、POST `/claude/encode`、POST `/command-a/decode`、POST `/command-a/encode`、POST `/command-r/decode`、POST `/command-r/encode`、POST `/deepseek/decode`、POST `/deepseek/encode`、POST `/gemma/decode`、POST `/gemma/encode`、POST `/gpt2/decode`、POST `/gpt2/encode`、POST `/jamba/decode`、POST `/jamba/encode`、POST `/llama/decode`、POST `/llama/encode`、POST `/llama3/decode`、POST `/llama3/encode`、POST `/mistral/decode`、POST `/mistral/encode`、POST `/nemo/decode`、POST `/nemo/encode`、POST `/nerdstash/decode`、POST `/nerdstash/encode`、POST `/nerdstash_v2/decode`、POST `/nerdstash_v2/encode`、POST `/openai/count`、POST `/openai/decode`、POST `/openai/encode`、POST `/qwen2/decode`、POST `/qwen2/encode`、POST `/remote/kobold/count`、POST `/remote/textgenerationwebui/encode`、POST `/yi/decode`、POST `/yi/encode`

### `/api/secrets`（8 条，⛔）

FlyAI：—。无

路由：POST `/delete`、POST `/find`、POST `/read`、POST `/rename`、POST `/rotate`、POST `/settings`、POST `/view`、POST `/write`

### `/api/speech`（2 条，🟡）

FlyAI：`/api/speech/tts`、`/api/speech/asr`。能力有，路径不同

路由：POST `/recognize`、POST `/synthesize`

### `/api/stats`（3 条，🟡）

FlyAI：`/api/my/usage`。统计口径不同

路由：POST `/get`、POST `/recreate`、POST `/update`

## 之前没列到、但后续可能需要的接口

| 接口 | 用途 | 为什么现在没列 |
|---|---|---|
| `/api/worldinfo/list` | 列出所有世界书 | 之前只看了 get/edit/create/delete；世界书管理面板需要 |
| `/api/worldinfo/import` | 导入世界书 | 卡/预设导入世界书可能用到 |
| `/api/chats/save` / `rename` / `delete` | 聊天保存/改名/删除 | 之前只列了 get/import；会话管理需要 |
| `/api/chats/recent` / `search` | 最近聊天/搜索 | 会话列表能力 |
| `/api/characters/all` / `get` / `duplicate` / `rename` | 角色列表/读取/复制/改名 | 之前只列了 create/edit/import/export |
| `/api/characters/edit-avatar` / `merge-attributes` | 头像替换/属性合并 | 角色编辑能力 |
| `/api/files/verify` | 校验文件 | 文件区能力 |
| `/api/avatars/get` | 读取头像 | persona/角色头像 |
| `/api/settings/save` + snapshot 系列 | 保存设置/快照 | 设置页能力 |
| `/api/extensions/discover` / `branches` / `switch` / `move` | 扩展发现/分支切换 | 扩展管理能力 |
| `/api/users/*`（21 条） | 登录、用户管理、备份、重置 | 之前只列了 `/api/users/me` |
| `/api/assets/*` | 角色资产下载/删除 | 卡资产能力 |
| `/api/backends/chat-completions/process` / `bias` | 提示词处理/偏置 | 之前只列了 generate/status |

## 对实现 TavernHelper API 的影响

- `characters` / `chats` / `worldinfo` / `presets` / `personas` 这几组 API 最终都要落到上述 ST 服务端能力上；只补 iframe 里的函数名不够。
- 如果目标是“两张卡可用”，优先补：`/api/chats/get`、`/api/users/me`、`/api/files/sanitize-filename`、`/api/worldinfo/*` 写回、`/api/backends/chat-completions/generate` 的主聊天语义。
- 如果目标是“通用卡兼容”，再补 characters/chats/worldinfo 的完整 CRUD 和 preset/persona/avatar/assets。

## 建议

1. 先做作者运行时 Batch A（事件/消息/全局暴露）。
2. 再做 ST 服务端适配的最小集合：worldinfo 写回、chats/get、users/me、sanitize-filename、accountStorage。
3. 最后按卡的实际使用补 characters/chats/worldinfo 完整 CRUD 与 preset/persona。