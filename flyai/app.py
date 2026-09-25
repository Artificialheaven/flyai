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

from .config import AppConfig, PROJECT_ROOT, get_active_provider, load_config, save_config
from .errors import ConfigError
from .providers.base import BaseProvider
from .providers.factory import create_provider
from .tools.attributes_tool import UpdateAttributesTool
from .tools.loader import PluginLoader
from .tools.lore_tool import QueryLoreTool
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
    _providers: Dict[str, BaseProvider] = field(default_factory=dict)
    _scopes: Dict[str, UserScope] = field(default_factory=dict)

    def user(self, request: Request) -> UserScope:
        """按请求的 X-User-Id 头解析单个用户的数据作用域；未登录返回 401。

        管理员（ADMIN_UID）凭据在 config，不在用户库；也拥有独立数据作用域。
        """
        uid = (request.headers.get("x-user-id") or "").strip()
        valid = (uid == ADMIN_UID) or bool(self.user_store.get_by_id(uid))
        if not uid or not valid:
            raise HTTPException(401, "未登录或用户不存在")
        scope = self._scopes.get(uid)
        if scope is None:
            scope = UserScope.build(uid, self.config, self.data_dir)
            self._scopes[uid] = scope
        return scope

    def reload_tools(self) -> None:
        self.registry = ToolRegistry()
        # 内置工具：角色/世界观内容表查询
        self.registry.register(QueryLoreTool())
        # 属性跟踪工具（可开关）
        if self.config.roleplay.attributes.enabled:
            self.registry.register(UpdateAttributesTool())
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

    async def aclose_providers(self) -> None:
        for p in self._providers.values():
            try:
                await p.aclose()
            except Exception:
                pass
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
        state.user_store = UserStore(data_dir)
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
        await state.aclose_providers()

    app = FastAPI(title="FlyAI", lifespan=lifespan)

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

    return app


app = create_app()