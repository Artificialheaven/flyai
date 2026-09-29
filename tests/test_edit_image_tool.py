"""EditImageTool（图生图）单测：参考图解析、调用与失败路径。"""

import asyncio
import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import ImageGenSettings, ImageModelConfig
from flyai.schemas import ImageAttachment, Message
from flyai.tools import edit_image_tool as mod
from flyai.tools.base import ToolContext
from flyai.tools.edit_image_tool import EditImageTool

# 最小合法 JPEG（魔数 + 占位）
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
JPEG_B64 = base64.b64encode(JPEG).decode()


def _model(api_key="ark-key", model="seedream-x"):
    return ImageModelConfig(id="m1", api_key=api_key, model=model)


def _tool():
    return EditImageTool(ImageGenSettings())


def _ctx(tmp_path, url_base="/uploads/u", history=None, model=None):
    services = {
        "upload_dir": tmp_path,
        "upload_url_base": url_base,
        "history": history or [],
        "image_models": [_model() if model is None else model],
    }
    return ToolContext(services=services)


def _patch_ark(monkeypatch, captured, result=None):
    async def fake_ark(model_cfg, prompt, size,
                       ref_media_type=None, ref_b64=None):
        captured.update(model=model_cfg.id, prompt=prompt, size=size,
                        ref_media_type=ref_media_type, ref_b64=ref_b64)
        return result or (b"OUT-JPEG", ".jpg", "image/jpeg")

    monkeypatch.setattr(mod, "ark_generate", fake_ark)


def test_auto_reference_from_user_image(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    history = [Message(role="user", content="照着画", images=[
        ImageAttachment(media_type="image/jpeg", data=JPEG_B64)
    ])]
    result = asyncio.run(_tool().run(
        {"prompt": "改成水彩风格"}, _ctx(tmp_path, history=history)
    ))
    assert result.ok
    assert captured["model"] == "m1"
    assert captured["ref_media_type"] == "image/jpeg"
    assert captured["ref_b64"] == JPEG_B64
    assert result.output.startswith("[图片已生成] /uploads/u/gen_")

    # [图片参数]：云端图生图方式
    mm = re.search(r"^\[图片参数\] (\{.*\})$", result.output, re.M)
    assert mm, result.output
    info = json.loads(mm.group(1))
    assert info["engine"] == "cloud"
    assert info["mode"] == "img2img"
    assert info["prompt"] == "改成水彩风格"


def test_auto_reference_from_generated_marker(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    # 上传目录里放一张「之前生成的图」
    (tmp_path / "gen_old.jpg").write_bytes(JPEG)
    history = [
        Message(role="user", content="画个狗"),
        Message(role="tool", name="generate_image", content=(
            "[图片已生成] /uploads/u/gen_old.jpg\n图片路径..."
        )),
    ]
    result = asyncio.run(_tool().run(
        {"prompt": "把狗换成猫"}, _ctx(tmp_path, history=history)
    ))
    assert result.ok and captured["ref_b64"] == JPEG_B64


def test_user_image_preferred_over_older_generated(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    (tmp_path / "gen_old.jpg").write_bytes(JPEG)
    newer = b"\xff\xd8\xff\xe0" + b"\x11" * 32
    history = [
        Message(role="tool", name="generate_image",
                content="[图片已生成] /uploads/u/gen_old.jpg\nx"),
        Message(role="user", content="参考这张", images=[
            ImageAttachment(media_type="image/jpeg",
                            data=base64.b64encode(newer).decode())
        ]),
    ]
    asyncio.run(_tool().run(
        {"prompt": "p"}, _ctx(tmp_path, history=history)
    ))
    assert captured["ref_b64"] == base64.b64encode(newer).decode()


def test_explicit_image_path(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    (tmp_path / "ref.jpg").write_bytes(JPEG)
    result = asyncio.run(_tool().run(
        {"prompt": "换成油画", "image": "/uploads/u/ref.jpg"}, _ctx(tmp_path)
    ))
    assert result.ok and captured["ref_b64"] == JPEG_B64


def test_missing_reference_error(tmp_path):
    result = asyncio.run(_tool().run(
        {"prompt": "改图"}, _ctx(tmp_path, history=[])
    ))
    assert not result.ok and "找不到" in result.output


def test_bad_reference_bytes(tmp_path):
    history = [Message(role="user", content="x", images=[
        ImageAttachment(media_type="image/jpeg",
                        data=base64.b64encode(b"not an image").decode())
    ])]
    result = asyncio.run(_tool().run(
        {"prompt": "p"}, _ctx(tmp_path, history=history)
    ))
    assert not result.ok and "图片格式" in result.output


def test_explicit_path_not_accessible(tmp_path):
    result = asyncio.run(_tool().run(
        {"prompt": "p", "image": "/uploads/other/secret.jpg"}, _ctx(tmp_path)
    ))
    assert not result.ok and "安全限制" in result.output


def test_ark_error_propagates(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("HTTP 429: limited")

    monkeypatch.setattr(mod, "ark_generate", boom)
    history = [Message(role="user", content="x", images=[
        ImageAttachment(media_type="image/jpeg", data=JPEG_B64)
    ])]
    result = asyncio.run(_tool().run(
        {"prompt": "p"}, _ctx(tmp_path, history=history)
    ))
    assert not result.ok and "429" in result.output


def test_no_available_model(tmp_path):
    ctx = ToolContext(services={
        "upload_dir": tmp_path, "upload_url_base": "/uploads/u",
        "history": [], "image_models": [],
    })
    result = asyncio.run(_tool().run({"prompt": "p"}, ctx))
    assert not result.ok and "没有可用的生图模型" in result.output


def test_missing_api_key(tmp_path):
    result = asyncio.run(_tool().run(
        {"prompt": "p"},
        _ctx(tmp_path, history=[], model=_model(api_key="")),
    ))
    assert not result.ok
    assert result.output.startswith("[工具错误]")


# ---- Ark base64 编码退化（data URI → 裸 base64）----

def test_ark_generate_retries_raw_base64_on_image_400():
    import httpx

    calls = []

    def handler(request):
        if not request.url.path.endswith("/images/generations"):
            # 结果图下载
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        body = json.loads(request.content)
        calls.append(body.get("image"))
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"message": "invalid image base64"}})
        return httpx.Response(200, json={"data": [{"url": "https://cdn/x.jpg"}]})

    transport = httpx.MockTransport(handler)
    model_cfg = _model()
    out = asyncio.run(_run_with(transport, model_cfg))
    assert out == JPEG
    assert calls[0].startswith("data:image/jpeg;base64,")
    assert calls[1] == JPEG_B64  # 裸 base64


async def _run_with(transport, model_cfg):
    import httpx

    from flyai.tools.image_tool import ark_generate

    # ark_generate 内部自建 client；补丁构造函数注入 MockTransport
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *a, **k):
        k["transport"] = transport
        orig_init(self, *a, **k)

    httpx.AsyncClient.__init__ = patched_init
    try:
        content, ext, mt = await ark_generate(
            model_cfg, "p", "1K",
            ref_media_type="image/jpeg", ref_b64=JPEG_B64,
        )
    finally:
        httpx.AsyncClient.__init__ = orig_init
    return content


def test_error_mentions_image():
    from flyai.tools.image_tool import _error_mentions_image

    assert _error_mentions_image('{"err": "bad image url"}')
    assert _error_mentions_image("不支持的图片")
    assert not _error_mentions_image("quota exceeded")
