"""会话工作区文件工具（P0）：fs_list/fs_read/fs_write/fs_append/fs_delete。

路径全部限定在该用户的沙箱双根内（会话根 + public 公共根）：
- 普通路径落在 data/{uid}/workspace/sessions/{sid}/，各会话互不可见；
- 以 public/ 开头的路径落在 data/{uid}/workspace/public/，跨会话共享。
写/追加/删除为 risk 工具，默认仅管理员可执行（注册表执行期授权）；
public 区域再收一层，沙箱仅对管理员开放写。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

from ..sandbox import Sandbox, SandboxError, build_user_sandbox
from .base import Tool, ToolContext, ToolResult


def _build(ctx: ToolContext) -> Sandbox:
    data_dir = ctx.services.get("data_dir")
    settings = ctx.services.get("sandbox_settings")
    if not data_dir or settings is None:
        raise SandboxError("沙箱服务未就绪（data_dir/sandbox_settings 缺失）")
    return build_user_sandbox(
        Path(data_dir),
        ctx.uid or "anon",
        ctx.session_id or "default",
        settings,
        public_writable=ctx.is_admin,
    )


def _fail(exc: Exception) -> ToolResult:
    return ToolResult(ok=False, output=f"[沙箱错误] {exc}")


class FsListTool(Tool):
    name = "fs_list"
    description = (
        "列出会话工作区目录。path 为相对路径，默认 '.'（当前会话根目录）；"
        "以 'public/' 开头可浏览跨会话公共素材库。禁止绝对路径与 '..'。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对目录路径，默认 '.'",
            }
        },
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel = str(arguments.get("path") or ".")
        try:
            sb = _build(ctx)
            items = await asyncio.to_thread(sb.list_dir, rel)
        except SandboxError as exc:
            return _fail(exc)
        if not items:
            return ToolResult(output=f"目录 {rel} 为空或不存在。")
        lines = [f"{it['type']:>4}  {it['size']:>10}  {it['name']}" for it in items]
        return ToolResult(output="\n".join(lines))


class FsReadTool(Tool):
    name = "fs_read"
    description = (
        "读取会话工作区中的文本文件并返回内容。path 为相对路径；"
        "以 'public/' 开头读公共素材库。二进制文件返回十六进制预览。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对文件路径"}
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel = str(arguments.get("path") or "").strip()
        if not rel:
            return ToolResult(ok=False, output="[参数错误] 请提供 path")
        try:
            sb = _build(ctx)
            text = await asyncio.to_thread(sb.read_file, rel)
        except SandboxError as exc:
            return _fail(exc)
        return ToolResult(output=text)


class FsWriteTool(Tool):
    name = "fs_write"
    description = (
        "向会话工作区写入文本文件（覆盖式）。path 为相对路径，会自动创建父目录；"
        "以 'public/' 开头写公共素材库（仅管理员）。受单文件大小与总配额限制。"
    )
    risk = True
    min_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对文件路径"},
            "content": {"type": "string", "description": "写入的文本内容"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel = str(arguments.get("path") or "").strip()
        if not rel:
            return ToolResult(ok=False, output="[参数错误] 请提供 path")
        content = str(arguments.get("content") or "")
        try:
            sb = _build(ctx)
            size = await asyncio.to_thread(sb.write_file, rel, content, False)
        except SandboxError as exc:
            return _fail(exc)
        return ToolResult(output=f"已写入 {rel}（{size} 字节）")


class FsAppendTool(Tool):
    name = "fs_append"
    description = (
        "向会话工作区的文本文件追加内容（不存在则创建）。path 为相对路径；"
        "以 'public/' 开头写公共素材库（仅管理员）。受配额限制。"
    )
    risk = True
    min_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对文件路径"},
            "content": {"type": "string", "description": "追加的文本内容"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel = str(arguments.get("path") or "").strip()
        if not rel:
            return ToolResult(ok=False, output="[参数错误] 请提供 path")
        content = str(arguments.get("content") or "")
        try:
            sb = _build(ctx)
            size = await asyncio.to_thread(sb.write_file, rel, content, True)
        except SandboxError as exc:
            return _fail(exc)
        return ToolResult(output=f"已追加到 {rel}（当前 {size} 字节）")


class FsDeleteTool(Tool):
    name = "fs_delete"
    description = (
        "删除会话工作区中的文件或目录（目录递归删除）。path 为相对路径；"
        "以 'public/' 开头删公共素材库（仅管理员）。禁止删除工作区根目录。"
    )
    risk = True
    min_level = 0
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对路径"}
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel = str(arguments.get("path") or "").strip()
        if not rel:
            return ToolResult(ok=False, output="[参数错误] 请提供 path")
        try:
            sb = _build(ctx)
            removed = await asyncio.to_thread(sb.delete, rel)
        except SandboxError as exc:
            return _fail(exc)
        if not removed:
            return ToolResult(output=f"{rel} 不存在，无需删除。")
        return ToolResult(output=f"已删除 {rel}")
