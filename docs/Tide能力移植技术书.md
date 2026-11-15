# Tide 尖端能力移植 FlyAI 技术书

> 版本：v1.0（2026-11-13）
> 源项目：`F:/PythonProgram/tide`（大肥鱼 · DeepSeek QQ 群聊 AI 框架，Python 3.12，SQLite + 35 张表）
> 目标项目：`F:/PythonProgram/flyai`（本地优先 AI 聊天平台，Python **3.9.13**，FastAPI + JSON 文件存储）
> 性质：落地设计文档。文中所有接口、表结构、默认值均来自两个项目的真实源码，代码片段为设计草案，可直接作为实现依据。

---

## 目录

1. [背景与约束](#1-背景与约束)
2. [功能对照矩阵：移植什么、不移植什么](#2-功能对照矩阵移植什么不移植什么)
3. [总体架构](#3-总体架构)
4. [P0 基础设施（先行层）](#4-p0-基础设施先行层)
5. [P1 旗舰：AI 自定义工具（自扩展运行时）](#5-p1-旗舰ai-自定义工具自扩展运行时)
6. [P2 长跑模式（无上限长任务 + 安全点打断）](#6-p2-长跑模式无上限长任务--安全点打断)
7. [P3 心情、自我记忆、主动行为与定时任务](#7-p3-心情自我记忆主动行为与定时任务)
8. [P4 生态扩展：MCP、外部触发、监测进程、提示词注册表](#8-p4-生态扩展mcp外部触发监测进程提示词注册表)
9. [Python 3.9 适配清单](#9-python-39-适配清单)
10. [前端改造方案](#10-前端改造方案)
11. [测试策略与验收标准](#11-测试策略与验收标准)
12. [里程碑与工作量](#12-里程碑与工作量)
13. [风险登记册](#13-风险登记册)
14. [附录](#14-附录)

---

## 1. 背景与约束

### 1.1 为什么移植

Tide 在工程上验证了一批「让智能体从对话工具变成长期运行实体」的机制：AI 自己写工具并隔离上线、可打断的无限轮长跑、分层记忆、半衰期心情、概率主动行为、定时/轮询调度。这些能力与 FlyAI 的 Agent 循环、ChatRun 后台运行、多模态工具链天然互补，且可显著强化参赛作品的技术叙事。

### 1.2 硬约束

| 约束 | 来源 | 影响 |
|---|---|---|
| **Python 3.9.13**，禁 match/case、TaskGroup、StrEnum、PEP 604 求值 | 项目强制约束 | Tide 的 `X \| Y` 注解、MCP SDK（要求 ≥3.10）不能直接搬 |
| 持久化**只有 JSON 文件 + 原子替换**，全仓库无 sqlite/SQLAlchemy | 源码事实 | 调度/心情/工具表需要新引入存储；`sqlite3` 是标准库，**不增加第三方依赖** |
| Pydantic 2.10.4 但全程 **v1 API**（`BaseModel`/`@validator`/`parse_obj`/`.dict()`） | `flyai/config.py` | 新配置模型必须沿用 v1 写法 |
| 单进程 uvicorn，无多 worker 部署 | 当前运行方式 | 进程内调度循环可行，但必须加单实例领导锁兜底 |
| 前端静态资源有**三份副本** | `flyai/web/static`、`mobile/www/static`、`mobile/android/.../assets/public/static` | 每个 UI 改动必须同步三份（或走现有 `npm run copy-web`） |
| 现有约 **1015 个测试必须保持全绿** | `tests/` 87 个文件 | 所有改造走增量扩展，不重写既有接口语义 |
| 用户等级 0–255、admin=256，鉴权为手工检查而非 Depends | `flyai/user.py`、`routes_auth.py` | Tide 的「主人 QQ」门禁映射为 admin/等级门禁 |

### 1.3 两个项目的关键差异（映射基准）

| Tide 概念 | FlyAI 对应物 | 映射关系 |
|---|---|---|
| QQ 群（group_id） | 聊天会话（session_id） | 群级作用域 → 会话级作用域 |
| 主人 QQ（owner_qq） | admin 用户 / 高等级用户 | 风险工具门禁 |
| 群锁 group_lock | `ChatRunManager.try_lock_for()` 会话锁 | 长跑复用会话锁 |
| bg_runs 后台留痕 | `ChatRun` + 事件缓存（TTL 600s） | 主动行为直接产出 ChatRun |
| SQLite `db/deepseek.db` | 新增 `data/{uid}/agent/agent.db` | 按用户隔离，符合 UserScope 约定 |
| 六条模型路由 | 单激活 provider | 新增可选「后台轻量模型」provider 引用，缺省回落激活 provider |
| `python -I` worker、Sandbox、InterruptHub、半衰期公式、概率调度 | 无 | 原样移植（纯逻辑部分），替换框架衔接层 |

---

## 2. 功能对照矩阵：移植什么、不移植什么

| Tide 能力 | FlyAI 现状 | 结论 |
|---|---|---|
| CJK BM25（unigram+bigram，k1=1.5/b=0.75，同款 IDF） | `flyai/memory/bm25.py` **已同构实现** | ✅ 不移植，直接复用 |
| 每 N 轮异步批量抽取、相似度三档合并（0.8 就地更新 / 0.5–0.8 软删） | `flyai/memory/extract.py` 已有批量抽取 + `apply_entry_ops` 同阈值逻辑 | ✅ 不移植 |
| BM25+向量混合召回、重要度/近因加权 | `flyai/memory/recall.py` `hybrid_rank()`（RRF，权重 0.45/0.30/0.15/0.10） | ✅ 不移植，且实现更优 |
| 约定/待办登记 | `flyai/memory/ledger.py` 五要素台账，task 有 open→done/cancelled 状态机 | 🟡 存储已具备，**缺到期提醒**（P3 补调度闭环） |
| 对话压缩 | 手动压缩（先 diff 后写入会话副本） | 🟡 长跑需要**自动滚动摘要**（P2 内嵌） |
| **AI 自定义工具 + 隔离子进程** | 无 | ⭐ P1 移植（旗舰） |
| **长跑模式**（无轮限/强制汇报/自动压缩/空转保护/打断/审批） | 有 ChatRun 后台化与双预算，但轮数有硬顶、无打断注入 | ⭐ P2 移植 |
| 工作区沙箱（双根校验、symlink 拦截、配额、public 共享库） | `file_reader` 只读白名单 + resolve 校验，无写能力、无配额 | 🟡 P0 增强为可写沙箱 |
| 执行期风险门禁（RISKY_TOOL_KEYS + owner） | **缺口**：`ToolRegistry.execute()` 不校验 allowed，权限只是模型可见性 | 🔴 P0 必须先补 |
| 通用调度器（jobs/polls/tick 循环） | 无任何周期任务设施 | 🔴 P0 新建 |
| 心情值 + 七绪（半衰期、来源限幅、只宣布一次） | 无 | P3 移植 |
| 第一人称自我记忆 + 每日配额 + 回忆链 | 记忆簿是角色维度，无「AI 自我」视角 | P3 移植（复用现有 BM25/Entry 机制） |
| 主动搭话/定时苏醒（概率表、静默时段、保底） | 无 | P3 移植，**默认关闭、按会话 opt-in** |
| 定时任务 once/daily/weekly | 无 | P3 移植 |
| MCP（stdio/http/sse） | 无；官方 SDK 要求 Python ≥3.10 | P4 自研精简 stdio 客户端，http/sse 走 sidecar |
| 外部触发 HTTP 服务 | 无 | P4 改造为 FastAPI 路由（不用 ThreadingHTTPServer） |
| 监测进程 watcher（detached 子进程 + 回调） | 无 | P4 移植 |
| 提示词注册表（20 分区 + 占位符白名单 + 覆盖） | 角色卡/全局格式指令可覆盖 | P4 可选 |
| 群说话风格画像（raw 语料库 + 定期重建） | 无 | P4 可选，价值偏低 |
| 初筛脚本 screen scripts | 无环境：FlyAI 会话内消息都是定向消息，不存在「 ambient 消息要不要理」的门控问题 | ❌ 不移植 |
| QQ 空间、好友审批、CQ 码、分条发送等 QQ 专用能力 | 不适用 | ❌ 不移植 |

---

## 3. 总体架构

### 3.1 新增包结构

```text
flyai/
├── agent/
│   ├── loop.py              # 改造：loop_ctx、安全点打断钩子、长跑预算
│   ├── runs.py              # 复用：ChatRun/ChatRunManager
│   ├── interrupt.py         # 新增：InterruptHub（会话级安全点插队）
│   └── loopmode.py          # 新增：LoopManager（长跑会话状态机、审批、空转/压缩判定）
├── agentdb/                 # 新增：SQLite 存储层（stdlib sqlite3，零新依赖）
│   ├── __init__.py          # connect() / migrate() / user_version 迁移
│   ├── schema.sql           # 全部 DDL（附录 B 汇总）
│   ├── custom_tools.py      # 自定义工具 DAL
│   ├── mood.py              # 心情状态/时间线 DAL
│   ├── jobs.py              # 定时任务/轮询/承诺 DAL
│   ├── self_memory.py       # 自我记忆 + trace DAL
│   ├── loops.py             # 长跑会话/申请 DAL
│   └── tool_worker.py       # python -I 隔离子进程入口（独立、无项目导入）
├── sandbox/                 # 新增：可写工作区沙箱
│   └── core.py              # Sandbox：双根校验、symlink 拦截、配额
├── proactor/                # 新增：调度与主动行为
│   ├── service.py           # SchedulerService（lifespan 单实例 tick 循环）
│   ├── leadlock.py          # 单实例领导锁（msvcrt/fcntl）
│   ├── mood.py              # 半衰期数学 + 判定/注入状态机（纯函数可测）
│   ├── proactor.py          # 概率苏醒/主动搭话/polls
│   ├── scheduler_jobs.py    # once/daily/weekly 作业
│   ├── reminders.py         # 台账到期提醒
│   ├── watchers.py          # detached 监测进程管理
│   └── triggers.py          # 外部触发冷却与入队
├── mcp_client/              # P4：自研精简 MCP stdio 客户端
│   └── stdio.py             # JSON-RPC 2.0 over stdin/stdout
├── tools/
│   ├── base.py              # 改造：Tool 增 risk/min_level；ToolContext 增 uid/level
│   ├── registry.py          # 改造：execute() 执行期授权
│   ├── custom_tool_manage.py# 新增：custom_tool_validate/publish/list/toggle/remove
│   ├── loop_tools.py        # 新增：loop_request/loop_finish
│   ├── job_tools.py         # 新增：定时/轮询/承诺工具
│   └── mcp_tool.py          # 新增：mcp__<server>__<tool> 动态桥接
└── api/
    └── routes_ext.py        # 新增：/api/ext/trigger 外部触发端点（独立 Bearer）
```

### 3.2 运行时关系

```text
                          ┌──────────────────────────────────────────────┐
                          │              FastAPI lifespan                │
                          │  SchedulerService.start()（领导锁抢占成功才跑）│
                          └───────┬──────────────────┬───────────────────┘
                                  │ 30/60s tick       │ 唤醒事件
                                  ▼                  ▼
                      jobs/polls/reminder      proactor 概率裁决
                                  └──────┬───────────┘
                                         ▼
                      合成隐藏通知 → ChatRunManager.create()
                                         ▼
                      run_agent_loop(loop_ctx=None|{...})
            ┌────────────────────┬──────────────┴───────────────┐
            ▼                    ▼                              ▼
     ToolRegistry          InterruptHub.drain()          CustomFunctionTool
   execute() 执行期授权     （每轮请求模型前的安全点）     → tool_worker 子进程
            │
            ▼
   内置工具 / 插件 / MCP 桥 / custom__* / 沙箱 fs 工具
                                         │
             所有持久化：data/{uid}/agent/agent.db（WAL，user_version 迁移）
```

### 3.3 数据归属

- 每个用户一个库：`data/{uid}/agent/agent.db`，与现有 `data/{uid}/sessions/`、`character-memory/` 的 UserScope 隔离约定一致。
- 备份/迁移随 `data/{uid}/` 整体走；对话导出 `.zip` 未来可追加 agent.db 导出（不在首批范围）。
- 自定义工具的「共享/上架」复用 `flyai/market/` 现有市场模式另做，v1 仅用户内可见。

---

## 4. P0 基础设施（先行层）

P0 不面向用户交付功能，但 P1–P4 全部依赖它，且包含一个**必须修复的既有权限缺口**。

### 4.1 执行期授权层（修复权限缺口）

**现状问题**：`ToolRegistry.specs(allowed)` 只过滤模型可见性；`execute()` 按名查表后直接执行，不重新校验。会话的 `disabled_tools` 与用户等级只影响「模型看不看得到」，模型若硬编一个工具名调用，没有第二道闸。

**改造**（`flyai/tools/base.py`、`registry.py`）：

```python
# tools/base.py
@dataclass
class ToolContext:
    # ...既有字段保持不变...
    uid: str = ""
    level: int = 0
    is_admin: bool = False

class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}
    interactive: bool = False
    # 新增：风险标记与最低等级。默认值保证既有工具行为不变
    risk: bool = False
    min_level: int = 0
```

```python
# tools/registry.py
class ToolRegistry:
    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        tool = self.get(call.name)
        if tool is None:
            return ToolResult(output=f"[工具错误] 未知工具：{call.name}", ok=False)
        # 新增执行期授权（可见性过滤之后的第二道闸）
        if tool.risk and not ctx.is_admin and ctx.level < tool.min_level:
            return ToolResult(
                output=f"[权限不足] 工具 {call.name} 需要等级 {tool.min_level}，"
                       f"当前等级 {ctx.level}；请联系管理员。", ok=False)
        # ...既有超时/异常转 ToolResult 逻辑不变...
```

**衔接**：`run_agent_loop()` 构造 `ToolContext` 时从路由层传入 `uid/level/is_admin`（路由已通过 `_identity(request)` 拿到这些值）。

**门禁分级约定**：

| 级别 | 示例工具 | 判定 |
|---|---|---|
| 普通 | web_search、query_lore、TTS（受各自 min_level 约束） | 现状不变 |
| risk=True，admin only | custom_tool_validate/publish/toggle/remove、loop 审批、watcher 启停、MCP server 管理 | `is_admin` |
| risk=True，可配等级 | 沙箱写/删、MCP 调用、长跑申请批准 | `min_level` 进配置 |

### 4.2 agentdb：SQLite 存储层

**为什么不用 JSON**：心情/tick/作业每 30–60 秒一次带条件的 UPSERT 与索引查询（`WHERE enabled=1 AND next_run_at<=?`），JSON 整文件重写在多会话规模下既慢又难保证原子；而 `sqlite3` 是标准库，**requirements.txt 一行都不用加**。

**连接与迁移**（`flyai/agentdb/__init__.py`）：

```python
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional

DB_VERSION = 1  # 首个迁移版本，后续只增不改

def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def migrate(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(_SCHEMA_SQL)   # schema.sql 全文
            conn.execute("PRAGMA user_version=1")
        # 后续：if version < 2: ... ALTER 前先 PRAGMA table_info 探列
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

约定（沿用 Tide 已验证的经验）：

- DAL **短开短关**，不缓存连接；时间一律 `time.time()`  epoch 秒；id 全 TEXT。
- 每个迁移步执行后立刻 `PRAGMA user_version=N`；`ALTER TABLE` 前先探列，保证可重入。
- 写入串行化：所有写操作集中在 SchedulerService 与 ChatRun 所在的事件循环线程，用 `asyncio.to_thread` 包裹同步 sqlite 调用，避免阻塞 loop。
- 单进程约束写进部署文档；多 worker 场景由 4.4 的领导锁保证只一个进程跑调度，Web 写冲突靠 `timeout=10` busy timeout 兜底。

### 4.3 可写沙箱（Sandbox）

移植 Tide `sandbox.py` 的校验算法（已验证可靠），落点 `flyai/sandbox/core.py`，根目录改为 UserScope 布局：

- 群根 → `data/{uid}/workspace/sessions/{session_id}/`
- 公共库 → `data/{uid}/workspace/public/`（v1 仅 admin 可写，普通会话可读）

```python
class SandboxError(Exception): ...

class Sandbox:
    def __init__(self, group_root: Path, public_root: Path,
                 max_file_kb: int = 512, quota_mb: int = 10,
                 public_max_file_kb: int = 4096, public_quota_mb: int = 50): ...

    def resolve(self, rel: str, for_write: bool = False) -> Path:
        # 1) 反斜杠转正斜杠；拒绝绝对路径（/ 开头或盘符）与 '..' 段
        # 2) 首段 == 'public' 走 public_root，否则 group_root
        # 3) target = base.joinpath(*parts)；real = target.resolve()
        # 4) 双根校验：base == real 或 real 在 base.parents 链下，否则 SandboxError('路径越界')
        # 5) real != target 时再验 real 仍在两根之下，否则 SandboxError('链接路径越界')
```

配额：单文件 512 KiB、会话目录总量 10 MiB；public 单文件 4 MiB、总量 50 MiB；写入前 `_dir_used()` 重算（overwrite 扣旧 size）；append 先合并校验再落盘。`list_dir` 上限 200 项；禁止删除两根本身。

**与 file_reader 的关系**：保留现有只读插件不动；新增 `fs_write/fs_append/fs_delete/fs_list` 四个 risk 工具走 Sandbox，`read_file` 后续版本可切换为同一套根解析（P0 不动它，避免回归）。

### 4.4 调度服务与单实例领导锁

`flyai/proactor/service.py`，在 `app.py:312` 的 lifespan 中启停：

```python
class SchedulerService:
    def __init__(self, state: "AppState") -> None:
        self._state = state
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._services = []   # MoodTicker / JobTicker / ReminderTicker / ProactorTicker

    async def start(self) -> None:
        if not LeadLock.try_acquire(self._state.data_dir / ".scheduler.lock"):
            logger.warning("未取得调度领导锁，本进程不运行周期任务")
            return
        for svc in self._services:
            svc.initialize()
        self._task = asyncio.create_task(self._run(), name="flyai-scheduler")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            await self._safe_tick()    # 每个子 tick 独立 try，互不拖垮

    async def aclose(self) -> None:
        self._stop.set()
        LeadLock.release()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, timeout=5)
```

领导锁 `leadlock.py`：Windows 用 `msvcrt.locking(LOCK_NB)`、POSIX 用 `fcntl.flock(LOCK_EX|LOCK_NB)` 锁 `data/.scheduler.lock`；拿不到锁即静默禁用调度（Web 能力不受影响）。

**时区**：Tide 隐含服务器本地时区（Docker UTC 会错点）。本方案所有落库时间为 epoch 秒，HH:MM 解析统一用 `zoneinfo.ZoneInfo`，默认 `Asia/Shanghai` 且进配置项 `scheduler.timezone`。Windows 下标准库 zoneinfo 可能缺系统库，requirements 增加纯 Python 的 `tzdata` 一个依赖。

**3.9 注意**：`asyncio.Lock` 在 3.9 构造时即绑定事件循环，所有 Manager 的锁必须在 `start()`/首次请求时惰性创建，禁止模块级构造。

### 4.5 配置模型（Pydantic v1 写法）

在 `flyai/config.py` 增加（全部带默认值，旧 config.json 自动获得默认，配合现有 `_migrate_config_data`）：

```python
class SandboxSettings(BaseModel):
    enabled: bool = False
    max_file_kb: int = 512
    quota_mb: int = 10
    public_quota_mb: int = 50

class SchedulerSettings(BaseModel):
    enabled: bool = False
    timezone: str = "Asia/Shanghai"
    tick_seconds: int = 30
    reminder_check_seconds: int = 60

class AppConfig(BaseModel):
    # ...既有字段...
    sandbox: SandboxSettings = SandboxSettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    # P1/P3/P4 的 settings 随各自阶段追加（附录 A 汇总）
```

---

## 5. P1 旗舰：AI 自定义工具（自扩展运行时）

这是 Tide 最有辨识度的能力：**AI 在对话里写出新工具，dry-run 验证通过后热上线，之后每轮都能直接调用；工具代码永不进主进程**。

### 5.1 三阶段生命周期

| 阶段 | 工具 | 语义 |
|---|---|---|
| 验证 | `custom_tool_validate(name, description, params_schema, code, test_args?)` | 主进程只做 `compile()` 静态检查；带 test_args 时在子进程真实 dry-run 一次（`ctx.dry_run=True`），不落库 |
| 上线 | `custom_tool_publish(...)` | 去掉 test_args 再做加载+契约检查；通过后 UPSERT。**「回滚」= 旧行根本没被写**，不做版本表 |
| 调用 | 模型直接调 `custom__<name>` | 每次从 DB 取 code，新起隔离子进程执行 |

辅助：`custom_tool_list` / `custom_tool_toggle(name, enabled)` / `custom_tool_remove(name)`，五个管理工具全部 `risk=True`（admin）。

校验规则（移植 Tide 常量）：

- 名称正则 `^[a-z][a-z0-9_]{0,31}$`；description 必填且 ≤500 字；code ≤ 8000 字；params_schema 必须是 `{"type":"object",...}` JSON。
- 每作用域上限 20 个；作用域两级：`user`（该用户全部会话）与 `session:<sid>`（单会话），会话级同名覆盖用户级。
- 契约：代码必须定义 `async def` 或普通 `def tool(args: dict, ctx: dict) -> str`；worker 归一化返回值（str 原样 / None→"" / dict·list→JSON）。

### 5.2 隔离子进程协议（核心安全边界）

新增 `flyai/agentdb/tool_worker.py`——**该文件在 `python -I` 下运行，只能导入标准库，禁止 import 项目包**。

启动命令：

```python
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-I", "-X", "utf8",
    str(Path(__file__).with_name("tool_worker.py")), result_path,
    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env=_clean_env(),          # 见下
    **_detached_kwargs(False), # 调用型不 detach；watcher 才 detach
)
```

- stdin 喂一个 JSON：`{code, args, ctx, mode, test_args, lib_dirs}`；协议通道是 argv 指向的临时结果文件（tempdir 前缀 `fltool_`），print 不污染协议。
- `-I` 隔离模式忽略 PYTHONPATH、用户 site、cwd 入 sys.path；worker 在 exec 前把 `lib_dirs` insert 到 sys.path（v1 不开放 lib_dirs，预留）。
- `_clean_env()`：复制 os.environ 后剔除 `FLAI_CONFIG` 及所有 `*_API_KEY`、`*_TOKEN`、Authorization 类变量——Tide 的教训是子进程默认继承父环境，密钥会泄漏给 AI 写的代码。
- 超时：validate 10s、call 20s（进配置）。超时杀**整棵进程树**：Windows `taskkill /PID <pid> /T /F`；POSIX 以 `start_new_session=True` 启动后 `os.killpg(os.getpgid(pid), SIGKILL)`（修正 Tide POSIX 只 kill 父进程的不足）。
- 结果文件缺失 → `{ok:false, stage:"crash", error: stderr 尾 1200 字}`；任何异常文本化返回，绝不抛进 agent loop（与 `ToolRegistry.execute` 的异常收敛一致）。
- worker 内每次新建 namespace `exec(compile(code, "<custom_tool>", "exec"), ns)`，无跨调用模块级状态；stdout/stderr 重定向到 StringIO 并截断（加载期 1000 字、调用期 2000 字）。

### 5.3 动态注册桥

`flyai/tools/custom_tool_manage.py` 与运行时：

```python
class CustomFunctionTool(Tool):
    risk = True                      # 默认风险调用；发布者可声明 owner_only → admin
    def __init__(self, row: sqlite3.Row, runtime: "CustomToolRuntime"):
        self.name = "custom__" + row["name"]
        self.description = "[自定义工具] " + row["description"]
        self.parameters = json.loads(row["params_schema"])
        self._row = row
        self._runtime = runtime

    async def run(self, arguments, ctx):
        out = await asyncio.to_thread(
            self._runtime.call_blocking, self._row, arguments,
            {"session_id": ctx.session_id, "run_id": ctx.run_id,
             "uid": ctx.uid, "dry_run": False, "now": int(time.time())},
            self._runtime.call_timeout)
        return ToolResult(output=out["result"] if out["ok"]
                          else f"[自定义工具错误] {out.get('error','')}",
                          ok=out["ok"])
```

注册时机：`AppState.reload_tools()` 重建 registry 的末尾，从各用户 agent.db 读启用行追加（请求级按 uid 过滤可见性，沿用 `allowed_tool_names()`）；管理工具的 publish/toggle 完成后触发一次 `reload_tools()`，复用既有热生效通道，正在跑的旧 run 持旧 registry 不受影响。

### 5.4 表结构（agent.db）

```sql
CREATE TABLE IF NOT EXISTS custom_tools (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scope          TEXT NOT NULL,              -- 'user' 或 'session:<sid>'
    name           TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL,
    params_schema  TEXT NOT NULL,
    code           TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    owner_only     INTEGER NOT NULL DEFAULT 0,
    created_by     TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    UNIQUE(scope, name)
);
CREATE INDEX IF NOT EXISTS idx_custom_scope ON custom_tools(scope, enabled);
```

### 5.5 配置项

```python
class CustomToolSettings(BaseModel):
    enabled: bool = False              # 默认关，admin 在设置页开启
    code_max_chars: int = 8000
    max_per_scope: int = 20
    validate_timeout: float = 10.0
    call_timeout: float = 20.0
    # 安全声明（写入设置页文案）：子进程有网络访问能力，非容器级隔离
```

### 5.6 安全模型（如实声明，不夸大）

| 已防住 | 没防住（文档与 UI 明确告知） |
|---|---|
| 崩溃/死循环/`sys.exit` 不影响主进程；超时杀进程树 | 无容器/网络命名空间隔离，自定义代码可访问网络与本机回环 |
| 密钥环境变量剥离；`-I` 阻断 sys.path 注入 | 无 CPU/内存配额，靠操作系统约束 |
| 沙箱路径逃逸（双根 + realpath）；管理操作 admin 门禁 | 上线后的工具可被会话内模型间接触发（owner_only 可收紧） |

---

## 6. P2 长跑模式（无上限长任务 + 安全点打断）

### 6.1 与现有 ChatRun 的关系

FlyAI 已有：后台 `asyncio.create_task` 驱动、事件序号缓存与 SSE 续读、会话级 `try_lock_for()` 互斥、cancel 端点、停止保存半截。长跑不是另起炉灶，而是给 `run_agent_loop` 增加一个 `loop_ctx` 状态字典（Tide 已验证的形态）：

```python
# flyai/agent/loopmode.py
@dataclass
class LoopContext:
    task: str
    loop_db_id: int
    finished: bool = False
    finish_text: str = ""
    since_spoke: int = 0          # 距上次汇报的工具轮数
    idle_turns: int = 0           # 连续无工具操作轮数
    total_rounds: int = 0
    compacts: int = 0
    compact_note: str = ""
    context_limit: int = 0        # 从超长报错中学到的模型上下文上限
```

`run_agent_loop(..., loop_ctx: Optional[LoopContext] = None)`：`loop_ctx is not None` 时跳过通用/联网双预算的封顶判定（工具轮 `max_iterations=None` 语义），其余流式/事件/断线逻辑全部不变。

### 6.2 四个保护机制

1. **每 5 个工具轮强制汇报**：有工具调用的模型轮累加 `since_spoke`；达到 `loop_every_rounds=5` 时追加一条临时 system「禁止调用任何工具，立刻用一两句口语汇报进展」，做一次**不带 tools** 的补问，产出正常流式消息后移除临时指令（不污染历史）。
2. **空转保护**：一轮零工具操作则 `idle_turns += 1`，否则清零；连续 3 轮（`loop_idle_turn_limit=3`）自动结束，结果写「连续多轮未进行工具操作，判定任务已结束」。
3. **自动滚动压缩**：字符兜底 40000 字或估算 token 达预算 80%（保留 30000 输出余量，比例外推只高估）时触发；保留最近 4 个完整工具轮（`loop_keep_rounds=4`），头部交便宜模型归纳为 `compact_note`，下一轮以纪要 + tail 重建请求。撞上下文上限报错时解析出 limit，强制压缩后重试，每请求最多 3 次。
4. **干净停止**：停止只把 DB 状态改 stopped，不 cancel task；worker 在下一轮开头读状态退出。服务重启时 `reconcile()` 把所有 running 置 `interrupted`（修复纯内存 run 重启无痕迹的问题——状态现在落 agent.db）。

### 6.3 安全点打断（InterruptHub）

```python
# flyai/agent/interrupt.py
class InterruptHub:
    def __init__(self): self._q: Dict[str, list] = {}    # session_id -> 待注入项
    def push(self, session_id: str, item: dict) -> None: ...
    def drain(self, session_id: str) -> list: ...        # 只在安全点调用
```

- 现状：同会话已有 running run 时新消息返回 409。改造为：会话处于长跑时，新消息不抢会话锁、改走 `hub.push()`；普通会话维持 409。
- `run_agent_loop` 在**每轮请求模型之前**（工具链全部完成之后）`drain()`，把插队项渲染成一条 system 通知 + 若干 user 消息注入；绝不会切断半截工具调用。
- 路由层提供 `POST /api/chat/loop/interrupt`（会话成员可用，对应 Tide 的全员可打断），前端在长跑进行中把输入框切为「打断/临时指示」模式。

### 6.4 申请-审批与收尾

- AI 可调 `loop_request(task, reason)`（risk 工具）：写 `loop_requests(status=pending)`，前端/管理页出现审批弹窗；admin 批准后启动。admin 也可直接用 `loop_start(task)` 免审批。
- `loop_finish(summary)` 工具只在 loop_ctx 存在时可见，置 finished；worker 收尾写 `loop_sessions.status=done` + result，结果同时作为 ChatRun 正常消息流回会话（Tide 的私聊汇报在 FlyAI 简化为会话内系统消息 + 通知中心条目）。

```sql
CREATE TABLE IF NOT EXISTS loop_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL, task TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    requester_uid TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    decided_by TEXT NOT NULL DEFAULT '', decided_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS loop_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL, task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',     -- running|done|stopped|interrupted|error|idle
    request_id INTEGER NOT NULL DEFAULT 0,
    rounds INTEGER NOT NULL DEFAULT 0, compacts INTEGER NOT NULL DEFAULT 0,
    compact_note TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL, ended_at REAL NOT NULL DEFAULT 0
);
```

---

## 7. P3 心情、自我记忆、主动行为与定时任务

### 7.1 心情系统（半衰期数学原样移植）

`flyai/proactor/mood.py`，纯函数部分不依赖 DB，先可测后接线。

```python
MOOD_HALF_LIFE = 3 * 3600      # 心情值 3 小时
DIM_HALF_LIFE = 90 * 60        # 七绪 90 分钟
MOOD_DIMS = ("开心", "踏实", "甜蜜", "心疼", "担心", "难过", "吃醋")
SOURCE_CAPS = {"human": 10, "talk": 5, "work": 3, "greet": 5}

def decay(anchor: float, updated_at: float, now: float, half_life: float,
          baseline: float = 0.0) -> float:
    dt = max(0.0, now - updated_at)
    return baseline + (anchor - baseline) * 0.5 ** (dt / half_life)
```

- 读时衰减、判定时重新锚定落库（无后台衰减定时器）；七绪衰减后 <0.5 的维度抹除。
- 便宜模型判定输出 `{delta, dims, reason, confidence}`，本地再 clamp 一次，不能只信模型；增量按来源限幅（真人 ±10 / 主动搭话 ±5 / 后台任务 ±3 / 问候 ±5）。
- 「只宣布一次」状态机：显著波动（|delta|≥5）、置信度 ≥60、reason 非空、当天注入 <6 次才注入。**修正 Tide 的一处时序缺陷**：Tide 在返回注入文本前就标记 consumed，模型轮失败会永久丢失这次宣布；FlyAI 改为先 pending，收到 ChatRun `done` 事件确认发送成功后再写 injected 标记。
- 作用域：绑定 `(session_id, character_id)`；表 `mood_state` / `mood_timeline`（DDL 附录 B），时间线每会话保留 200 条。
- 模型路由：新增配置 `mood.judge_provider_id` + `judge_model`，留空回落当前激活 provider（对应 Tide 六条路由的轻量模型思路，但不引入多 provider 并发复杂度）。
- 注入位置：走尾部实时块（见 7.5 的 LIVE_CTX），不插静态 system 前缀。

### 7.2 第一人称自我记忆

FlyAI 现有记忆簿是**角色维度**（`data/{uid}/character-memory/`），Tide 的自我记忆是全局第一人称视角（「我」经历过什么、承诺过什么）。不复刻一套 SQLite BM25，而是**复用现有 `memory/bm25.py` 与 `MemoryEntry` 结构**，差异部分落 agent.db：

- 记忆正文存 `self_memories` 表，召回时用 `tokenize` + `BM25` 现算（数据量小，每用户上限配额天然约束规模），排序沿用 `0.75*归一化BM25 + 0.15*importance + 0.10*60天近因`。
- 每日写配额 8 条（本地自然日，`self_memory_state` 表），只对「新建」计费；**修正 Tide 配额 bug**：合并函数返回 `(row, created: bool)`，以返回值统一计费，且每条落库后立即失效进程内索引。
- 回忆链 = `self_memory_traces` 审计流水（recall/revise/supersede），**永不拼进模型上下文**，只在前端记忆管理页展示。
- 工具：`remember_self(statement, tags, importance)`、`recall_self(keyword)`；抽取链路在现有 `_schedule_extraction` 中增加 self 条目分支。

### 7.3 主动行为（默认关闭，会话级 opt-in）

这是最需要克制的能力——本地工具没有 QQ 群场景的天然预期，主动说话必须显式开启。

`flyai/proactor/proactor.py` 移植裁决逻辑：

- 30 分钟一个 tick；基础唤醒概率 10%，连续 20 次未触发保底唤醒一次；主动搭话按工作日峰谷表（峰 20% / 谷 60%）、冷场乘子（<20 分钟 0.15，>6 小时 1.5）、连没中每次 +10%（封顶 +40）、机器人 20 分钟自冷却、静默时段 01:00–08:00。
- 活跃门：最后一条真人消息超过 12 小时不触发；冷群不掷骰。
- 触发后合成隐藏通知（`name=wake-notice/speakup-notice`，`allow_silent=True`），**直接复用 ChatRun 机制创建后台 run**；模型可输出 `[静默]` 选择不发言，静默轮不写消息、不留 assistant 历史。
- 自建轮询 polls：每会话最多 10 条，概率/保底/活跃时限独立配置，`ticks_since_fire` 落库重启不丢；同一 tick 至多选一条。
- 前端：会话设置里「主动行为」开关 + 概率/时段表单，默认全部关闭；Galgame 单角色会话可作为首选试点场景。

### 7.4 定时任务与到期提醒

- `scheduled_jobs`：mode = once/daily/weekly；daily/weekly 仅接受 HH:MM；**严格未来语义**——时刻已过顺延到明天/下周，宕机重启不补跑错过的轮次（Tide 验证过的防连发策略）。作业触发同样产出 ChatRun；compose 阶段不带历史、不带工具，仅人设 + brief + 时间，max_tokens=500。失败后置 `next_run_at = now+300` 重试。
- 提醒闭环：现有 ledger 的 task 没有到期字段。两条路二选一，**推荐方案 A**：
  - A. 在 agent.db 新建 `commitments` 表（session_id、owner 显示名、content、due_at、status、reminded），AI 通过新工具 `remember_commitment` / `close_commitment` 维护，ReminderTicker 每 60 秒扫 `due_at<=now AND reminded=0`，到点注入一条提醒消息触发 run；ledger 继续管剧情内台账，职责分离。
  - B. 扩展 ledger JSON schema 加 due_at——会牵动现有记忆压缩与导出，回归面大，不推荐。
- 关闭会话的主动能力时：不标记 reminded（将来重开还会提醒）；会话被删除时：标记并停用。两个策略不要合并（Tide 的经验）。

### 7.5 尾部实时块与前缀缓存

Tide 的关键工程经验：**所有逐轮变化的内容（心情、记忆、约定、风格、时钟）拼成一条尾部 system 消息，静态 system 前缀与历史头部保持字节稳定**，以命中服务商前缀缓存。FlyAI 当前在 `routes_chat` 组装系统提示词，P3 改造为：

```text
[静态前缀] 角色/世界观/格式规范（跨轮稳定）
[历史窗]  仅追加，压缩锚点前移时才断前缀
[LIVE_CTX 尾部块] 心情 · 自我记忆 · 未结约定 · 时钟 · 主动通知（逐轮易变）
```

### 7.6 P3 配置汇总

```python
class MoodSettings(BaseModel):
    enabled: bool = False
    baseline: int = 70
    delta_threshold: int = 5
    min_confidence: int = 60
    daily_cap: int = 6
    timeline_keep: int = 200
    judge_provider_id: str = ""
    judge_model: str = ""

class ProactorSettings(BaseModel):
    enabled: bool = False              # 总闸，默认关
    wake_check_minutes: int = 30
    wake_prob_percent: int = 10
    wake_every_n: int = 20
    active_hours: float = 12
    speakup_peak_percent: int = 20
    speakup_valley_percent: int = 60
    self_cooldown_minutes: int = 20
    quiet_hours: str = "01:00-08:00"
    polls_max_per_session: int = 10

class SelfMemorySettings(BaseModel):
    enabled: bool = True
    daily_quota: int = 8
    recall_topk: int = 5
    recall_budget: int = 300
```

---

## 8. P4 生态扩展：MCP、外部触发、监测进程、提示词注册表

### 8.1 MCP 接入（绕开 3.10 阻塞）

官方 `mcp` SDK 1.30 与 anyio 4.15 均要求 Python ≥3.10，不能进 FlyAI 主环境。两方案：

| 方案 | 内容 | 评价 |
|---|---|---|
| **A（推荐）自研精简 stdio 客户端** | `flyai/mcp_client/stdio.py` 用 stdlib + httpx 实现 JSON-RPC 2.0：initialize / tools/list / tools/call 三个方法，按 `\n` 分帧 | 依赖零增加，覆盖最常用的 stdio server；工作量可控 |
| B sidecar 桥 | 独立 Python 3.10+ 进程跑官方 SDK，通过本地 HTTP 暴露给主进程 | 兼容性最好但部署变重，留给 streamable-http/SSE 传输时再做 |

v1 范围：仅 stdio；工具命名空间化 `mcp__<server_id>__<tool>`，schema 缓存落 `mcp_servers.tools_json`；所有 MCP 工具默认 `risk=True`（远程桌面类风险，Tide 同策略），按会话工具开关复用 `disabled_tools`；连接懒建立、失败标 status=failed、调用遇 EOF/超时重连一次。媒体结果（图片/音频）存沙箱 `mcp/` 目录，TTL 6 小时启动清扫。

### 8.2 外部触发（FastAPI 路由化）

Tide 用独立 ThreadingHTTPServer 是因为其 Web 面板有 session 鉴权且 GUI 模式可能没启动。FlyAI 本身就是 FastAPI，直接加路由更合理：

- `POST /api/ext/trigger`，Header `Authorization: Bearer <ext_token>`，`hmac.compare_digest` 比对；ext_token 独立于用户登录令牌，配置为空则端点禁用。
- body：`{session_id, text, source}`；source 正则白名单；text ≤2000 字；请求体上限 64 KiB。
- 冷却键 `(session_id, source)` 默认 30 秒，内存字典；先记冷却再入队，入队失败回滚。
- 入站请求经 `run_in_threadpool` 转事件循环后创建 ChatRun；天然复用 netguard 之外的全部会话/权限校验。

### 8.3 监测进程（watcher）

移植 `watchers.py`：AI 先经沙箱写脚本（必须 `.py`、相对路径、禁 public），管理工具 `watcher_start/stop`（risk=admin）以 detached 方式启动：

- Windows：`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`；POSIX：`start_new_session=True`；stdin 接 DEVNULL，stdout/stderr 写沙箱 `.watchers/<ts>.log`。
- 环境变量只注入 `TRIGGER_URL/TRIGGER_TOKEN/TRIGGER_SESSION/TRIGGER_SOURCE/PYTHONIOENCODING`，其余走 `_clean_env()`。
- 每会话上限 5 个；登记表落 agent.db，重启时 `reconcile()` 探活（Windows `OpenProcess`+退出码 259，POSIX `kill(pid,0)`），活的不重启、死的标 stopped——刻意不做崩溃自动重启。
- 脚本通过 8.2 的触发端点回调，形成「外部变化 → 事件入队 → AI 处理」闭环。

### 8.4 提示词注册表（可选）

Tide 的 20 分区注册表缩小为 FlyAI 需要的子集：mood_wrap、wake_notice、speakup_notice、loop_start、loop_continue、compact、self_memory_header。结构照抄：`(key, 允许占位符元组, 默认文本, 说明)`，覆盖存 `prompt_overrides` 表，渲染时只替换白名单占位符，未知 `{...}` 保留原样。设置页做一个通用「提示词分区」编辑器即可，不做 20 个分区的全量搬迁。

### 8.5 群说话风格（暂缓）

FlyAI 的群聊房间是低频功能且无 ambient 语料采集惯例，raw_messages  corpus + 风格重建的收益/成本比低，列为 backlog，不进本次批次。

---

## 9. Python 3.9 适配清单

| 项 | 要求 |
|---|---|
| 联合类型 | 禁止 `X \| Y` 出现在会被求值的位置；新文件统一 `from __future__ import annotations` + `Optional/Union`；Pydantic 字段必须用 `Optional[X]` |
| 异步结构 | 禁 `TaskGroup`/`ExceptionGroup`；并发用 `asyncio.gather`，取消隔离用 `contextlib.suppress` + `wait_for` 超时（参考 Tide `wait_resilient`） |
| 锁 | `asyncio.Lock`/`Queue` 惰性创建，禁止模块级或 dataclass 默认值构造（3.9 构造即绑 loop，TestClient 换 loop 会炸） |
| 线程桥 | `asyncio.to_thread` 3.9 已有，sqlite/子进程阻塞调用统一走它 |
| 枚举/匹配 | 禁 `StrEnum`、match/case；状态机用字符串常量 + if 分支 |
| 时区 | `zoneinfo` + requirements 增加 `tzdata`（Windows 无系统 tz 库） |
| worker 文件 | `tool_worker.py` 在 `-I` 下运行，只用标准库；无 future import 也不能出现任何 PEP 604 注解（Tide 的 tool_worker.py 就栽在这） |
| 子进程 | `create_subprocess_exec` + 进程树清理双平台实现；字符串命令一律禁用 shell |
| Pydantic | 全部 v1 风格：`class Config`、`@validator`、`parse_obj`、`.dict()`，与 `config.py` 现状一致 |
| 语法自检 | CI 加一步：`ast.parse(src, feature_version=(3, 9))` 扫描 `flyai/agentdb`、`flyai/proactor`、`flyai/sandbox` 全部新文件 |

---

## 10. 前端改造方案

### 10.1 通用规则

- 沿用无构建 classic script + 全局函数风格，不引入打包器；新 JS 文件按依赖顺序在 `index.html` 底部加 `<script ?v=>`。
- 每个改动同步三份：`flyai/web/static/` → `mobile/www/static/` → Android assets；或改完执行现有 `npm run copy-web` 流程。
- 管理员元素加 `admin-only`（既有约定）；设置新页走 `m-page` + `data-goto` 栈式导航。

### 10.2 新增页面与组件

| 设置页 | 内容 | 阶段 |
|---|---|---|
| `custom-tools` | 工具列表（作用域/启用开关/owner_only）、代码编辑 dialog（带 test_args 输入、dry-run 结果面板、publish 按钮）、错误/stderr 展示 | P1 |
| `loop` | 长跑任务列表、loop_requests 审批弹窗、运行中状态（轮数/压缩次数/打断输入框） | P2 |
| `mood` | 心情曲线（timeline 读数）、七绪当前值、阈值/路由配置 | P3 |
| `proactive` | 会话级主动行为开关、概率表/静默时段/轮询编辑、定时任务日历式列表、承诺与提醒列表 | P3 |
| `mcp` | server 增删（command/args/env）、测试连接、工具缓存查看、按会话开关 | P4 |
| `prompts` | 提示词分区编辑器（占位符说明 + 恢复默认） | P4 |

后端配套：`routes_config.py` 的 `ConfigPayload` 增字段；新管理端点放新 `routes_agentdb.py`，鉴权沿用手工 `_require_admin`；会话级开关写入 session 模型（新增字段走 `sessions/versions.py` 的既有迁移风格）。

### 10.3 流式与通知

- 长跑/主动唤醒全部走既有 ChatRun SSE：复用 `consumeRun/autoReconnect/maybeResumeRun`，无需新传输通道。
- 新事件只增加可选类型（如 `loop_status`、`mood`），旧客户端忽略未知事件，保证 APK 向后兼容。
- 主动行为产生的会话在侧栏加「主动消息」角标，避免用户困惑。

---

## 11. 测试策略与验收标准

沿用 `tests/` 既有风格：pytest + pytest-asyncio 0.24、脚本化 provider（`_ScriptedProvider`）、`SimpleNamespace` 桩、monkeypatch，不引重型 mock 框架。当前 1015 个测试全程保持全绿。

### 11.1 各阶段测试清单（要点）

**P0**

- `test_tool_authz.py`：risk 工具对低等级/ admin 的执行期放行与拒绝；验证 specs 过滤后硬编工具名仍被 execute 拦截（回归锁住本次修复的缺口）。
- `test_sandbox.py`：`..` 越界、盘符绝对路径、symlink 逃逸（tmp_path 造软链）、public 只读、配额（单文件/总量/append 合并）、双根边界。
- `test_agentdb.py`：WAL 开启、user_version 迁移可重入、重复 migrate 不报错。
- `test_scheduler_lock.py`：领导锁同进程二次获取失败、释放后可再获取。

**P1**

- worker 协议：正常返回/None/dict 归一化、print 截断、`sys.exit` 与死循环超时杀树（用短超时桩加速）、结果文件缺失的 crash 文案。
- 环境剥离：子进程内读不到 `FLAI_CONFIG`/注入的假 API_KEY。
- 生命周期：validate 不落库、publish 失败旧行保留、名称正则/上限/配额、reload_tools 后 `custom__*` 可见且旧 run 不受影响。
- 安全：管理五工具非 admin 全拒；owner_only 行间接触发被拒。

**P2**

- 预算解除：loop_ctx 存在时双预算不封顶；普通会话预算语义不变（保护既有用例）。
- 强制汇报：脚本化 provider 制造 5 轮纯工具调用，断言出现一次无 tools 补问且临时指令不进 history。
- 压缩：40k 字符阈值触发、keep 4 轮切点、context_limit 学习后强制压缩重试 ≤3 次。
- 打断：hub 只在安全点排空；工具执行中 push 不切断当前链；409 语义在普通会话不变、长跑会话改道 hub。
- 重启 reconcile：running 遗留行全部转 interrupted。

**P3**

- 心情数学：半衰期点位值（3h/90min 锚点）、来源限幅、<0.5 抹除、宣布一次状态机（含「发送失败不消费」新语义）。
- 自我记忆：每日配额、update 不计费、批内缓存失效、trace 不进 prompt。
- 概率裁决：注入固定随机种子 roll 验证峰谷/静默/冷却/保底；时钟全部走可注入的 `now_provider`，测试中不 sleep。
- 作业：strict-future 顺延、宕机不补跑、失败 5 分钟重试；提醒的「关闭不标记、删除即标记」两策略。
- 时区：`TZ` 无关性（显式 ZoneInfo，改机器时区不影响结果）。

**P4**

- MCP stdio：用假 JSON-RPC 脚本当 server 测 initialize/list/call、EOF 重连一次、risk 门禁。
- trigger：错误 token 401、冷却 429、入队失败回滚冷却、body 上限。
- watcher：detached 进程登记/探活/stop 杀树/重启 reconcile。

### 11.2 验收门槛（每阶段 Definition of Done）

1. 新增测试数 ≥ 该阶段新增 Python 文件数 × 3；全套 `pytest tests/ -q` 全绿。
2. 新配置项全部有默认值且旧 config.json 可直接启动；`ast.parse(feature_version=(3,9))` 扫描通过。
3. 管理类能力全部有 admin 门禁的 API 级测试；risk 工具在非授权上下文返回 ok=False 而非抛异常。
4. 手工 E2E（`test/` 下 Playwright 脚本风格）至少一条 happy path：P1 端到端「对话中发布工具→下一轮调用成功」；P2 「长跑→打断→审批→重启后 interrupted」；P3 「定时任务到点产出会话消息」。
5. 三份静态资源同步，debug APK 可构建。

---

## 12. 里程碑与工作量

按 1 人投入估算（人日），每阶段独立可交付、可回退（配置总闸默认关闭）。

| 阶段 | 内容 | 人日 | 交付后可演示的卖点 |
|---|---|---:|---|
| P0 | 执行期授权、agentdb、Sandbox、SchedulerService、配置骨架 | 3–4 | 安全沙箱与权限体系（答辩素材） |
| P1 | 自定义工具五件套 + worker + 前端编辑器 | 5–6 | **「AI 给自己造工具」现场演示，最强差异点** |
| P2 | loopmode + InterruptHub + 审批 + 自动压缩 | 4–5 | 无限轮长任务、可打断、重启可恢复 |
| P3 | 心情 + 自我记忆 + 主动行为 + 作业/提醒 + LIVE_CTX | 6–7 | 有状态、会主动开口的长期智能体 |
| P4 | MCP stdio + trigger + watcher + 提示词注册表 | 5–6 | 外部事件闭环与工具生态接入 |
| 合计 | | **23–28** | |

建议参赛节奏：**P0+P1 必做**（故事完整、风险可控）；P2 视时间追加；P3/P4 作为申报书「演进路线」储备——即使不全部实现，答辩时能讲清已验证的设计取舍。

每阶段单独分支、单独 PR，提交信息沿用仓库 conventional 风格（`feat(agent): ...`）。

---

## 13. 风险登记册

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R1 | 自定义工具子进程非容器隔离，可访问网络 | 高 | 环境变量剥离 + 管理门禁 + 默认关闭 + UI 如实声明；后续可接 AppContainer/容器后端 |
| R2 | SQLite 多 worker 写冲突（`database is locked`） | 中 | 领导锁保证单实例调度；busy timeout 10s；文档明确单 worker 部署；DAL 短连接 |
| R3 | 主动行为打扰用户、夜间烧 token | 中 | 默认关闭 + 会话 opt-in + 静默时段 + 自冷却 + `[静默]` 出口 + 后台轮数 8 轮上限沿用 |
| R4 | 长跑任务失控烧钱 | 中 | 空转 3 轮自停、每 5 轮强制汇报、审批制、token 计入会话既有 `token_usage` 可观测 |
| R5 | 3.9 事件循环锁绑定导致测试 flaky | 中 | 锁全部惰性化；conftest 既有 `_ensure_event_loop` fixture 兜底；新 manager 不在 import 期构造 |
| R6 | 前端三份副本漏改导致 APK 行为不一致 | 中 | DoD 纳入同步检查；考虑后续把 copy-web 接进 prebuild 钩子 |
| R7 | 心情判定依赖便宜模型，误判导致人设漂移 | 低 | 本地 clamp 二次约束、单日注入上限、只宣布一次、timeline 可人工编辑/清空 |
| R8 | MCP 自研客户端协议覆盖不全 | 低 | v1 只承诺 stdio 三方法；http/sse 明确标 sidecar 路线；schema 缓存保证停机可查看 |
| R9 | 配置默认值三处不一致的既有痼疾（如 timeout 60/120） | 低 | 新配置以代码默认值为唯一事实源，前端 placeholder 只展示不持有默认 |
| R10 | 调度触发的 run 与用户手动请求竞争会话锁 | 中 | 调度侧先 try_lock，拿不到就跳过本 tick（不排队、不补发），下一 tick 重试 |

---

## 14. 附录

### 附录 A：新增配置键总表（均带默认值，旧配置无感升级）

```text
sandbox.enabled=false / max_file_kb=512 / quota_mb=10 / public_quota_mb=50
scheduler.enabled=false / timezone="Asia/Shanghai" / tick_seconds=30 / reminder_check_seconds=60
custom_tools.enabled=false / code_max_chars=8000 / max_per_scope=20
             / validate_timeout=10 / call_timeout=20
loop.enabled=true / every_rounds=5 / idle_turn_limit=3 / keep_rounds=4
     / compact_threshold_chars=40000 / compact_percent=80
mood.enabled=false / baseline=70 / delta_threshold=5 / min_confidence=60
     / daily_cap=6 / timeline_keep=200 / judge_provider_id="" / judge_model=""
self_memory.enabled=true / daily_quota=8 / recall_topk=5 / recall_budget=300
proactor.enabled=false / wake_check_minutes=30 / wake_prob_percent=10
        / wake_every_n=20 / active_hours=12 / speakup_peak_percent=20
        / speakup_valley_percent=60 / self_cooldown_minutes=20
        / quiet_hours="01:00-08:00" / polls_max_per_session=10
mcp.enabled=false / timeout=30
ext_trigger.enabled=false / token="" / cooldown_seconds=30
watchers.max_per_session=5
```

### 附录 B：agent.db 完整表清单（首批）

```text
custom_tools            P1  自定义工具（代码本体、作用域、owner_only、启用态）
loop_requests           P2  长跑申请审批
loop_sessions           P2  长跑运行状态（重启 reconcile 依据）
mood_state              P3  每会话+角色的心情锚点/注入状态机
mood_timeline           P3  判定流水（裁剪保留 200 条）
self_memories           P3  第一人称记忆正文
self_memory_state       P3  每日写配额
self_memory_traces      P3  回忆链审计流水（不进模型上下文）
commitments             P3  约定/待办与到期提醒
scheduled_jobs          P3  once/daily/weekly 作业
polls                   P3  会话内自建轮询
proactor_state          P3  保底计数/冷场 miss  streak
prompt_overrides        P4  提示词分区覆盖
mcp_servers             P4  server 配置/状态/工具 schema 缓存
watchers                P4  监测进程登记与探活
```

DDL 风格统一：`INTEGER PRIMARY KEY AUTOINCREMENT`、时间 REAL epoch 秒、布尔 INTEGER 0/1、外键 `PRAGMA foreign_keys=ON`、查询字段建复合索引（参考 Tide 既有 35 表的索引习惯：状态列 + 时间列组合）。

### 附录 C：Tide → FlyAI 术语映射

```text
group_id              → session_id
owner_qq / is_owner   → admin（ADMIN_LEVEL=256）/ ToolContext.is_admin
group_lock(gid)       → ChatRunManager.try_lock_for(session_id)
bg_runs               → ChatRun 事件缓存 + loop_sessions 落库
Sandbox(workspace/<gid>) → Sandbox(data/{uid}/workspace/sessions/<sid>)
engine._run_turn      → run_agent_loop（agent/loop.py）
engine._chat_loop     → loop 内部工具轮循环 + LoopContext
RISKY_TOOL_KEYS       → Tool.risk / Tool.min_level + registry.execute 授权
messages 表系统通知    → 隐藏 user 消息 + LIVE_CTX 尾部块
cfg dict (160 键)     → config.py 的 Pydantic v1 Settings 模型
deepseek.db           → data/{uid}/agent/agent.db
```

### 附录 D：源码参照索引

| 移植主题 | Tide 源文件 | FlyAI 落点 |
|---|---|---|
| 自定义工具 | `Core/custom_tools.py`、`Core/tool_worker.py` | `agentdb/`、`tools/custom_tool_manage.py` |
| 沙箱 | `Core/sandbox.py` | `sandbox/core.py` |
| 长跑 | `Core/loop_run.py`、engine 压缩段 | `agent/loopmode.py`、`agent/loop.py` |
| 心情 | `Core/mood.py` | `proactor/mood.py` |
| 主动/轮询 | `Core/proactor.py` | `proactor/proactor.py` |
| 作业/提醒 | `Core/scheduler.py`、`Core/tasks.py` | `proactor/scheduler_jobs.py`、`reminders.py` |
| 自我记忆 | `Core/self_memory.py` | `agentdb/self_memory.py` + 复用 `memory/bm25.py` |
| MCP | `Core/mcp_manager.py`（协议参考，SDK 不搬） | `mcp_client/stdio.py` |
| 触发/监测 | `Core/triggers.py`、`Core/watchers.py`、`Core/coderunner.py` | `api/routes_ext.py`、`proactor/watchers.py` |
| 存储迁移 | `Core/storage.py`（WAL/user_version 写法） | `agentdb/__init__.py` |
| 提示词注册表 | `Core/prompts.py` | `proactor/prompt_registry.py` |

---

*本书基于两项目 2026-11-13 的源码现状编写。实现中若 Tide 上游或 FlyAI 主干发生结构性变化，以各自源码为准并回修本文。*
