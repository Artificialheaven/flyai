"""配置读写接口。api_key 掩码显示、保存时掩码串不覆盖真实 key。

- GET /api/config：按当前用户权限过滤不可见模型。
- PUT /api/config：仅管理员（全局配置）。
- /api/models/...：每用户自建模型 CRUD（仅可操作归属自己的条目；
  共享设置仅管理员）。
"""

from __future__ import annotations

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
    RoleplaySettings,
    mask_api_key,
    save_config,
)
from ..models_access import model_visible, visible_image_models, visible_providers
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
    vision: bool = False
    owner: Optional[str] = None
    shared: Optional[bool] = None
    min_level: Optional[int] = None


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

    emb = cfg.embeddings.dict()
    emb["api_key"] = mask_api_key(cfg.embeddings.api_key) if cfg.embeddings.api_key else ""
    return {
        "active_provider": cfg.active_provider,
        "providers": [_masked_provider(p) for p in providers],
        "tools": cfg.tools.dict(),
        "history": cfg.history.dict(),
        "server": cfg.server.dict(),
        "roleplay": cfg.roleplay.dict(),
        "embeddings": emb,
        "image_generation": cfg.image_generation.dict(),
        "image_models": [_masked_image_model(m) for m in image_models],
        # SD 段不含密钥，对所有登录用户可见（前端据此显示引擎开关）
        "stable_diffusion": cfg.stable_diffusion.dict(),
        "pixiv": cfg.pixiv.dict(),
        "web_search": cfg.web_search.dict(),
    }


def _credit_key(incoming: str, existing: str) -> str:
    """若 incoming 是掩码串（用户未改 key），保留原 key。"""
    if incoming and incoming == mask_api_key(existing) and existing:
        return existing
    return incoming


def _uid(request: Request) -> str:
    uid = (request.headers.get("x-user-id") or "").strip()
    state = request.app.state.flyai
    valid = (uid == ADMIN_UID) or bool(state.user_store.get_by_id(uid))
    if not uid or not valid:
        raise HTTPException(401, "未登录或用户不存在")
    return uid


def _persist(state) -> None:
    save_config(state.config, state.config_path)
    state.reload_providers()


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

    _persist(state)
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
    if _find_provider(state.config, mid) is not None:
        raise HTTPException(400, "模型 ID 已存在")
    item = ProviderConfig(
        id=mid,
        type=payload.type,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
        params=payload.params or {},
        vision=payload.vision,
        owner=uid,
        shared=False,
        min_level=0,
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
        "vision": payload.vision,
        "owner": item.owner,
        "shared": item.shared,
        "min_level": item.min_level,
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
