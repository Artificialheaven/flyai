"""外链插图服务端代理（方案 4）：权限门槛、域名白名单、SSRF 与响应约束。"""

import asyncio
import ipaddress
import os
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api import routes_st


# ---- 假客户端 ----

class _FakeResponse:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self._chunks = list(chunks)

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        return None


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None):
        return httpx.Request(method, url, headers=headers)

    async def send(self, request, stream=False):
        self.urls.append(str(request.url))
        return self.responses.pop(0)


def _patch_http(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(routes_st.httpx, "AsyncClient", lambda **kw: client)
    return client


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    async def fake_resolve(hostname):
        return [ipaddress.IPv4Address("93.184.216.34")]

    monkeypatch.setattr(routes_st, "resolve_and_check", fake_resolve)
    # 测试机可能带系统代理；钉住断言按“无代理”环境验证
    import flyai.netguard as netguard

    monkeypatch.setattr(netguard, "proxy_in_use", lambda *a, **k: False)


def _run(coro):
    return asyncio.run(coro)


# ---- 权限 ----

def _sc(level=0, uid="u1"):
    return SimpleNamespace(level=level, uid=uid)


def _cfg(enabled=True, min_level=256, domains=None):
    return SimpleNamespace(st_image=SimpleNamespace(
        enabled=enabled, min_level=min_level, allowed_domains=domains or [],
    ))


def test_access_disabled_is_404():
    with pytest.raises(HTTPException) as exc:
        routes_st._check_access(_sc(255), _cfg(enabled=False))
    assert exc.value.status_code == 404


def test_access_requires_level_unless_admin():
    with pytest.raises(HTTPException) as exc:
        routes_st._check_access(_sc(level=10), _cfg(min_level=100))
    assert exc.value.status_code == 403
    assert "Lv100" in exc.value.detail
    routes_st._check_access(_sc(level=100), _cfg(min_level=100))
    # admin 不受等级限制
    routes_st._check_access(_sc(level=0, uid="admin"), _cfg(min_level=256))


def test_access_admin_uid_bypasses_level(monkeypatch):
    monkeypatch.setattr(routes_st, "ADMIN_UID", "admin")
    routes_st._check_access(_sc(level=0, uid="admin"), _cfg(min_level=255))


# ---- 域名白名单 ----

def test_host_allowlist():
    assert routes_st._host_allowed("cdn.example.com", []) is True
    assert routes_st._host_allowed("example.com", ["example.com"]) is True
    assert routes_st._host_allowed("cdn.example.com", ["example.com"]) is True
    assert routes_st._host_allowed("evil-example.com", ["example.com"]) is False
    assert routes_st._host_allowed("example.com.evil.com", ["example.com"]) is False
    assert routes_st._host_allowed("", ["example.com"]) is False


# ---- 下载 ----

def test_fetch_rejects_non_http_scheme():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("ftp://example.com/a.png", []))
    assert exc.value.status_code == 400


def test_fetch_rejects_domain_outside_allowlist():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("https://cdn.evil.com/a.png", ["example.com"]))
    assert exc.value.status_code == 403


def test_fetch_follows_redirect_with_recheck(monkeypatch):
    client = _patch_http(monkeypatch, [
        _FakeResponse(302, {"location": "https://cdn.example.com/final.png"}),
        _FakeResponse(200, {"content-type": "image/png; charset=binary"},
                      [b"\x89PNG", b"rest"]),
    ])
    body, ctype = _run(routes_st._fetch_image(
        "https://example.com/start.png", ["example.com"]))
    assert body == b"\x89PNGrest"
    assert ctype == "image/png"
    assert len(client.urls) == 2
    # 每一跳都钉住解析出的 IP，Host 头保留原域名
    assert client.urls[1].startswith("https://93.184.216.34")


def test_fetch_rejects_redirect_to_unlisted_domain(monkeypatch):
    _patch_http(monkeypatch, [
        _FakeResponse(302, {"location": "https://evil.com/a.png"}),
    ])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("https://example.com/a.png", ["example.com"]))
    assert exc.value.status_code == 403


def test_fetch_rejects_non_image_content_type(monkeypatch):
    _patch_http(monkeypatch, [
        _FakeResponse(200, {"content-type": "text/html"}, [b"<html>"]),
    ])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("https://example.com/a.png", []))
    assert exc.value.status_code == 415


def test_fetch_rejects_oversize(monkeypatch):
    monkeypatch.setattr(routes_st, "_MAX_BYTES", 4)
    _patch_http(monkeypatch, [
        _FakeResponse(200, {"content-type": "image/png"}, [b"aaaa", b"bbbb"]),
    ])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("https://example.com/a.png", []))
    assert exc.value.status_code == 413


def test_fetch_rejects_too_many_redirects(monkeypatch):
    responses = [
        _FakeResponse(302, {"location": "https://example.com/%d.png" % i})
        for i in range(routes_st._MAX_REDIRECTS + 1)
    ]
    _patch_http(monkeypatch, responses)
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("https://example.com/0.png", []))
    assert exc.value.status_code == 502


def test_fetch_rejects_upstream_error(monkeypatch):
    _patch_http(monkeypatch, [_FakeResponse(404, {})])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_image("https://example.com/a.png", []))
    assert exc.value.status_code == 502


# ---- 路由 ----

def _request(sc, cfg):
    state = SimpleNamespace(user=lambda _req: sc, config=cfg)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def test_route_returns_image_with_hardening_headers(monkeypatch):
    _patch_http(monkeypatch, [
        _FakeResponse(200, {"content-type": "image/jpeg"}, [b"jpeg"]),
    ])
    resp = _run(routes_st.img_proxy(_request(_sc(256), _cfg()), url="https://example.com/a.jpg"))
    assert resp.body == b"jpeg"
    assert resp.media_type == "image/jpeg"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "sandbox" in resp.headers["content-security-policy"]


def test_route_requires_url():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.img_proxy(_request(_sc(256), _cfg()), url="  "))
    assert exc.value.status_code == 400
