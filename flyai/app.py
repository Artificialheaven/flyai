"""应用工厂与模块级 app。"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import time

from .config import (
    AppConfig,
    PROJECT_ROOT,
    get_active_provider,
    load_config,
    save_config,
)
from .auth_sessions import AuthSessionStore, LoginRateLimiter, resolve_uid
from .errors import ConfigError
from .providers.base import BaseProvider
from .providers.factory import create_provider
from .tools.attributes_tool import UpdateAttributesTool
from .tools.image_tool import GenerateImageTool
from .tools.web_search_tool import PageTextCache
from .tools.loader import PluginLoader
from .tools.lore_tool import QueryLoreTool
from .memory.logs import RecallLogBuffer
from .tools.memory_cache import MemoryVectorCache
from .tools.memory_tool import QueryChatMemoryTool, UpdateChatMemoryTool
from .tools.registry import ToolRegistry
from .agent.runs import ChatRunManager
from .galgame.choices import GalgameChoiceManager
from .market.store import MarketStore
from .proactor import SchedulerService
from .tools.custom_tool_runtime import CustomToolRuntime
from .agent.interrupt import InterruptHub
from .agent.loopmode import LoopManager
from .user import ADMIN_UID, UserScope, UserStore, migrate_legacy

logger = logging.getLogger("flyai")

WEB_DIR = Path(__file__).resolve().parent / "web" / "static"


@dataclass
class AppState:
    """应用级共享状态。"""

    config: AppConfig
    config_path: Path = None
    registry: ToolRegistry = field(default_factory=ToolRegistry)
    loader: PluginLoader = None
    data_dir: Path = None
    user_store: UserStore = None
    # AI 浏览器会话管理（Playwright 可选依赖；未启用时为 None）
    browser: Optional["BrowserSessionManager"] = None
    # 浏览器搜索引擎（ChromiumSearcher，浏览器进程懒启动；不用时为 None）
    search_browser: Optional["ChromiumSearcher"] = None
    _providers: Dict[str, BaseProvider] = field(default_factory=dict)
    _scopes: Dict[str, UserScope] = field(default_factory=dict)
    _embedder: "EmbeddingClient" = None
    # 登录凭证（随机 token）与登录限速
    auth_sessions: AuthSessionStore = field(default_factory=AuthSessionStore)
    login_limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)
    # 待确认的压缩提案：{proposal_id: {uid,sid,room,raw,basesig,ts}}
    _compress_proposals: Dict[str, dict] = field(default_factory=dict)
    # web_fetch 完整正文缓存（分页翻页不重复下载，短 TTL）
    page_cache: PageTextCache = field(default_factory=PageTextCache)
    # 断线可续的聊天运行（后台任务 + 事件缓存，刷新后凭 run_id 重连）
    run_manager: "ChatRunManager" = field(default_factory=ChatRunManager)
    # 记忆消息向量的增量缓存（按 会话+角色，只 embed 新增消息）
    memory_vec_cache: "MemoryVectorCache" = field(default_factory=MemoryVectorCache)
    # 召回日志滚动缓冲（自动/主动召回记录，记忆管理页查看）
    recall_logs: RecallLogBuffer = field(default_factory=RecallLogBuffer)
    # 保存配置时热重建工具表的串行锁（配置接口在线程池中执行）
    _tools_lock: threading.Lock = field(default_factory=threading.Lock)
    # Galgame 阻塞选项撮合（按 run_id，内存态）
    galgame_choices: GalgameChoiceManager = field(default_factory=GalgameChoiceManager)
    # sid -> {对白文本: 音频 url}：TTS 端点产出但 drive 尚未合并落盘的映射
    galgame_tts_overlay: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # 后台调度服务（P0 骨架；scheduler.enabled=false 时只待命不 tick）
    scheduler: Optional[SchedulerService] = None
    # AI 自定义工具运行时（P1）：每用户 agent.db + 隔离子进程；未启用时仅待命
    custom_runtime: Optional[CustomToolRuntime] = None
    # P2 长跑：每用户 agent.db 状态机（申请/运行/重启 reconcile）+ 安全点打断队列
    loop_manager: Optional[LoopManager] = None
    interrupt_hub: InterruptHub = field(default_factory=InterruptHub)
    # P3 心情/自我记忆/主动行为：每用户 agent.db 的同步 DAL（None=未就绪）
    mood_store: Optional[Any] = None
    self_memory_store: Optional[Any] = None
    proactor_store: Optional[Any] = None
    job_store: Optional[Any] = None
    commitment_store: Optional[Any] = None
    # 判定模型派生 provider 缓存：(provider_id, model) -> provider
    _judge_providers: Dict[str, Any] = field(default_factory=dict)
    # P4 生态扩展：MCP 连接管理 / watcher / 提示词注册表 / 外部触发冷却
    mcp_manager: Optional[Any] = None
    watcher_manager: Optional[Any] = None
    prompt_registry: Optional[Any] = None
    trigger_cooldown: Optional[Any] = None

    _PROPOSAL_TTL = 1800
    _PROPOSAL_MAX = 32

    def put_compress_proposal(self, uid: str, sid: str, room: bool, raw: dict, basesig) -> str:
        """存入一个待确认压缩提案，返回随机 id。顺带 prune 过期/超量提案。"""
        import secrets

        now = time.time()
        stale = [k for k, p in self._compress_proposals.items() if now - p.get("ts", 0) > self._PROPOSAL_TTL]
        for k in stale:
            self._compress_proposals.pop(k, None)

        pid = secrets.token_hex(8)
        self._compress_proposals[pid] = {
            "uid": uid, "sid": sid, "room": room, "raw": raw, "basesig": basesig, "ts": now,
        }
        if len(self._compress_proposals) > self._PROPOSAL_MAX:
            oldest = min(self._compress_proposals.items(), key=lambda kv: kv[1].get("ts", 0))[0]
            self._compress_proposals.pop(oldest, None)
        return pid

    def get_compress_proposal(self, proposal_id: str, uid: str) -> Optional[dict]:
        """按 id + uid 取提案；不存在/过期/uid 不符返回 None。不消费。"""
        p = self._compress_proposals.get(proposal_id)
        if p is None or p.get("uid") != uid:
            return None
        if time.time() - p.get("ts", 0) > self._PROPOSAL_TTL:
            self._compress_proposals.pop(proposal_id, None)
            return None
        return p

    def drop_compress_proposal(self, proposal_id: str) -> None:
        self._compress_proposals.pop(proposal_id, None)

    def user(self, request: Request) -> UserScope:
        """按请求的 Bearer 令牌解析单个用户的数据作用域；未登录返回 401。

        令牌仅在 /api/auth/login 成功后签发；管理员（ADMIN_UID）凭据在 config，
        不在用户库，但同样需要令牌才能访问。
        """
        uid = (resolve_uid(self, request) or "").strip()
        valid = (uid == ADMIN_UID) or bool(self.user_store.get_by_id(uid))
        if not uid or not valid:
            raise HTTPException(401, "未登录或登录已失效")
        scope = self._scopes.get(uid)
        if scope is None:
            scope = UserScope.build(uid, self.config, self.data_dir, self.user_store)
            self._scopes[uid] = scope
        return scope

    def get_browser(self) -> "BrowserSessionManager":
        """AI 浏览器管理器；功能对某用户生效（全局开启或用户覆盖开启）时按需创建。

        工具表始终注册 browser_* 工具，是否暴露给模型由每请求的工具过滤决定；
        Chromium 进程本身仍在首次 open 时才启动。
        """
        if self.browser is None:
            from .browser.manager import BrowserSessionManager

            self.browser = BrowserSessionManager(self)
        return self.browser

    def get_search_browser(self) -> "ChromiumSearcher":
        """浏览器搜索引擎；首次使用时按当前配置创建（Chromium 本身再懒启动）。"""
        if self.search_browser is None:
            from .browser.search import ChromiumSearcher

            self.search_browser = ChromiumSearcher(
                self.config.web_search, self.config.browser
            )
        return self.search_browser

    async def reset_search_browser(self) -> None:
        """配置变更后关掉旧搜索引擎（代理等设置可能已变），下次使用重建。"""
        if self.search_browser is not None:
            old = self.search_browser
            self.search_browser = None
            try:
                await old.aclose()
            except Exception:
                pass

    def reload_tools(self) -> None:
        """按当前配置重建工具表。保存配置后调用，立即生效无需重启。

        进行中的聊天运行持有旧注册表引用，不受影响；新请求拿到新表。
        """
        with self._tools_lock:
            self._reload_tools()

    def reconfigure_scheduler(self) -> None:
        """配置接口（线程池内同步执行）热更新调度器：在运行中的循环里安排重启。"""
        if self.scheduler is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.scheduler.restart())

    def _reload_tools(self) -> None:
        # 配置热更新后让运行时跟随新设置（超时/配额/开关）
        if self.custom_runtime is not None:
            self.custom_runtime.settings = self.config.custom_tools
        self.registry = ToolRegistry()
        # 内置工具：角色/世界观内容表查询
        self.registry.register(QueryLoreTool())
        # 记忆工具：会话内跨窗口检索 + 更新本会话记忆（房间模式）
        self.registry.register(QueryChatMemoryTool())
        self.registry.register(UpdateChatMemoryTool())
        # 属性跟踪工具恒注册：角色卡自带属性时即生效；
        # 会话无属性上下文时工具返回"未启用属性跟踪"，不再靠全局开关决定注册
        self.registry.register(UpdateAttributesTool())
        # 读取图片：AI 需要确认/查看图片画面时调用（生图路径或白名单内图片）
        from .tools.read_image_tool import ReadImageTool

        self.registry.register(ReadImageTool())
        # AI 生图 / Galgame / P 站 / 联网 / 浏览器工具一律注册：是否暴露给模型
        # 由每请求的用户功能偏好过滤决定（flyai.feature_prefs.allowed_tool_names），
        # 不再按全局开关决定注册，普通用户才能覆盖全局默认。
        self.registry.register(
            GenerateImageTool(
                self.config.image_generation, self.config.stable_diffusion
            )
        )
        # 图生图：以对话已有图片为参考生成新图
        from .tools.edit_image_tool import EditImageTool

        self.registry.register(EditImageTool(self.config.image_generation))
        # Galgame 模式：背景/立绘绘制、AI 作曲、资产清单、场景切换、阻塞选项
        from .tools.galgame_tools import (
            GalgameChoiceTool,
            GalgameComposeMusicTool,
            GalgameDrawBackgroundTool,
            GalgameDrawSpriteTool,
            GalgameListAssetsTool,
            GalgameSetSceneTool,
        )

        for gal_tool in (
            GalgameDrawBackgroundTool(),
            GalgameDrawSpriteTool(),
            GalgameComposeMusicTool(),
            GalgameListAssetsTool(),
            GalgameSetSceneTool(),
            GalgameChoiceTool(),
        ):
            self.registry.register(gal_tool)
        # P 站搜图：搜索候选 → 视觉筛选 → 下载精选原图
        from .tools.pixiv_tool import PickPixivTool, SearchPixivTool

        self.registry.register(SearchPixivTool(self.config.pixiv))
        self.registry.register(PickPixivTool(self.config.pixiv))
        # 联网工具：web_search 返回结果列表，web_fetch 抓单页，由当前 AI 自主编排
        from .tools.web_search_tool import WebFetchTool, WebSearchTool

        self.registry.register(WebSearchTool(self.config.web_search))
        self.registry.register(WebFetchTool(self.config.web_search))
        # AI 浏览器（Playwright）：逐级 open/snapshot/click/type 浏览；
        # 管理器在首个可用用户请求时经 state.get_browser() 懒创建
        from .tools.browser_tool import (
            BrowserBackTool,
            BrowserClickTool,
            BrowserCloseTool,
            BrowserOpenTool,
            BrowserSnapshotTool,
            BrowserTextTool,
            BrowserTypeTool,
        )

        for tool_cls in (
            BrowserOpenTool,
            BrowserSnapshotTool,
            BrowserClickTool,
            BrowserTypeTool,
            BrowserBackTool,
            BrowserTextTool,
            BrowserCloseTool,
        ):
            self.registry.register(tool_cls())
        # 语音合成（websockets 是必装依赖，但以 ImportError 守卫保持启动韧性）
        if self.config.tts.enabled:
            try:
                from .tools.tts_tool import TextToSpeechTool

                self.registry.register(TextToSpeechTool(self.config.tts))
            except ImportError:
                logger.warning(
                    "语音合成已启用但未安装 websockets，请 pip install websockets"
                )
        # 会话工作区沙箱工具：默认不注册（sandbox.enabled=false 时与旧版一致）
        if self.config.sandbox.enabled:
            from .tools.fs_tools import (
                FsAppendTool,
                FsDeleteTool,
                FsListTool,
                FsReadTool,
                FsWriteTool,
            )

            for fs_tool in (
                FsListTool(),
                FsReadTool(),
                FsWriteTool(),
                FsAppendTool(),
                FsDeleteTool(),
            ):
                self.registry.register(fs_tool)
        # AI 自定义工具管理五件套：risk 工具，注册表执行期 admin 门禁；
        # 真正的自定义函数工具按请求 extras 注入，不进全局表。
        # runtime 在 lifespan 中创建，启动早期的 reload 直接跳过。
        if self.config.custom_tools.enabled and self.custom_runtime is not None:
            from .tools.custom_tool_manage import MANAGE_TOOLS

            for manage_tool_cls in MANAGE_TOOLS:
                self.registry.register(manage_tool_cls())
        # P2 长跑：设置先同步给状态机；工具仅在启用且管理器就绪时注册
        # （loop_finish 为请求级 extra，不在全局表注册）
        if self.loop_manager is not None:
            self.loop_manager.settings = self.config.loop
        if self.config.loop.enabled and self.loop_manager is not None:
            from .tools.loop_tools import LOOP_TOOLS

            for loop_tool_cls in LOOP_TOOLS:
                tool = loop_tool_cls()
                if tool.name == "loop_request":
                    tool.min_level = int(self.config.loop.request_min_level)
                self.registry.register(tool)
        # P3：设置热同步给 DAL/ticker（时区改名即时生效）；工具恒注册，
        # 是否暴露给模型由 feature_prefs 的 self_memory/proactor 类别过滤
        tzname = self.config.scheduler.timezone
        if self.mood_store is not None:
            self.mood_store.settings = self.config.mood
            self.mood_store.tzname = tzname
        if self.self_memory_store is not None:
            self.self_memory_store.settings = self.config.self_memory
            self.self_memory_store.tzname = tzname
        if self.proactor_store is not None:
            self.proactor_store.settings = self.config.proactor
            self.proactor_store.tzname = tzname
        if self.job_store is not None:
            self.job_store.tzname = tzname
        from .tools.proactor_tools import PROACTOR_TOOLS

        for proactor_tool_cls in PROACTOR_TOOLS:
            self.registry.register(proactor_tool_cls())
        # P4：MCP 设置热同步给管理器；watcher 管理三件套随 mcp 生态总闸注册
        # （桥接工具 mcp__* 是请求级 extras，不进全局表）
        if self.mcp_manager is not None:
            self.mcp_manager.settings = self.config.mcp
        if self.watcher_manager is not None:
            self.watcher_manager.settings = self.config.watchers
            self.watcher_manager.ext_settings = self.config.ext_trigger
        if self.config.mcp.enabled and self.watcher_manager is not None:
            from .tools.watcher_tools import WATCHER_TOOLS

            for watcher_tool_cls in WATCHER_TOOLS:
                # 重名时跳过（reload 会重建注册表，正常不会撞上）
                if self.registry.get(watcher_tool_cls.name) is None:
                    self.registry.register(watcher_tool_cls())
        self.loader = PluginLoader(self.registry)
        self.loader.load_all(self.config.tools.plugin_dirs)

    def get_provider(self, provider_id: Optional[str] = None) -> Optional[BaseProvider]:
        if provider_id:
            cfg = next((p for p in self.config.providers if p.id == provider_id), None)
        else:
            cfg = get_active_provider(self.config)
        if cfg is None:
            return None
        if cfg.id not in self._providers:
            self._providers[cfg.id] = create_provider(cfg)
        return self._providers[cfg.id]

    def reload_providers(self) -> None:
        for p in self._providers.values():
            pass  # 复用即可；如需强制刷新可关闭重建
        self._providers.clear()
        self._embedder = None  # 配置变更后重建语义检索客户端

    def get_embedder(self):
        """返回配置的语义检索客户端；未配置 embeddings 则返回 None。"""
        cfg = self.config.embeddings
        if not cfg.configured:
            return None
        if self._embedder is None:
            from .providers.embedding import EmbeddingClient

            self._embedder = EmbeddingClient(cfg)
        return self._embedder

    async def aclose_providers(self) -> None:
        for p in self._providers.values():
            try:
                await p.aclose()
            except Exception:
                pass
        if self._embedder is not None:
            try:
                await self._embedder.aclose()
            except Exception:
                pass
            self._embedder = None
        self._providers.clear()


def create_app(config_path: Optional[Path] = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            state = AppState(config=load_config(config_path), config_path=config_path)
        except ConfigError as exc:
            logger.error("启动失败: %s", exc)
            raise
        data_dir = Path(__file__).resolve().parent.parent / "data"
        migrate_legacy(data_dir)
        state.data_dir = data_dir
        # 自定义工具运行时先于工具表重建：管理工具注册与请求级 extras 都靠它
        state.custom_runtime = CustomToolRuntime(
            data_dir, state.config.custom_tools
        )
        # P2 长跑状态机：先 reconcile 遗留 running，再开放新任务
        state.loop_manager = LoopManager(data_dir, state.config.loop)
        try:
            reconciled = state.loop_manager.reconcile_all()
            if reconciled:
                logger.warning("长跑 reconcile：%d 个遗留运行置为 interrupted",
                               reconciled)
        except Exception:
            logger.exception("长跑 reconcile 失败，不阻断启动")
        # P3 存储层：心情/自我记忆/主动行为/作业/承诺（每用户 agent.db 短连接）
        tzname = state.config.scheduler.timezone
        from .agentdb.self_memory import SelfMemoryStore
        from .proactor.mood import MoodStore
        from .proactor.proactor import ProactorStore, ProactorTicker
        from .proactor.reminders import CommitmentStore, ReminderTicker
        from .proactor.scheduler_jobs import JobStore, JobTicker

        state.mood_store = MoodStore(data_dir, state.config.mood, tzname)
        state.self_memory_store = SelfMemoryStore(
            data_dir, state.config.self_memory, tzname
        )
        state.proactor_store = ProactorStore(
            data_dir, state.config.proactor, tzname
        )
        state.job_store = JobStore(data_dir, tzname)
        state.commitment_store = CommitmentStore(data_dir)
        # P4：提示词注册表/外部触发冷却/MCP 管理器（admin agent.db + 懒连接）
        from .mcp_client.manager import McpManager
        from .proactor.prompt_registry import PromptRegistry
        from .proactor.trigger import TriggerCooldown

        state.prompt_registry = PromptRegistry(data_dir)
        state.trigger_cooldown = TriggerCooldown(
            int(state.config.ext_trigger.cooldown_seconds)
        )
        state.mcp_manager = McpManager(data_dir, state.config.mcp)
        # P3 存储可选挂载注册表：未挂载时各自退回内置默认文本
        state.mood_store.prompts = state.prompt_registry
        state.self_memory_store.prompts = state.prompt_registry
        state.reload_tools()
        # 令牌持久化：重启服务不再全部掉登录态（data/ 在 .gitignore 中）
        state.auth_sessions.bind_file(data_dir / "auth_sessions.json")
        state.user_store = UserStore(data_dir)
        # 公共市场（跨用户）：条目元数据 + 快照载荷，目录由 store 自建
        state.market = MarketStore(data_dir / "market")
        # P4 watcher：依赖用户库做全量 reconcile，故在 user_store 就绪后创建
        from .proactor.watchers import WatcherManager

        state.watcher_manager = WatcherManager(
            data_dir, state.config.watchers, state.config.ext_trigger,
            state.user_store,
        )
        try:
            watcher_stopped = state.watcher_manager.reconcile_all()
            if watcher_stopped:
                logger.warning(
                    "watcher reconcile：%d 个遗留进程标记 stopped",
                    watcher_stopped,
                )
        except Exception:
            logger.exception("watcher reconcile 失败，不阻断启动")
        # MCP 媒体 6h TTL 启动清扫
        try:
            swept = state.mcp_manager.sweep_media()
            if swept:
                logger.info("MCP 媒体清扫：删除 %d 个过期文件", swept)
        except Exception:
            logger.exception("MCP 媒体清扫失败，不阻断启动")
        # AI 浏览器管理器已在 reload_tools 内按启用状态懒创建
        (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        # 后台调度服务：enabled=false 时内部直接待命，领导锁保证多 worker 单实例
        state.scheduler = SchedulerService(state)
        proactor_ticker = ProactorTicker(state, state.proactor_store)
        proactor_ticker.prompts = state.prompt_registry
        state.scheduler.add_ticker(proactor_ticker)
        state.scheduler.add_ticker(JobTicker(state, state.job_store))
        state.scheduler.add_ticker(ReminderTicker(
            state, state.commitment_store, state.proactor_store,
            check_seconds=state.config.scheduler.reminder_check_seconds,
        ))
        await state.scheduler.start()
        app.state.flyai = state
        logger.info(
            "FlyAI 启动完成: active_provider=%s tools=%d plugins=%d users=%d",
            state.config.active_provider,
            len(state.registry.all()),
            len(state.loader.list_status()),
            len(state.user_store.list()),
        )
        yield
        if state.scheduler is not None:
            await state.scheduler.aclose()
        if state.mcp_manager is not None:
            await state.mcp_manager.aclose()
        await state.run_manager.aclose()
        if state.browser is not None:
            await state.browser.aclose()
        if state.search_browser is not None:
            await state.search_browser.aclose()
        await state.aclose_providers()

    app = FastAPI(title="FlyAI", lifespan=lifespan)

    # APK（Capacitor WebView）源：页面内置在安装包、接口跨域到本站，需放行
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://localhost",       # Capacitor Android 默认源
            "http://localhost",        # androidScheme=http 时
            "capacitor://localhost",   # Capacitor iOS 源
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def no_cache_app_shell(request, call_next):
        """应用外壳（index 与 /static 下的脚本/样式）每次带 ETag 重新校验。

        StaticFiles 默认不发 Cache-Control，浏览器启发式缓存会在覆盖部署后
        继续使用旧 app.js（历史回显依赖其中的标记解析），导致新功能静默失效。
        no-cache 不是不缓存，而是使用前必须向服务器校验，未变返回 304。
        """
        resp = await call_next(request)
        path = request.url.path
        # 32MB 的 GS 音色库：内容按文件名固化，给一年 immutable 缓存，
        # 只下载一次；其余 /static 资源维持使用前校验
        if path.startswith("/static/") and path.endswith(".sf2"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path == "/" or path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # 路由
    from .api.routes_agentdb import router as agentdb_router
    from .api.routes_auth import router as auth_router
    from .api.routes_ext import router as ext_router
    from .api.routes_chat import router as chat_router
    from .api.routes_config import router as config_router
    from .api.routes_galgame import router as galgame_router
    from .api.routes_gen import router as gen_router
    from .api.routes_lore import router as lore_router
    from .api.routes_loop import router as loop_router
    from .api.routes_market import router as market_router
    from .api.routes_memory import router as memory_router
    from .api.routes_plugins import router as plugins_router
    from .api.routes_preset import router as preset_router
    from .api.routes_proactor import router as proactor_router
    from .api.routes_room import router as room_router
    from .api.routes_session import router as session_router

    app.include_router(agentdb_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(ext_router)
    app.include_router(galgame_router)
    app.include_router(gen_router)
    app.include_router(lore_router)
    app.include_router(loop_router)
    app.include_router(market_router)
    app.include_router(memory_router)
    app.include_router(plugins_router)
    app.include_router(preset_router)
    app.include_router(proactor_router)
    app.include_router(room_router)
    app.include_router(session_router)

    # 静态资源与头像上传目录（挂载目录需先存在；各用户上传在 /uploads/<uid>/...）
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    upload_dir = PROJECT_ROOT / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(str(WEB_DIR / "index.html"))

    @app.get("/login", include_in_schema=False)
    async def login_page():
        # 新 SPA 取消独立 login.html：未登录访问 /login 由 #/login 渲染登录页，
        # 这里直接返回 index.html，兼容旧 APK 外壳与书签链接
        return FileResponse(str(WEB_DIR / "index.html"))

    # SPA 从页面根目录取运行期配置与图标（资源本身挂在 /static/，这两个在产物根）
    @app.get("/config.json", include_in_schema=False)
    async def runtime_config():
        return FileResponse(str(WEB_DIR / "config.json"))

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon():
        return FileResponse(str(WEB_DIR / "favicon.svg"))

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def root_files(full_path: str, request: Request):
        """根目录兜底：未匹配任何路由时，从 server.web_root 寻找同名文件。

        命中则返回，未找到或目标越界一律 404。
        """
        state = request.app.state.flyai
        web_root = (state.config.server.web_root or "webroot").strip()
        root = Path(web_root)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        target = (root / full_path).resolve()
        try:
            target.relative_to(root.resolve())
        except (OSError, ValueError):
            raise HTTPException(404)
        if not target.is_file():
            raise HTTPException(404)
        return FileResponse(str(target))

    return app


app = create_app()