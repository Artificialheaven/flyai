"""配置读写接口。api_key 掩码显示、保存时掩码串不覆盖真实 key。

- GET /api/config：按当前用户权限过滤不可见模型。
- PUT /api/config：仅管理员（全局配置）。
- /api/config/test/deepseek：管理员校验 DeepSeek Key（轻量 TTS 探测）。
- /api/config/providers/...：模型 CRUD 仅管理员（用户只能选择共享模型）。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..config import (
    AccessSettings,
    AfdianSettings,
    AnnouncementSettings,
    AppConfig,
    DeepSeekSettings,
    EmailSettings,
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
    LoreSettings,
    McpSettings,
    MoodSettings,
    MvuSettings,
    PromptTemplateSettings,
    StImageSettings,
    StNetSettings,
    ExtTriggerSettings,
    ProactorSettings,
    RegistrationSettings,
    SelfMemorySettings,
    WatcherSettings,
    mask_api_key,
    save_config,
)
from ..models_access import visible_image_models, visible_providers
from ..deepseek_tools import DeepSeekToolsClient
from ..auth_sessions import resolve_uid
from ..ids import InvalidName, check_model_id
from ..mailer import MailError
from ..user import ADMIN_UID, BG_MODES, _normalize_background
from ..errors import DeepSeekError, ProviderError  # noqa: F401

router = APIRouter(prefix="/api", tags=["config"])


class ProviderPayload(BaseModel):
    id: Optional[str] = None
    type: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    # 分组（空串=未分组）；None=不传（保留原值）
    group: Optional[str] = None
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
    # 按 token 计费配置（每 1M token 的点数+时段）；None=不改，{}=关闭
    pricing: Optional[dict] = None


class DiscoverPayload(BaseModel):
    """管理员拉取上游模型列表：只带临时凭据，不落库。"""

    type: str = "openai"
    base_url: str = ""
    api_key: str = ""


class ImageModelPayload(BaseModel):
    id: Optional[str] = None
    name: str = ""
    type: str = ""
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
    # 内容库导入限制（角色卡大小等，无密钥）
    lore: Optional[dict] = None
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
    # 功能权限门槛（无密钥，整段读写）
    access: Optional[dict] = None
    # 酒馆卡扩展：外链图片代理 / 受控网络代理 / MVU 总开关（无密钥，整段读写）
    st_image: Optional[dict] = None
    st_net: Optional[dict] = None
    mvu: Optional[dict] = None
    prompt_template: Optional[dict] = None
    # DeepSeek 官方服务（含独立 Key，仅 admin 可见/可改）
    deepseek: Optional[dict] = None
    # 邀请码注册设置（仅 admin 可见/可改）
    registration: Optional[dict] = None
    # 启动公告（仅 admin 可改；展示内容经公开接口下发）
    announcement: Optional[dict] = None
    # SMTP 邮件服务（含授权码，仅 admin 可见/可改）
    email: Optional[dict] = None
    # 爱发电自动发卡（含 API Token/webhook Token，仅 admin 可见/可改）
    afdian: Optional[dict] = None


class TestPayload(BaseModel):
    provider_id: Optional[str] = None


class TestEmailPayload(BaseModel):
    # 收件人；空则用 SMTP 账号/发件地址
    to: str = ""


# ---- 视图 ----

def _merge_pricing(payload_pricing, current):
    """None=保留原值；空 dict=关闭（回到每轮 rate 计费）；否则写入。"""
    if payload_pricing is None:
        return current
    if not payload_pricing:
        return None
    return payload_pricing


def _provider_key(p: ProviderConfig) -> str:
    """同一提供商（同 base_url + 同 key）的稳定标识，供管理端合并展示。"""
    raw = f"{p.base_url.strip()}\n{p.api_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _masked_provider(p: ProviderConfig) -> dict:
    d = p.dict()
    d["api_key"] = mask_api_key(p.api_key) if p.api_key else ""
    d["provider_key"] = _provider_key(p)
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
        "group": p.group,
        "vision": p.vision,
        "rate": p.rate,
        "pricing": p.pricing.dict() if p.pricing is not None else None,
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

    # 模型视图：管理员全量（可编辑视图）；普通用户只拿共享模型的精简视图
    # （无 base_url/归属/key/参数），自建模型能力已下线
    provider_views = []
    for p in providers:
        if uid == ADMIN_UID:
            provider_views.append(_masked_provider(p))
        else:
            provider_views.append(_public_provider(p))
    image_views = []
    for m in image_models:
        if uid == ADMIN_UID:
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
        # 导入大小限制不含密钥，对所有登录用户可见
        "lore": cfg.lore.dict(),
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
        # 权限门槛不含密钥，对所有登录用户可见（前端据此提示不可用功能）
        "access": cfg.access.dict(),
        # 酒馆卡扩展：图片代理门槛与 MVU 总开关（前端据此显示设置项）
        "st_image": cfg.st_image.dict(),
        "st_net": cfg.st_net.dict(),
        "mvu": cfg.mvu.dict(),
        "prompt_template": cfg.prompt_template.dict(),
        "st_image_available": (
            bool(cfg.st_image.enabled)
            and (uid == ADMIN_UID or level >= int(cfg.st_image.min_level or 0))
        ),
        "st_net_available": (
            bool(cfg.st_net.enabled)
            and (uid == ADMIN_UID or level >= int(cfg.st_net.min_level or 0))
        ),
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
        # DeepSeek 独立 Key 仅 admin 可见；普通用户视图完全不含该段
        ds = cfg.deepseek.dict()
        ds["key_set"] = bool(ds.get("api_key"))
        ds["api_key"] = mask_api_key(ds.get("api_key") or "")
        out["deepseek"] = ds
        # 注册默认额度仅 admin 可见
        out["registration"] = cfg.registration.dict()
        # 公告内容本身是公开信息，但编辑入口仅 admin
        out["announcement"] = cfg.announcement.dict()
        # SMTP 邮件服务：授权码只给掩码 + 是否已设置
        em = cfg.email.dict()
        em["password_set"] = bool(em.get("password"))
        em["password"] = (
            mask_api_key(em.get("password") or "") if em.get("password") else ""
        )
        out["email"] = em
        # 爱发电：API Token 只给掩码；webhook Token 需在后台拼完整回调地址，
        # 因此原样下发（仅管理员可见，保存时原样回传即保持不变）
        af = cfg.afdian.dict()
        af["token_set"] = bool(af.get("token"))
        af["token"] = mask_api_key(af.get("token") or "") if af.get("token") else ""
        af["webhook_token_set"] = bool(af.get("webhook_token"))
        af["webhook_token"] = af.get("webhook_token") or ""
        out["afdian"] = af
    return out


def _masked_tts(cfg: AppConfig) -> dict:
    d = cfg.tts.dict()
    d["api_key"] = mask_api_key(cfg.tts.api_key) if cfg.tts.api_key else ""
    return d


def _credit_key(incoming: str, existing: str) -> str:
    """若 incoming 为空或掩码串（用户未改 key），保留原 key。

    空串不能当作「清空 key」：前端未编辑 key 时不会回传明文，早先按空串处理
    会把已保存的 key 抹掉（编辑模型的其它字段也会误伤）。
    """
    if not (incoming or "").strip():
        return existing
    if incoming == mask_api_key(existing) and existing:
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
        raise HTTPException(403, "全局配置仅管理员可修改")
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

    if payload.lore is not None:
        cfg.lore = LoreSettings.parse_obj(dict(payload.lore))

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

    if payload.access is not None:
        # 等级由模型校验器统一 clamp 到 0..255
        cfg.access = AccessSettings.parse_obj(dict(payload.access))

    if payload.st_image is not None:
        cfg.st_image = StImageSettings.parse_obj(dict(payload.st_image))

    if payload.st_net is not None:
        cfg.st_net = StNetSettings.parse_obj(dict(payload.st_net))

    if payload.mvu is not None:
        cfg.mvu = MvuSettings.parse_obj(dict(payload.mvu))

    if payload.prompt_template is not None:
        cfg.prompt_template = PromptTemplateSettings.parse_obj(
            dict(payload.prompt_template))

    if payload.deepseek is not None:
        dd = dict(payload.deepseek)
        dd["api_key"] = _credit_key(
            dd.get("api_key", ""), cfg.deepseek.api_key)
        cfg.deepseek = DeepSeekSettings.parse_obj(dd)

    if payload.registration is not None:
        # 注册接口每次读 cfg.registration，保存后立即生效
        cfg.registration = RegistrationSettings.parse_obj(dict(payload.registration))

    if payload.announcement is not None:
        # 公开接口 /api/app/announcement 每次读 cfg.announcement，保存后立即生效
        cfg.announcement = AnnouncementSettings.parse_obj(dict(payload.announcement))

    if payload.email is not None:
        # 空授权码/掩码串=保留旧值；认证路由每次读 cfg.email，保存后热生效
        ed = dict(payload.email)
        ed["password"] = _credit_key(
            ed.get("password", ""), cfg.email.password)
        cfg.email = EmailSettings.parse_obj(ed)

    if payload.afdian is not None:
        # 整段替换：Token 留空/掩码=保留原值；webhook Token 留空则自动生成
        ad = dict(payload.afdian)
        ad["token"] = _credit_key(ad.get("token", ""), cfg.afdian.token)
        ad["webhook_token"] = (
            _credit_key(ad.get("webhook_token", ""), cfg.afdian.webhook_token)
            or secrets.token_urlsafe(24)
        )
        cfg.afdian = AfdianSettings.parse_obj(ad)

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
    _require_admin(request)
    state = request.app.state.flyai
    provider = state.get_provider(payload.provider_id)
    if provider is None:
        raise HTTPException(400, "提供商不可用")
    return {"ok": True, "message": "连通性检测（v1 简化为状态可用）"}


@router.post("/config/test/deepseek")
async def test_deepseek_key(request: Request):
    """管理员校验「DeepSeek 服务」里保存的 Key：极短 TTS 探测，不写用量账本。"""
    _require_admin(request)
    state = request.app.state.flyai
    ds = getattr(state.config, "deepseek", None)
    if not (getattr(ds, "api_key", "") or "").strip():
        return {"ok": False, "message": "未配置 DeepSeek API Key"}
    try:
        audio = await DeepSeekToolsClient(ds).tts("好", "stella", "pcm")
    except DeepSeekError as exc:
        msg = str(exc) or type(exc).__name__
        if exc.status_code in (401, 403):
            msg += "（请到设置 → DeepSeek 服务更新 API Key）"
        return {"ok": False, "message": "Key 不可用：%s" % msg[:300]}
    except Exception as exc:  # noqa: BLE001 - 探测失败一律回显原因
        return {
            "ok": False,
            "message": "检测失败：%s: %s" % (type(exc).__name__, str(exc)[:300]),
        }
    return {"ok": True, "message": "Key 可用（已合成 %d 字节音频）" % len(audio)}


@router.post("/config/test/email")
async def test_email(payload: TestEmailPayload, request: Request):
    """管理员用已保存的 SMTP 配置发送一封测试邮件。"""
    _require_admin(request)
    state = request.app.state.flyai
    cfg = getattr(state.config, "email", None)
    mailer = getattr(state, "mailer", None)
    if cfg is None or mailer is None:
        return {"ok": False, "message": "邮件服务未就绪"}
    to = (
        (payload.to or "").strip()
        or (getattr(cfg, "username", "") or "").strip()
        or (getattr(cfg, "from_address", "") or "").strip()
    )
    if not to:
        return {"ok": False, "message": "请填写收件邮箱"}
    try:
        await asyncio.to_thread(mailer.send_test, cfg, to)
    except MailError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 探测失败一律回显原因
        return {
            "ok": False,
            "message": "发送失败：%s" % type(exc).__name__,
        }
    return {"ok": True, "message": "测试邮件已发送至 %s" % to}


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
    # 第一人称自我记忆个人开关（默认关闭；非覆盖项，不与全局做三态合并）
    self_memory_enabled: bool = False
    # 向模型提供真实当前时间（默认关闭；纯个人开关）
    real_time_enabled: bool = False

    # 酒馆卡扩展（None=跟随默认：正则/HTML/MVU 默认开，外链插图/代理默认关）
    st_regex_prompt_enabled: Optional[bool] = None
    st_regex_display_enabled: Optional[bool] = None
    st_html_enabled: Optional[bool] = None
    st_external_images_enabled: Optional[bool] = None
    st_image_proxy_enabled: Optional[bool] = None
    st_js_sandbox_enabled: Optional[bool] = None
    st_author_scripts_enabled: Optional[bool] = None
    # L4：卡内授权动作 / 会话副本写入 / 受控网络代理（默认关）
    st_card_actions_enabled: Optional[bool] = None
    st_card_write_enabled: Optional[bool] = None
    st_net_proxy_enabled: Optional[bool] = None
    # 卡内 chat 变量单次写回上限（KB）；None=默认 128
    st_mvu_max_kb: Optional[int] = None
    mvu_enabled: Optional[bool] = None
    # EJS 模板（方案 B）个人开关
    template_enabled: Optional[bool] = None
    template_output_enabled: Optional[bool] = None


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
                "image_enabled", "galgame_enabled",
                "st_regex_prompt_enabled", "st_regex_display_enabled",
                "st_html_enabled", "st_external_images_enabled",
                "st_image_proxy_enabled", "st_js_sandbox_enabled",
                "st_author_scripts_enabled",
                "st_card_actions_enabled", "st_card_write_enabled",
                "st_net_proxy_enabled", "mvu_enabled",
                "template_enabled", "template_output_enabled"):
        v = getattr(payload, key)
        setattr(us, key, None if v is None else bool(v))
    us.galgame_max_loops = _opt_int(
        payload.galgame_max_loops, "galgame_max_loops", 1, 100)
    us.st_mvu_max_kb = _opt_int(
        payload.st_mvu_max_kb, "st_mvu_max_kb", 64, 4096)
    us.self_memory_enabled = bool(payload.self_memory_enabled)
    us.real_time_enabled = bool(payload.real_time_enabled)

    scope.save_user_settings()
    return scope.user_settings.to_dict()


# ---- 应用背景（电脑/手机各一套；专用接口避免被整体 PUT 覆盖）----

_BG_DEVICES = ("desktop", "mobile")
_BG_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_BG_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


class MyBackgroundPayload(BaseModel):
    device: str  # desktop | mobile
    # 缺省/None = 保持不变；"" = 清空（图片模式下清空图片会自动回落 theme）
    mode: Optional[str] = None
    color: Optional[str] = None
    image: Optional[str] = None
    # 背景图层不透明度 0-100
    opacity: Optional[int] = None


def _background_view(us) -> dict:
    return {
        device: {
            "mode": getattr(us, "bg_%s_mode" % device),
            "color": getattr(us, "bg_%s_color" % device),
            "image": getattr(us, "bg_%s_image" % device),
            "opacity": int(getattr(us, "bg_%s_opacity" % device)),
        }
        for device in _BG_DEVICES
    }


def _owned_background_image(scope, url: str) -> bool:
    """图片必须是当前用户 upload 目录下的真实文件（防引用他人文件/路径穿越）。"""
    prefix = "/uploads/%s/" % scope.uid
    if not url.startswith(prefix):
        return False
    name = url[len(prefix):]
    if not name or name in (".", ".."):
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if not name.lower().endswith(_BG_IMAGE_EXTS):
        return False
    try:
        return (scope.upload_dir / name).is_file()
    except OSError:
        return False


@router.get("/my/settings/background")
def get_my_background(request: Request):
    scope = request.app.state.flyai.user(request)
    return _background_view(scope.user_settings)


@router.put("/my/settings/background")
def put_my_background(payload: MyBackgroundPayload, request: Request):
    scope = request.app.state.flyai.user(request)
    us = scope.user_settings
    device = (payload.device or "").strip()
    if device not in _BG_DEVICES:
        raise HTTPException(400, "device 必须是 desktop 或 mobile")

    prefix = "bg_%s_" % device
    mode = getattr(us, prefix + "mode")
    color = getattr(us, prefix + "color")
    image = getattr(us, prefix + "image")
    opacity = getattr(us, prefix + "opacity")

    if payload.mode is not None:
        mode = payload.mode.strip().lower()
        if mode not in BG_MODES:
            raise HTTPException(400, "mode 必须是 theme/solid/image")
    if payload.color is not None:
        color = payload.color.strip().lower()
        if color and not _BG_COLOR_RE.match(color):
            raise HTTPException(400, "颜色格式应为 #rrggbb")
    if payload.image is not None:
        image = payload.image.strip()
        if image and not _owned_background_image(scope, image):
            raise HTTPException(400, "背景图片不存在或无权使用")
    if payload.opacity is not None:
        if not 0 <= payload.opacity <= 100:
            raise HTTPException(400, "opacity 必须是 0-100 的整数")
        opacity = payload.opacity

    # 再次归一化：如 image 模式但图片被清空 → 自动回落 theme
    mode, color, image, opacity = _normalize_background(mode, color, image, opacity)
    setattr(us, prefix + "mode", mode)
    setattr(us, prefix + "color", color)
    setattr(us, prefix + "image", image)
    setattr(us, prefix + "opacity", opacity)
    scope.save_user_settings()
    return _background_view(us)


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

def _require_admin(request: Request) -> str:
    """模型管理仅管理员可用；普通用户只能选择已共享的模型。"""
    uid = _uid(request)
    if uid != ADMIN_UID:
        raise HTTPException(403, "模型管理仅管理员可用")
    return uid


def _find_provider(cfg, mid):
    return next((p for p in cfg.providers if p.id == mid), None)


def _require_item(item):
    if item is None:
        raise HTTPException(404, "模型不存在")
    return item


@router.post("/models/providers")
def create_provider_model(payload: ProviderPayload, request: Request):
    uid = _require_admin(request)
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
        group=payload.group or "",
        params=payload.params or {},
        thinking_params=payload.thinking_params or {},
        thinking_off_params=payload.thinking_off_params or {},
        vision=payload.vision,
        owner=uid,
        shared=False,
        min_level=0,
        rate=(payload.rate if payload.rate is not None else 1.0),
        pricing=_merge_pricing(payload.pricing, None),
    )
    state.config.providers.append(item)
    _persist(state)
    return _masked_provider(item)


@router.post("/models/providers/discover")
async def discover_provider_models(payload: DiscoverPayload, request: Request):
    """管理员：用临时 base_url/api_key 拉取上游 /models 列表，供批量建模型。"""
    _require_admin(request)
    base = (payload.base_url or "").strip().rstrip("/")
    api_key = (payload.api_key or "").strip()
    if not base:
        raise HTTPException(400, "请先填写 Base URL")
    kind = "anthropic" if (payload.type or "").lower() == "anthropic" else "openai"
    if kind == "anthropic":
        url = base + "/models" if base.endswith("/v1") else base + "/v1/models"
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
    else:
        url = base + "/models"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001 - 统一回显网络错误
        raise HTTPException(502, f"请求模型列表失败：{type(exc).__name__}: {str(exc)[:200]}")
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:200]
        raise HTTPException(502, f"上游返回 {resp.status_code}：{detail}")
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(502, "上游返回的不是 JSON，无法解析模型列表")
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        items = []
    models = []
    for item in items:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str):
            mid = mid.strip()
            if mid and mid not in models:
                models.append(mid)
    models.sort()
    return {"models": models}


@router.put("/models/providers/{mid}")
def update_provider_model(mid: str, payload: ProviderPayload, request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    item = _require_item(_find_provider(state.config, mid))
    data = _masked_provider(item)
    merged = {
        "id": item.id,
        "type": payload.type or item.type,
        "base_url": payload.base_url if payload.base_url else item.base_url,
        "api_key": _credit_key(payload.api_key, item.api_key),
        "model": payload.model if payload.model else item.model,
        "group": payload.group if payload.group is not None else item.group,
        "params": payload.params or item.params,
        "thinking_params": payload.thinking_params or item.thinking_params,
        "thinking_off_params": payload.thinking_off_params or item.thinking_off_params,
        "vision": payload.vision,
        "owner": item.owner,
        "shared": item.shared,
        "min_level": item.min_level,
        "rate": (payload.rate if payload.rate is not None else item.rate),
        "pricing": _merge_pricing(payload.pricing, item.pricing),
    }
    new_item = ProviderConfig.parse_obj(merged)
    idx = state.config.providers.index(item)
    state.config.providers[idx] = new_item
    _persist(state)
    return _masked_provider(new_item)


@router.delete("/models/providers/{mid}")
def delete_provider_model(mid: str, request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    item = _require_item(_find_provider(state.config, mid))
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
    uid = _require_admin(request)
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
    kind = (payload.type or "ark").strip().lower()
    defaults = {
        "ark": ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seedream-5-0-260128"),
        "openai": ("https://api.openai.com/v1", "gpt-image-1"),
        "google": ("https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash-image"),
    }.get(kind, ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seedream-5-0-260128"))
    item = ImageModelConfig(
        id=mid,
        name=payload.name,
        type=kind,
        base_url=payload.base_url or defaults[0],
        api_key=payload.api_key,
        model=payload.model or defaults[1],
        owner=uid,
        shared=False,
        min_level=0,
    )
    state.config.image_models.append(item)
    _persist(state)
    return _masked_image_model(item)


@router.put("/models/images/{mid}")
def update_image_model(mid: str, payload: ImageModelPayload, request: Request):
    _require_admin(request)
    state = request.app.state.flyai
    item = _require_item(_find_image_model(state.config, mid))
    merged = {
        "id": item.id,
        "name": payload.name if payload.name else item.name,
        "type": (payload.type or item.type),
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
    _require_admin(request)
    state = request.app.state.flyai
    item = _require_item(_find_image_model(state.config, mid))
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
