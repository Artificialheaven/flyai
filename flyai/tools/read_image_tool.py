"""ReadImageTool：让 AI 读取并"看到"一张图片。

典型场景：AI 调用 generate_image 后拿到图片路径（前端不展示该文本），
需要确认画面实际效果或据此修改时，调用本工具，图片以图像内容回送视觉模型。

路径来源（各工具自主声明安全边界）：
- 生图结果返回的站内路径 `/uploads/<uid>/...`：限定在本用户上传目录内，
  Path.resolve() 展开后校验归属，`..` 越界一律拒绝；
- 白名单目录（设置中文件读取白名单）内的绝对/相对路径，规则同 read_file。

成功时 meta 携带图片 base64，由 agent loop 仅在视觉模型下回贴；
output 首行 `[图片] <url>` 让前端工具卡片只渲染图片、不显示其余文本。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import Tool, ToolContext, ToolResult

# 扩展名 -> (media_type, 文件头魔数)
_IMAGE_TYPES: Dict[str, Tuple[str, bytes]] = {
    ".jpg": ("image/jpeg", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", b"\xff\xd8\xff"),
    ".png": ("image/png", b"\x89PNG\r\n\x1a\n"),
    ".gif": ("image/gif", b"GIF8"),
    ".webp": ("image/webp", b"RIFF"),
}
_MAX_BYTES = 10 * 1024 * 1024


class ReadImageTool(Tool):
    name = "read_image"
    description = (
        "读取一张图片并直接查看其画面内容。当你需要确认某张图片的实际画面"
        "（例如刚用 generate_image 生成、需要检查或据此修改），或需要查看用户指明的"
        "图片文件时调用。path 传入：① 生图结果中给你的 /uploads/ 路径；"
        "② 文件读取白名单目录内图片的绝对路径。仅当前模型支持视觉时有效。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "图片路径：生图结果返回的 /uploads/<uid>/... 或白名单内绝对路径",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        path_arg = str(arguments.get("path", "")).strip()
        if not path_arg:
            return ToolResult(ok=False, output="[参数错误] path 不能为空")

        services = ctx.services or {}
        target, url = self._resolve(path_arg, ctx, services)
        if target is None:
            roots = self._read_roots(ctx)
            return ToolResult(
                ok=False,
                output=(
                    "[安全限制] 路径不存在或不在允许访问的目录内: " + path_arg +
                    "。可读路径：本用户的 /uploads/ 目录与白名单目录 "
                    + str([str(r) for r in roots])
                ),
            )

        if not target.is_file():
            return ToolResult(ok=False, output=f"[参数错误] 文件不存在: {target}")

        size = target.stat().st_size
        if size > _MAX_BYTES:
            return ToolResult(
                ok=False, output=f"[参数错误] 图片过大（{size} 字节），上限 {_MAX_BYTES} 字节"
            )

        ext = target.suffix.lower()
        spec = _IMAGE_TYPES.get(ext)
        if spec is None:
            return ToolResult(
                ok=False,
                output=f"[参数错误] 不支持的图片类型 {ext or '(无扩展名)'}，"
                "支持 jpg/jpeg/png/gif/webp",
            )
        media_type, magic = spec
        try:
            raw = target.read_bytes()
        except Exception as exc:
            return ToolResult(ok=False, output=f"[读取失败] {type(exc).__name__}: {exc}")

        if not raw.startswith(magic):
            # webp 还需校验 RIFF....WEBP
            if not (ext == ".webp" and raw[8:12] == b"WEBP"):
                return ToolResult(
                    ok=False, output="[参数错误] 文件内容与图片类型不符（可能不是真实图片）"
                )

        meta = {
            "image": {
                "media_type": media_type,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        }
        # 上传目录内的图可经 /uploads 展示给用户；白名单目录内的图无 Web 路径，
        # 不出标记行（图片本身仍经 meta 送达模型）
        if url:
            output = (
                f"[图片] {url}\n"
                f"图片已读取（{media_type}，{size} 字节，磁盘路径 {target}），"
                "画面随本结果一并提供。"
            )
        else:
            output = (
                f"图片已读取（{media_type}，{size} 字节，路径 {target}），"
                "画面随本结果一并提供。"
            )
        return ToolResult(ok=True, output=output, meta=meta)

    # ---- 路径解析 ----

    @staticmethod
    def _read_roots(ctx: ToolContext) -> List[Path]:
        try:
            return [Path(p).resolve() for p in ctx.allowed_read_dirs]
        except Exception:
            return []

    def _resolve(
        self, path_arg: str, ctx: ToolContext, services: Dict[str, Any]
    ) -> Tuple[Optional[Path], str]:
        """返回 (绝对路径, 可展示的 /uploads URL 或 "")。不可访问返回 (None, "")。"""
        # 1) /uploads/<uid>/...：仅本用户目录
        upload_dir = services.get("upload_dir")
        url_base = (services.get("upload_url_base") or "").rstrip("/")
        if upload_dir is not None and url_base and (
            path_arg == url_base or path_arg.startswith(url_base + "/")
        ):
            rel = path_arg[len(url_base):].lstrip("/")
            base = Path(upload_dir).resolve()
            target = (base / rel).resolve() if rel else base
            if target == base or target.is_relative_to(base):
                url = url_base if not rel else f"{url_base}/{rel}"
                return target, url
            return None, ""

        # 2) 白名单目录内绝对/相对路径（规则同 read_file）
        roots = self._read_roots(ctx)
        raw_path = Path(path_arg).expanduser()
        if not raw_path.is_absolute() and roots:
            candidates = [root / raw_path for root in roots]
            existing = [c for c in candidates if c.exists()]
            raw_path = existing[0] if existing else candidates[0]
        target = raw_path.resolve()
        if any(target == root or target.is_relative_to(root) for root in roots):
            return target, ""
        return None, ""
