"""应用工厂与模块级 app。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Request
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
from .tools.loader import PluginLoader
from .tools.lore_tool import QueryLoreTool
from .tools.memory_tool import QueryChatMemoryTool, UpdateChatMemoryTool
from .tools.registry import ToolRegistry
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
    _providers: Dict[str, BaseProvider] = field(default_factory=dict)
    _scopes: Dict[str, UserScope] = field(default_factory=dict)
    _embedder: "EmbeddingClient" = None
    # 登录凭证（随机 token）与登录限速
    auth_sessions: AuthSessionStore = field(default_factory=AuthSessionStore)
    login_limiter: LoginRateLimiter = field(default_factory=LoginRateLimiter)
    # 待确认的压缩提案：{proposal_id: {uid,sid,room,raw,basesig,ts}}
    _compress_proposals: Dict[str, dict] = field(default_factory=dict)

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

    def reload_tools(self) -> None:
        self.registry = ToolRegistry()
        # 内置工具：角色/世界观内容表查询
        self.registry.register(QueryLoreTool())
        # 记忆工具：跨会话检索 + 更新本会话记忆（房间模式）
        self.registry.register(QueryChatMemoryTool())
        self.registry.register(UpdateChatMemoryTool())
        # 属性跟踪工具恒注册：角色卡自带属性时即生效；
        # 会话无属性上下文时工具返回"未启用属性跟踪"，不再靠全局开关决定注册
        self.registry.register(UpdateAttributesTool())
        # 读取图片：AI 需要确认/查看图片画面时调用（生图路径或白名单内图片）
        from .tools.read_image_tool import ReadImageTool

        self.registry.register(ReadImageTool())
        # AI 生图工具（可开关）：具体模型在运行时由 services.image_models 提供
        if self.config.image_generation.enabled:
            self.registry.register(
                GenerateImageTool(
                    self.config.image_generation, self.config.stable_diffusion
                )
            )
            # 图生图：以对话已有图片为参考生成新图
            from .tools.edit_image_tool import EditImageTool

            self.registry.register(EditImageTool(self.config.image_generation))
        # P 站搜图：搜索候选 → 视觉筛选 → 下载精选原图
        if self.config.pixiv.enabled:
            from .tools.pixiv_tool import PickPixivTool, SearchPixivTool

            self.registry.register(SearchPixivTool(self.config.pixiv))
            self.registry.register(PickPixivTool(self.config.pixiv))
        # 联网工具：web_search 返回结果列表，web_fetch 抓单页，由当前 AI 自主编排
        if self.config.web_search.enabled:
            from .tools.web_search_tool import WebFetchTool, WebSearchTool

            self.registry.register(WebSearchTool(self.config.web_search))
            self.registry.register(WebFetchTool(self.config.web_search))
        # AI 浏览器（Playwright）：逐级 open/snapshot/click/type 浏览
        if self.config.browser.enabled:
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
        state.reload_tools()
        data_dir = Path(__file__).resolve().parent.parent / "data"
        migrate_legacy(data_dir)
        state.data_dir = data_dir
        # 令牌持久化：重启服务不再全部掉登录态（data/ 在 .gitignore 中）
        state.auth_sessions.bind_file(data_dir / "auth_sessions.json")
        state.user_store = UserStore(data_dir)
        # AI 浏览器管理器（enabled=false 时工具不注册，管理器也不创建）
        if state.config.browser.enabled:
            from .browser.manager import BrowserSessionManager

            state.browser = BrowserSessionManager(state)
        (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        app.state.flyai = state
        logger.info(
            "FlyAI 启动完成: active_provider=%s tools=%d plugins=%d users=%d",
            state.config.active_provider,
            len(state.registry.all()),
            len(state.loader.list_status()),
            len(state.user_store.list()),
        )
        yield
        if state.browser is not None:
            await state.browser.aclose()
        await state.aclose_providers()

    app = FastAPI(title="FlyAI", lifespan=lifespan)

    @app.middleware("http")
    async def no_cache_app_shell(request, call_next):
        """应用外壳（index 与 /static 下的脚本/样式）每次带 ETag 重新校验。

        StaticFiles 默认不发 Cache-Control，浏览器启发式缓存会在覆盖部署后
        继续使用旧 app.js（历史回显依赖其中的标记解析），导致新功能静默失效。
        no-cache 不是不缓存，而是使用前必须向服务器校验，未变返回 304。
        """
        resp = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    # 路由
    from .api.routes_auth import router as auth_router
    from .api.routes_chat import router as chat_router
    from .api.routes_config import router as config_router
    from .api.routes_gen import router as gen_router
    from .api.routes_lore import router as lore_router
    from .api.routes_plugins import router as plugins_router
    from .api.routes_room import router as room_router
    from .api.routes_session import router as session_router

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(gen_router)
    app.include_router(lore_router)
    app.include_router(plugins_router)
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
        return FileResponse(str(WEB_DIR / "login.html"))

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