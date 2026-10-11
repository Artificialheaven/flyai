"""CORS：APK（Capacitor WebView）源可跨域调接口，其他源不放行。

不进 lifespan（TestClient 不用 with），预检请求由 CORSMiddleware 直接应答，
因此不会触碰真实 data 目录。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from flyai.app import create_app


client = TestClient(create_app())


def _preflight(origin):
    return client.options(
        "/api/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )


def test_capacitor_android_origin_allowed():
    r = _preflight("https://localhost")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://localhost"
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_capacitor_ios_origin_allowed():
    r = _preflight("capacitor://localhost")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "capacitor://localhost"


def test_unknown_origin_rejected():
    r = _preflight("https://evil.example.com")
    assert "access-control-allow-origin" not in r.headers
