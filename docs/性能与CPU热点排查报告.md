# FlyAI 性能与 CPU 热点排查报告

- 排查日期：2026-11-23
- 排查范围：`flyai/`（后端）、`webapp/src/`（前端）、`data/`（真实会话数据）
- 排查方式：代码静态分析 + 对本机 `data/` 真实会话文件的只读微基准测量
- **本次未修改任何业务代码**
- 结论基于单进程 uvicorn（`main.py` → `uvicorn.run(..., reload=False)`，无 `workers`）的部署形态；线上 `/root/flyai` 单实例运行时应同样成立

---

## 0. 摘要

本项目当前最突出的 CPU 开销不是某个算法，而是**「会话整文件 JSON 读写 + 整会话下发 + 全量 gzip」的放大链路**：

- 旧会话文件达 **30.5 MB**（`tavern_helper` 内嵌）；新代码重存后同一会话降到 **11.6 MB**，而其中真正的对话内容只有 **47 KB**；
- 每发一条消息，服务端至少完整读写该文件 2 次以上，客户端再拉取一次完整会话；
- 每次读取要 `json.load` 十几到三十 MB（≈100–200ms），每次保存同样量级，每次下发还要 gzip L9 压缩（≈1–1.9s）；
- 这些 CPU 绝大部分是**同步代码直接跑在事件循环里**（单进程单事件循环），会阻塞所有用户的 SSE 流式输出。

其次是每轮消息处理里三个「O(历史/条目数)」的同步计算：自动回忆 BM25、世界书触发词模糊匹配、整卡签名。

优先级与实测成本：

| 优先级 | 位置 | 问题 | 实测成本 |
|---|---|---|---|
| P0 | `flyai/sessions/json_store.py:170,253` | 会话整文件 `json.load` / `json.dump` | 30MB 文件读/写各 ≈150–200ms |
| P0 | `flyai/sessions/store.py:37` | `save_merged` = 先 `get` 再 `save`，单次落盘成本翻倍 | 单次 ≈300–400ms |
| P0 | 会话文件结构 | `character_override` 与 `members[].override` 完全重复；卡内 `regex_scripts` 与 `extensions.regex_scripts` 也重复 | 9 条消息 → 11.6MB（旧文件 30.5MB） |
| P0 | `flyai/app.py:613` | GZip 默认 `compresslevel=9`，不过滤 content-type | 30MB JSON 1.5–1.9s；23MB 图片 1.1s |
| P1 | `flyai/api/routes_chat.py:456` | 自动回忆 BM25 每轮对全部历史重新分词（默认开启） | 1000 条 ≈238ms，2000 条 ≈496ms |
| P1 | `flyai/lore/trigger.py:170` | 世界书触发词模糊匹配逐字滑窗 + `SequenceMatcher` | 300 条目 ≈180ms，500 条目 ≈311ms |
| P1 | `flyai/user.py:546` | `_get_querier` 每轮用整卡 `json.dumps(sort_keys=True)` 算缓存 key | 大卡 ≈120ms/轮/座位 |
| P2 | `flyai/agent/loop.py:426` | 每个工具轮重跑 ST 正则、宏展开、MVU 剥离、模板剥离 | 历史条数 × 脚本数 × 轮数 |
| P2 | `webapp/src/services/chat/db.ts:84` | 每次落盘把整个会话结构化克隆进 IndexedDB | 十几 MB 会话每次百 ms 级 |
| P2 | `flyai/proactor/*` | 调度器每 30s 扫全部用户目录并开 sqlite（当前配置关闭） | 随用户数线性增长 |
| P3 | `flyai/sandbox/core.py:90` | 每次写文件都 `rglob("*")` 统计配额 | 随工作区文件数线性增长 |

---

## 1. 环境与数据现状

### 1.1 运行形态

```python
# main.py
uvicorn.run("flyai.app:app", host=cfg.server.host, port=cfg.server.port, reload=False)
```

- 单进程、单事件循环；
- 同步路由（`def`）由 FastAPI 线程池执行，但 `json` 编码/解码与 `zlib` 压缩基本持有 GIL，仍会挤占事件循环；
- 异步路由中的同步调用（如 `sc.store.save_merged(...)`）**直接阻塞事件循环**。

### 1.2 本机会话数据

> 测量期间本机服务在运行，同一个文件被新代码重写后从 34.9MB 降到 11.6MB。两种状态都存在：旧文件未再保存前仍是 30MB+（`tavern_helper` 内嵌），新写入的文件已由 `strip_heavy_extensions` 剥离该字段。下面的拆解以**当前代码写入后的稳态**为准。

```
data/ 下 sessions/*.json：92 个文件，合计 143 MB
其中 >5MB 的 7 个（3 个旧文件约 30.5MB），>2MB 的 9 个
```

一个 9 条消息的会话（`data/admin/sessions/4e07830a888c43debc6d5b01fa39a18d.json`，当前 11.6MB）：

```
messages（9 条，thread 内）   0.047 MB
character_override           4.59 MB
members[1].override          4.59 MB   ← 与 character_override 完全相等
world_override               2.70 MB
  └ 其中 table[]             2.70 MB
character_override 内部：
  regex_scripts[]            0.94 MB
  extensions.regex_scripts[] 0.94 MB   ← 与顶层 regex_scripts 完全相等
```

结论：

1. `tavern_helper` 已在落盘/下发时剥离（`flyai/sessions/resolve.py:31 strip_heavy_extensions`），**这部分旧问题已修**，旧文件重存后会自动缩小；
2. 但**整卡仍被存了两份**（`character_override` 与 `members[].override` 完全相同），约 4.6MB；
3. 卡内 `regex_scripts` 与 `extensions.regex_scripts` 完全相同，再乘上两份，约 1.9MB；
4. `world_override` 是一份完整的世界书副本（2.7MB），即使未修改也全量保存。

所以现在 11.6MB 的会话里，真正属于对话内容的只有 **约 47KB**。

---

## 2. P0：会话整文件读写链路

### 2.1 代码路径

| 位置 | 行为 |
|---|---|
| `flyai/sessions/json_store.py:115` `session_to_dict` | 全量序列化 `messages`、`character_override`、`members[].to_dict()`、`threads` 等 |
| `flyai/sessions/json_store.py:170-175` `get` | 每次请求都 `json.load` 整个文件，无内存缓存 |
| `flyai/sessions/json_store.py:253-262` `save` | 每次 `json.dump(..., ensure_ascii=False, indent=2)` 整个文件 + 写十几到三十 MB |
| `flyai/sessions/store.py:37-44` `save_merged` | **先 `self.get()` 再从磁盘回填字段，然后 `self.save()`** |
| `flyai/api/routes_chat.py:1145` | 每次 `submit_chat` 先 `sc.store.get(req.session_id)` |
| `flyai/api/routes_chat.py:1423,1669,2666,2719,2761,3019` 等 | 一轮聊天至少 2–4 次 `save_merged` |
| `flyai/api/routes_session.py:197-226` `GET /sessions/{sid}` | 读取全量会话并下发（客户端每轮 `done` 后会重新拉取） |

### 2.2 实测

对 30.5MB 的旧文件（新代码重存后约 11.6MB）：

```
json.load                 201 ms
json.dumps（紧凑）         184 ms
gzip L9                   1851 ms   （L6 1624ms / L1 1026ms）
```

一条消息在大体量会话上的估算纯 CPU：

- `submit_chat` 读会话：≈200ms
- 至少 2 次 `save_merged`：2 ×（200ms 读 + 200ms 写）≈ 800ms
- 客户端 `done` 后重新 GET：200ms 读 + 200ms 序列化 + **1850ms gzip** ≈ 2.25s

合计 **≈3.3 秒 CPU / 条消息**（还没算磁盘写 70MB+、传输 20MB）。

### 2.3 为什么会被放大

1. 会话文件里卡数据重复（见 1.2）；
2. `save_merged` 每次都要先把磁盘上的旧文件完整读一遍；
3. 无内存缓存/脏标记，读盘是常态；
4. 响应又被 gzip L9 全量压缩一次。

### 2.4 建议方向（未实施）

- 卡数据去重：`members[].override` 与 `character_override` 只保留一份，或按卡 id/内容哈希引用；`tavern_helper` 这类只应在打开卡时下发一次的字段不要进会话副本；
- 会话对象加进程内缓存（按 `(mtime_ns, size)` 或显式脏标记），消除「读一次只为改一个字段」；
- `save_merged` 改为「读元数据合并」或引入 `MERGED_FIELDS` 的独立小文件，避免整文件读；
- 保存去抖/写后台线程（`asyncio.to_thread`），至少不要阻塞事件循环；
- 客户端 `done` 后不要整会话重拉，改为增量帧或 ETag 条件请求。

---

## 3. P0：GZip 中间件按 L9 压缩一切

### 3.1 代码

```python
# flyai/app.py:613
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=1024)
```

Starlette 0.41.3 的 `GZipMiddleware` 默认 `compresslevel=9`；我核对了 venv 里 `GZipResponder` 的源码，它**只跳过已带 `content-encoding` 的响应，不判断 content-type**。

### 3.2 受影响的响应

| 资源 | 实测 L9 成本 | 说明 |
|---|---|---|
| `/static/js/main.*.js`（2.17MB） | 157ms | L1 仅 31ms，压缩率几乎相同 |
| `/uploads/*.png` 8.9MB | 300ms | 图片不可压 |
| `/uploads/*.png` 23MB | 1078ms | 图片不可压 |
| 30MB 会话 JSON | 1851ms | L1 为 1026ms |
| 各 API JSON（KB 级） | 可忽略 | 但仍在占用 CPU |

注意 `/static` 被 `no_cache_app_shell` 设成 `no-cache`（每次使用前校验），命中 304 时无 body 不压缩；但只要发生一次 200 响应（部署后首次、缓存淘汰），就会按 L9 重新压。

### 3.3 建议方向

- `compresslevel` 降到 1–6（对文本压缩率影响很小）；
- 对 `/uploads/`、图片/音频/视频/二进制 MIME 直接跳过压缩；
- `/static` 预压缩或给长缓存（文件名带 hash 的产物可以 immutable）。

---

## 4. P1：每轮消息处理中的同步 CPU

> 以下三项均在 `async def` 请求处理链中同步执行，会阻塞事件循环。

### 4.1 自动回忆 BM25（默认开启）

- 入口：`flyai/api/routes_chat.py:456 build_auto_recall_block`
- 调用点：单聊 `routes_chat.py:1467`；群聊每个座位一次 `routes_chat.py:2345`（在 `2180` 的 `for spk in speakers` 循环内）
- 用户设置默认值：`flyai/user.py:300` `auto_recall: bool = True`
- 实现：`flyai/memory/recall.py:60 hybrid_rank` → `flyai/memory/bm25.py:27 tokenize`（对每个字符调用 `_is_cjk`，Python 级循环）

实测（无 embedder，纯本地 CPU）：

```
200 条历史    54 ms
500 条历史   127 ms
1000 条历史  238 ms
2000 条历史  496 ms
```

群聊 N 个座位即 ×N。会话越长越慢，没有上限。

建议方向：候选截断到最近 N 条、按会话缓存分词/BM25 索引（追加消息时增量更新）、或下沉到 `to_thread`。

### 4.2 世界书触发词匹配

- 入口：`flyai/lore/query.py` 的 `trigger_block` → `flyai/lore/trigger.py:170 keyword_hits`
- 调用点：`routes_chat.py:1467-1476`（单聊）、`2352`（群聊每座位）
- `flyai/lore/trigger.py:55 fuzzy_hit`：精确子串未命中时，对消息尾部 `_SCAN_TAIL=800` 字做逐段、逐位置滑窗比对；4 字以上再叠加 `SequenceMatcher`
- 每轮还会传入**全量历史**作为 `scan_texts`（`routes_chat.py:1470`、`2374-2376`），且每个配置了 `scan_depth` 的条目都会重新 `"\n".join(scan_texts[-depth:])`

实测：

```
300 条目        179.9 ms
300 条目 + depth 178.8 ms
500 条目 + depth 310.9 ms
单次 fuzzy_hit 未命中 ≈0.58 ms
```

触发词越长、条目越多、历史越长，成本越高；命中通常只有几条，绝大部分成本花在「未命中」的模糊扫描上。

建议方向：对模糊匹配做字符集合/前缀预筛；只对精确分 Top-K 的条目做模糊补充；`scan_depth` 的合并文本每轮预计算一次而不是每条目重建；大表时考虑放到线程池。

### 4.3 整卡签名 `content_signature`

- 入口：`flyai/user.py:546 get_querier`
- 实现：`flyai/lore/diff.py:15`
  ```python
  blob = json.dumps(d, ensure_ascii=False, sort_keys=True)
  return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
  ```
- 每轮对有效角色卡（含上 MB 的脚本/正则/世界书）做一次全量 `json.dumps(sort_keys=True)`

实测对该卡：**≈120 ms/轮/座位**。缓存本身命中率很高，但算 key 的成本与命中无关。

另外 `flyai/sessions/resolve.py:207 room_base_signatures` 也对每个座位副本做同样计算。

建议方向：签名排除 `tavern_helper` 等大字段、或按对象身份/更轻的版本号缓存；`sort_keys=True` 可改为对稳定结构直接 dump。

---

## 5. P2：其它可观测开销

### 5.1 每个工具轮重建请求

`flyai/agent/loop.py:426 build_request` 每次工具轮都会重新执行：

- `_actor_view`（群聊视图复制）
- `raw_content` 替换、`strip_template_markers`
- `apply_to_messages`（`flyai/st/regex_scripts.py`）：对**每条历史消息 × 每个正则脚本**执行 `re.sub`
- `strip_update_blocks`（MVU 会话）
- @depth 块注入、系统提示词拼接

历史 200 条 × 卡上几十个脚本 × 多轮工具，会累积到几百 ms 级。建议对已处理过的历史前缀做缓存（只处理新增尾部）。

### 5.2 前端

- **首包**：`flyai/web/static/js/main.00ba199a.js` 2.17MB（未压缩），低端手机解析成本明显；可按面板/功能拆包懒加载。
- **流式渲染**：`webapp/src/services/chat/store.ts:406 updateReply` 每帧 `previous.messages.map(...)`，`MessageList` 每帧 `buildMessageVersions`（`webapp/src/services/chat/branches.ts:40`）遍历全部消息。已有 rAF 合并与 Markdown 节流，问题不严重，但会话很长时仍是 O(n)/帧。
- **落盘**：`webapp/src/services/chat/db.ts:84 saveSession` 每次把**整个会话**（含 base64 图片）结构化克隆写入 IndexedDB，`PERSIST_DELAY=400ms` 去抖。大会话每次落盘都是明显的主线程/内存开销。
- 卡沙箱内的 `setInterval`（如 `cardApp.ts:1708` 250ms 的 composer 同步）依赖卡本身脚本，属外部因素。

### 5.3 调度器

- `flyai/config.py:844` 默认 `tick_seconds = 30`；当前 `config/config.json` 中 `scheduler.enabled = false`
- 若线上开启：`JobTicker` 每 30s 扫描 `data_dir.iterdir()` 并对每个用户打开 sqlite（`scheduler_jobs.py:181`）；`ReminderTicker`（`reminders.py:118`）与 `ProactorTicker`（`proactor.py:230`）同样全量扫用户
- 用户数多时会形成持续的后台 CPU/IO 负载；建议只扫描有作业的用户（索引/缓存集合）而不是每 tick 全目录列举。

### 5.4 其它

| 位置 | 问题 |
|---|---|
| `flyai/sandbox/core.py:90 _dir_used` | 每次写文件都 `rglob("*")` 全树 stat，工作区文件多时写一次卡一次 |
| `flyai/api/routes_session.py` `batch_delete_sessions` | 每个 id 先 `store.get()`（全量十几 MB 解析）只为判断存在性，`delete` 本身不需要 |
| `flyai/sessions/json_store.py:195 list` | 首次（或任何会话文件变更后）要完整解析每个会话文件才能生成摘要；大会话目录首次列表会比较慢，之后靠 `(mtime_ns, size)` 缓存 |
| MCP 媒体清扫 / watcher reconcile | 启动时全量扫用户目录，属一次性开销 |

---

## 6. 顺带发现的功能回退（非 CPU）

`GET /api/sessions/search`（`flyai/api/routes_session.py:76`）遍历 `sc.store.list()` 返回的对象并读取 `s.messages`，但 `JsonSessionStore.list()`（`json_store.py:195`）返回的是**摘要**，`messages` 恒为 `[]`。

因此该接口实际只能按**标题**命中，消息正文/墓碑文本永远搜不到。前端 `searchSessionList`（`webapp/src/services/chat/store.ts:1656`）使用的正是这个接口。

---

## 7. 建议的修复顺序（按投入产出比）

1. **GZip**：降级到 L1–L6，并对 `/uploads`、图片/二进制 MIME 跳过压缩。预计可省掉每次大响应的 1–2 秒。
2. **会话文件去重**：去掉 `members[].override` / `character_override` 的重复存储与 `extensions` 内重复字段，示例会话可从 11.6MB 降到约 3.7MB。
3. **会话读写优化**：进程内缓存 + 脏标记写盘，去掉 `save_merged` 的「先 get」；写盘与序列化下沉到 `to_thread`。
4. **自动回忆**：候选截断 + 分词/BM25 索引缓存（增量更新）。
5. **世界书触发**：模糊匹配预筛、`scan_depth` 文本预合并、必要时移出事件循环。
6. **`content_signature`**：排除大字段或改用轻量版本号。
7. **前端**：会话落盘改为增量、首包拆包懒加载。

---

## 8. 如何在线上进一步确认

本次结论来自静态分析与本机数据微基准，建议在线上做一次只读采样确认：

```bash
# 1. 实时看热点（需安装 py-spy）
py-spy top --pid <main.py 的 pid>
# 2. 拿到栈快照
py-spy dump --pid <pid> --locals
# 3. 观察是否有持续 CPU 的线程（调度器/JSON）
ps -L -p <pid> -o pid,tid,pcpu,comm
```

预期：`json.load` / `json.dump` / `zlib`（gzip）/ `re.sub` / `tokenize` / `SequenceMatcher` 会成为高频栈顶。

---

## 9. 与 SillyTavern 传输方式的对比及带宽优化方案

### 9.1 SillyTavern 也是「全量发送」，但它的全量很小

依据本仓库 `docs/SillyTavern服务端接口清单.md`（上游 ST 1.19.1）：`/api/chats` 共 13 条路由——

```
delete / export / get / import / recent / rename / save / search
group/delete / group/get / group/import / group/info / group/save
```

没有 append / patch / 增量接口，也就是说：

- 打开聊天：`POST /api/chats/get` 返回**整份 chat 数组**；
- 保存聊天：`POST /api/chats/save` 提交**整份 chat 数组**，服务端把 JSONL 整体重写。

所以「每次全量发送」本身是 ST 的行为。但 ST 的全量通常只有**几十 KB 到几百 KB**，原因是：

| 数据 | ST 的存放位置 | 是否进入每次全量传输 |
|---|---|---|
| 对话消息 | `chats/<char>/<file>.jsonl`（纯文本，一行一条） | 是（全量） |
| 角色卡 | `characters/*.json` / `.png`，按 avatar 单独取 | 否（另接口按需取） |
| 世界书/预设 | 独立目录/文件 | 否 |
| 图片 | `/api/images/upload` 落盘，消息里存路径/URL | 否（只走图片 URL） |
| `chat_metadata` | chat 文件头部 | 是，但很小 |

一句话：**ST 的「全量」是文本全量；FlyAI 的「全量」是把整张卡再抄两遍。**

### 9.2 我们的十几 MB 会话不是「兼容 ST」造成的

- FlyAI 的 `/api/sessions/*` 是自有协议；`/api/st/*` 也只是自有代理接口。ST 兼容发生在**卡沙箱 / 作者运行时**这一层，不要求会话接口按 ST 形状传输；
- 实测一个 9 条消息的会话（当前代码写入后的稳态，11.6MB）：

```
messages（9 条）              0.047 MB
character_override            4.59 MB
members[1].override           4.59 MB   ← 与 character_override 完全相等
world_override                2.70 MB   ← 世界书全量副本，即使未修改
character_override 内部：
  table[]                     2.70 MB
  regex_scripts[]             0.94 MB
  extensions.regex_scripts[]  0.94 MB   ← 与顶层 regex_scripts 完全相等
```

- 旧文件（未再保存过）仍是 30.5MB：`tavern_helper` 脚本内嵌其中。现有代码已在落盘/下发时用 `strip_heavy_extensions` 剥离该字段，所以旧文件重存后会自动缩小；但上面列出的三类重复**仍然存在**。

结论：**带宽问题出在会话数据模型（重复存卡），不是 ST 兼容层**。即使为了兼容保留 ST 的「全量语义」，也可以在传输层去重。

### 9.3 带宽优化方案（按收益排序）

#### A. 数据去重（一次性改造，收益最大）

1. **会话不再保存整卡**，二选一：
   - 方案 A1（推荐）：只存 `{card_id, card_digest, patch}`，`patch` 仅包含与主卡的差异字段；未做副本编辑时 `patch` 为空；
   - 方案 A2（改动最小）：`character_override` 与 `members[].override` 只保留一份（成员引用会话级 override），并从会话副本剔除与顶层完全相等的 `extensions.regex_scripts`。
2. `tavern_helper` 的剥离已经做了（`resolve.py:31`，落盘 `json_store.py:120,136`、下发 `routes_session.py:1669,1703`）；**剩下的重复是 override 双份与 regex_scripts 双份**，需要在序列化层处理。
3. **卡包与脚本按 digest 单独取**：新增 `GET /api/sessions/{sid}/card-bundle?digest=<md5>`，响应可 `Cache-Control: public, max-age=31536000, immutable`（digest 命中就不重复下载）；`_st_payload` 里的 `st.author.scripts`（最大 5MB）不应每次打开会话都随响应下发。
4. `world_override` 未修改时只返回 `world_id` + digest。

预期：示例会话 11.6MB → **约 3.7MB**（一份卡 3.65MB + 消息 47KB；世界书按引用）；若世界书确实被修改则再 +2.7MB。旧 30.5MB 文件重存后同样按此收敛。

#### B. 图片出 JSON

- 用户上传的图片目前以 `[image omitted]` 内联在消息里；
- 改为上传即落盘：`/uploads/<uid>/<sha256>.webp`，消息只存 `{url, thumb_url, w, h, sha256}`；
- 历史会话读取时懒迁移（读到旧 base64 时转存并改写引用）；
- 前端消息流用缩略图 + `loading="lazy"` / `IntersectionObserver`，看图器再取原图；
- 卡内脚本素材图（`tavern_helper` 数据里的 webp）由 A 的卡包接口一次性下发并本地缓存。

#### C. HTTP 层

1. **条件请求**：`GET /api/sessions/{sid}` 返回 `ETag: "<rev>"`；客户端带 `If-None-Match`，未变化返回 `304`（0 字节）。
   - `rev` 可用 `updated_at_ns + message_count + card_digest` 组合，避免内容变了但秒级时间戳没变。
   - 当前代码里只有静态资源有 ETag（`app.py:651`），API 响应没有任何条件请求支持。
2. **增量消息接口**：
   - `GET /api/sessions/{sid}/messages?after=<seq>&limit=200`：只返回新消息；
   - `GET /api/sessions/{sid}/messages?before=<seq>&limit=50`：向上翻历史；
   - 每条消息带会话内单调 `seq`，会话元数据带 `last_seq`；
   - 编辑/撤回/删除用 tombstone 或版本表表达，客户端按 `seq` 合并。
3. **元数据与正文分离**：`GET /api/sessions/{sid}/meta`（小）+ 按需 `messages` / `card`。
4. **SSE 已经是增量**：事件带 `seq`，且已实现断线续读（后台任务 + 序号回放），这部分保持现状即可。
5. **压缩策略**：
   - JSON 用 gzip L6（或 nginx brotli，文本体积比 gzip 再省约 15–20%）；
   - 图片/音视频/二进制**跳过压缩**；
   - 带 hash 的静态产物用 `Cache-Control: public, max-age=31536000, immutable` + `gzip_static` / `brotli_static`；
   - nginx 开启 `http2`（多路复用，减少连接开销）。
6. **首屏分页**：打开会话只取最近 N 条（如 50 条），向上滚动再按 `before` 拉取。

#### D. 前端

- 打开会话先渲染 IndexedDB 缓存，再发条件/增量请求（`?after=lastSeq`），未变化直接跳过；
- 落盘改增量：按 `(sessionId, seq)` 分条存储，只写变更，不再整会话 `put`（当前 `db.ts:84 saveSession` 每次结构化克隆整会话）；
- 消息数超过阈值（如 200）时启用虚拟列表；
- 图片懒加载 + 缩略图。

#### E. 如果将来要补 ST 兼容接口

- `/api/chats/get|save` 保持 ST 的全量语义（卡内脚本依赖它）；
- 但服务端在 `save` 收到全量数组后按消息 id/seq 做 diff，只增量落盘；
- `get` 默认全量，FlyAI 前端走扩展参数（如 `?mode=delta&after=`）拿增量；
- 请求/响应带协议版本号，便于以后协商。

### 9.4 收益估算

| 阶段 | 单次打开会话 | 每条消息 |
|---|---|---|
| 现状 | ~30MB 传输 + 约 1.85s gzip | 全量读写 + 全量重拉 |
| A 去重后 | 约 1.2MB（未改副本几 KB） | 量级降两个数量级 |
| + C 条件请求 / 增量 | 稳态 0 字节（304） | 只追加 KB 级 |
| + B 图片外置 | 消息体不含 base64 | 图片按需加载 |

> 实际数值取决于卡的大小、图片数量与历史长度；上述会话可作为极端样本。

### 9.5 一句话结论

**ST 的全量是「文本全量」，FlyAI 的全量是「把整张卡再抄两遍」。** 优化重点应放在会话数据模型（去重 + 图片外置 + 条件/增量接口），而不是模仿 ST 的传输方式；ST 兼容层与这些优化不冲突。

---

## 附录 A：本次使用的测量命令（只读）

### A.1 会话读写成本

```python
import json, time, os
p = "data/admin/sessions/4e07830a888c43debc6d5b01fa39a18d.json"
print("size %.1f MB" % (os.path.getsize(p)/1e6))
t=time.perf_counter(); d=json.load(open(p,encoding="utf-8")); print("load %.0fms"%((time.perf_counter()-t)*1000))
t=time.perf_counter(); json.dumps(d,ensure_ascii=False); print("dump %.0fms"%((time.perf_counter()-t)*1000))
```

### A.2 gzip 等级对比

```python
import gzip,time
raw=open(p,'rb').read()
for lvl in (9,6,1):
    t=time.perf_counter(); out=gzip.compress(raw,compresslevel=lvl)
    print("L%d %.0fms -> %.0f%%"%(lvl,(time.perf_counter()-t)*1000,100*len(out)/len(raw)))
```

### A.3 自动回忆 BM25

```python
import asyncio,time
from flyai.memory.recall import Candidate, hybrid_rank
cands=[Candidate(text="……"*random.randint(40,200), kind="message", position=i) for i in range(n)]
t=time.perf_counter(); asyncio.run(hybrid_rank(cands, "查询文本", embedder=None))
print("%.1f ms"%((time.perf_counter()-t)*1000))
```

### A.4 世界书触发词

```python
from flyai.lore.models import LoreEntry
from flyai.lore.trigger import keyword_hits
entries=[LoreEntry(title="条目%d"%i, content="……", keys=["三字词"]) for i in range(n)]
t=time.perf_counter(); keyword_hits([("world",entries)], msg, scan_texts=history)
print("%.1f ms"%((time.perf_counter()-t)*1000))
```

### A.5 整卡签名

```python
import time
from flyai.lore.diff import content_signature
t=time.perf_counter(); content_signature(card_override_dict)
print("%.0f ms"%((time.perf_counter()-t)*1000))
```

---

## 附录 B：关键文件索引

| 文件 | 关注点 |
|---|---|
| `flyai/sessions/json_store.py` | `get`(170) / `list`(195) / `save`(253) / `session_to_dict`(115) |
| `flyai/sessions/store.py` | `save_merged`(37) |
| `flyai/api/routes_chat.py` | `build_auto_recall_block`(456)、`get`(1145)、多个 `save_merged`、群聊座位循环(2180) |
| `flyai/api/routes_session.py` | `search`(76)、`get_session`(197)、`_session_dict`(1643)、`_st_payload`(1494) |
| `flyai/lore/trigger.py` | `fuzzy_hit`(55)、`keyword_hits`(170) |
| `flyai/memory/recall.py` / `bm25.py` | `hybrid_rank`(60) / `tokenize`(27) |
| `flyai/user.py` | `get_querier`(546) |
| `flyai/lore/diff.py` | `content_signature`(15) |
| `flyai/agent/loop.py` | `build_request`(426)、ST 正则应用(484) |
| `flyai/app.py` | GZip(613)、静态缓存头(619) |
| `flyai/proactor/` | `service.py`、`scheduler_jobs.py`(181)、`reminders.py`(118)、`proactor.py`(230) |
| `flyai/sandbox/core.py` | `_dir_used`(90) |
| `webapp/src/services/chat/store.ts` | `updateReply`(406)、`refreshSession`(2568)、`searchSessionList`(1655) |
| `webapp/src/services/chat/db.ts` | `saveSession`(84) |
| `webapp/src/services/chat/branches.ts` | `buildMessageVersions`(40) |
