"""本地 Stable Diffusion（Anything V5）引擎单测：

- 内置 AI 参数生成（generate_sd_params）：完整/缺字段/越界/坏 JSON
- Gradio 客户端（SDGradioClient）：MockTransport 打桩整条 HTTP 链
- GenerateImageTool 的 sd 引擎分支
"""

import asyncio
import base64
import contextlib
import json
import os
import re
import sys

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import ImageGenSettings, StableDiffusionSettings
from flyai.providers.base import StreamEventType
from flyai.sd.client import SDGradioClient
from flyai.sd.prompt import generate_sd_params
from flyai.tools.base import ToolContext
from flyai.tools.image_tool import GenerateImageTool


def _sd_settings(**kw):
    return StableDiffusionSettings(**kw)


# ---- 假 provider（complete 走 stream_chat）----

class _Event:
    def __init__(self, text):
        self.type = StreamEventType.CONTENT_DELTA
        self.text = text


class FakeProvider:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def stream_chat(self, messages, tool_specs=None):
        self.calls.append(messages)
        yield _Event(self.text)


# ---- generate_sd_params ----

def test_sd_params_full_preserved():
    payload = (
        '{"prompt": "masterpiece, 1girl, silver hair", "negative_prompt": "lowres", '
        '"width": 768, "height": 512, "steps": 30, "cfg_scale": 6.5, '
        '"sampler": "Euler a", "schedule": "Automatic", "seed": 42}'
    )
    got = asyncio.run(
        generate_sd_params(FakeProvider(payload), "一个银发女生", _sd_settings())
    )
    assert got["prompt"] == "masterpiece, 1girl, silver hair"
    assert got["negative_prompt"] == "lowres"
    assert (got["width"], got["height"]) == (768, 512)
    assert got["steps"] == 30
    assert got["cfg_scale"] == 6.5
    assert got["sampler"] == "Euler a"
    assert got["schedule"] == "Automatic"
    assert got["seed"] == 42


def test_sd_params_missing_fields_use_settings_defaults():
    payload = '{"prompt": "1girl"}'
    settings = _sd_settings(
        width=512, height=768, steps=25, cfg_scale=8.0,
        sampler="DPM++ SDE", schedule="Exponential",
        negative_prompt="custom negative",
    )
    got = asyncio.run(
        generate_sd_params(FakeProvider(payload), "猫耳少女", settings)
    )
    assert got["prompt"] == "1girl"
    assert got["negative_prompt"] == "custom negative"
    assert (got["width"], got["height"]) == (512, 768)
    assert got["steps"] == 25
    assert got["cfg_scale"] == 8.0
    assert got["sampler"] == "DPM++ SDE"
    assert got["schedule"] == "Exponential"
    assert got["seed"] == -1


def test_sd_params_out_of_range_clamped_and_rounded():
    payload = (
        '{"prompt": "x", "width": 9999, "height": 100, '
        '"steps": 999, "cfg_scale": 99, "seed": 123}'
    )
    got = asyncio.run(
        generate_sd_params(FakeProvider(payload), "x", _sd_settings())
    )
    assert got["width"] == 1024          # 夹到上限
    assert got["height"] == 256          # 夹到下限
    assert got["steps"] == 150
    assert got["cfg_scale"] == 30.0


def test_sd_params_width_rounds_to_multiple_of_8():
    payload = '{"prompt": "x", "width": 513, "height": 770}'
    got = asyncio.run(
        generate_sd_params(FakeProvider(payload), "x", _sd_settings())
    )
    assert got["width"] == 512
    assert got["height"] == 768          # 770 → 768（向下取 8 的倍数）


def test_sd_params_bad_json_falls_back_to_scene():
    got = asyncio.run(
        generate_sd_params(
            FakeProvider("我不会输出 JSON 对不起"),
            "一位金发女仆端着红茶",
            _sd_settings(negative_prompt="fallback neg"),
        )
    )
    assert got["prompt"] == "一位金发女仆端着红茶"  # 用原始画面描述兜底
    assert got["negative_prompt"] == "fallback neg"
    assert (got["width"], got["height"]) == (512, 768)
    assert got["seed"] == -1


def test_sd_params_empty_prompt_in_json_falls_back_to_scene():
    got = asyncio.run(
        generate_sd_params(
            FakeProvider('{"prompt": "  ", "steps": 22}'), "海边少女", _sd_settings()
        )
    )
    assert got["prompt"] == "海边少女"
    assert got["steps"] == 22


# ---- SDGradioClient ----

def _fake_config():
    """62 个输入组件的最小 config（fn_index 99）。"""
    comps = [
        {"id": 900 + i, "type": "textbox", "props": {"value": ""}}
        for i in range(62)
    ]
    # slot 0 的任务参数为 {}（Label）
    comps[0] = {"id": 900, "type": "label", "props": {"value": {}}}
    # slot 21：多选下拉，WebUI 端要求列表
    comps[21] = {
        "id": 921, "type": "dropdown",
        "props": {"value": None, "multiselect": True},
    }
    deps = [{"inputs": []} for _ in range(99)]
    deps.append({"inputs": [c["id"] for c in comps]})
    return {"components": comps, "dependencies": deps}


def test_client_txt2img_success():
    state = {"posted": None}

    def handler(request):
        path = request.url.path
        if path == "/config":
            return httpx.Response(200, json=_fake_config())
        if path == "/api/predict":
            state["posted"] = json.loads(request.content)
            return httpx.Response(200, json={"data": [
                [{"name": r"C:\sd\outputs\00001.png?1790.1", "is_file": True}],
                "", "", "",
            ]})
        if path.startswith("/file="):
            return httpx.Response(
                200, content=b"PNG-BYTES",
                headers={"content-type": "image/png"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = SDGradioClient(_sd_settings(), transport=transport)
    params = {
        "prompt": "masterpiece, 1girl", "negative_prompt": "lowres",
        "width": 512, "height": 768, "steps": 28, "cfg_scale": 7.0,
        "sampler": "DPM++ 2M", "schedule": "Karras", "seed": -1,
    }
    content, ext, media_type = asyncio.run(client.txt2img(params))

    assert content == b"PNG-BYTES"
    assert ext == ".png"
    assert media_type == "image/png"

    posted = state["posted"]
    assert posted["fn_index"] == 99
    data = posted["data"]
    assert data[1] == "masterpiece, 1girl"
    assert data[2] == "lowres"
    assert data[6] == 7.0
    assert data[7] == 768 and data[8] == 512
    assert data[21] == []
    assert data[23] == 28
    assert data[24] == "DPM++ 2M"
    assert data[25] == "Karras"
    assert data[29] == -1


def test_client_txt2img_embedded_base64():
    png_b64 = base64.b64encode(b"EMBEDDED-PNG").decode()

    def handler(request):
        if request.url.path == "/config":
            return httpx.Response(200, json=_fake_config())
        if request.url.path == "/api/predict":
            return httpx.Response(200, json={"data": [
                [{"name": None, "data": "data:image/png;base64," + png_b64}],
                "", "", "",
            ]})
        return httpx.Response(404)

    client = SDGradioClient(
        _sd_settings(), transport=httpx.MockTransport(handler)
    )
    content, ext, media_type = asyncio.run(client.txt2img({
        "prompt": "x", "negative_prompt": "x", "width": 512, "height": 512,
        "steps": 20, "cfg_scale": 7.0, "sampler": "Euler",
        "schedule": "Automatic", "seed": -1,
    }))
    assert content == b"EMBEDDED-PNG"
    assert media_type == "image/png"


def test_client_txt2img_error_html_raises():
    def handler(request):
        if request.url.path == "/config":
            return httpx.Response(200, json=_fake_config())
        if request.url.path == "/api/predict":
            return httpx.Response(200, json={"data": [
                [], "", "",
                "<div class='error'>RuntimeError: CUDA out of memory</div>",
            ]})
        return httpx.Response(404)

    client = SDGradioClient(
        _sd_settings(), transport=httpx.MockTransport(handler)
    )
    with _expect_runtime_error("CUDA out of memory"):
        asyncio.run(client.txt2img({
            "prompt": "x", "negative_prompt": "x", "width": 512, "height": 512,
            "steps": 20, "cfg_scale": 7.0, "sampler": "Euler",
            "schedule": "Automatic", "seed": -1,
        }))


@contextlib.contextmanager
def _expect_runtime_error(fragment):
    try:
        yield
    except RuntimeError as exc:
        assert fragment in str(exc), str(exc)
    else:
        raise AssertionError("expected RuntimeError")


# ---- 工具 sd 分支 ----

def test_tool_routes_to_sd_engine(tmp_path):
    tool = GenerateImageTool(ImageGenSettings(), _sd_settings(enabled=True))
    sd_params = {
        "prompt": "masterpiece, 1girl, fox ears",
        "negative_prompt": "lowres, bad anatomy",
        "width": 512, "height": 768, "steps": 28, "cfg_scale": 7.0,
        "sampler": "DPM++ 2M", "schedule": "Karras", "seed": 12345,
    }

    async def fake_run_sd(prompt, services):
        return b"SD-IMAGE", ".png", "image/png", sd_params

    tool._run_sd = fake_run_sd
    ctx = ToolContext(services={
        "upload_dir": tmp_path, "upload_url_base": "/uploads/u",
        "image_engine": "sd",
    })
    result = asyncio.run(tool.run({"prompt": "1girl"}, ctx))
    assert result.ok
    first_line = result.output.splitlines()[0]
    m = re.fullmatch(r"\[图片已生成] (/uploads/u/gen_.+\.png)", first_line)
    assert m, first_line
    saved = tmp_path / os.path.basename(m.group(1))
    assert saved.read_bytes() == b"SD-IMAGE"

    # [图片参数]：SD 方式的完整信息
    mm = re.search(r"^\[图片参数\] (\{.*\})$", result.output, re.M)
    assert mm, result.output
    info = json.loads(mm.group(1))
    assert info["engine"] == "sd"
    assert info["prompt"] == "masterpiece, 1girl, fox ears"
    assert info["negative_prompt"] == "lowres, bad anatomy"
    assert (info["width"], info["height"]) == (512, 768)
    assert info["sampler"] == "DPM++ 2M"
    assert info["seed"] == 12345


def test_tool_sd_failure_reports_error(tmp_path):
    tool = GenerateImageTool(ImageGenSettings(), _sd_settings(enabled=True))

    async def fake_run_sd(prompt, services):
        raise RuntimeError("connection refused")

    tool._run_sd = fake_run_sd
    ctx = ToolContext(services={
        "upload_dir": tmp_path, "upload_url_base": "/uploads/u",
        "image_engine": "sd",
    })
    result = asyncio.run(tool.run({"prompt": "1girl"}, ctx))
    assert not result.ok
    assert "SD 生图失败" in result.output
    assert "connection refused" in result.output


def test_tool_without_sd_engine_still_uses_ark(tmp_path):
    # 未传 image_engine：即使 SD 已配置也应走云端路径（无 image_models → 报错）
    tool = GenerateImageTool(ImageGenSettings(), _sd_settings(enabled=True))
    ctx = ToolContext(services={
        "upload_dir": tmp_path, "upload_url_base": "/uploads/u",
    })
    result = asyncio.run(tool.run({"prompt": "1girl"}, ctx))
    assert not result.ok
    assert "没有可用的生图模型" in result.output
