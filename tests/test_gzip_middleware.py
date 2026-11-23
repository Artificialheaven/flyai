"""GZip 中间件：文本压缩、SSE/图片等二进制直通、压缩级别可调。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from flyai.app import SSEAwareGZipMiddleware, _is_compressible

BODY = ("x" * 4000).encode("utf-8")


def _client(media_type: str) -> TestClient:
    async def endpoint(request):
        return Response(BODY, media_type=media_type)

    app = Starlette(routes=[Route("/x", endpoint)])
    app.add_middleware(SSEAwareGZipMiddleware, minimum_size=1, compresslevel=5)
    return TestClient(app)


def test_is_compressible_types():
    assert _is_compressible("application/json; charset=utf-8")
    assert _is_compressible("text/html")
    assert _is_compressible("image/svg+xml")
    assert not _is_compressible("image/png")
    assert not _is_compressible("video/mp4")
    assert not _is_compressible("application/vnd.android.package-archive")
    assert not _is_compressible("application/octet-stream")
    assert not _is_compressible("font/woff2")


def test_json_is_gzipped():
    res = _client("application/json").get("/x", headers={"Accept-Encoding": "gzip"})
    assert res.headers.get("content-encoding") == "gzip"
    assert res.content == BODY


def test_png_is_passthrough():
    res = _client("image/png").get("/x", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in res.headers
    assert res.content == BODY


def test_sse_is_passthrough():
    res = _client("text/event-stream").get("/x", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in res.headers
    assert res.content == BODY
