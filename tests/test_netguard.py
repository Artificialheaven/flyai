"""netguard SSRF 边界回归：CGNAT、非标准 IP 字面量、DNS 解析校验、连接钉住。"""

import asyncio
import ipaddress
import os
import socket
import sys

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import flyai.netguard as netguard
from flyai.netguard import (
    BlockedHost,
    is_blocked_host,
    pin_request,
    proxy_in_use as _real_proxy_in_use,
    resolve_and_check,
)


@pytest.fixture(autouse=True)
def _no_system_proxy(monkeypatch):
    """测试机器可能配置了系统代理；默认按“无代理”验证钉住逻辑。"""
    monkeypatch.setattr(netguard, "proxy_in_use", lambda *a, **k: False)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- 字面量判定 ----

@pytest.mark.parametrize("host", [
    "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
    "169.254.169.254", "0.0.0.0", "100.64.0.1", "::1", "fe80::1",
    # OS 会识别的非标准写法（ipaddress 不认 → 旧版会放行）
    "0x7f000001", "127.1", "0177.0.0.1", "2130706433",
    "localhost", "foo.localhost",
])
def test_blocked_literals(host):
    assert is_blocked_host(host)


@pytest.mark.parametrize("host", [
    "example.com", "sub.example.com", "127.0.0.1.nip.io",  # 域名层留给 DNS 收口
    "8.8.8.8", "1.1.1.1",
])
def test_allowed_literals(host):
    assert not is_blocked_host(host)


# ---- DNS 解析校验 ----

def _patch_dns(monkeypatch, mapping):
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host not in mapping:
            raise socket.gaierror(socket.EAI_NONAME, "Name not resolved")
        addr = mapping[host]
        fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (addr, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_domain_public_ip_passes(monkeypatch):
    _patch_dns(monkeypatch, {"good.example": "93.184.216.34"})
    ips = _run(resolve_and_check("good.example"))
    assert [str(ip) for ip in ips] == ["93.184.216.34"]


def test_domain_resolving_internal_is_blocked(monkeypatch):
    # nip.io 式：公网域名解析到内网
    _patch_dns(monkeypatch, {"127.0.0.1.nip.io": "127.0.0.1"})
    with pytest.raises(BlockedHost):
        _run(resolve_and_check("127.0.0.1.nip.io"))


def test_domain_resolving_cgnat_is_blocked(monkeypatch):
    _patch_dns(monkeypatch, {"nat.example": "100.64.0.9"})
    with pytest.raises(BlockedHost):
        _run(resolve_and_check("nat.example"))


def test_nxdomain_is_blocked(monkeypatch):
    _patch_dns(monkeypatch, {})
    with pytest.raises(BlockedHost):
        _run(resolve_and_check("no-such-host.example"))


def test_ambiguous_literal_blocked_without_dns(monkeypatch):
    called = {"n": 0}

    def fake_getaddrinfo(*a, **k):
        called["n"] += 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BlockedHost):
        _run(resolve_and_check("0x7f000001"))
    assert called["n"] == 0  # 纯字面量，不应触发 DNS


def test_localhost_blocked_without_dns():
    with pytest.raises(BlockedHost):
        _run(resolve_and_check("localhost"))


# ---- 连接钉住 ----

def test_pin_request_https_preserves_host_and_sni():
    req = httpx.Request("GET", "https://good.example/path")
    pin_request(req, ipaddress.ip_address("93.184.216.34"))
    assert req.url.host == "93.184.216.34"
    assert req.headers["host"] == "good.example"
    # anyio ≥4.13 要求 str（会自行 encode），bytes 反而触发 AttributeError
    assert req.extensions["sni_hostname"] == "good.example"


def test_pin_request_http_no_sni_extension():
    req = httpx.Request("GET", "http://good.example/path")
    pin_request(req, ipaddress.ip_address("93.184.216.34"))
    assert req.url.host == "93.184.216.34"
    assert req.headers["host"] == "good.example"
    assert "sni_hostname" not in req.extensions


def test_proxy_in_use_respects_scheme_and_bypass(monkeypatch):
    monkeypatch.setattr(
        netguard, "getproxies",
        lambda: {"https": "http://127.0.0.1:7890"},
    )
    monkeypatch.setattr(netguard, "proxy_bypass", lambda host: False)
    assert _real_proxy_in_use("example.com", "https") is True
    # 只配了 https 代理：http 请求仍直连，可以继续钉 IP
    assert _real_proxy_in_use("example.com", "http") is False
    monkeypatch.setattr(netguard, "proxy_bypass", lambda host: True)
    assert _real_proxy_in_use("example.com", "https") is False
    monkeypatch.setattr(
        netguard, "getproxies",
        lambda: {"https": "http://127.0.0.1:7890", "no": "*"},
    )
    assert _real_proxy_in_use("example.com", "https") is False


def test_pin_request_skips_behind_http_proxy(monkeypatch):
    # 代理通道不处理 sni_hostname：改成 IP 会按 IP 校验证书而失败
    monkeypatch.setattr(netguard, "proxy_in_use", lambda *a, **k: True)
    req = httpx.Request("GET", "https://good.example/path")
    pin_request(req, ipaddress.ip_address("93.184.216.34"))
    assert req.url.host == "good.example"
    assert req.headers["host"] == "good.example"
    assert "sni_hostname" not in req.extensions


def test_pin_request_ipv6():
    req = httpx.Request("GET", "https://good.example/")
    pin_request(req, ipaddress.ip_address("2606:4700::1"))
    assert req.url.host == "2606:4700::1"
    assert "[2606:4700::1]" in str(req.url)
    assert req.headers["host"] == "good.example"
