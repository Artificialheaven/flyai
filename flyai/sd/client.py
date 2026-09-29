"""Stable Diffusion WebUI 的 Gradio API 客户端（Gradio 3.41，无需 --api）。

调用链（已实测）：
1. GET  /config          → 取 dependencies[fn_index].inputs 各组件默认值拼默认 data；
                           多选下拉空值必须给 []，否则 WebUI 端 TypeError。
2. POST /api/predict     → {"fn_index", "data", "session_hash"}，
                           返回 data[0] 为画廊（[{name: 服务器图片路径, is_file: true}]），
                           data[3] 为状态 HTML（出错时含 <div class='error'>）。
3. GET  /file=<路径>     → 下载结果图。
"""

from __future__ import annotations

import html
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

from ..config import StableDiffusionSettings

# Forge/重绘系 WebUI 文生图函数（fn_index 99）的固定槽位
_SLOT_NEGATIVE = 2
_SLOT_CFG = 6
_SLOT_HEIGHT = 7
_SLOT_WIDTH = 8
_SLOT_OVERRIDE = 21
_SLOT_STEPS = 23
_SLOT_SAMPLER = 24
_SLOT_SCHEDULE = 25
_SLOT_SEED = 29

_ERR_DIV_RE = re.compile(r"<div class='error'>(.*?)</div>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _error_text(status_html: str) -> str:
    """从状态 HTML 抽出错误信息并去标签/反转义。"""
    m = _ERR_DIV_RE.search(status_html or "")
    if not m:
        return ""
    text = _TAG_RE.sub("", m.group(1))
    return html.unescape(text).strip()


class SDGradioClient:
    def __init__(self, settings: StableDiffusionSettings, transport=None):
        # transport 仅供测试注入 httpx.MockTransport
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")
        self.session_hash = uuid.uuid4().hex
        self._transport = transport
        self._defaults: Optional[List[Any]] = None

    async def _load_defaults(self) -> None:
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            resp = await client.get(self.base_url + "/config")
            resp.raise_for_status()
            cfg = resp.json()
        dep = cfg["dependencies"][int(self.settings.fn_index)]
        comps = {c["id"]: c for c in cfg["components"]}

        defaults: List[Any] = []
        for cid in dep["inputs"]:
            c = comps[cid]
            props = c["props"]
            v = props.get("value")
            if c["type"] == "slider" and v is None:
                v = props.get("minimum", 0)
            # 多选下拉的空值 WebUI 端会被迭代，None → []
            if v is None and c["type"] == "dropdown" and props.get("multiselect"):
                v = []
            defaults.append(v)
        self._defaults = defaults

    async def txt2img(self, params: Dict[str, Any]) -> Tuple[bytes, str, str]:
        """提交文生图并下载第一张结果，返回 (图片字节, 扩展名, media_type)。"""
        if self._defaults is None:
            await self._load_defaults()

        data = list(self._defaults)
        data[1] = params["prompt"]
        data[_SLOT_NEGATIVE] = params["negative_prompt"]
        data[_SLOT_CFG] = float(params["cfg_scale"])
        data[_SLOT_HEIGHT] = int(params["height"])
        data[_SLOT_WIDTH] = int(params["width"])
        data[_SLOT_OVERRIDE] = []  # override settings：多选列表，恒空
        data[_SLOT_STEPS] = int(params["steps"])
        data[_SLOT_SAMPLER] = params["sampler"]
        data[_SLOT_SCHEDULE] = params["schedule"]
        data[_SLOT_SEED] = int(params["seed"])

        body = {
            "fn_index": int(self.settings.fn_index),
            "data": data,
            "session_hash": self.session_hash,
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(240.0), transport=self._transport
        ) as client:
            resp = await client.post(self.base_url + "/api/predict", json=body)
            resp.raise_for_status()
            rows = resp.json().get("data") or []

            gallery = rows[0] if rows else None
            if not gallery:
                err = _error_text(rows[3]) if len(rows) > 3 else ""
                raise RuntimeError("SD 未返回图片" + (": " + err if err else ""))

            content, media_type = await self._fetch_image(client, gallery[0])

        ext = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
        }.get(media_type, ".png")
        return content, ext, media_type

    async def _fetch_image(
        self, client: httpx.AsyncClient, item: Dict[str, Any]
    ) -> Tuple[bytes, str]:
        """画廊条目取图：优先内嵌 base64，否则按 name 走 /file= 下载。"""
        raw = item.get("data")
        if raw:
            if isinstance(raw, str) and raw.startswith("data:"):
                head, b64 = raw.split(",", 1)
                media_type = head.split(";", 1)[0].split(":", 1)[1]
                import base64

                return base64.b64decode(b64), media_type
            import base64

            return base64.b64decode(raw), "image/png"

        name = str(item.get("name") or "").split("?", 1)[0]  # 丢掉画廊加的缓存戳
        if not name:
            raise RuntimeError("画廊条目既无内嵌数据也无文件路径")
        resp = await client.get(self.base_url + "/file=" + quote(name))
        resp.raise_for_status()
        media_type = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
        return resp.content, media_type
