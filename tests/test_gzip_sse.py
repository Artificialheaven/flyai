# -*- coding: utf-8 -*-
"""GZip 中间件：大 JSON 压缩、小响应不压、SSE 原样直通（不缓冲、不加编码头）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from flyai.app import SSEAwareGZipMiddleware

BIG = {"data": "x" * 5000}
SSE_BODY = "data: one\n\ndata: two\n\n"

app = FastAPI()
app.add_middleware(SSEAwareGZipMiddleware, minimum_size=1024)


@app.get("/big")
def big():
    return BIG


@app.get("/small")
def small():
    return {"ok": True}


@app.get("/sse")
def sse():
    async def gen():
        yield "data: one\n\n"
        yield "data: two\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


client = TestClient(app)
GZIP = {"Accept-Encoding": "gzip"}


def test_large_json_is_gzipped():
    r = client.get("/big", headers=GZIP)
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    # httpx 已透明解压，内容与原文一致
    assert r.json() == BIG


def test_small_response_not_gzipped():
    r = client.get("/small", headers=GZIP)
    assert "content-encoding" not in r.headers
    assert r.json() == {"ok": True}


def test_sse_passthrough_not_gzipped():
    with client.stream("GET", "/sse", headers=GZIP) as r:
        assert r.status_code == 200
        # 关键：SSE 不加 gzip 头，避免 GZipResponder 攒块导致逐帧被延迟
        assert "content-encoding" not in r.headers
        body = "".join(r.iter_text())
    assert body == SSE_BODY
