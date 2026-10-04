"""EditImageTool：图生图 —— 以对话中已有图片为参考，调用 seedream 生成新图。

典型触发：
- 用户发来一张图并要求改风格/换装/局部修改/延展，或「照着这张画」；
- 用户要求以 AI 之前生成的某张图为基础再生成；
- 用户给出多张图并要求组合：换装（图1人物 + 图2服装）、把人物放进某场景、
  融合两人长相、参考多角度姿态等。

参考图来源（二选一）：
- 参数 image：显式给出路径——单张传字符串，多张传字符串数组；路径为
  /uploads/<uid>/... 站内路径，或文件读取白名单内绝对路径（安全规则与
  read_image 完全一致，本类直接继承其路径解析）。多张时数组顺序即
  「图1、图2…」，必须与 prompt 中的编号严格一致。
- 省略 image：自动沿历史收集——用 image_count 指定张数（默认 1）：从最近的
  图片向前收集 image_count 张（用户上传的 base64 图与已生成的站内图都算），
  按时间先后编号图1…图N；历史不足该数量直接报错，不静默少图。

结果落盘与输出格式与 generate_image 一致（[图片已生成] 标记 + meta 回贴）。
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

from ..config import ImageGenSettings
from .base import ToolContext, ToolResult
from .image_tool import (
    _ALLOWED_SIZES,
    ark_generate,
    logger,
    save_generated_image,
    save_reference_image,
    select_image_model,
)
from .read_image_tool import _IMAGE_TYPES, _MAX_BYTES, ReadImageTool

# seedream 5：参考图最多 14 张（参考图 + 生成图 ≤ 15）
_MAX_REF_IMAGES = 14


class EditImageTool(ReadImageTool):
    name = "edit_image"
    description = (
        "图生图：以对话中已有图片为参考生成新图——改风格、换装、换表情/动作、"
        "局部修改、画面延展、同人新画面，以及多张参考图的组合任务（把图1人物的"
        "服装换成图2的服装、把图1的人物放进图2的场景、融合图1与图2的特征等）。"
        "必须提供 prompt：详细描述最终画面，并逐张写清每张参考图承担的角色。"
        "多张参考图时，prompt 中必须用「图1」「图2」指代（例：「将图1人物的服装"
        "换为图2的服装，保留图1人物的面孔、姿势与图2的画风」），图片顺序与编号"
        "严格一致；用户先后发了多张图时，先发的是图1。参考图最多 14 张，只传与"
        "任务有关的图。image 参数：单张传一个路径字符串，多张传路径数组；留空则"
        "按 image_count 自动从历史收集最近的图片（用户上传的与已生成的都算，"
        "默认 1 张）。没有参考图、从零画图请用 generate_image。一次调用生成一张。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "完整的成图提示词；多张参考图时用「图1」「图2」逐张说明用途",
            },
            "image": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
                "description": (
                    "参考图路径：/uploads/<uid>/... 或白名单内绝对路径。"
                    "单张传字符串，多张传数组（顺序=图1、图2…）；留空则按 "
                    "image_count 自动收集"
                ),
            },
            "image_count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 14,
                "description": (
                    "image 留空时生效：从历史最近向前收集的参考图数量（1~14），"
                    "按时间先后编号；历史不足会报错。默认 1"
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

        # 取参考图字节并校验（显式路径 或 按 image_count 从历史收集）
        image_spec = arguments.get("image")
        paths: List[str] = []
        if isinstance(image_spec, list):
            paths = [str(p).strip() for p in image_spec if str(p).strip()]
        elif image_spec:
            paths = [str(image_spec).strip()]
        try:
            count = int(arguments.get("image_count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, _MAX_REF_IMAGES))

        try:
            refs = await self._load_references(paths, count, ctx, services)
        except _NoReference as exc:
            return ToolResult(ok=False, output=str(exc))

        ref_payload = [
            (media, base64.b64encode(raw).decode("ascii"))
            for raw, media in refs
        ]
        try:
            content, ext, media_type = await ark_generate(
                model_cfg,
                prompt,
                size,
                ref_images=ref_payload,
            )
        except Exception as exc:
            logger.exception("图生图失败（云端路径）")
            return ToolResult(
                ok=False, output=f"[工具错误] 图生图失败: {type(exc).__name__}: {exc}"
            )
        # 生成成功后才另存参考图（用户上传图在历史里无路径），记录 URL 供看图器回看
        ref_urls = [
            save_reference_image(services, raw, media)
            for raw, media in refs
        ]
        image_meta = {
            "engine": "cloud",
            "mode": "img2img",
            "engine_label": "云端图生图 · " + model_cfg.model,
            "prompt": prompt,
            "model": model_cfg.model,
            "size": size,
            "ref_count": len(refs),
            "refs": ref_urls,
        }
        saved = save_generated_image(
            services, content, ext, media_type, image_meta=image_meta
        )
        return ToolResult(ok=True, output=saved["output"], meta=saved["meta"])

    # ---- 参考图解析 ----

    async def _load_references(
        self,
        paths: List[str],
        count: int,
        ctx: ToolContext,
        services: Dict[str, Any],
    ) -> List[Tuple[bytes, str]]:
        """显式 paths 优先；为空时自动从历史收集 count 张，按时间先后返回。"""
        if paths:
            if len(paths) > _MAX_REF_IMAGES:
                raise _NoReference(
                    f"[参数错误] 参考图最多 {_MAX_REF_IMAGES} 张，收到 {len(paths)} 张"
                )
            refs: List[Tuple[bytes, str]] = []
            for p in paths:
                target, _url = self._resolve(p, ctx, services)
                if target is None or not target.is_file():
                    raise _NoReference(
                        "[安全限制] 参考图路径不存在或不允许访问: " + p
                    )
                refs.append(self._read_and_check(target))
            return refs

        # 自动模式：沿历史向前收集 count 张（用户图 base64；已生成图走标记）
        collected: List[Tuple[bytes, str]] = []
        history: List = services.get("history") or []
        for m in reversed(history):
            if len(collected) >= count:
                break
            if m.role == "user" and getattr(m, "images", None):
                for img in m.images:
                    if len(collected) >= count:
                        break
                    raw = base64.b64decode(img.data)
                    collected.append(self._check_bytes(raw, img.media_type))
            elif m.role == "tool":
                marker = _extract_marker(getattr(m, "content", "") or "")
                if marker:
                    target, _url = self._resolve(marker, ctx, services)
                    if target is not None and target.is_file():
                        collected.append(self._read_and_check(target))
        if len(collected) < count:
            raise _NoReference(
                f"[工具错误] 需要 {count} 张参考图，对话历史里只有 "
                f"{len(collected)} 张：请让用户再上传，或在 image 参数中显式给出路径"
            )
        # 倒序收集后翻回时间先后（先发的是图1）
        collected.reverse()
        return collected

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
