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


# ---- Local Dream（手机端 8081 生成 + 8808 控制面） ----

def _sse(payload):
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in payload)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def test_localdream_selects_model_then_generates_png():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path == "/models":
            return httpx.Response(200, json={
                "models": [{
                    "id": "sd15", "name": "SD1.5", "generation_size": 512,
                    "dit_kind": "", "is_anima": False,
                    "defaults": {"steps": 20, "cfg": 7.0, "scheduler": "dpm",
                                 "negative_prompt": "lowres"},
                }],
            })
        if path == "/select":
            return httpx.Response(200, json={"ok": True})
        if path == "/status":
            return httpx.Response(200, json={"state": "running", "serving_model_id": "sd15"})
        if path == "/generate":
            return _sse([{"type": "complete", "image": PNG_B64, "format": "png",
                          "width": 512, "height": 512, "channels": 3}])
        return httpx.Response(404, json={})

    cfg = ImageModelConfig(id="ld", type="localdream",
                           base_url="http://127.0.0.1:8081", model="sd15", api_key="")
    content, ext, media = _run(httpx.MockTransport(handler), cfg, size="2K")
    assert content == PNG and (ext, media) == (".png", "image/png")
    paths = [p for _, p, _ in calls]
    assert "/select" in paths
    select = [c for _, p, c in calls if p == "/select"][0]
    select_body = json.loads(select)
    assert select_body == {"model_id": "sd15", "width": 512, "height": 512}
    gen = json.loads([c for _, p, c in calls if p == "/generate"][0])
    assert gen["width"] == 512 and gen["steps"] == 20 and gen["output_format"] == "png"
    assert "image" not in gen


def test_localdream_raw_rgb_fallback_becomes_png():
    raw = bytes([255, 0, 0] * 16)  # 4x4 RGB 红图

    def handler(request):
        if request.url.path == "/models":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/generate":
            return _sse([{"type": "complete", "image": base64.b64encode(raw).decode(),
                          "format": "raw", "width": 4, "height": 4, "channels": 3}])
        return httpx.Response(404, json={})

    cfg = ImageModelConfig(id="ld", type="localdream",
                           base_url="http://127.0.0.1:8081", model="", api_key="")
    content, ext, media = _run(httpx.MockTransport(handler), cfg)
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert (ext, media) == (".png", "image/png")


def test_localdream_without_control_plane_uses_manual_model():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/models":
            return httpx.Response(404, json={})
        if request.url.path == "/generate":
            return _sse([{"type": "complete", "image": PNG_B64, "format": "png",
                          "width": 512, "height": 512, "channels": 3}])
        return httpx.Response(404, json={})

    cfg = ImageModelConfig(id="ld", type="localdream",
                           base_url="http://127.0.0.1:8081", model="", api_key="")
    content, _, _ = _run(httpx.MockTransport(handler), cfg)
    assert content == PNG
    assert "/select" not in calls


def test_localdream_uses_supported_resolution_pair():
    calls = []

    def handler(request):
        calls.append((request.url.path, request.content))
        if request.url.path == "/models":
            return httpx.Response(200, json={
                "use_img2img": True,
                "models": [{
                    "id": "sd15", "generation_size": 512,
                    "resolutions": [[512, 768], [512, 512], [768, 512]],
                    "defaults": {"steps": 20, "cfg": 7.0, "scheduler": "dpm"},
                }],
            })
        if request.url.path == "/select":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/status":
            return httpx.Response(200, json={"state": "running", "serving_model_id": "sd15"})
        if request.url.path == "/generate":
            return _sse([{"type": "complete", "image": PNG_B64, "format": "png",
                          "width": 512, "height": 512, "channels": 3}])
        return httpx.Response(404, json={})

    cfg = ImageModelConfig(id="ld", type="localdream",
                           base_url="http://127.0.0.1:8081", model="sd15")
    _run(httpx.MockTransport(handler), cfg)
    select = json.loads([c for p, c in calls if p == "/select"][0])
    assert (select["width"], select["height"]) == (512, 512)
    gen = json.loads([c for p, c in calls if p == "/generate"][0])
    assert (gen["width"], gen["height"]) == (512, 512)


def test_localdream_blank_model_reuses_serving_model():
    calls = []

    def handler(request):
        calls.append((request.url.path, request.content))
        if request.url.path == "/models":
            return httpx.Response(200, json={"models": [
                {"id": "first", "generation_size": 512},
                {"id": "second", "generation_size": 768},
            ]})
        if request.url.path == "/status":
            return httpx.Response(200, json={"state": "running", "serving_model_id": "second"})
        if request.url.path == "/select":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/generate":
            return _sse([{"type": "complete", "image": PNG_B64, "format": "png",
                          "width": 768, "height": 768, "channels": 3}])
        return httpx.Response(404, json={})

    cfg = ImageModelConfig(id="ld", type="localdream",
                           base_url="http://127.0.0.1:8081", model="")
    _run(httpx.MockTransport(handler), cfg)
    select = json.loads([c for p, c in calls if p == "/select"][0])
    assert select["model_id"] == "second"
    assert (select["width"], select["height"]) == (768, 768)


def test_localdream_rejects_img2img_when_host_disabled():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/models":
            return httpx.Response(200, json={
                "use_img2img": False,
                "models": [{"id": "sd15", "generation_size": 512}],
            })
        return httpx.Response(404, json={})

    cfg = ImageModelConfig(id="ld", type="localdream",
                           base_url="http://127.0.0.1:8081", model="sd15")
    try:
        _run(httpx.MockTransport(handler), cfg, refs=[("image/png", PNG_B64)])
    except RuntimeError as exc:
        assert "图生图" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    assert "/select" not in calls and "/generate" not in calls
