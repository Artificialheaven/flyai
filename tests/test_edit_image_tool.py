"""EditImageTool（图生图）单测：参考图解析、调用与失败路径。"""

import asyncio
import base64
import json
import os
import re
import sys

import pytest

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
    async def fake_ark(model_cfg, prompt, size, ref_images=None):
        captured.update(model=model_cfg.id, prompt=prompt, size=size,
                        ref_images=ref_images)
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
    assert captured["ref_images"] == [("image/jpeg", JPEG_B64)]
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
    assert result.ok
    assert captured["ref_images"] == [("image/jpeg", JPEG_B64)]


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
    assert captured["ref_images"] == [
        ("image/jpeg", base64.b64encode(newer).decode())
    ]


def test_explicit_image_path(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    (tmp_path / "ref.jpg").write_bytes(JPEG)
    result = asyncio.run(_tool().run(
        {"prompt": "换成油画", "image": "/uploads/u/ref.jpg"}, _ctx(tmp_path)
    ))
    assert result.ok
    assert captured["ref_images"] == [("image/jpeg", JPEG_B64)]


IMG2 = b"\xff\xd8\xff\xe0" + b"\x11" * 32
IMG2_B64 = base64.b64encode(IMG2).decode()


def test_auto_count_collects_two_user_images_chronological(tmp_path, monkeypatch):
    """image_count=2：倒序收集两张用户图后翻回时间先后，先发=图1。"""
    captured = {}
    _patch_ark(monkeypatch, captured)
    history = [
        Message(role="user", content="图1", images=[
            ImageAttachment(media_type="image/jpeg", data=JPEG_B64)]),
        Message(role="user", content="图2", images=[
            ImageAttachment(media_type="image/jpeg", data=IMG2_B64)]),
    ]
    result = asyncio.run(_tool().run(
        {"prompt": "将图1人物的服装换成图2的服装", "image_count": 2},
        _ctx(tmp_path, history=history)))
    assert result.ok
    assert captured["ref_images"] == [
        ("image/jpeg", JPEG_B64), ("image/jpeg", IMG2_B64)]


def test_auto_count_two_attachments_one_message(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    history = [Message(role="user", content="两张", images=[
        ImageAttachment(media_type="image/jpeg", data=JPEG_B64),
        ImageAttachment(media_type="image/jpeg", data=IMG2_B64)])]
    result = asyncio.run(_tool().run(
        {"prompt": "融合图1与图2", "image_count": 2},
        _ctx(tmp_path, history=history)))
    assert result.ok
    assert len(captured["ref_images"]) == 2


def test_auto_count_shortage_error(tmp_path):
    """历史只有 1 张却要 2 张：报错且不静默少图。"""
    history = [Message(role="user", content="一张", images=[
        ImageAttachment(media_type="image/jpeg", data=JPEG_B64)])]
    result = asyncio.run(_tool().run(
        {"prompt": "p", "image_count": 2},
        _ctx(tmp_path, history=history)))
    assert not result.ok
    assert "需要 2 张" in result.output and "只有 1 张" in result.output


def test_explicit_array_of_paths(tmp_path, monkeypatch):
    captured = {}
    _patch_ark(monkeypatch, captured)
    (tmp_path / "a.jpg").write_bytes(JPEG)
    (tmp_path / "b.jpg").write_bytes(IMG2)
    result = asyncio.run(_tool().run(
        {"prompt": "把图1人物放进图2场景",
         "image": ["/uploads/u/a.jpg", "/uploads/u/b.jpg"]},
        _ctx(tmp_path)))
    assert result.ok
    assert captured["ref_images"] == [
        ("image/jpeg", JPEG_B64), ("image/jpeg", IMG2_B64)]


def test_too_many_explicit_paths(tmp_path):
    paths = ["/uploads/u/%d.jpg" % i for i in range(15)]
    result = asyncio.run(_tool().run(
        {"prompt": "p", "image": paths}, _ctx(tmp_path)))
    assert not result.ok and "最多 14 张" in result.output


def test_refs_persisted_in_meta(tmp_path, monkeypatch):
    _patch_ark(monkeypatch, {})
    history = [
        Message(role="user", content="图1", images=[
            ImageAttachment(media_type="image/jpeg", data=JPEG_B64)]),
        Message(role="user", content="图2", images=[
            ImageAttachment(media_type="image/jpeg", data=IMG2_B64)]),
    ]
    result = asyncio.run(_tool().run(
        {"prompt": "p", "image_count": 2},
        _ctx(tmp_path, history=history)))
    mm = re.search(r"^\[图片参数\] (\{.*\})$", result.output, re.M)
    info = json.loads(mm.group(1))
    assert info["ref_count"] == 2
    refs = info["refs"]
    assert len(refs) == 2
    assert all(r.startswith("/uploads/u/ref_") for r in refs)
    # 参考图实际落盘
    assert all((tmp_path / r.rsplit("/", 1)[1]).is_file() for r in refs)


def test_missing_reference_error(tmp_path):
    result = asyncio.run(_tool().run(
        {"prompt": "改图"}, _ctx(tmp_path, history=[])
    ))
    assert not result.ok and "只有 0 张" in result.output


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


# ---- Ark image 编码与退化（data URI → 裸 base64）----

def _make_handler(calls, first_response):
    import httpx

    def handler(request):
        if not request.url.path.endswith("/images/generations"):
            # 结果图下载
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        body = json.loads(request.content)
        calls.append(body.get("image"))
        if len(calls) == 1:
            return first_response
        return httpx.Response(200, json={"data": [{"url": "https://cdn/x.jpg"}]})

    return handler


def test_ark_generate_retries_raw_base64_on_image_400():
    import httpx

    calls = []
    handler = _make_handler(calls, httpx.Response(
        400, json={"error": {"message": "invalid image base64"}}))

    out = asyncio.run(_run_with(
        httpx.MockTransport(handler), _model(),
        refs=[("image/jpeg", JPEG_B64)]))
    assert out == JPEG
    assert calls[0].startswith("data:image/jpeg;base64,")
    assert calls[1] == JPEG_B64  # 裸 base64


def test_ark_generate_multiple_refs_send_array():
    """多张参考图：image 发 data URI 数组（顺序即图1、图2）；400 后退化裸 base64 数组。"""
    import httpx

    calls = []
    handler = _make_handler(calls, httpx.Response(
        400, json={"error": {"message": "invalid image"}}))

    asyncio.run(_run_with(
        httpx.MockTransport(handler), _model(),
        refs=[("image/jpeg", "AAA"), ("image/png", "BBB")]))
    assert isinstance(calls[0], list) and len(calls[0]) == 2
    assert calls[0] == ["data:image/jpeg;base64,AAA",
                        "data:image/png;base64,BBB"]
    assert calls[1] == ["AAA", "BBB"]  # 裸 base64 数组


async def _run_with(transport, model_cfg, refs=None):
    import httpx

    from flyai.tools.image_tool import ark_generate

    # ark_generate 内部自建 client；补丁构造函数注入 MockTransport
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *a, **k):
        k["transport"] = transport
        orig_init(self, *a, **k)

    httpx.AsyncClient.__init__ = patched_init

    from flyai.tools import image_tool as _imod

    async def _fake_resolve(host):
        import ipaddress as _ipa
        return [_ipa.ip_address("203.0.113.1")]

    orig_resolve = _imod.resolve_and_check
    _imod.resolve_and_check = _fake_resolve
    try:
        content, ext, mt = await ark_generate(
            model_cfg, "p", "1K", ref_images=refs,
        )
    finally:
        httpx.AsyncClient.__init__ = orig_init
        _imod.resolve_and_check = orig_resolve
    return content


def test_ark_generate_both_failures_surfaces_first_error():
    """data URI 与裸 base64 都 400 时，异常必须含首次 data URI 的真实错误，
    不能只抛裸 base64 的 invalid url specified（真实事故：真实原因被回退掩盖）。"""
    import httpx

    calls = []
    handler = _make_handler(calls, httpx.Response(400, json={"error": {
        "message": "image resolution ratio out of range"}}))
    # 第二次（裸 base64）返回 invalid url specified：_make_handler 成功分支不同，
    # 这里单独构造
    def handler2(request):
        body = json.loads(request.content)
        calls.append(body.get("image"))
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {
                "message": "image resolution ratio out of range"}})
        return httpx.Response(400, json={"error": {
            "message": "invalid url specified"}})

    with pytest.raises(RuntimeError) as ei:
        asyncio.run(_run_with(
            httpx.MockTransport(handler2), _model(),
            refs=[("image/jpeg", JPEG_B64)]))
    msg = str(ei.value)
    assert "invalid url specified" in msg
    assert "resolution ratio out of range" in msg  # 首次错误未被吞


def test_error_mentions_image():
    from flyai.tools.image_tool import _error_mentions_image

    assert _error_mentions_image('{"err": "bad image url"}')
    assert _error_mentions_image("不支持的图片")
    assert not _error_mentions_image("quota exceeded")
