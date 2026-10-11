"""AI 生图工具：调用火山方舟 Ark 图片生成接口（doubao-seedream）。

当用户要求画图/生成图片时，模型把用户的画面要求整理扩写为细致的提示词并调用本工具。
图片保存到该用户的上传目录（data/uploads/<uid>/），经 /uploads/<uid>/... 静态服务访问。

成功时 output 首行带标记 `[图片已生成] <url>`，前端据此在工具卡片中渲染图片；
历史消息里同样保留该标记，刷新后仍可展示。

本模块同时承载给文生图/图生图两个工具共用的底层函数：
- ark_generate：实际 HTTP 调用并下载结果图
- save_generated_image：落盘 + 构造输出文本与 meta
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx

from ..config import ImageGenSettings, ImageModelConfig, StableDiffusionSettings
from ..netguard import BlockedHost, pin_request, resolve_and_check
from .base import Tool, ToolContext, ToolResult

logger = logging.getLogger("flyai.image")

# 结果图下载：20MB 封顶（防 SSRF 拖取超大文件），手动跟随最多 5 跳重定向
_IMAGE_DOWNLOAD_MAX = 20 * 1024 * 1024
_IMAGE_MAX_REDIRECTS = 5
_IMAGE_REDIRECT_CODES = {301, 302, 303, 307, 308}

_ALLOWED_SIZES = ("1K", "2K", "4K")

_EXT_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class GenerateImageTool(Tool):
    name = "generate_image"
    description = (
        "当用户要求画图、画一张、生成图片时调用（没有参考图的纯文生图）。"
        "把用户的画面要求（主体、服饰、动作、表情、场景、画风、镜头、光线、色彩、"
        "氛围等）整理并扩写为一段细致的中文生图提示词；用户没有指定的细节由你合理补全。"
        "一次调用生成一张图。不要用本工具去识别或分析用户上传的图片；"
        "需要以已有图片为参考改图/图生图时改用 edit_image。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "整理扩写后的完整生图提示词，包含主体与各类画面细节",
            },
            "size": {
                "type": "string",
                "enum": list(_ALLOWED_SIZES),
                "description": "图片尺寸，默认 2K",
            },
        },
        "required": ["prompt"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        settings: ImageGenSettings,
        sd_settings: Optional[StableDiffusionSettings] = None,
    ):
        self.settings = settings
        self.sd_settings = sd_settings
        self._sd_client = None

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, output="[参数错误] 请提供生图提示词（prompt）")

        services = ctx.services or {}
        if services.get("upload_dir") is None or not (services.get("upload_url_base") or ""):
            return ToolResult(
                ok=False, output="[工具错误] 当前对话没有可用的图片保存目录上下文"
            )

        # 引擎选择：对话时由输入区「SD」开关决定；选中且 SD 已启用则走本地 SD
        engine = str(services.get("image_engine") or "").strip().lower()
        if engine == "sd" and self.sd_settings is not None and self.sd_settings.enabled:
            try:
                content, ext, media_type, params = await self._run_sd(prompt, services)
            except Exception as exc:
                logger.exception("生图失败（SD 路径）")
                return ToolResult(
                    ok=False,
                    output=f"[工具错误] SD 生图失败: {type(exc).__name__}: {exc}",
                )
            image_meta = {
                "engine": "sd",
                "mode": "txt2img",
                "engine_label": "Stable Diffusion · Anything V5",
                "prompt": params.get("prompt") or prompt,
                "negative_prompt": params.get("negative_prompt") or "",
                "width": params.get("width"),
                "height": params.get("height"),
                "steps": params.get("steps"),
                "cfg_scale": params.get("cfg_scale"),
                "sampler": params.get("sampler"),
                "schedule": params.get("schedule"),
                "seed": params.get("seed", -1),
            }
            saved = save_generated_image(
                services, content, ext, media_type, image_meta=image_meta
            )
            return ToolResult(ok=True, output=saved["output"], meta=saved["meta"])

        size = str(arguments.get("size") or self.settings.default_size or "2K").strip()
        if size not in _ALLOWED_SIZES:
            size = "2K"

        model_cfg = select_image_model(services)
        if model_cfg is None:
            return ToolResult(
                ok=False,
                output=(
                    "[工具错误] 当前没有可用的生图模型：请在设置中添加自己的生图模型，"
                    "或联系管理员共享"
                ),
            )
        if not (model_cfg.api_key or "").strip():
            return ToolResult(
                ok=False, output="[工具错误] 该生图模型未配置 API Key"
            )

        try:
            content, ext, media_type = await self._generate(model_cfg, prompt, size)
        except Exception as exc:
            # 完整堆栈进日志：只拼异常消息无法定位是哪一层（本项目/httpx/httpcore）抛的
            logger.exception("生图失败（云端路径）")
            return ToolResult(
                ok=False, output=f"[工具错误] 生图失败: {type(exc).__name__}: {exc}"
            )
        image_meta = {
            "engine": "cloud",
            "mode": "txt2img",
            "engine_label": "云端生图 · " + model_cfg.model,
            "prompt": prompt,
            "model": model_cfg.model,
            "size": size,
        }
        saved = save_generated_image(
            services, content, ext, media_type, image_meta=image_meta
        )
        return ToolResult(ok=True, output=saved["output"], meta=saved["meta"])

    async def _generate(self, model_cfg: ImageModelConfig, prompt: str, size: str):
        """薄包装：测试可在此处打桩替换网络调用。"""
        return await ark_generate(model_cfg, prompt, size)

    async def _run_sd(self, prompt: str, services: Dict[str, Any]):
        """本地 SD 路径：内置 AI 生成全套参数 → Gradio API 出图。

        客户端缓存在工具实例上（配置变更会整体重建工具）；测试可打桩本方法。
        """
        provider = services.get("llm_provider")
        if provider is None:
            raise RuntimeError("当前对话没有可用于生成 SD 参数的 AI 提供商")
        if self._sd_client is None:
            from ..sd.client import SDGradioClient

            self._sd_client = SDGradioClient(self.sd_settings)
        from ..sd.prompt import generate_sd_params

        params = await generate_sd_params(provider, prompt, self.sd_settings)
        content, ext, media_type = await self._sd_client.txt2img(params)
        return content, ext, media_type, params


# ---- 模型选择 ----

def select_image_model(services: Dict[str, Any]) -> Optional[ImageModelConfig]:
    """从当前用户可见的生图模型中选择：优先 image_model_id，否则第一条。"""
    models = services.get("image_models") or []
    wanted = (services.get("image_model_id") or "").strip()
    if wanted:
        for m in models:
            if m.id == wanted:
                return m
    return models[0] if models else None


# ---- 共用底层 ----

async def ark_generate(
    model_cfg: ImageModelConfig,
    prompt: str,
    size: str,
    ref_images: Optional[List[Tuple[str, str]]] = None,
    watermark: bool = True,
) -> Tuple[bytes, str, str]:
    """提交生图任务并下载图片。

    传 ref_images=[(media_type, base64), ...] 时为图生图（seedream 的 image 字段）。
    1 张时 image 发字符串，多张时发数组（数组顺序即接口侧「图1、图2…」）。
    参考图用 data URI（data:image/<格式>;base64,...，格式小写——seedream 5
    文档规定的形态）；若接口报 image 相关 400，再兼容旧形态裸 base64 重试一次，
    两次都失败时必须把两段错误都抛出，避免回退响应（invalid url specified）
    掩盖真实原因。返回 (图片字节, 扩展名, media_type)。
    """
    url = model_cfg.base_url.rstrip("/") + "/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {model_cfg.api_key}",
    }

    def payload(image_value):
        # image_value：str（单张）/ list[str]（多张）/ None（文生图）
        body = {
            "model": model_cfg.model,
            "prompt": prompt,
            "response_format": "url",
            "size": size,
            "stream": False,
            "watermark": watermark,
        }
        if image_value is not None:
            body["image"] = image_value
        return body

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        data_value = None
        raw_value = None
        if ref_images:
            data_uris = [
                f"data:{mt or 'image/jpeg'};base64,{b64}"
                for mt, b64 in ref_images
            ]
            raw = [b64 for _mt, b64 in ref_images]
            # 单张保持字符串（与单图文档一致），多张发数组
            data_value = data_uris[0] if len(data_uris) == 1 else data_uris
            raw_value = raw[0] if len(raw) == 1 else raw
        resp = await client.post(url, headers=headers, json=payload(data_value))
        first_text = ""
        if (
            ref_images
            and resp.status_code == 400
            and _error_mentions_image(resp.text)
        ):
            # 兼容旧形态：裸 base64。seedream 5 已将其按 URL 校验（invalid url specified），
            # 两次都失败时一起抛出，第一次的错误才是 data URI 被拒的真实原因
            first_text = resp.text
            resp = await client.post(url, headers=headers, json=payload(raw_value))
        if resp.status_code >= 400:
            if first_text:
                raise RuntimeError(
                    f"HTTP {resp.status_code} [裸base64重试]: {resp.text[:300]}\n"
                    f"[首次 data URI 请求的错误]: {first_text[:300]}"
                )
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        images = data.get("data") or []
        if not images or not images[0].get("url"):
            raise RuntimeError(f"返回中没有图片地址: {data}")

        # 图片地址由生图服务返回（外部可控输入）：SSRF 收口后再下载，
        # 不能裸 client.get——否则可被用来读取内网/本机任意地址
        img_content, img_resp = await _guarded_image_get(client, images[0]["url"])

    content_type = img_resp.headers.get("content-type", "").split(";")[0].strip()
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".jpg")
    return img_content, ext, content_type or "image/jpeg"


async def _guarded_image_get(
    client: httpx.AsyncClient, url: str
) -> Tuple[bytes, httpx.Response]:
    """下载生图服务给的图片地址：逐跳 DNS 校验 + 钉 IP，流式限 20MB。"""
    for _ in range(_IMAGE_MAX_REDIRECTS + 1):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise RuntimeError("图片地址非法（仅允许 http:// 或 https://）")
        try:
            ips = await resolve_and_check(parsed.hostname)
        except BlockedHost as exc:
            raise RuntimeError(str(exc))
        req = client.build_request("GET", url)
        pin_request(req, ips[0])
        resp = await client.send(req)
        if resp.status_code in _IMAGE_REDIRECT_CODES:
            location = resp.headers.get("location") or ""
            await resp.aclose()
            if not location:
                raise RuntimeError("图片下载：重定向缺少 Location")
            url = str(httpx.URL(url).join(location))
            continue
        if resp.status_code >= 400:
            await resp.aclose()
            raise RuntimeError(f"图片下载失败 HTTP {resp.status_code}")
        chunks: List[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > _IMAGE_DOWNLOAD_MAX:
                await resp.aclose()
                raise RuntimeError("图片过大（超过 20MB），已拒绝下载")
            chunks.append(chunk)
        return b"".join(chunks), resp
    raise RuntimeError("图片下载：重定向次数过多（超过 5 次）")


def _error_mentions_image(text: str) -> bool:
    low = (text or "").lower()
    return ("image" in low) or ("base64" in low) or ("图片" in text)


_MEDIA_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def save_reference_image(
    services: Dict[str, Any], raw: bytes, media_type: str
) -> str:
    """把一张参考图落进上传目录（ref_ 前缀），返回站内 URL。

    用户上传的图在历史里只有 base64、没有路径；不另存一份，看图器的
    提示词面板就无法回看「用了哪张参考图」。
    """
    upload_dir = Path(services["upload_dir"])
    upload_url_base = (services["upload_url_base"] or "").rstrip("/")
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ext = _MEDIA_EXT.get(media_type or "", ".jpg")
    filename = f"ref_{stamp}_{uuid.uuid4().hex[:8]}{ext}"
    (upload_dir / filename).write_bytes(raw)
    return f"{upload_url_base}/{filename}"


def save_generated_image(
    services: Dict[str, Any],
    content: bytes,
    ext: str,
    media_type: str,
    image_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把结果图落进上传目录，返回 {url, output, meta}。

    image_meta 非空时追加一行 ``[图片参数] <单行 JSON>``：随工具消息持久化，
    供前端看图器按生图方式展示提示词与参数；卡片渲染只取首行标记，不外显。
    """
    upload_dir = Path(services["upload_dir"])
    upload_url_base = (services["upload_url_base"] or "").rstrip("/")
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"gen_{stamp}_{uuid.uuid4().hex[:8]}{ext}"
    (upload_dir / filename).write_bytes(content)

    url = f"{upload_url_base}/{filename}"
    lines = [
        f"[图片已生成] {url}",
    ]
    if image_meta:
        lines.append(
            "[图片参数] "
            + json.dumps(image_meta, ensure_ascii=False, separators=(",", ":"))
        )
    lines.extend([
        "图片已成功生成。请用一句简短自然的话告知用户，并可简单描述画面内容；"
        "不要输出链接或文件路径。",
        f"图片路径为 {url}（已提供给你）；如需查看实际画面再检查或修改，"
        "可调用 read_image 并把该路径作为 path 传入。",
    ])
    output = "\n".join(lines)
    # 图片 base64 放入 meta：视觉模型下由 agent loop 回贴到工具消息，
    # 让 AI 在后续轮次直接"看到"自己刚生成的画面
    meta = {
        "image": {
            "media_type": media_type or "image/jpeg",
            "data": base64.b64encode(content).decode("ascii"),
        }
    }
    return {"url": url, "output": output, "meta": meta}
