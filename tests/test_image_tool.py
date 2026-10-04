"""AI 生图工具与生图模型选择单测。"""

import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest

from flyai.config import ImageGenSettings, ImageModelConfig
from flyai.tools.base import ToolContext
from flyai.tools.image_tool import GenerateImageTool, select_image_model


def _model(mid="m1", api_key="ark-key", model="seedream"):
    return ImageModelConfig(id=mid, api_key=api_key, model=model)


def _tool():
    return GenerateImageTool(ImageGenSettings())


def _ctx(tmp_path, url_base="/uploads/u", **extra):
    services = {"upload_dir": tmp_path, "upload_url_base": url_base}
    services.update(extra)
    return ToolContext(services=services)


def test_select_prefer_explicit_id():
    m1, m2 = _model("m1"), _model("m2")
    got = select_image_model({"image_models": [m1, m2], "image_model_id": "m2"})
    assert got.id == "m2"


def test_select_unknown_id_falls_back_first():
    m1 = _model("m1")
    got = select_image_model({"image_models": [m1], "image_model_id": "nope"})
    assert got.id == "m1"


def test_select_empty():
    assert select_image_model({}) is None


def test_generate_success(tmp_path):
    tool = _tool()

    captured = {}

    async def fake_generate(model_cfg, prompt, size):
        captured["model"] = model_cfg.id
        return b"FAKE-JPEG", ".jpg", "image/jpeg"

    tool._generate = fake_generate
    ctx = _ctx(tmp_path, "/uploads/admin",
               image_models=[_model()], image_model_id="m1")
    result = asyncio.run(tool.run({"prompt": "一个二次元女生", "size": "2K"}, ctx))

    assert result.ok
    assert captured["model"] == "m1"
    first_line = result.output.splitlines()[0]
    m = re.fullmatch(r"\[图片已生成\] (/uploads/admin/gen_.+\.jpg)", first_line)
    assert m, first_line
    saved = tmp_path / os.path.basename(m.group(1))
    assert saved.read_bytes() == b"FAKE-JPEG"

    # [图片参数]：云端文生图方式
    import json as _json
    mm = re.search(r"^\[图片参数\] (\{.*\})$", result.output, re.M)
    assert mm, result.output
    info = _json.loads(mm.group(1))
    assert info["engine"] == "cloud"
    assert info["mode"] == "txt2img"
    assert info["model"] == "seedream"
    assert info["size"] == "2K"
    assert info["prompt"] == "一个二次元女生"


def test_save_without_meta_has_no_param_line(tmp_path):
    from flyai.tools.image_tool import save_generated_image

    out = save_generated_image(
        {"upload_dir": tmp_path, "upload_url_base": "/uploads/u"},
        b"x", ".png", "image/png",
    )
    assert "[图片参数]" not in out["output"]


def test_generate_default_size(tmp_path):
    tool = _tool()
    captured = {}

    async def fake_generate(model_cfg, prompt, size):
        captured["size"] = size
        return b"x", ".jpg", "image/jpeg"

    tool._generate = fake_generate
    ctx = _ctx(tmp_path, image_models=[_model()])
    asyncio.run(tool.run({"prompt": "猫"}, ctx))
    assert captured["size"] == "2K"  # 取默认值


def test_generate_bad_size_falls_back(tmp_path):
    tool = _tool()
    captured = {}

    async def fake_generate(model_cfg, prompt, size):
        captured["size"] = size
        return b"x", ".jpg", "image/jpeg"

    tool._generate = fake_generate
    ctx = _ctx(tmp_path, image_models=[_model()])
    asyncio.run(tool.run({"prompt": "猫", "size": "8K"}, ctx))
    assert captured["size"] == "2K"


def test_fail_empty_prompt(tmp_path):
    ctx = _ctx(tmp_path, image_models=[_model()])
    result = asyncio.run(_tool().run({"prompt": "  "}, ctx))
    assert not result.ok
    assert result.output.startswith("[参数错误]")


def test_fail_no_available_model(tmp_path):
    ctx = _ctx(tmp_path)
    result = asyncio.run(_tool().run({"prompt": "猫"}, ctx))
    assert not result.ok
    assert "没有可用的生图模型" in result.output


def test_fail_missing_api_key(tmp_path):
    ctx = _ctx(tmp_path, image_models=[_model(api_key="")])
    result = asyncio.run(_tool().run({"prompt": "猫"}, ctx))
    assert not result.ok
    assert "API Key" in result.output


def test_fail_missing_upload_context():
    ctx = ToolContext(services={"image_models": [_model()]})
    result = asyncio.run(_tool().run({"prompt": "猫"}, ctx))
    assert not result.ok
    assert result.output.startswith("[工具错误]")


def test_generate_error_from_api(tmp_path):
    tool = _tool()

    async def fake_generate(model_cfg, prompt, size):
        raise RuntimeError("HTTP 429: limited")

    tool._generate = fake_generate
    ctx = _ctx(tmp_path, image_models=[_model()])
    result = asyncio.run(tool.run({"prompt": "猫"}, ctx))
    assert not result.ok
    assert "429" in result.output


# ---- 结果图下载 SSRF 收口（_guarded_image_get）----

import ipaddress

from flyai.tools import image_tool as imod
from flyai.tools.image_tool import _guarded_image_get


def _patch_resolver(monkeypatch, blocked=()):
    async def fake_resolve_and_check(host):
        h = (host or "").strip().lower().rstrip(".")
        if h in blocked:
            raise imod.BlockedHost("域名 %s 解析到内网或本机地址" % h)
        return [ipaddress.ip_address("203.0.113.%d" % (1 + abs(hash(h)) % 250))]

    monkeypatch.setattr(imod, "resolve_and_check", fake_resolve_and_check)


def _run_guarded(transport, url):
    async def main():
        async with httpx.AsyncClient(transport=transport) as client:
            return await _guarded_image_get(client, url)
    return asyncio.run(main())


def test_guarded_get_success(monkeypatch):
    _patch_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "image/png"},
                                 content=b"PNGDATA")
    )
    data, resp = _run_guarded(transport, "https://img.example/a.png")
    assert data == b"PNGDATA"
    assert "image/png" in resp.headers.get("content-type", "")


def test_guarded_get_rejects_bad_scheme(monkeypatch):
    _patch_resolver(monkeypatch)
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    with pytest.raises(RuntimeError, match="图片地址非法"):
        _run_guarded(transport, "file:///etc/passwd")


def test_guarded_get_redirect_to_internal_blocked(monkeypatch):
    _patch_resolver(monkeypatch, blocked=("127.0.0.1",))

    def handler(request):
        if request.headers.get("host") == "img.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/x"})
        return httpx.Response(200, text="内网响应")

    transport = httpx.MockTransport(handler)
    with pytest.raises(RuntimeError, match="内网或本机"):
        _run_guarded(transport, "https://img.example/a.png")


def test_guarded_get_too_many_redirects(monkeypatch):
    _patch_resolver(monkeypatch)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "/next"})
    )
    with pytest.raises(RuntimeError, match="重定向次数过多"):
        _run_guarded(transport, "https://loop.example/start")


def test_guarded_get_oversize_blocked(monkeypatch):
    _patch_resolver(monkeypatch)
    big = b"x" * (20 * 1024 * 1024 + 1)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, headers={"content-type": "image/jpeg"},
                                 content=big)
    )
    with pytest.raises(RuntimeError, match="图片过大"):
        _run_guarded(transport, "https://img.example/big.jpg")
