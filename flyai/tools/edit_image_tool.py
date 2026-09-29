"""EditImageTool：图生图 —— 以对话中已有图片为参考，调用 seedream 生成新图。

典型触发：
- 用户发来一张图并要求改风格/换装/局部修改/延展，或「照着这张画」；
- 用户要求以 AI 之前生成的某张图为基础再生成。

参考图来源（二选一）：
- 参数 image：显式给出 /uploads/<uid>/... 站内路径，或文件读取白名单内绝对路径
  （安全规则与 read_image 完全一致，本类直接继承其路径解析）；
- 省略 image：自动取**对话中最近的一张图片**——优先用户最新上传的图（历史里
  user 消息的 images，base64），否则取最近生成的图（tool 消息里的 [图片] 标记
  → 站内文件）。

结果落盘与输出格式与 generate_image 一致（[图片已生成] 标记 + meta 回贴）。
"""

from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional, Tuple

from ..config import ImageGenSettings
from .base import ToolContext, ToolResult
from .image_tool import (
    _ALLOWED_SIZES,
    ark_generate,
    save_generated_image,
    select_image_model,
)
from .read_image_tool import _IMAGE_TYPES, _MAX_BYTES, ReadImageTool


class EditImageTool(ReadImageTool):
    name = "edit_image"
    description = (
        "图生图：当需要以对话中已有图片（用户刚上传的、或你之前生成的）为参考"
        "生成新图——改风格、换装、换表情/动作、局部修改、画面延展、同人新画面"
        "等情况时调用。必须提供 prompt：详细描述最终画面，以及要对参考图做的"
        "具体改动。image 参数可省略：省略时自动使用对话中最近的一张图片"
        "（用户最新上传优先，否则最近生成的）；需要指定更早的图片时，"
        "把它的 /uploads/<uid>/... 路径显式传入 image。"
        "没有参考图、从零画图请用 generate_image。一次调用生成一张。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "完整的成图提示词，并写清对参考图的具体改动",
            },
            "image": {
                "type": "string",
                "description": (
                    "参考图路径：/uploads/<uid>/... 或白名单内绝对路径；"
                    "留空则自动用对话中最近的图片"
                ),
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

    def __init__(self, settings: ImageGenSettings):
        self.settings = settings

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = str(arguments.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, output="[参数错误] 请提供成图提示词（prompt）")

        services = ctx.services or {}
        if services.get("upload_dir") is None or not (services.get("upload_url_base") or ""):
            return ToolResult(
                ok=False, output="[工具错误] 当前对话没有可用的图片保存目录上下文"
            )

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

        size = str(arguments.get("size") or self.settings.default_size or "2K").strip()
        if size not in _ALLOWED_SIZES:
            size = "2K"

        # 取参考图字节并校验
        image_arg = str(arguments.get("image") or "").strip()
        try:
            ref_raw, ref_media = await self._load_reference(
                image_arg, ctx, services
            )
        except _NoReference as exc:
            return ToolResult(ok=False, output=str(exc))

        try:
            content, ext, media_type = await ark_generate(
                model_cfg,
                prompt,
                size,
                ref_media_type=ref_media,
                ref_b64=base64.b64encode(ref_raw).decode("ascii"),
            )
        except Exception as exc:
            return ToolResult(
                ok=False, output=f"[工具错误] 图生图失败: {type(exc).__name__}: {exc}"
            )
        image_meta = {
            "engine": "cloud",
            "mode": "img2img",
            "engine_label": "云端图生图 · " + model_cfg.model,
            "prompt": prompt,
            "model": model_cfg.model,
            "size": size,
        }
        saved = save_generated_image(
            services, content, ext, media_type, image_meta=image_meta
        )
        return ToolResult(ok=True, output=saved["output"], meta=saved["meta"])

    # ---- 参考图解析 ----

    async def _load_reference(
        self, image_arg: str, ctx: ToolContext, services: Dict[str, Any]
    ) -> Tuple[bytes, str]:
        if image_arg:
            target, _url = self._resolve(image_arg, ctx, services)
            if target is None or not target.is_file():
                raise _NoReference(
                    "[安全限制] 参考图路径不存在或不允许访问: " + image_arg
                )
            return self._read_and_check(target)

        # 自动模式：沿历史向前找最近的图片
        history: List = services.get("history") or []
        for m in reversed(history):
            if m.role == "user" and getattr(m, "images", None):
                img = m.images[0]
                raw = base64.b64decode(img.data)
                return self._check_bytes(raw, img.media_type)
            if m.role == "tool":
                marker = _extract_marker(getattr(m, "content", "") or "")
                if marker:
                    target, _url = self._resolve(marker, ctx, services)
                    if target is not None and target.is_file():
                        return self._read_and_check(target)
        raise _NoReference(
            "[工具错误] 对话中找不到可作为参考的图片：请让用户先上传图片，"
            "或在 image 参数中显式给出图片路径"
        )

    @staticmethod
    def _read_and_check(target) -> Tuple[bytes, str]:
        raw = target.read_bytes()
        ext = target.suffix.lower()
        media_type = _IMAGE_TYPES.get(ext, (None, None))[0]
        return EditImageTool._check_bytes(raw, media_type)

    @staticmethod
    def _check_bytes(raw: bytes, media_type: Optional[str]) -> Tuple[bytes, str]:
        if len(raw) == 0:
            raise _NoReference("[参数错误] 参考图内容为空")
        if len(raw) > _MAX_BYTES:
            raise _NoReference(
                f"[参数错误] 参考图过大（{len(raw)} 字节），上限 {_MAX_BYTES} 字节"
            )
        # 魔数识别真实类型，同时校验与声称类型一致（media_type 给空则按内容识别）
        for ext, (mt, magic) in _IMAGE_TYPES.items():
            if raw.startswith(magic) and (ext != ".webp" or raw[8:12] == b"WEBP"):
                if media_type and mt != media_type:
                    raise _NoReference("[参数错误] 参考图内容与图片类型不符")
                return raw, mt
        raise _NoReference(
            "[参数错误] 参考图不是支持的图片格式（jpg/jpeg/png/gif/webp）"
        )


class _NoReference(Exception):
    """参考图不可用：消息直接作为工具错误返回。"""


def _extract_marker(text: str) -> str:
    """从工具消息文本提取 [图片已生成]/[图片] 后的站内路径。"""
    for line in str(text).splitlines():
        line = line.strip()
        if line.startswith("[图片已生成]") or line.startswith("[图片]"):
            return line.split("]", 1)[1].strip()
    return ""
