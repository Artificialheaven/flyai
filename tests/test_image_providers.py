"""生图接口分发：Ark / OpenAI 图片接口 / Google Gemini 的请求与解析。"""

import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx

from flyai.config import ImageModelConfig
from flyai.tools.image_tool import ark_generate

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
PNG_B64 = base64.b64encode(PNG).decode()


class _Recorder:
    def __init__(self, response_factory):
        self.calls = []
        self.response_factory = response_factory

    def __call__(self, request):
        self.calls.append(request)
        return self.response_factory(request)


def _run(transport, cfg, prompt="画只猫", size="2K", refs=None):
    orig = httpx.AsyncClient.__init__

    def patched(self, *a, **k):
        k["transport"] = transport
        orig(self, *a, **k)

    httpx.AsyncClient.__init__ = patched
    try:
        return asyncio.run(ark_generate(cfg, prompt, size, ref_images=refs))
    finally:
        httpx.AsyncClient.__init__ = orig


def _json_response(payload):
    return lambda request: httpx.Response(200, json=payload)


def test_google_generate_content_parses_inline_image():
    seen = _Recorder(_json_response({
        "candidates": [{"content": {"parts": [
            {"text": "好的"},
            {"inlineData": {"mimeType": "image/png", "data": PNG_B64}},
        ]}}],
    }))
    cfg = ImageModelConfig(
        id="g", type="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-2.5-flash-image", api_key="",
    )
    content, ext, media = _run(httpx.MockTransport(seen), cfg, refs=[("image/jpeg", "QUJD")])
    assert content == PNG
    assert (ext, media) == (".png", "image/png")
    req = seen.calls[0]
    assert req.url.path.endswith("/models/gemini-2.5-flash-image:generateContent")
    assert "x-goog-api-key" not in req.headers
    body = json.loads(req.content)
    parts = body["contents"][0]["parts"]
    assert parts[0]["text"] == "画只猫"
    assert parts[1]["inline_data"] == {"mime_type": "image/jpeg", "data": "QUJD"}
    assert body["generationConfig"]["responseModalities"] == ["TEXT", "IMAGE"]


def test_google_sends_api_key_header_when_present():
    seen = _Recorder(_json_response({
        "candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/jpeg", "data": PNG_B64}},
        ]}}],
    }))
    cfg = ImageModelConfig(id="g", type="google", model="gemini-3-pro-image-preview", api_key="k-1")
    content, ext, media = _run(httpx.MockTransport(seen), cfg)
    assert content == PNG
    assert (ext, media) == (".jpg", "image/jpeg")
    assert seen.calls[0].headers["x-goog-api-key"] == "k-1"


def test_openai_generate_b64_json_and_keyless():
    seen = _Recorder(_json_response({"data": [{"b64_json": PNG_B64}]}))
    cfg = ImageModelConfig(id="o", type="openai", base_url="https://api.openai.com/v1",
                           model="gpt-image-1", api_key="")
    content, ext, media = _run(httpx.MockTransport(seen), cfg, size="1K")
    assert content == PNG and ext == ".png"
    req = seen.calls[0]
    assert req.url.path == "/v1/images/generations"
    assert "authorization" not in req.headers
    body = json.loads(req.content)
    assert body["size"] == "1024x1024" and body["n"] == 1


def test_openai_edit_uses_multipart_and_returns_b64():
    seen = _Recorder(_json_response({"data": [{"b64_json": PNG_B64}]}))
    cfg = ImageModelConfig(id="o", type="openai", base_url="https://api.openai.com/v1",
                           model="gpt-image-1", api_key="")
    content, ext, media = _run(
        httpx.MockTransport(seen), cfg,
        refs=[("image/jpeg", base64.b64encode(b"\xff\xd8\xff" + b"0" * 8).decode())],
    )
    assert content == PNG and ext == ".png"
    req = seen.calls[0]
    assert req.url.path == "/v1/images/edits"
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="model"' in req.content and b"gpt-image-1" in req.content
