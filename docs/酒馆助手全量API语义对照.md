# 酒馆助手全量 API 语义对照

上游：`N0VI028/JS-Slash-Runner` 4.12.2  
本地：`webapp/src/services/st/authorRuntime.ts`  
生成时间：2026-11-23

图例：✅ 已实现且语义基本一致；🟡 已实现但有语义差异；⚪ 仅桩实现；⛔ 未实现。

## 总览

- 上游 `TavernHelper.*` 共 **137** 项
- 本地同名入口 **27** 项（其中桩 **3** 项）
- 未实现 **110** 项

> 注意：本表只统计 `authorRuntime.ts`（状态面板/手札 iframe）。
> 卡的消息 HTML（开场页、选项、手机、终端）跑在另一个 shim `cardApp.ts` 里，
> 它的 API 面更小，且 `chatMetadata` 是硬编码 `{}`。两张卡都同时使用两个运行时，
> 只补 `authorRuntime.ts` 不会让消息内的功能一起变好。

## audio - 暂不实现

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `playAudio` | 播放给定的音频; 如果该音频没在播放列表中, 则会加入到播放列表. | ⛔ 未实现 | 未实现。 |
| `pauseAudio` | 暂停音乐 | ⛔ 未实现 | 未实现。 |
| `getAudioList` | 获取播放列表 | ⛔ 未实现 | 未实现。 |
| `replaceAudioList` | 完全替换播放列表为 `audio_list` | ⛔ 未实现 | 未实现。 |
| `appendAudioList` | 向播放列表末尾添加不存在的音频, 不会重复添加同 `title` 或 `url` 的音频 | ⛔ 未实现 | 未实现。 |
| `getAudioSettings` | 获取音频设置 | ⛔ 未实现 | 未实现。 |
| `setAudioSettings` | 修改音频设置, 如果某字段不存在, 则使用原本的设置. | ⛔ 未实现 | 未实现。 |
| `getCurrentAudio` | 获取当前播放的音频信息: 链接、标题、是否在播、进度 | ⛔ 未实现 | 未实现。 |

## builtin

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `builtin` | 酒馆助手内置对象/工具集合 | ⛔ 未实现 | 未实现。 |

## character

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getCharacterNames` | 获取角色卡的显示名称列表 | ⛔ 未实现 | 未实现。 |
| `getCharacterIds` | 获取角色卡的头像 id 列表 | ⛔ 未实现 | 未实现。 |
| `getCurrentCharacterName` | 获取当前角色卡的显示名称 | ⛔ 未实现 | 未实现。 |
| `getCurrentCharacterId` | 获取当前角色卡的头像 id | 🟡 部分 | 返回 `avatar \|\| name`，不是完整头像 id，也没有“无角色返回 null”的语义；只挂在 helper 上。 |
| `createCharacter` | 新建 `character_name_or_id` 角色卡, 内容为 `character` | ⛔ 未实现 | 未实现。 |
| `createOrReplaceCharacter` | 创建或替换名为 `character_name_or_id` 的角色卡, 内容为 `character` | ⛔ 未实现 | 未实现。 |
| `deleteCharacter` | 删除 `character_name_or_id` 角色卡 | ⛔ 未实现 | 未实现。 |
| `getCharacter` | 获取 `character_name_or_id` 角色卡的内容 | 🟡 部分 | 只返回 `{name, description, avatar}`，缺 `first_messages`、`extensions`、`worldbook`、`creator_notes` 等字段；只挂在 helper 上。 |
| `replaceCharacter` | 完全替换 `character_name_or_id` 角色卡的内容为 `character` | ⛔ 未实现 | 未实现。 |
| `updateCharacterWith` | 用 `updater` 函数更新 `character_name_or_id` 角色卡 | ⛔ 未实现 | 未实现。 |

## chat_message

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getChatMessages` | 获取聊天消息, 仅获取每楼被 AI 使用的消息页 | 🟡 部分 | 只支持单个 id 或 id 数组；不支持 `0-10`、`{{lastMessageId}}`、负数深度、`role`、`hide_state`、`include_swipes`。 |
| `setChatMessages` | 修改聊天消息的数据 | ⛔ 未实现 | 未实现。 |
| `createChatMessages` | 创建聊天消息 | 🟡 部分 | 只在 iframe 内追加消息并返回数组；不回写宿主/服务端，不支持 `insert_before`、`refresh`；只挂在 helper 上。 |
| `deleteChatMessages` | 删除聊天消息 | ⛔ 未实现 | 未实现。 |
| `rotateChatMessages` | 将原本顺序是 `[begin, middle) [middle, end)` 的楼层旋转为 `[middle, end) [begin, middle)` | ⛔ 未实现 | 未实现。 |

## displayed_message

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `formatAsDisplayedMessage` | 把文本按酒馆显示规则渲染成 HTML（宏、正则、Markdown） | ⛔ 未实现 | 未实现。 |
| `retrieveDisplayedMessage` | 获取指定楼层的已渲染 DOM（JQuery 实例） | ⛔ 未实现 | 未实现。 |
| `refreshOneMessage` | 刷新或替换指定楼层的显示 | ⛔ 未实现 | 未实现。 |

## extension - 不实现

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `isAdmin` | 当前用户是否为酒馆管理员 | ⛔ 未实现 | 未实现。 |
| `getExtensionType` | 获取已安装扩展的类型 | ⛔ 未实现 | 未实现。 |
| `getExtensionStatus` | 查询扩展安装信息 | ⛔ 未实现 | 未实现。 |
| `isInstalledExtension` | 检查是否已安装某一扩展 | ⛔ 未实现 | 未实现。 |
| `installExtension` | 安装扩展; 新安装的扩展需要刷新页面 (`triggerSlash('/reload-page')`) 才生效 | ⛔ 未实现 | 未实现。 |
| `uninstallExtension` | 卸载扩展; 卸载后需要刷新页面 (`triggerSlash('/reload-page')`) 才生效 | ⛔ 未实现 | 未实现。 |
| `reinstallExtension` | 重新安装扩展; 重新安装后需要刷新页面 (`triggerSlash('/reload-page')`) 才生效 | ⛔ 未实现 | 未实现。 |
| `updateExtension` | 更新扩展; 更新后需要刷新页面 (`triggerSlash('/reload-page')`) 才生效 | ⛔ 未实现 | 未实现。 |

## file - 暂不实现

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `uploadTavernFile` | 将 `base64` 存储的文件数据以 `name` 为文件名上传到 `酒馆存档文件夹/users/files/` 文件夹内 | ⛔ 未实现 | 未实现。 |
| `deleteTavernFile` | 从 `酒馆存档文件夹/users/files/` 文件夹内删除指定文件 | ⛔ 未实现 | 未实现。 |
| `getTavernFile` | 从 `酒馆存档文件夹/users/files/` 文件夹内获取文件内容 | ⛔ 未实现 | 未实现。 |

## generate

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `builtin_prompt_default_order` | 内置提示词默认顺序 | ⛔ 未实现 | 未实现。 |
| `generate` | 使用酒馆当前启用的预设, 让 AI 生成一段文本. | ⛔ 未实现 | 未实现。 |
| `generateRaw` | 不使用酒馆当前启用的预设, 让 AI 生成一段文本. | 🟡 部分 | 支持 `ordered_prompts` / `messages`，忽略 `custom_api` 的 endpoint/key，不支持流式、工具调用、reasoning 详情；只挂在 window 上。 |
| `getModelList` | 获取模型列表 | ⛔ 未实现 | 未实现。 |
| `getProxyPresetNames` | 获取代理预设名称列表 | ⛔ 未实现 | 未实现。 |
| `stopGenerationById` | 根据生成请求唯一标识符停止特定的生成请求 | 🟡 部分 | 只拒绝本地 Promise，无法真正取消宿主正在进行的请求；只挂在 window 上。 |
| `stopAllGeneration` | 停止所有正在进行的生成请求 | ⛔ 未实现 | 未实现。 |

## global

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `initializeGlobal` | 将接口共享到全局, 使其可以在其他前端界面或脚本中使用. | ⛔ 未实现 | 未实现。 |
| `waitGlobalInitialized` | 等待其他前端界面或脚本中共享出来的全局接口初始化完毕, 并使之在当前前端界面或脚本中可用. | 🟡 部分 | 轮询 `window[name]`，10 秒超时；上游可等待 `initializeGlobal` 共享的接口；只挂在 window 上。 |

## import_raw - 暂不实现

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `importRawCharacter` | 像酒馆界面里那样导入新角色/更新现有角色卡 | ⛔ 未实现 | 未实现。 |
| `importRawChat` | 像酒馆界面里那样导入聊天文件, 目前仅能导入到当前选择的角色卡 | ⛔ 未实现 | 未实现。 |
| `importRawPreset` | 像酒馆界面里那样导入新预设/更新现有预设 | ⛔ 未实现 | 未实现。 |
| `importRawWorldbook` | 像酒馆界面里那样导入新世界书/更新现有世界书 | ⛔ 未实现 | 未实现。 |
| `importRawTavernRegex` | 像酒馆界面里那样导入酒馆正则 | ⛔ 未实现 | 未实现。 |

## inject

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `injectPrompts` | 注入提示词 | 🟡 部分 | 返回 Promise，没有 `{ uninject }`；`filter` 函数无法跨 postMessage。 |
| `uninjectPrompts` | 移除注入的提示词 | ✅ 完整 |  |

## lorebook_entry - 已弃用

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getLorebookEntries` | （已弃用）读取世界书条目数组，等价于 `getWorldbook` | ⛔ 未实现 | 未实现。 |
| `replaceLorebookEntries` | （已弃用）完全替换世界书条目，等价于 `replaceWorldbook` | ⛔ 未实现 | 未实现。 |
| `updatelorebookEntriesWith` | （已弃用）按 updater 更新世界书条目，等价于 `updateWorldbookWith` | ⛔ 未实现 | 未实现。 |
| `setLorebookEntries` | （已弃用）按 uid 批量设置世界书条目 | ⛔ 未实现 | 未实现。 |
| `createLorebookEntries` | （已弃用）创建世界书条目，返回新 uid | ⛔ 未实现 | 未实现。 |
| `deleteLorebookEntries` | （已弃用）按 uid 删除世界书条目 | ⛔ 未实现 | 未实现。 |

## lorebook

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getLorebookSettings` | 获取世界书全局设置 | ⛔ 未实现 | 未实现。 |
| `setLorebookSettings` | 修改世界书全局设置 | ⛔ 未实现 | 未实现。 |
| `getLorebooks` | 获取世界书名称列表 | ⛔ 未实现 | 未实现。 |
| `deleteLorebook` | 删除指定世界书 | ⛔ 未实现 | 未实现。 |
| `createLorebook` | 创建空世界书 | ⛔ 未实现 | 未实现。 |
| `getCharLorebooks` | 获取角色卡绑定的世界书 | ⛔ 未实现 | 未实现。 |
| `setCurrentCharLorebooks` | 设置当前角色卡绑定的世界书 | ⛔ 未实现 | 未实现。 |
| `getCurrentCharPrimaryLorebook` | 获取当前角色卡的主世界书 | ⛔ 未实现 | 未实现。 |
| `getOrCreateChatLorebook` | 获取或创建当前聊天绑定的世界书 | ⛔ 未实现 | 未实现。 |

## macrolike

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `registerMacroLike` | 注册一个新的助手宏 | ⛔ 未实现 | 未实现。 |

## preset

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `isPresetNormalPrompt` | 判断预设提示词是否为普通提示词 | ⛔ 未实现 | 未实现。 |
| `isPresetSystemPrompt` | 判断预设提示词是否为系统提示词 | ⛔ 未实现 | 未实现。 |
| `isPresetPlaceholderPrompt` | 判断预设提示词是否为占位提示词 | ⛔ 未实现 | 未实现。 |
| `default_preset` | 酒馆助手的默认预设对象 | ⛔ 未实现 | 未实现。 |
| `getPresetNames` | 获取预设名称列表 | ⚪ 桩 | 永远返回 `[]`（桩）；只挂在 helper 上。 |
| `getLoadedPresetName` | 获取当前启用的预设来源名称 | ⛔ 未实现 | 未实现。 |
| `loadPreset` | 加载指定预设为当前启用预设 | ⛔ 未实现 | 未实现。 |
| `createPreset` | 创建新预设 | ⛔ 未实现 | 未实现。 |
| `createOrReplacePreset` | 创建或替换指定预设 | ⛔ 未实现 | 未实现。 |
| `deletePreset` | 删除指定预设 | ⛔ 未实现 | 未实现。 |
| `renamePreset` | 重命名预设 | ⛔ 未实现 | 未实现。 |
| `getPreset` | 读取指定预设内容 | ⛔ 未实现 | 未实现。 |
| `replacePreset` | 完全替换指定预设内容 | ⛔ 未实现 | 未实现。 |
| `updatePresetWith` | 用 updater 更新指定预设 | ⛔ 未实现 | 未实现。 |
| `setPreset` | 把修改后的预设内容写回预设 | ⛔ 未实现 | 未实现。 |

## persona

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getPersonaNames` | 获取 persona 名称列表 | ⛔ 未实现 | 未实现。 |
| `getPersonaIds` | 获取 persona 头像 id 列表 | ⛔ 未实现 | 未实现。 |
| `getCurrentPersonaName` | 获取当前 persona 名称 | ⛔ 未实现 | 未实现。 |
| `getCurrentPersonaId` | 获取当前 persona 头像 id | ⛔ 未实现 | 未实现。 |
| `getPersonaAvatarPath` | 获取 persona 的头像路径 | ⛔ 未实现 | 未实现。 |
| `createPersona` | 新建 persona | ⛔ 未实现 | 未实现。 |
| `createOrReplacePersona` | 创建或替换名为 `persona_name` 的 persona | ⛔ 未实现 | 未实现。 |
| `deletePersona` | 删除 persona | ⛔ 未实现 | 未实现。 |
| `getPersona` | 获取 persona 的内容 | ⛔ 未实现 | 未实现。 |
| `replacePersona` | 完全替换 persona 的内容 | ⛔ 未实现 | 未实现。 |
| `updatePersonaWith` | 用 `updater` 函数更新 persona | ⛔ 未实现 | 未实现。 |

## raw_character

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `RawCharacter` | 角色卡数据管理类 | ⛔ 未实现 | 未实现。 |
| `getCharData` | 获取角色卡数据 | ⛔ 未实现 | 未实现。 |
| `getCharAvatarPath` | 获取角色头像路径 | ⛔ 未实现 | 未实现。 |
| `getChatHistoryBrief` | 获取角色聊天历史摘要 | ⛔ 未实现 | 未实现。 |
| `getChatHistoryDetail` | 获取聊天历史详情 | 🟡 部分 | 基于当前消息镜像合成单个文件，未读取完整聊天文件/分支；只挂在 helper 上。 |

## script - 暂不实现

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getAllEnabledScriptButtons` | 获取所有处于启用状态的酒馆助手脚本按钮, 主要是方便 QR 助手等兼容脚本按钮 | ⛔ 未实现 | 未实现。 |

## slash

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `triggerSlash` | 运行 Slash 命令, 注意如果命令写错了将不会有任何反馈 | 🟡 部分 | 只支持少量内置命令，未知命令没有上游的管道/错误语义。 |

## tavern_regex

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `formatAsTavernRegexedString` | 对 `text` 应用酒馆正则 | ⛔ 未实现 | 未实现。 |
| `isCharacterTavernRegexesEnabled` | 判断局部正则是否启用 | ⛔ 未实现 | 未实现。 |
| `getTavernRegexes` | 获取酒馆正则 | ⛔ 未实现 | 未实现。 |
| `replaceTavernRegexes` | 完全替换酒馆正则为 `regexes`. | ⛔ 未实现 | 未实现。 |
| `updateTavernRegexesWith` | 用 `updater` 函数更新酒馆正则 | ⛔ 未实现 | 未实现。 |

## util

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `substitudeMacros` | 替换字符串中的酒馆宏 | 🟡 部分 | 只替换少量宏（`{{char}}`、`{{user}}`、`{{lastMessageId}}` 等）。 |
| `getLastMessageId` | 获取最新楼层 id | ✅ 完整 |  |
| `errorCatched` | 包装任意函数，返回一个会将报错消息通过酒馆通知显示出来的同功能函数 | ⛔ 未实现 | 未实现。 |
| `getMessageId` | 从前端界面的 iframe 标识名称 `iframe_name` 获取它所在楼层的楼层号, **只能对前端界面 iframe 标识名称使用** | ⛔ 未实现 | 未实现。 |

## variables

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getVariables` | 获取变量表 | 🟡 部分 | 只区分 `message` 和聊天变量；`global/preset/character/script/extension` 都退化为聊天变量。 |
| `replaceVariables` | 完全替换变量表为 `variables` | 🟡 部分 | 只写聊天/消息变量，返回 Promise；上游按 option 写各作用域且返回 void。 |
| `updateVariablesWith` | 用 `updater` 函数更新变量表 | 🟡 部分 | 只支持同步 updater；上游支持 async updater。 |
| `insertOrAssignVariables` | 插入或修改变量值, 取决于变量是否存在. | 🟡 部分 | 只做顶层浅合并；上游是 lodash 深合并（数组整体替换）。 |
| `insertVariables` | 插入新变量, 如果变量已经存在则什么也不做 | 🟡 部分 | 只做顶层浅合并；上游是 lodash 深合并且保留已有值。 |
| `deleteVariable` | 删除变量, 如果变量不存在则什么也不做 | 🟡 部分 | 只删除顶层键；上游按 `a.b.c` 路径删除并返回 `{variables, delete_occurred}`。 |

## version - 按照酒馆(助手)最新版本号硬编码

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getTavernHelperVersion` | 获取酒馆助手版本号 | ⛔ 未实现 | 未实现。 |
| `getTavernHelperExtensionId` | — | ⛔ 未实现 | 未实现。 |
| `getTavernVersion` | 获取酒馆版本号 | ⛔ 未实现 | 未实现。 |

## worldbook

| API | 上游语义 | 本地 | 差异 / 影响 |
|---|---|---|---|
| `getWorldbookNames` | 获取世界书名称列表 | 🟡 部分 | 只返回当前角色主世界书/世界观名，不包含全部世界书。 |
| `getGlobalWorldbookNames` | 获取当前全局开启的世界书名称列表 | ⚪ 桩 | 永远返回 `[]`（桩）。 |
| `rebindGlobalWorldbooks` | 重新绑定全局世界书 | ⛔ 未实现 | 未实现。 |
| `getCharWorldbookNames` | 获取角色卡绑定的世界书 | 🟡 部分 | 忽略参数，只返回当前角色的 `{primary, additional:[]}`。 |
| `rebindCharWorldbooks` | 重新绑定角色卡世界书 | ⛔ 未实现 | 未实现。 |
| `getChatWorldbookName` | 获取聊天文件绑定的世界书 | ⚪ 桩 | 永远返回 `null`（桩）。 |
| `rebindChatWorldbook` | 重新绑定聊天文件世界书 | ⛔ 未实现 | 未实现。 |
| `getOrCreateChatWorldbook` | 获取或新建聊天文件世界书 | ⛔ 未实现 | 未实现。 |
| `createWorldbook` | 创建新的世界书 | ⛔ 未实现 | 未实现。 |
| `createOrReplaceWorldbook` | 创建或替换名为 `worldbook_name` 的世界书, 内容为 `worldbook` | ⛔ 未实现 | 未实现。 |
| `deleteWorldbook` | 删除 `worldbook_name` 世界书 | ⛔ 未实现 | 未实现。 |
| `getWorldbook` | 获取 `worldbook_name` 世界书的内容 | 🟡 部分 | 返回 `{entries: {...}}` 对象；上游返回 `WorldbookEntry[]` 数组。 |
| `replaceWorldbook` | 完全替换 `worldbook_name` 世界书的内容为 `worldbook` | 🟡 部分 | 接受/保存对象形状；上游接受 `WorldbookEntry[]`。 |
| `updateWorldbookWith` | 用 `updater` 函数更新世界书 `worldbook_name` | 🟡 部分 | updater 收到对象而非数组；上游传数组、返回数组。 |
| `createWorldbookEntries` | 向世界书中新增条目 | ⛔ 未实现 | 未实现。 |
| `deleteWorldbookEntries` | 删除世界书中的条目 | ⛔ 未实现 | 未实现。 |

## 全局暴露差异

上游会把每个 API 同时挂到 `TavernHelper.*` 和 iframe 的裸全局上。本地存在单边暴露：

| 只在 helper 上 | 只在 window 上 |
|---|---|
| `createChatMessages`、`getCharacter`、`getChatHistoryDetail`、`getCurrentCharacterId`、`getPresetNames` | `generateRaw`、`stopGenerationById`、`waitGlobalInitialized` |

影响：卡里直接写 `createChatMessages(...)` 或 `TavernHelper.generateRaw(...)` 会拿到 `undefined`。

## 事件 API

| 项 | 上游 | 本地 | 差异 |
|---|---|---|---|
| `eventOn` / `eventOnce` / `eventMakeFirst` / `eventMakeLast` | 返回 `{ stop() }` | 返回 `undefined` | 依赖 `stop` 的清理逻辑会失败 |
| `eventEmit` | 返回 `Promise<void>` | 同步返回 `undefined` | `await eventEmit` 语义不完整 |
| `eventOnButton` / `eventEmitAndWait` / `eventClearEvent` / `eventClearListener` / `eventClearAll` | 存在 | 未实现 | 事件 API 不完整 |
| `tavern_events` | 82 个常量 | 14 个 | 缺 68 个；`GENERATION_AFTER_COMMANDS` 值也不一致 |
| `iframe_events` | 7 个常量 | 6 个，4 个值不同 | 缺两个流式事件，生成事件字符串对不上 |

## `SillyTavern.getContext()`

上游 114 个字段，本地 29 个。高频缺失：`addOneMessage`、`deleteLastMessage`、`sendSystemMessage`、`activateSendButtons` / `deactivateSendButtons`、`substituteParams`、`generateQuietPrompt`、`getCharacters`、`getCharacterCardFields`、`getThumbnailUrl`、`messageFormatting`、`updateMessageBlock`、`getCurrentChatId`、`maxContext`、`onlineStatus`、`getTokenCountAsync`、`registerMacro` / `unregisterMacro`、`Popup` / `callGenericPopup`、`SlashCommandParser`。

## 本地卡实际使用但缺失

扫描 8 张含作者脚本的卡：`setChatMessages`（2 张）、`deleteChatMessages`（1 张）、`errorCatched`（3 张）、`retrieveDisplayedMessage`（3 张）、`getCurrentPersonaName`（1 张）、`rebindGlobalWorldbooks` / `rebindCharWorldbooks` / `rebindChatWorldbook`（各 1 张）。

其中 `builtin`、`replacePreset` 为按钮 id / 卡内同名局部函数误命中。

## 实测确认的语义 bug

| API | 实测结果 | 影响 |
|---|---|---|
| `getWorldbook` / `updateWorldbookWith` | 返回对象，updater 收到对象（`Array.isArray = false`） | 红楼时线调度器返回空时间线；约会大作战判定世界书能力不可用 |
| `getChatMessages` | `getChatMessages('0-10', {role:'user'})` 只返回最后一条 | 红楼修复开场玩家资料读错楼层 |
| `injectPrompts` | 返回 Promise，没有 `.uninject` | 解构 `uninject` 的卡会报错；条件注入失效 |
| `eventOn` | 返回 `undefined`，没有 `.stop` | 依赖 `{stop}` 清理监听的脚本会失败 |
| `getVariables({type:'global'})` | 返回的键与 `{type:'chat'}` 相同 | 全局变量功能完全失效 |

## 高优先级语义修复

1. `getWorldbook` / `updateWorldbookWith` / `replaceWorldbook` 改为数组语义。红楼时线调度器当前会静默返回空数组；约会大作战会判定世界书能力不可用。
2. `getChatMessages` 支持范围和筛选；红楼 `getChatMessages('0-10', {role:'user'})` 当前只返回最后一条。
3. `setChatMessages` / `deleteChatMessages` 最小实现；红楼去重、约会大作战能力清单依赖。
4. 事件 API 返回值与常量值；`injectPrompts` 返回 `{uninject}`。
5. `context.generate` 接入主聊天生成，或明确返回可识别的失败。
