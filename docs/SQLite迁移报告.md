# SQLite 迁移报告

日期：2026-11-25 / 2026-11-26
分支：`feature/sqlite-storage`（基于 `fd1994b`）
范围：除 `config/config.json` 外，把 FlyAI 运行期的 JSON / JSONL 数据存储全部换成 SQLite。

## 1. 目标与结论

目标是解决 JSON 存储的三个结构性问题：

1. **写放大**：每次 `save_merged()` 都要整份读取、解析、自愈、再整份写回；群聊里每个发言座位都会触发一次，文件越大越慢，同步写还会阻塞 SSE。
2. **冷启动与列表**：`list()` 要逐个 `json.load` 所有会话文件。
3. **搜索**：消息搜索把全部会话解析成内存文本缓存，没有上限，2GB 内存的机器有 OOM 风险。

迁移后：

- 会话追加一条消息只写一行（+ FTS 索引），不再重写整份文件；
- 会话列表只读元数据列；
- 中文搜索走 FTS5 trigram，<3 字回退 `LIKE`；
- 用户、设置、内容库、市场、扩展状态、提示词归档、存档点、计费流水都有独立库；
- 旧数据在首次打开时自动导入，源文件改名 `.bak-<时间戳>`，可回滚。

`config/config.json` 按你的要求保持 JSON，未做任何迁移。

## 2. 改动范围

### 2.1 新增的共享层

`flyai/sqlite_store.py`

- `SqliteDB`：线程本地连接；`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=10000`；`isolation_level=None` 自动提交，需要事务时显式 `BEGIN`。
- `JsonTable`：`k TEXT PRIMARY KEY, doc TEXT, updated_at REAL` 的单表文档存储，覆盖 `get/put/delete/exists/keys/items/values/count/clear`。
- `migrate_legacy_file()` / `migrate_legacy_dir()`：导入旧 JSON，成功后将源文件改名为 `.bak-YYYYmmdd-HHMMSS`；坏 JSON 跳过且保留原文件。
- `backup_path()`：统一备份命名。

### 2.2 数据库清单

| 路径 | 表 | 内容 |
|---|---|---|
| `data/users.db` | `users` | 账号、密码哈希、等级、额度 |
| `data/auth_sessions.db` | `sessions` | 登录令牌（重启不掉登录态） |
| `data/cdk.db` / `invites.db` / `afdian.db` | `codes` / `orders` | CDK、邀请码、爱发电订单 |
| `data/<uid>/sessions/sessions.db` | `sessions` / `threads` / `messages` / `messages_fts*` | 会话、线程、消息、全文索引 |
| `data/<uid>/settings.db` | `settings` | 个人设置（单行） |
| `data/<uid>/lore.db` | `characters` / `worlds` | 角色卡、世界观 |
| `data/shared/lore.db` | `characters` | 共享角色卡（只读来源） |
| `data/<uid>/memory.db` | `memory` / `ledger` | 角色记忆簿与记忆台账 |
| `data/<uid>/presets.db` | `presets` | 预设 |
| `data/market/market.db` | `entries` / `payloads`（BLOB） | 市场条目元数据与原始载荷 |
| `data/extensions/extensions.db` | `registry` / `records` / `user_state` | 扩展注册、安装记录、用户开关 |
| `data/template_vars/template_vars.db` | `vars` | ST 模板全局变量 |
| `data/<uid>/prompts.db` | `turns` | 提示词归档 |
| `data/<uid>/savepoints.db` | `savepoints` / `savepoint_snapshots` | Galgame 存档索引与快照 |
| `data/<uid>/usage.db` | `usage` | token / 搜索 / TTS / ASR 计费流水 |
| `data/admin/agent/agent.db` | 17 张表 | 原本就是 SQLite：心情、自我记忆、主动行为、作业、承诺、watcher、MCP 等 |

扩展安装包、角色卡附件、上传文件、导出 zip、静态资源仍是文件，不入库；`config/config.json` 仍是 JSON。

### 2.3 会话存储设计（核心）

`flyai/sessions/sqlite_store.py`

- `save()`：`session_to_dict()` 后拆出 threads/messages。
  - `sessions` 行 UPSERT；
  - 每个 thread 一行（doc 去掉 messages）；
  - 每条消息按 `sha1(compact json)` 与库内 `hash` 比较，只 `INSERT/UPDATE` 变化的行，删除已不存在的行，并同步 FTS（先删后插）；
  - 顶层旧格式消息以 `thread_id=""` 入库，只用于搜索；`get()` 时照旧 `bootstrap_room()` 生成窗口。
- `get()`：读 doc + threads + messages 重建，随后执行与旧实现相同的 `bootstrap_room/heal_thread/migrate_actor_ids`，有迁移就回写。
- `list()`：只读 `sessions` 元数据列，`updated_ts` 倒序、置顶优先，`messages=[]`，不再解析每个文件。
- `message_search()`：≥3 字符用 FTS5 `MATCH`；<3 字符或 FTS 不可用回退 `search_text LIKE ... ESCAPE '\'`。`search_text` 为正文 + 墓碑正文 + 角色名，中文 2 字也能命中。
- `stat()`：`mtime_ns=int(updated_ts*1e9)`、`size=doc_size+Σ(LENGTH(doc))`。
- 旧 `data/<uid>/sessions/*.json` 首次打开自动导入并改名；坏 JSON 同样改名留档，不阻塞启动。

## 3. 迁移策略

- **懒迁移**：每个库在所属 store 首次构造时导入对应旧文件/目录，不写一次性全量迁移脚本，避免启动时长时间扫描。
- **幂等**：导入前用 `exists(key)` 防覆盖；源文件已改名，二次启动不会重复导入。改名失败（只读目录）也会因主键存在而跳过重复写入。
- **可回滚**：源文件统一改名 `.bak-<时间戳>`；回滚时停服、还原代码、把 `.bak-*` 改回原文件名即可。
- **坏数据**：坏 JSON 跳过并保留原名，下次启动重试；会话旧文件的坏 JSON 也会改名，避免每次启动重复解析。

本次线上实际观察到的迁移：

- `data/auth_sessions.json` → `data/auth_sessions.json.bak-20261125-173229`；
- `data/admin/characters/b6cec96b321c41988c2a8a3cdf0bd174.json` → `.bak-20261125-173314`，并成功导入 `data/admin/lore.db`（1 个角色）；
- 迁移后 `data/` 下已无遗留 `.json` / `.jsonl` 数据文件。

## 4. 性能对比（本机 t4g.small，真实 `JsonSessionStore` 基准）

单会话 400 条消息、746 KB；120 个会话共 5.6 MB：

| 操作 | JSON | SQLite | 倍数 |
|---|---|---|---|
| 追加 1 条消息 | 12.8 ms（整份写） | 0.02 ms（WAL 追加） | ~640× |
| `save_merged` 等价（读+写） | 19.0 ms | — | |
| 读取整个会话 | 5.9 ms | 0.58 ms（最近 200 条） | ~10× |
| 列表冷启动（120 会话） | 36.7 ms | 0.17 ms | ~200× |
| 全文搜索冷缓存 | 53.1 ms | 0.02 ms（FTS5 trigram） | >1000× |

另外 `indent=2` 的 JSON 改紧凑可再省约 16% 体积，但那是旧方案；现在会话不再整份重写。

说明：聊天端到端延迟主要受模型影响，小会话几乎无感；收益集中在大会话/群聊、会话列表、搜索、冷启动、内存占用和原子性。

## 5. 测试

- 全量：`2057 passed, 5 failed`。
- 5 个失败均为环境问题，已在迁移前的 HEAD worktree 复现，与本次改动无关：
  - `test_enter_send_logic`、`test_galgame_replay_logic`：仓库里没有旧版 `flyai/web/static/js/{ui,galgame}.js`；
  - `test_proactor::test_get_zone_fallback_and_fixed_offset`：本机 `TZ=Asia/Shanghai`，用例默认 UTC；
  - `test_trigger_watchers` 两例：子进程 kill 后未在超时内退出。
- 新增覆盖：
  - `tests/test_sqlite_session_store.py`：增量写、列表、搜索（含中文 2 字/3 字、名字命中、删除失效）、删除/stat、旧 JSON 导入幂等、顶层旧消息兼容。
  - `tests/test_sqlite_store_migration.py`：lore/共享卡/memory/台账/presets/market 的导入、坏数据、只读目录、重开幂等。
  - 现有测试中依赖 JSON 文件布局的断言改为 DB 行数或 `.bak` 存在性。

## 6. 部署与线上验证

- 分支：`feature/sqlite-storage`，5 个提交：
  - `2a1598a` feat(storage): add SQLite core and session store
  - `6b08ce4` feat(storage): migrate users, settings, lore, market and assets to SQLite
  - `60b96ec` test(storage): update suites for SQLite backends
  - `cb3306e` docs: describe SQLite data layout
  - `a15c6c7` feat(storage): persist usage ledger in SQLite
- 部署前已备份整个 `data/`：`/home/ubuntu/flyai-data-backup-20261125-173226.tar.gz`（24 MB）。
- `systemctl restart flyai.service` 后：
  - 服务 active，`https://ai.syd.eastcloud.top/` 与 `/login` 均 200；
  - 管理员登录成功；
  - 新建会话 → 列表可见 → 搜索命中 → 删除成功；
  - 新建会话后重启服务，会话仍在（SQLite 持久化验证通过）；
  - `/api/my/usage` 200，`usage.db` 自动创建。

## 7. 风险与限制

1. **多进程**：WAL 允许并发读、单写；但部分 store（如 accounts）保留“内存缓存 + 全量同步”的写法，多 worker/多实例同时写同一库会互相覆盖。当前 systemd 单进程部署没有该问题；要横向扩容需改成每次操作直读库或加进程级锁。
2. **会话保存仍是 read-modify-write**：`save_merged()` 先 `get()` 再 `save()`，SQLite 只是把“整份文件重写”换成“按行 diff + 整份重建对象”，群聊每个座位仍有 2× 读放大；后续可改为就地追加。
3. **FTS 索引体积**：trigram 索引通常比正文大数倍；`data/` 目前 38 MB，可接受，GB 级语料要评估磁盘。
4. **缓存 md5 变化**：角色卡/世界观的 md5 从“文件字节”变成“库内文档字节”，前端缓存会一次性失效，之后稳定。
5. **备份/回滚**：`.db` 需要连同 `-wal`/`-shm` 一起备份，或先 `PRAGMA wal_checkpoint(TRUNCATE)`；回滚依赖 `.bak-*` 文件，务必在清理磁盘前确认迁移正确。
6. **媒体与安装包仍在文件系统**：上传图、角色卡附件、扩展代码、市场 zip 载荷仍按路径存；市场条目的 BLOB 已入库（`payloads` 表）。
7. **单用户数据删除**：`delete/clear` 走 DB 行删除；WAL 不会立即缩小文件，需要时可 `VACUUM`。

## 8. 运维速查

```bash
# 备份（建议停服或先 checkpoint）
sudo systemctl stop flyai.service
sqlite3 data/users.db 'PRAGMA wal_checkpoint(TRUNCATE);'
tar czf ~/flyai-data-$(date +%F).tar.gz -C /home/ubuntu/flyai data
sudo systemctl start flyai.service

# 查看会话数 / 消息数
sqlite3 data/admin/sessions/sessions.db \
  'select (select count(*) from sessions), (select count(*) from messages);'

# 搜索测试（FTS）
sqlite3 data/admin/sessions/sessions.db \
  "select session_id, snippet(messages_fts,0,'[',']','…',10) from messages_fts where messages_fts match '关键词' limit 5;"

# 找回旧 JSON：停服后把 .bak-* 改回原名，再回滚代码
```

## 9. 后续建议

1. `save_merged` 改为按消息增量写入（去掉 get+save 的 2× 读）。
2. 账号类 store 去掉内存缓存，直接以 DB 为准，支持多进程。
3. 给 `data/` 加定期 `VACUUM`/备份脚本（systemd timer）。
4. 若未来要支持跨用户全文搜索，可把 `messages_fts` 提到独立搜索库，按 uid 分片。
