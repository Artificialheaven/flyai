"""DeepSeek 开放平台接入：管理员登录/绑定 API Key、官方余额查询。

说明：DeepSeek 官方没有提供第三方 OAuth，也没有“创建 API Key”的公开接口。
因此这里提供：
- 跳转官方登录/API Keys/充值/用量页面（管理员）；
- 管理员把平台生成的 Key 粘贴回来，服务端校验 `/user/balance` 后
  自动创建/更新模型列表中的 DeepSeek 官方 provider。
任何接口都不回传 Key 明文。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import ProviderConfig, save_config
from ..user import ADMIN_UID

router = APIRouter(prefix="/api/deepseek", tags=["deepseek"])

OFFICIAL_BASE = "https://api.deepseek.com"
PLATFORM = {
    "sign_in": "https://platform.deepseek.com/sign_in",
    "api_keys": "https://platform.deepseek.com/api_keys",
    "top_up": "https://platform.deepseek.com/top_up",
    "usage": "https://platform.deepseek.com/usage",
}


def _scope(request: Request):
    return request.app.state.flyai.user(request)


def _is_admin(sc) -> bool:
    return sc.uid == ADMIN_UID or str(getattr(sc, "role", "") or "") == "admin" or int(getattr(sc, "level", 0) or 0) >= 256


def _official_provider(config) -> Optional[ProviderConfig]:
    for provider in getattr(config, "providers", []) or []:
        base = str(getattr(provider, "base_url", "") or "")
        if "api.deepseek.com" in base:
            return provider
    return None


def _masked_key(key: str) -> str:
    raw = str(key or "")
    if len(raw) <= 8:
        return "****" if raw else ""
    return raw[:4] + "****" + raw[-4:]


async def _fetch_balance(api_key: str) -> Dict[str, Any]:
    if not api_key:
        raise HTTPException(400, "未配置 DeepSeek API Key")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(
                OFFICIAL_BASE + "/user/balance",
                headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, "连接 DeepSeek 开放平台失败：%s" % exc)
    if response.status_code == 401:
        raise HTTPException(400, "API Key 无效或已失效")
    if response.status_code >= 400:
        raise HTTPException(502, "DeepSeek 返回 HTTP %d" % response.status_code)
    try:
        data = response.json()
    except Exception:
        raise HTTPException(502, "DeepSeek 返回了非 JSON 响应")
    balances = []
    for item in data.get("balance_infos") or []:
        if isinstance(item, dict):
            balances.append({
                "currency": str(item.get("currency") or ""),
                "total": str(item.get("total_balance") or "0"),
                "granted": str(item.get("granted_balance") or "0"),
                "topped_up": str(item.get("topped_up_balance") or "0"),
            })
    return {"available": bool(data.get("is_available")), "balances": balances}


@router.get("/status")
def deepseek_status(request: Request):
    state = request.app.state.flyai
    config = state.config
    provider = _official_provider(config)
    active_id = str(getattr(config, "active_provider", "") or "")
    active_provider = next(
        (item for item in (getattr(config, "providers", []) or []) if item.id == active_id),
        None,
    )
    is_official_active = bool(
        active_provider
        and "api.deepseek.com" in str(getattr(active_provider, "base_url", "") or "")
    )
    return {
        "configured": provider is not None,
        "provider_id": provider.id if provider else "",
        "model": provider.model if provider else "",
        "key_masked": _masked_key(provider.api_key if provider else ""),
        "is_official_active": is_official_active,
        "platform": PLATFORM,
        "admin": _is_admin(_scope(request)),
    }


@router.get("/balance")
async def deepseek_balance(request: Request):
    provider = _official_provider(request.app.state.flyai.config)
    if provider is None or not provider.api_key:
        return {"configured": False, "balances": []}
    data = await _fetch_balance(provider.api_key)
    return {"configured": True, "provider_id": provider.id, **data}


class ConnectBody(BaseModel):
    api_key: str = ""
    model: str = "deepseek-chat"
    activate: bool = True


@router.post("/connect")
async def deepseek_connect(body: ConnectBody, request: Request):
    state = request.app.state.flyai
    sc = _scope(request)
    if not _is_admin(sc):
        raise HTTPException(403, "仅管理员可以绑定 DeepSeek 开放平台")
    key = (body.api_key or "").strip()
    if not key:
        raise HTTPException(400, "请填写 API Key")
    model = (body.model or "deepseek-chat").strip() or "deepseek-chat"
    # 先校验 Key（/user/balance），避免把无效 Key 写进模型列表
    await _fetch_balance(key)

    provider = _official_provider(state.config)
    if provider is None:
        provider = ProviderConfig(
            id="deepseek-official",
            type="openai",
            base_url=OFFICIAL_BASE,
            model=model,
            api_key=key,
            owner=ADMIN_UID,
            shared=True,
            min_level=0,
        )
        state.config.providers.append(provider)
    else:
        provider.api_key = key
        provider.model = model or provider.model
    if body.activate:
        state.config.active_provider = provider.id
    save_config(state.config, getattr(state, "config_path", None))
    try:
        state.reload_providers()
    except Exception:
        pass
    return {
        "ok": True,
        "provider_id": provider.id,
        "model": provider.model,
        "key_masked": _masked_key(key),
        "activated": bool(body.activate),
    }
