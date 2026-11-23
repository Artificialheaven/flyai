# 酒馆助手（TavernHelper）API 覆盖对比

对比时间：2026-11-22  
上游：`N0VI028/JS-Slash-Runner` 4.12.2（main 分支源码包）  
本地实现：`webapp/src/services/st/authorRuntime.ts`  
参考类型：`@types/function/index.d.ts`、`@types/iframe/event.d.ts`、`@types/iframe/exported.sillytavern.d.ts`

## 总览

| 面 | 上游数量 | 本地同名实现 | 缺口 |
|---|---:|---:|---:|
| `TavernHelper.*` 函数 | 137 | 27 | 110（19.7% 覆盖） |
| `tavern_events` 常量 | 82 | 14 | 68 |
| `iframe_events` 常量 | 7 | 6 | 1，且 4 个值不一致 |
| 全局事件函数 | 10 | 8 | 5（含别名差异） |
| `SillyTavern.getContext()` 字段 | 114 | 29 | 89 |

> 27 个同名入口里有 2 个是桩：`getPresetNames()` 返回 `[]`，`getChatWorldbookName()` 返回 `null`。另有 `generate` 只挂在 `context.generate` / `__flyaiGenerate`，没有挂到 `TavernHelper.generate` 和全局 `generate`。

## 分类明细

| 分类 | 上游 | 已实现 | 缺失 | 缺失项 |
|---|---:|---:|---:|---|
| audio | 8 | 0 | 8 | 全部 |
| builtin | 1 | 0 | 1 | `builtin` |
| character | 10 | 2 | 8 | 名称/ID 列表、当前角色名、增删改 |
| chat_message | 5 | 2 | 3 | `setChatMessages`、`deleteChatMessages`、`rotateChatMessages` |
| displayed_message | 3 | 0 | 3 | 全部 |
| extension | 8 | 0 | 8 | 全部 |
| file | 3 | 0 | 3 | 全部 |
| generate | 7 | 2 | 5 | `generate`、`stopAllGeneration`、`getModelList`、`getProxyPresetNames`、`builtin_prompt_default_order` |
| global | 2 | 1 | 1 | `initializeGlobal` |
| import_raw | 5 | 0 | 5 | 全部 |
| inject | 2 | 2 | 0 | — |
| lorebook_entry | 6 | 0 | 6 | 全部 |
| lorebook | 9 | 0 | 9 | 全部 |
| macrolike | 1 | 0 | 1 | `registerMacroLike` |
| preset | 15 | 1 | 14 | 除 `getPresetNames` 外全部 |
| persona | 11 | 0 | 11 | 全部 |
| raw_character | 5 | 1 | 4 | `RawCharacter`、`getCharData`、`getCharAvatarPath`、`getChatHistoryBrief` |
| script | 1 | 0 | 1 | `getAllEnabledScriptButtons` |
| slash | 1 | 1 | 0 | — |
| tavern_regex | 5 | 0 | 5 | 全部 |
| util | 4 | 2 | 2 | `errorCatched`、`getMessageId` |
| variables | 6 | 6 | 0 | — |
| version | 3 | 0 | 3 | 全部 |
| worldbook | 16 | 7 | 9 | rebind/create/delete/orCreate + 条目增删 |

## 优先缺口（对卡影响大）

1. **`setChatMessages` / `deleteChatMessages` / `rotateChatMessages`**
   - 红楼卡的 `hlm-storage-dedupe-v1` 直接用 `setChatMessages` 清理 message 0 的重复配置键；当前因函数不存在直接跳过。
2. **`generate` 全局入口**
   - 目前只有 `context.generate` 和 `__flyaiGenerate`，卡里直接调 `generate(...)` 会 ReferenceError。`generateRaw` 已补。
3. **展示消息 API**
   - `formatAsDisplayedMessage` / `retrieveDisplayedMessage` / `refreshOneMessage`，用于把消息按正则/宏渲染后取回或刷新。
4. **角色只读信息**
   - `getCharacterNames` / `getCharacterIds` / `getCurrentCharacterName`、`getCharData`、`getCharAvatarPath`、`getChatHistoryBrief`。红楼手札用固定名单绕过了，但通用卡常依赖。
5. **世界书条目 / 预设 / 正则**
   - 世界书条目 CRUD、预设读写（14 项）、酒馆正则读写（5 项）都是常见卡面功能。
6. **`getContext()` 高频字段**
   - `addOneMessage`、`deleteLastMessage`、`sendSystemMessage`、`activateSendButtons` / `deactivateSendButtons`、`substituteParams`、`generateQuietPrompt`、`getCharacters`、`getCharacterCardFields`、`getThumbnailUrl`、`messageFormatting`、`updateMessageBlock`、`getCurrentChatId`、`maxContext`、`onlineStatus`、`getTokenCountAsync`、`registerMacro` / `unregisterMacro`、`registerFunctionTool` / `unregisterFunctionTool`、`Popup` / `callGenericPopup`、`SlashCommandParser` / `executeSlashCommandsWithOptions`。

## 事件 API 的具体差异

- `tavern_events` 只有 14/82 个常量；缺失的多为 ST 内部事件，但卡里访问 `tavern_events.XXX` 会拿到 `undefined`。
- **值不一致**：`GENERATION_AFTER_COMMANDS` 本地是 `generation_after_commands`，上游是 `GENERATION_AFTER_COMMANDS`。
- `iframe_events` 本地值是 `iframe_generation_*`，上游是 `js_generation_requested` / `js_generation_started` / `js_generation_ended`；还缺 `STREAM_TOKEN_RECEIVED_FULLY`、`STREAM_TOKEN_RECEIVED_INCREMENTALLY`。
- `eventOn` / `eventOnce` / `eventMakeFirst` / `eventMakeLast` 上游返回 `{ stop() }`，本地返回 `undefined`；依赖 `const { stop } = eventOn(...)` 的脚本会崩。
- `eventEmit` 上游返回 `Promise<void>`，本地是同步 `void`。
- 缺 `eventOnButton`、`eventEmitAndWait`、`eventClearEvent`、`eventClearListener`、`eventClearAll`。
- `eventMakeFirst` / `eventMakeLast` 本地与普通注册同序，没有真正的插队语义。

## 实际卡验证：《红楼风月录》

自动扫描 12 个作者脚本命中 16 个 API 名字。人工核对后：

- `builtin` 是按钮 id 的误命中；
- `generate` 是 `generateRaw` 的子串误命中；
- 真正缺失的只有 **`setChatMessages`（2 处）**。

其余 `waitGlobalInitialized`、`injectPrompts` / `uninjectPrompts`、`getVariables`、`getChatMessages`、`getLastMessageId`、`updateWorldbookWith`、`getCharWorldbookNames`、`getChatWorldbookName`、`insertOrAssignVariables`、`replaceVariables`、`generateRaw`、`stopGenerationById` 均已有入口。

## 建议顺序

1. 先修事件 API 的返回值、常量值和 `setChatMessages`（改动小，直接解卡）。
2. 补 `generate` 全局别名、展示消息 API、角色只读信息。
3. 再评估世界书条目、预设、正则、persona。
4. audio / file / extension / import_raw 属于宿主管理能力，建议明确标记为“不支持”，而不是逐个模拟。
