"""L4-C 受控网络代理：默认关闭、域名策略、逐跳 SSRF、请求头剥离与限流。"""

import asyncio
import base64
import ipaddress
import os
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api import routes_st
from flyai.netguard import BlockedHost


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
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, content=None):
        req = httpx.Request(method, url, headers=headers, content=content)
        self.requests.append(req)
        return req

    async def send(self, request, stream=False):
        self.urls.append(str(request.url))
        return self.responses.pop(0)


def _patch_http(monkeypatch, responses):
    client = _FakeClient(responses)
    monkeypatch.setattr(routes_st.httpx, "AsyncClient", lambda **kw: client)
    return client


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # 每个用例都从公网解析成功、无限流历史开始
    async def fake_resolve(hostname):
        return [ipaddress.IPv4Address("93.184.216.34")]

    monkeypatch.setattr(routes_st, "resolve_and_check", fake_resolve)
    routes_st._net_rate.clear()
    routes_st._net_inflight.clear()
    yield
    routes_st._net_rate.clear()
    routes_st._net_inflight.clear()


def _run(coro):
    return asyncio.run(coro)


# ---- 权限 ----

def _sc(level=0, uid="u1", net=True):
    return SimpleNamespace(
        level=level,
        uid=uid,
        user_settings=SimpleNamespace(st_net_proxy_enabled=net),
    )


def _cfg(enabled=True, min_level=0, domains=None, allow_any=True, **kw):
    base = dict(
        enabled=enabled,
        allow_any_public_https=allow_any,
        allowed_domains=domains or [],
        min_level=min_level,
        max_bytes=5 * 1024 * 1024,
        timeout=15.0,
        per_minute=60,
    )
    base.update(kw)
    return SimpleNamespace(st_net=SimpleNamespace(**base))


def test_access_disabled_is_404():
    with pytest.raises(HTTPException) as exc:
        routes_st._check_net_access(_sc(), _cfg(enabled=False))
    assert exc.value.status_code == 404


def test_access_requires_level_unless_admin(monkeypatch):
    monkeypatch.setattr(routes_st, "ADMIN_UID", "admin")
    with pytest.raises(HTTPException) as exc:
        routes_st._check_net_access(_sc(level=10), _cfg(min_level=100))
    assert exc.value.status_code == 403
    assert "Lv100" in exc.value.detail
    routes_st._check_net_access(_sc(level=100), _cfg(min_level=100))
    routes_st._check_net_access(_sc(level=0, uid="admin"), _cfg(min_level=256))


def test_access_requires_user_toggle():
    with pytest.raises(HTTPException) as exc:
        routes_st._check_net_access(_sc(net=False), _cfg())
    assert exc.value.status_code == 403


# ---- 域名策略 ----

def test_host_policy_https_any_or_allowlist():
    assert routes_st._net_host_allowed(
        "https", "example.com", True, []
    ) is True
    # 任意公网只放行 HTTPS；HTTP 必须靠白名单
    assert routes_st._net_host_allowed(
        "http", "example.com", True, []
    ) is False
    assert routes_st._net_host_allowed(
        "http", "example.com", True, ["example.com"]
    ) is True
    assert routes_st._net_host_allowed(
        "https", "cdn.example.com", False, ["example.com"]
    ) is True
    assert routes_st._net_host_allowed(
        "https", "evil-example.com", False, ["example.com"]
    ) is False
    assert routes_st._net_host_allowed(
        "https", "example.com.evil.com", False, ["example.com"]
    ) is False
    assert routes_st._net_host_allowed("https", "", True, []) is False


# ---- 代发 ----

def test_fetch_rejects_non_http_scheme(monkeypatch):
    _patch_http(monkeypatch, [])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_net(_cfg().st_net, "GET", "ftp://example.com/a", {}, b""))
    assert exc.value.status_code == 400


def test_fetch_rejects_url_credentials(monkeypatch):
    _patch_http(monkeypatch, [])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_net(
            _cfg().st_net, "GET", "https://user:pw@example.com/a", {}, b""
        ))
    assert exc.value.status_code == 400


def test_fetch_public_https_ok_and_headers_subset(monkeypatch):
    client = _patch_http(monkeypatch, [
        _FakeResponse(
            200,
            {"content-type": "application/json", "set-cookie": "a=b",
             "x-secret": "nope"},
            [b'{"ok":'],
            )
    ])
    status, headers, data, truncated = _run(routes_st._fetch_net(
        _cfg().st_net, "GET", "https://api.example.com/x", {"accept": "application/json"}, b""
    ))
    assert status == 200
    assert data == b'{"ok":'
    assert truncated is False
    # 响应头只回 content-type / content-length
    assert headers == {"content-type": "application/json"}
    # 请求被钉到解析出的公网 IP
    assert client.urls[0].startswith("https://93.184.216.34")


def test_fetch_rejects_http_without_allowlist(monkeypatch):
    _patch_http(monkeypatch, [])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_net(_cfg().st_net, "GET", "http://example.com/a", {}, b""))
    assert exc.value.status_code == 403


def test_fetch_allows_http_when_allowlisted(monkeypatch):
    _patch_http(monkeypatch, [_FakeResponse(200, {}, [b"ok"])])
    _, _, data, _ = _run(routes_st._fetch_net(
        _cfg(allow_any=False, domains=["example.com"]).st_net,
        "GET", "http://example.com/a", {}, b"",
    ))
    assert data == b"ok"


def test_fetch_rejects_redirect_outside_allowlist(monkeypatch):
    _patch_http(monkeypatch, [
        _FakeResponse(302, {"location": "https://evil.com/a"}),
    ])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_net(
            _cfg(allow_any=False, domains=["example.com"]).st_net,
            "GET", "https://example.com/a", {}, b"",
        ))
    assert exc.value.status_code == 403


def test_fetch_rejects_redirect_to_internal_host(monkeypatch):
    async def fake_resolve(hostname):
        if hostname == "intranet.example.com":
            raise BlockedHost("内网地址")
        return [ipaddress.IPv4Address("93.184.216.34")]

    monkeypatch.setattr(routes_st, "resolve_and_check", fake_resolve)
    _patch_http(monkeypatch, [
        _FakeResponse(302, {"location": "https://intranet.example.com/a"}),
    ])
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_net(
            _cfg().st_net, "GET", "https://example.com/a", {}, b""
        ))
    assert exc.value.status_code == 403


def test_fetch_truncates_oversize(monkeypatch):
    _patch_http(monkeypatch, [
        _FakeResponse(200, {}, [b"aaaa", b"bbbb"]),
    ])
    _, _, data, truncated = _run(routes_st._fetch_net(
        _cfg(max_bytes=4).st_net, "GET", "https://example.com/a", {}, b""
    ))
    assert data == b"aaaa"
    assert truncated is True


def test_fetch_rejects_too_many_redirects(monkeypatch):
    responses = [
        _FakeResponse(302, {"location": "https://example.com/%d" % i})
        for i in range(routes_st._MAX_REDIRECTS + 1)
    ]
    _patch_http(monkeypatch, responses)
    with pytest.raises(HTTPException) as exc:
        _run(routes_st._fetch_net(
            _cfg().st_net, "GET", "https://example.com/0", {}, b""
        ))
    assert exc.value.status_code == 502


def test_fetch_303_switches_post_to_get(monkeypatch):
    client = _patch_http(monkeypatch, [
        _FakeResponse(303, {"location": "https://example.com/result"}),
        _FakeResponse(200, {}, [b"ok"]),
    ])
    status, _, data, _ = _run(routes_st._fetch_net(
        _cfg().st_net, "POST", "https://example.com/submit", {}, b"payload"
    ))
    assert status == 200
    assert data == b"ok"
    assert client.requests[0].method == "POST"
    assert client.requests[1].method == "GET"
    assert client.requests[1].content in (b"", None)


# ---- 请求头与限流 ----

def test_clean_headers_drops_credentials_and_crlf():
    out = routes_st._net_clean_headers({
        "Accept": "text/html",
        "cookie": "session=1",
        "Authorization": "Bearer t",
        "Referer": "https://a",
        "Origin": "https://a",
        "Content-Type": "application/json",
        "X-Bad": "a\r\nInjected: 1",
    })
    assert out == {"accept": "text/html", "content-type": "application/json"}


def test_rate_limit_per_minute():
    routes_st._net_rate_check("u1", 2)
    routes_st._net_rate_check("u1", 2)
    with pytest.raises(HTTPException) as exc:
        routes_st._net_rate_check("u1", 2)
    assert exc.value.status_code == 429


def test_rate_limit_is_per_user():
    routes_st._net_rate_check("u1", 1)
    routes_st._net_rate_check("u2", 1)
    with pytest.raises(HTTPException):
        routes_st._net_rate_check("u1", 1)


def test_concurrency_cap():
    routes_st._net_inflight["u1"] = routes_st._NET_INFLIGHT_MAX
    with pytest.raises(HTTPException) as exc:
        routes_st._net_rate_check("u1", 60)
    assert exc.value.status_code == 429


# ---- 路由 ----

def _request(sc, cfg):
    state = SimpleNamespace(user=lambda _req: sc, config=cfg)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


def _payload(**kw):
    data = dict(method="GET", url="https://example.com/a", headers=None,
                body_base64=None)
    data.update(kw)
    return SimpleNamespace(**data)


def test_route_rejects_unknown_method():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.net_proxy(_payload(method="PUT"), _request(_sc(), _cfg())))
    assert exc.value.status_code == 400


def test_route_requires_url():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.net_proxy(_payload(url="  "), _request(_sc(), _cfg())))
    assert exc.value.status_code == 400


def test_route_rejects_body_on_get():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.net_proxy(
            _payload(method="GET", body_base64=base64.b64encode(b"x").decode()),
            _request(_sc(), _cfg()),
        ))
    assert exc.value.status_code == 400


def test_route_rejects_invalid_base64():
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.net_proxy(
            _payload(method="POST", body_base64="!!!"), _request(_sc(), _cfg())
        ))
    assert exc.value.status_code == 400


def test_route_rejects_oversize_body():
    big = base64.b64encode(b"x" * (routes_st._NET_UPLOAD_MAX + 1)).decode()
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.net_proxy(
            _payload(method="POST", body_base64=big), _request(_sc(), _cfg())
        ))
    assert exc.value.status_code == 413


def test_route_returns_base64_and_releases_inflight(monkeypatch):
    _patch_http(monkeypatch, [
        _FakeResponse(200, {"content-type": "text/plain"}, [b"hello"]),
    ])
    out = _run(routes_st.net_proxy(
        _payload(
            headers={"Accept": "text/plain", "Cookie": "a=b"},
            body_base64=None,
        ),
        _request(_sc(), _cfg()),
    ))
    assert out["status"] == 200
    assert base64.b64decode(out["body_base64"]) == b"hello"
    assert out["truncated"] is False
    assert out["headers"] == {"content-type": "text/plain"}
    assert routes_st._net_inflight.get("u1", 0) == 0


def test_route_rate_limited(monkeypatch):
    _patch_http(monkeypatch, [_FakeResponse(200, {}, [b"ok"])])
    cfg = _cfg(per_minute=1)
    _run(routes_st.net_proxy(_payload(), _request(_sc(), cfg)))
    with pytest.raises(HTTPException) as exc:
        _run(routes_st.net_proxy(_payload(), _request(_sc(), cfg)))
    assert exc.value.status_code == 429
