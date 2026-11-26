"""出站网络重试：只重试连接阶段的瞬时错误，不重试业务错误或流中途中断。"""

import asyncio
import socket
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest

from flyai.net_retry import request_with_retry, send_with_retry, stream_with_retry


DNS_ERROR = httpx.ConnectError("[Errno -3] Temporary failure in name resolution")


class FlakyClient:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    async def request(self, method, url, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "response"

    async def send(self, request):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "response"


def test_request_retries_transient_then_succeeds():
    client = FlakyClient([DNS_ERROR, DNS_ERROR])
    out = asyncio.run(
        request_with_retry(client, "GET", "https://example.com", base_delay=0)
    )
    assert out == "response"
    assert client.calls == 3


def test_request_gives_up_after_attempts():
    client = FlakyClient([DNS_ERROR, DNS_ERROR, DNS_ERROR, DNS_ERROR])
    with pytest.raises(httpx.ConnectError):
        asyncio.run(request_with_retry(client, "POST", "https://x", base_delay=0))
    assert client.calls == 3


def test_request_does_not_retry_non_network_errors():
    class Boom:
        calls = 0

        async def request(self, method, url, **kwargs):
            self.calls += 1
            raise RuntimeError("bad request")

    client = Boom()
    with pytest.raises(RuntimeError):
        asyncio.run(request_with_retry(client, "GET", "https://x", base_delay=0))
    assert client.calls == 1


def test_send_with_retry_rebuilds_request():
    client = FlakyClient([DNS_ERROR])
    built = []

    def make_request():
        built.append(1)
        return "request"

    out = asyncio.run(send_with_retry(client, make_request, base_delay=0))
    assert out == "response"
    assert len(built) == 2


class FakeStream:
    def __init__(self, fail):
        self.fail = fail

    async def __aenter__(self):
        if self.fail:
            raise DNS_ERROR
        return "resp"

    async def __aexit__(self, *exc):
        return False


class StreamClient:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def stream(self, method, url, **kwargs):
        self.calls += 1
        return FakeStream(self.calls <= self.failures)


def test_stream_retries_until_headers():
    client = StreamClient(failures=2)

    async def run():
        async with stream_with_retry(client, "POST", "https://x", base_delay=0) as resp:
            return resp

    assert asyncio.run(run()) == "resp"
    assert client.calls == 3


def test_stream_does_not_retry_body_failure():
    client = StreamClient(failures=0)

    async def run():
        async with stream_with_retry(client, "POST", "https://x", base_delay=0):
            raise httpx.ConnectError("mid-stream")

    with pytest.raises(httpx.ConnectError):
        asyncio.run(run())
    assert client.calls == 1


# ---- netguard DNS 解析重试 ----

def test_resolve_ips_retries_transient_dns(monkeypatch):
    from flyai.netguard import resolve_ips

    calls = {"n": 0}

    def fake_getaddrinfo(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise socket.gaierror(-3, "Temporary failure in name resolution")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    ips = asyncio.run(resolve_ips("example.com"))
    assert [str(ip) for ip in ips] == ["93.184.216.34"]
    assert calls["n"] >= 3


def test_resolve_ips_does_not_retry_nxdomain(monkeypatch):
    from flyai.netguard import BlockedHost, resolve_ips

    calls = {"n": 0}

    def fake_getaddrinfo(*args, **kwargs):
        calls["n"] += 1
        raise socket.gaierror(getattr(socket, "EAI_NONAME", -2), "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BlockedHost):
        asyncio.run(resolve_ips("does-not-exist.invalid"))
    assert calls["n"] == 1
