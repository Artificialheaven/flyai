"""ReadFileTool：只允许读取白名单目录内的文本文件。

安全要点：
- Path.resolve() 展开符号链接/junction，防止 symlink 逃逸。
- 与白名单根比较归属（同一目录下），越界拒绝。
- 大小上限截断。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from flyai.tools import Tool, ToolContext, ToolResult


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "读取服务器本地文本文件的内容。只能访问配置允许的目录。"
        "输入文件路径，返回文件内容（超大文件会被截断）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件的绝对路径或相对于允许目录的相对路径",
            },
            "encoding": {
                "type": "string",
                "description": "文本编码，默认 utf-8",
                "default": "utf-8",
            },
            "max_bytes": {
                "type": "integer",
                "description": "最多读取的字节数，默认 65536",
                "default": 65536,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        path_arg = str(arguments.get("path", "")).strip()
        encoding = str(arguments.get("encoding", "utf-8"))
        max_bytes = int(arguments.get("max_bytes", 65536))
        if not path_arg:
            return ToolResult(ok=False, output="[参数错误] path 不能为空")

        try:
            roots = [Path(p).resolve() for p in ctx.allowed_read_dirs]
        except Exception:
            roots = []
        raw_path = Path(path_arg).expanduser()
        if not raw_path.is_absolute() and roots:
            # 相对路径逐个转换白名单根解析，取存在的那个
            candidates = [root / raw_path for root in roots]
            exists = [c for c in candidates if c.exists()]
            raw_path = exists[0] if exists else candidates[0]
        target = raw_path.resolve()

        if not self._is_allowed(target, roots):
            return ToolResult(
                ok=False,
                output=(
                    f"[安全限制] 路径不在允许访问的目录内: {target}。"
                    f"允许的目录: {[str(r) for r in roots]}"
                ),
            )

        if not target.is_file():
            if target.exists():
                return ToolResult(ok=False, output=f"[参数错误] 不是文件（可能是目录）: {target}")
            return ToolResult(ok=False, output=f"[参数错误] 文件不存在: {target}")

        size = target.stat().st_size
        try:
            raw = target.read_bytes()
        except Exception as exc:
            return ToolResult(ok=False, output=f"[读取失败] {type(exc).__name__}: {exc}")

        truncated = size > max_bytes
        if truncated:
            raw = raw[:max_bytes]
            note = f"\n\n[文件过大已截断，总大小 {size} 字节，仅显示前 {max_bytes} 字节]"
        else:
            note = ""

        try:
            text = raw.decode(encoding, errors="replace")
        except LookupError:
            return ToolResult(ok=False, output=f"[参数错误] 未知编码: {encoding}")
        text += note
        return ToolResult(ok=True, output=text, meta={"path": str(target), "size": size})

    @staticmethod
    def _is_allowed(target: Path, roots: List[Path]) -> bool:
        # Path.is_relative_to 是 3.9 新增
        return any(target == root or target.is_relative_to(root) for root in roots)