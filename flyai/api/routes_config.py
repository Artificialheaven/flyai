"""配置读写接口。api_key 掩码显示、保存时掩码串不覆盖真实 key。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..config import AppConfig, ProviderConfig, RoleplaySettings, mask_api_key, save_config
from ..errors import ProviderError

router = APIRouter(prefix="/api", tags=["config"])


class ProviderPayload(BaseModel):
    id: Optional[str] = None
    type: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    params: dict = {}


class ConfigPayload(BaseModel):
    active_provider: Optional[str] = None
    providers: Optional[list] = None
    tools: Optional[dict] = None
    roleplay: Optional[dict] = None


class TestPayload(BaseModel):
    provider_id: Optional[str] = None


def _masked_config(state):
    """返回脱敏后的配置视图。"""
    cfg = state.config
    providers = []
    for p in cfg.providers:
        d = p.dict()
        d["api_key"] = mask_api_key(p.api_key) if p.api_key else ""
        providers.append(d)
    return {
        "active_provider": cfg.active_provider,
        "providers": providers,
        "tools": cfg.tools.dict(),
        "history": cfg.history.dict(),
        "server": cfg.server.dict(),
        "roleplay": cfg.roleplay.dict(),
    }


def _credit_key(cfg: AppConfig, incoming: str, existing: str) -> str:
    """若 incoming 是掩码串（用户未改 key），保留原 key。"""
    if incoming and incoming == mask_api_key(existing) and existing:
        return existing
    return incoming


@router.get("/config")
def get_config(request: Request):
    return _masked_config(request.app.state.flyai)


@router.put("/config")
def put_config(payload: ConfigPayload, request: Request):
    state = request.app.state.flyai
    cfg = state.config

    if payload.providers is not None:
        new_providers = []
        for pdata in payload.providers:
            # 找到原配置里的 provider 以保留未修改的 key
            old = next((p for p in cfg.providers if p.id == pdata.get("id")), None)
            pdata = dict(pdata)
            if old is not None:
                pdata["api_key"] = _credit_key(cfg, pdata.get("api_key", ""), old.api_key)
            new_providers.append(pdata)
        cfg.providers = [ProviderConfig.parse_obj(p) for p in new_providers]

    if payload.active_provider is not None:
        cfg.active_provider = payload.active_provider

    if payload.tools is not None:
        cfg.tools = type(cfg.tools).parse_obj(payload.tools)

    if payload.roleplay is not None:
        cfg.roleplay = RoleplaySettings.parse_obj(payload.roleplay)

    save_config(cfg, state.config_path)
    state.reload_tools()
    state.reload_providers()
    return _masked_config(state)


@router.post("/config/activate/{provider_id}")
def activate_provider(provider_id: str, request: Request):
    state = request.app.state.flyai
    if not any(p.id == provider_id for p in state.config.providers):
        raise HTTPException(404, "提供商不存在")
    state.config.active_provider = provider_id
    save_config(state.config, state.config_path)
    state.reload_providers()
    return {"ok": True}


@router.post("/config/test")
async def test_connection(payload: TestPayload, request: Request):
    state = request.app.state.flyai
    provider = state.get_provider(payload.provider_id)
    if provider is None:
        raise HTTPException(400, "提供商不可用")
    return {"ok": True, "message": "连通性检测（v1 简化为状态可用）"}