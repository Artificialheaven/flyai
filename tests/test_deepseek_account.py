"""DeepSeek 开放平台接入：状态、管理员绑定、Key 不回传。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from flyai.api import routes_deepseek
from flyai.config import AppConfig, ProviderConfig


def _request(uid="admin", level=256):
    sc = SimpleNamespace(uid=uid, level=level, role="admin" if level >= 256 else "user")
    state = SimpleNamespace(
        config=AppConfig(),
        config_path=None,
        user=lambda request: sc,
        reload_providers=lambda: None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state))), state


def test_status_without_provider():
    request, _ = _request()
    data = routes_deepseek.deepseek_status(request)
    assert data["configured"] is False
    assert data["is_official_active"] is False
    assert data["admin"] is True


def test_connect_requires_admin():
    request, _ = _request(uid="u1", level=0)
    with pytest.raises(HTTPException) as ei:
        asyncio.get_event_loop().run_until_complete(
            routes_deepseek.deepseek_connect(
                routes_deepseek.ConnectBody(api_key="sk-test", model="deepseek-chat"), request
            )
        )
    assert ei.value.status_code == 403


def test_connect_adds_official_provider(monkeypatch, tmp_path):
    request, state = _request()
    calls = []

    async def fake_balance(key):
        calls.append(key)
        return {"available": True, "balances": []}

    monkeypatch.setattr(routes_deepseek, "_fetch_balance", fake_balance)
    monkeypatch.setattr(routes_deepseek, "save_config", lambda cfg, path: tmp_path / "c.json")
    state.config_path = tmp_path / "config.json"
    result = asyncio.get_event_loop().run_until_complete(
        routes_deepseek.deepseek_connect(
            routes_deepseek.ConnectBody(api_key="sk-abcdefgh12345678", model="deepseek-reasoner"),
            request,
        )
    )
    assert result["ok"] is True
    assert result["provider_id"] == "deepseek-official"
    assert "abcdefgh12345678" not in str(result)
    assert calls == ["sk-abcdefgh12345678"]
    provider = routes_deepseek._official_provider(state.config)
    assert provider is not None and provider.model == "deepseek-reasoner"
    assert state.config.active_provider == provider.id
    status = routes_deepseek.deepseek_status(request)
    assert status["configured"] is True and status["is_official_active"] is True
    assert "sk-abc" not in status["key_masked"] or status["key_masked"].startswith("sk-a")
