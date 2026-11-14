"""配置读写接口。api_key 掩码显示、保存时掩码串不覆盖真实 key。

- GET /api/config：按当前用户权限过滤不可见模型。
- PUT /api/config：仅管理员（全局配置）。
- /api/models/...：每用户自建模型 CRUD（仅可操作归属自己的条目；
  共享设置仅管理员）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..config import (
    AppConfig,
    EmbeddingsConfig,
    ImageGenSettings,
    ImageModelConfig,
    PixivSettings,
    ProviderConfig,
    StableDiffusionSettings,
    WebSearchSettings,
    BrowserSettings,
    TtsSettings,
    RoleplaySettings,
    SandboxSettings,
    SchedulerSettings,
    CustomToolSettings,
    LoopSettings,
    McpSettings,
    MoodSettings,
    ExtTriggerSettings,
    ProactorSettings,
    SelfMemorySettings,
    WatcherSettings,
    mask_api_key,
    save_config,
)
from ..models_access import model_visible, visible_image_models, visible_providers
from ..auth_sessions import resolve_uid
from ..ids import InvalidName, check_model_id
from ..user import ADMIN_UID
from ..errors import ProviderError  # noqa: F401

router = APIRouter(prefix="/api", tags=["config"])


class ProviderPayload(BaseModel):
    id: Optional[str] = None
    type: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    params: dict = {}
    # 深度思考开关开启时合并的请求参数；None=不传（保留原值）
    thinking_params: Optional[dict] = None
    # 开关关闭时合并的请求参数（关掉默认思考的模型）；None=不传
    thinking_off_params: Optional[dict] = None
    vision: bool = False
    owner: Optional[str] = None
    shared: Optional[bool] = None
    min_level: Optional[int] = None
    # 计费倍率（普通用户每轮扣 1×rate）；None=不改
    rate: Optional[float] = None


class ImageModelPayload(BaseModel):
    id: Optional[str] = None
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    owner: Optional[str] = None
    shared: Optional[bool] = None
    min_level: Optional[int] = None


class SharePayload(BaseModel):
    shared: bool = False
    min_level: int = 0


class ConfigPayload(BaseModel):
    active_provider: Optional[str] = None
    providers: Optional[list] = None
    tools: Optional[dict] = None
    roleplay: Optional[dict] = None
    embeddings: Optional[dict] = None
    image_generation: Optional[dict] = None
    image_models: Optional[list] = None
    stable_diffusion: Optional[dict] = None
    pixiv: Optional[dict] = None
    web_search: Optional[dict] = None
    browser: Optional[dict] = None
    tts: Optional[dict] = None
    galgame: Optional[dict] = None
    sandbox: Optional[dict] = None
    scheduler: Optional[dict] = None
    custom_tools: Optional[dict] = None
    loop: Optional[dict] = None
    # P3 心情/自我记忆/主动行为（均无密钥，整段读写）
    mood: Optional[dict] = None
    self_memory: Optional[dict] = None
    proactor: Optional[dict] = None
    # P4 生态扩展：mcp/watchers 无密钥；ext_trigger 含令牌，单独脱敏
    mcp: Optional[dict] = None
    ext_trigger: Optional[dict] = None
    watchers: Optional[dict] = None


class TestPayload(BaseModel):
    provider_id: Optional[str] = None


# ---- 视图 ----

def _masked_provider(p: ProviderConfig) -> dict:
    d = p.dict()
    d["api_key"] = mask_api_key(p.api_key) if p.api_key else ""
    return d


def _masked_image_model(m: ImageModelConfig) -> dict:
    d = m.dict()
    d["api_key"] = mask_api_key(m.api_key) if m.api_key else ""
    return d


def _public_provider(p: ProviderConfig) -> dict:
    """他人共享模型对普通用户的精简视图：不含 base_url/归属/api_key/参数。"""
    return {
        "id": p.id,
        "model": p.model,
        "vision": p.vision,
        "rate": p.rate,
        "min_level": p.min_level,
    }


def _public_image_model(m: ImageModelConfig) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "model": m.model,
        "min_level": m.min_level,
    }


def _masked_config(state, uid: Optional[str] = None):
    """返回脱敏后的配置视图。指定 uid 时按权限过滤模型。"""
    cfg = state.config
    uid = uid if uid is not None else ""
    level = 0
    if uid == ADMIN_UID:
        providers = list(cfg.providers)
        image_models = list(cfg.image_models)
    elif uid:
        scope = None
        u = state.user_store.get_by_id(uid)
        level = int(u.level or 0) if u else 0
        providers = visible_providers(cfg, uid, level)
        image_models = visible_image_models(cfg, uid, level)
    else:
        providers = []
        image_models = []

    # 模型视图：管理员全量；普通用户对「本人自建」拿到可编辑视图（含自己的
    # base_url/掩码 key），对「他人共享」只拿精简视图（无 URL/归属/key/参数）
    provider_views = []
    for p in providers:
        if uid == ADMIN_UID or p.owner == uid:
            provider_views.append(_masked_provider(p))
        else:
            provider_views.append(_public_provider(p))
    image_views = []
    for m in image_models:
        if uid == ADMIN_UID or m.owner == uid:
            image_views.append(_masked_image_model(m))
        else:
            image_views.append(_public_image_model(m))

    out = {
        "active_provider": cfg.active_provider,
        "providers": provider_views,
        "tools": cfg.tools.dict(),
        "history": cfg.history.dict(),
        "server": cfg.server.dict(),
        "roleplay": cfg.roleplay.dict(),
        "image_generation": cfg.image_generation.dict(),
        "image_models": image_views,
        # SD 段不含密钥，对所有登录用户可见（前端据此显示引擎开关）
        "stable_diffusion": cfg.stable_diffusion.dict(),
        "pixiv": cfg.pixiv.dict(),
        "web_search": cfg.web_search.dict(),
        "browser": cfg.browser.dict(),
        "galgame": cfg.galgame.dict(),
        "tts": _masked_tts(cfg),
        # 沙箱/调度器/自定义工具/长跑段不含密钥，对所有登录用户可见（同 SD 段）
        "sandbox": cfg.sandbox.dict(),
        "scheduler": cfg.scheduler.dict(),
        "custom_tools": cfg.custom_tools.dict(),
        "loop": cfg.loop.dict(),
        # P3 三段不含密钥，与 scheduler 同策略对所有登录用户可见
        "mood": cfg.mood.dict(),
        "self_memory": cfg.self_memory.dict(),
        "proactor": cfg.proactor.dict(),
        # P4：mcp/watchers 不含密钥，同策略对所有登录用户可见
        "mcp": cfg.mcp.dict(),
        "watchers": cfg.watchers.dict(),
    }
    # 嵌入配置含 api_key（即使掩码）与内部缓存路径，不对普通用户下发
    if uid == ADMIN_UID:
        emb = cfg.embeddings.dict()
        emb["api_key"] = mask_api_key(cfg.embeddings.api_key) if cfg.embeddings.api_key else ""
        out["embeddings"] = emb
        # 外部触发令牌绝不下发明文，只给「是否已配置」标志位
        ext = cfg.ext_trigger.dict()
        ext["token_set"] = bool(ext.get("token"))
        ext["token"] = ""
        out["ext_trigger"] = ext
    return out


def _masked_tts(cfg: AppConfig) -> dict:
    d = cfg.tts.dict()
    d["api_key"] = mask_api_key(cfg.tts.api_key) if cfg.tts.api_key else ""
    return d


def _credit_key(incoming: str, existing: str) -> str:
    """若 incoming 是掩码串（用户未改 key），保留原 key。"""
    if incoming and incoming == mask_api_key(existing) and existing:
        return existing
    return incoming


def _uid(request: Request) -> str:
    state = request.app.state.flyai
    uid = (resolve_uid(state, request) or "").strip()
    valid = (uid == ADMIN_UID) or bool(state.user_store.get_by_id(uid))
    if not uid or not valid:
        raise HTTPException(401, "未登录或登录已失效")
    return uid


def _persist(state) -> None:
    save_config(state.config, state.config_path)
    state.reload_providers()
    # 按最新配置热重建工具表：API Key / 启用开关等保存后立即生效，无需重启
    state.reload_tools()


# ---- 全局配置 ----

@router.get("/config")
def get_config(request: Request):
    uid = _uid(request)
    return _masked_config(request.app.state.flyai, uid)


@router.put("/config")
def put_config(payload: ConfigPayload, request: Request):
    uid = _uid(request)
    if uid != ADMIN_UID:
        raise HTTPException(403, "全局配置仅管理员可修改，请使用「我的模型」管理自建模型")
    state = request.app.state.flyai
    cfg = state.config

    if payload.providers is not None:
        new_providers = []
        for pdata in payload.providers:
            old = next((p for p in cfg.providers if p.id == pdata.get("id")), None)
            pdata = dict(pdata)
            if old is not None:
                pdata["api_key"] = _credit_key(pdata.get("api_key", ""), old.api_key)
            new_providers.append(pdata)
        cfg.providers = [ProviderConfig.parse_obj(p) for p in new_providers]

    if payload.active_provider is not None:
        cfg.active_provider = payload.active_provider

    if payload.tools is not None:
        cfg.tools = type(cfg.tools).parse_obj(payload.tools)

    if payload.roleplay is not None:
        cfg.roleplay = RoleplaySettings.parse_obj(payload.roleplay)

    if payload.embeddings is not None:
        old = cfg.embeddings
        ed = dict(payload.embeddings)
        ed["api_key"] = _credit_key(ed.get("api_key", ""), old.api_key)
        ed["cache_file"] = ed.get("cache_file") or old.cache_file
        cfg.embeddings = EmbeddingsConfig.parse_obj(ed)

    if payload.image_generation is not None:
        igd = dict(payload.image_generation)
        old = cfg.image_generation
        cfg.image_generation = ImageGenSettings(
            enabled=igd.get("enabled", old.enabled),
            default_size=igd.get("default_size") or old.default_size,
        )

    if payload.image_models is not None:
        new_models = []
        for mdata in payload.image_models:
            old = next((m for m in cfg.image_models if m.id == mdata.get("id")), None)
            mdata = dict(mdata)
            if old is not None:
                mdata["api_key"] = _credit_key(mdata.get("api_key", ""), old.api_key)
            new_models.append(mdata)
        cfg.image_models = [ImageModelConfig.parse_obj(m) for m in new_models]

    if payload.stable_diffusion is not None:
        cfg.stable_diffusion = StableDiffusionSettings.parse_obj(
            dict(payload.stable_diffusion)
        )

    if payload.pixiv is not None:
        pxd = dict(payload.pixiv)
        old = cfg.pixiv
        pxd["api_base"] = pxd.get("api_base") or old.api_base
        cfg.pixiv = PixivSettings.parse_obj(pxd)

    if payload.web_search is not None:
        cfg.web_search = WebSearchSettings.parse_obj(dict(payload.web_search))
        # 代理/引擎等设置可能已变：关掉旧浏览器搜索器，下次使用按新配置重建
        old = state.search_browser
        if old is not None:
            state.search_browser = None
            try:
                asyncio.create_task(old.aclose())
            except RuntimeError:
                pass

    if payload.browser is not None:
        cfg.browser = BrowserSettings.parse_obj(dict(payload.browser))

    if payload.tts is not None:
        td = dict(payload.tts)
        td["api_key"] = _credit_key(td.get("api_key", ""), cfg.tts.api_key)
        cfg.tts = TtsSettings.parse_obj(td)

    if payload.galgame is not None:
        cfg.galgame = type(cfg.galgame).parse_obj(dict(payload.galgame))

    if payload.sandbox is not None:
        cfg.sandbox = SandboxSettings.parse_obj(dict(payload.sandbox))

    scheduler_changed = payload.scheduler is not None
    if scheduler_changed:
        cfg.scheduler = SchedulerSettings.parse_obj(dict(payload.scheduler))

    if payload.custom_tools is not None:
        # 热重建工具表时 _reload_tools 会把新设置同步给 custom_runtime
        cfg.custom_tools = CustomToolSettings.parse_obj(dict(payload.custom_tools))

    if payload.loop is not None:
        # 热重建工具表时 _reload_tools 会注册/摘除 loop 工具并同步 loop_manager
        cfg.loop = LoopSettings.parse_obj(dict(payload.loop))

    if payload.mood is not None:
        # _reload_tools 会把新设置同步给 mood_store
        cfg.mood = MoodSettings.parse_obj(dict(payload.mood))

    if payload.self_memory is not None:
        cfg.self_memory = SelfMemorySettings.parse_obj(dict(payload.self_memory))

    if payload.proactor is not None:
        # ticker 每 tick 读 store.settings，保存后自然热生效
        cfg.proactor = ProactorSettings.parse_obj(dict(payload.proactor))

    if payload.mcp is not None:
        # _reload_tools 会把新设置同步给 mcp_manager
        cfg.mcp = McpSettings.parse_obj(dict(payload.mcp))

    if payload.watchers is not None:
        cfg.watchers = WatcherSettings.parse_obj(dict(payload.watchers))
        watcher_mgr = getattr(state, "watcher_manager", None)
        if watcher_mgr is not None:
            watcher_mgr.settings = cfg.watchers

    if payload.ext_trigger is not None:
        # 空令牌=不改原令牌（前端只拿得到 token_set 标志位）
        data = dict(payload.ext_trigger)
        incoming = (data.get("token") or "").strip()
        if not incoming:
            data["token"] = cfg.ext_trigger.token
        cfg.ext_trigger = ExtTriggerSettings.parse_obj(data)
        cooldown = getattr(state, "trigger_cooldown", None)
        if cooldown is not None:
            cooldown.cooldown_seconds = cfg.ext_trigger.cooldown_seconds
        watcher_mgr = getattr(state, "watcher_manager", None)
        if watcher_mgr is not None:
            watcher_mgr.ext_settings = cfg.ext_trigger

    _persist(state)
    if scheduler_changed:
        # 调度器开关/周期/时区热生效：在运行循环内安排重启
        state.reconfigure_scheduler()
    return _masked_config(state, ADMIN_UID)


@router.post("/config/activate/{provider_id}")
def activate_provider(provider_id: str, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    if uid == ADMIN_UID:
        if not any(p.id == provider_id for p in state.config.providers):
            raise HTTPException(404, "提供商不存在")
        state.config.active_provider = provider_id
        save_config(state.config, state.config_path)
        state.reload_providers()
        return {"ok": True}
    # 普通用户：校验该模型对自己可见；选择记录由前端本地保存
    u = state.user_store.get_by_id(uid)
    level = int(u.level or 0) if u else 0
    p = next((x for x in visible_providers(state.config, uid, level)
              if x.id == provider_id), None)
    if p is None:
        raise HTTPException(404, "提供商不存在或不可用")
    return {"ok": True}


@router.post("/config/test")
async def test_connection(payload: TestPayload, request: Request):
    state = request.app.state.flyai
    provider = state.get_provider(payload.provider_id)
    if provider is None:
        raise HTTPException(400, "提供商不可用")
    return {"ok": True, "message": "连通性检测（v1 简化为状态可用）"}


# ---- 用户个人设置（data/{uid}/settings.json，所有登录用户可用）----

class MySettingsPayload(BaseModel):
    keep_turns: Optional[int] = 0
    auto_recall: Optional[bool] = False
    auto_recall_max: Optional[int] = 5
    extract_every_turns: Optional[int] = 10
    lore_auto_inject: Optional[bool] = True
    max_tokens: Optional[int] = None

    # 功能开关覆盖：None（JSON null）= 跟随全局默认
    tools_enabled: Optional[bool] = None
    tool_max_iterations: Optional[int] = None
    tool_timeout_seconds: Optional[int] = None
    roleplay_format_enabled: Optional[bool] = None
    roleplay_attributes_enabled: Optional[bool] = None
    intro_chars: Optional[int] = None
    background_chars: Optional[int] = None
    world_chars: Optional[int] = None
    mes_example_chars: Optional[int] = None
    pinned_chars: Optional[int] = None
    web_enabled: Optional[bool] = None
    browser_enabled: Optional[bool] = None
    pixiv_enabled: Optional[bool] = None
    image_enabled: Optional[bool] = None
    galgame_enabled: Optional[bool] = None
    galgame_max_loops: Optional[int] = None


@router.get("/my/settings")
def get_my_settings(request: Request):
    scope = request.app.state.flyai.user(request)
    return scope.user_settings.to_dict()


@router.put("/my/settings")
def put_my_settings(payload: MySettingsPayload, request: Request):
    scope = request.app.state.flyai.user(request)

    def _as_int(value, name, default, minimum, maximum):
        try:
            n = int(value if value is not None else default)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{name} 必须是整数")
        return max(minimum, min(maximum, n))

    def _opt_int(value, name, minimum, maximum):
        if value is None:
            return None
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{name} 必须是整数或 null")
        return max(minimum, min(maximum, n))

    scope.user_settings.keep_turns = _as_int(
        payload.keep_turns, "keep_turns", 0, 0, 500)
    scope.user_settings.auto_recall = bool(payload.auto_recall)
    scope.user_settings.auto_recall_max = _as_int(
        payload.auto_recall_max, "auto_recall_max", 5, 1, 20)
    scope.user_settings.extract_every_turns = _as_int(
        payload.extract_every_turns, "extract_every_turns", 10, 0, 500)
    scope.user_settings.lore_auto_inject = bool(payload.lore_auto_inject)
    # 0/空 = 跟随提供商配置，归一化为 None 落盘
    scope.user_settings.max_tokens = _opt_int(
        payload.max_tokens, "max_tokens", 0, 100000) or None

    # ---- 功能开关覆盖（None=跟随全局默认）----
    us = scope.user_settings

    us.tools_enabled = (None if payload.tools_enabled is None
                        else bool(payload.tools_enabled))
    us.tool_max_iterations = _opt_int(
        payload.tool_max_iterations, "tool_max_iterations", 0, 40)
    us.tool_timeout_seconds = _opt_int(
        payload.tool_timeout_seconds, "tool_timeout_seconds", 5, 600)
    us.roleplay_format_enabled = (
        None if payload.roleplay_format_enabled is None
        else bool(payload.roleplay_format_enabled))
    us.roleplay_attributes_enabled = (
        None if payload.roleplay_attributes_enabled is None
        else bool(payload.roleplay_attributes_enabled))
    for key, label in (
        ("intro_chars", "intro_chars"),
        ("background_chars", "background_chars"),
        ("world_chars", "world_chars"),
        ("mes_example_chars", "mes_example_chars"),
        ("pinned_chars", "pinned_chars"),
    ):
        setattr(us, key, _opt_int(getattr(payload, key), label, 0, 50000))
    for key in ("web_enabled", "browser_enabled", "pixiv_enabled",
                "image_enabled", "galgame_enabled"):
        v = getattr(payload, key)
        setattr(us, key, None if v is None else bool(v))
    us.galgame_max_loops = _opt_int(
        payload.galgame_max_loops, "galgame_max_loops", 1, 100)

    scope.save_user_settings()
    return scope.user_settings.to_dict()


@router.get("/my/quota")
def get_my_quota(request: Request):
    """当前用户对话次数额度；管理员返回 {"unlimited": true}。"""
    state = request.app.state.flyai
    uid = _uid(request)
    if uid == ADMIN_UID:
        return {"unlimited": True}
    from ..billing import quota_view

    q = quota_view(state.user_store, uid)
    return q if q is not None else {"unlimited": True}


# ---- 自建 LLM 提供商 CRUD ----

def _find_provider(cfg, mid):
    return next((p for p in cfg.providers if p.id == mid), None)


def _can_operate(item, uid) -> None:
    if item is None:
        raise HTTPException(404, "模型不存在")
    if item.owner != uid and uid != ADMIN_UID:
        raise HTTPException(403, "只能操作归属自己的模型")


@router.post("/models/providers")
def create_provider_model(payload: ProviderPayload, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    mid = (payload.id or "").strip() or uuid.uuid4().hex
    try:
        check_model_id(mid)
    except InvalidName:
        raise HTTPException(
            400, "模型 ID 只能以字母或数字开头，仅含字母/数字/点/下划线/短横线"
        )
    if _find_provider(state.config, mid) is not None:
        raise HTTPException(400, "模型 ID 已存在")
    item = ProviderConfig(
        id=mid,
        type=payload.type,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
        params=payload.params or {},
        thinking_params=payload.thinking_params or {},
        thinking_off_params=payload.thinking_off_params or {},
        vision=payload.vision,
        owner=uid,
        shared=False,
        min_level=0,
        rate=(payload.rate if payload.rate is not None else 1.0),
    )
    state.config.providers.append(item)
    _persist(state)
    return _masked_provider(item)


@router.put("/models/providers/{mid}")
def update_provider_model(mid: str, payload: ProviderPayload, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    item = _find_provider(state.config, mid)
    _can_operate(item, uid)
    data = _masked_provider(item)
    merged = {
        "id": item.id,
        "type": payload.type or item.type,
        "base_url": payload.base_url if payload.base_url else item.base_url,
        "api_key": _credit_key(payload.api_key, item.api_key),
        "model": payload.model if payload.model else item.model,
        "params": payload.params or item.params,
        "thinking_params": payload.thinking_params or item.thinking_params,
        "thinking_off_params": payload.thinking_off_params or item.thinking_off_params,
        "vision": payload.vision,
        "owner": item.owner,
        "shared": item.shared,
        "min_level": item.min_level,
        "rate": (payload.rate if payload.rate is not None else item.rate),
    }
    new_item = ProviderConfig.parse_obj(merged)
    idx = state.config.providers.index(item)
    state.config.providers[idx] = new_item
    _persist(state)
    return _masked_provider(new_item)


@router.delete("/models/providers/{mid}")
def delete_provider_model(mid: str, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    item = _find_provider(state.config, mid)
    _can_operate(item, uid)
    state.config.providers.remove(item)
    _persist(state)
    return {"ok": True}


@router.post("/models/providers/{mid}/share")
def share_provider_model(mid: str, payload: SharePayload, request: Request):
    uid = _uid(request)
    if uid != ADMIN_UID:
        raise HTTPException(403, "共享设置仅管理员可操作")
    state = request.app.state.flyai
    item = _find_provider(state.config, mid)
    if item is None:
        raise HTTPException(404, "模型不存在")
    item.shared = bool(payload.shared)
    item.min_level = max(0, min(int(payload.min_level or 0), 255))
    _persist(state)
    return _masked_provider(item)


# ---- 自建生图模型 CRUD ----

def _find_image_model(cfg, mid):
    return next((m for m in cfg.image_models if m.id == mid), None)


@router.post("/models/images")
def create_image_model(payload: ImageModelPayload, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    mid = (payload.id or "").strip() or uuid.uuid4().hex
    try:
        check_model_id(mid, "生图模型 ID")
    except InvalidName:
        raise HTTPException(
            400, "模型 ID 只能以字母或数字开头，仅含字母/数字/点/下划线/短横线"
        )
    if _find_image_model(state.config, mid) is not None:
        raise HTTPException(400, "模型 ID 已存在")
    item = ImageModelConfig(
        id=mid,
        name=payload.name,
        base_url=payload.base_url or "https://ark.cn-beijing.volces.com/api/v3",
        api_key=payload.api_key,
        model=payload.model or "doubao-seedream-5-0-260128",
        owner=uid,
        shared=False,
        min_level=0,
    )
    state.config.image_models.append(item)
    _persist(state)
    return _masked_image_model(item)


@router.put("/models/images/{mid}")
def update_image_model(mid: str, payload: ImageModelPayload, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    item = _find_image_model(state.config, mid)
    _can_operate(item, uid)
    merged = {
        "id": item.id,
        "name": payload.name if payload.name else item.name,
        "base_url": payload.base_url if payload.base_url else item.base_url,
        "api_key": _credit_key(payload.api_key, item.api_key),
        "model": payload.model if payload.model else item.model,
        "owner": item.owner,
        "shared": item.shared,
        "min_level": item.min_level,
    }
    new_item = ImageModelConfig.parse_obj(merged)
    idx = state.config.image_models.index(item)
    state.config.image_models[idx] = new_item
    _persist(state)
    return _masked_image_model(new_item)


@router.delete("/models/images/{mid}")
def delete_image_model(mid: str, request: Request):
    uid = _uid(request)
    state = request.app.state.flyai
    item = _find_image_model(state.config, mid)
    _can_operate(item, uid)
    state.config.image_models.remove(item)
    _persist(state)
    return {"ok": True}


@router.post("/models/images/{mid}/share")
def share_image_model(mid: str, payload: SharePayload, request: Request):
    uid = _uid(request)
    if uid != ADMIN_UID:
        raise HTTPException(403, "共享设置仅管理员可操作")
    state = request.app.state.flyai
    item = _find_image_model(state.config, mid)
    if item is None:
        raise HTTPException(404, "模型不存在")
    item.shared = bool(payload.shared)
    item.min_level = max(0, min(int(payload.min_level or 0), 255))
    _persist(state)
    return _masked_image_model(item)
