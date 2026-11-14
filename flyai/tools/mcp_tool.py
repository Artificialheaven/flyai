"""MCP 桥接工具（P4）：把外部 MCP server 的工具映成 mcp__<server>__<tool>。

桥接工具不进全局注册表，由 McpManager 按请求生成 extras（技术书 §8.1）：
- 全部 risk=True，执行期授权走注册表统一门禁（min_level 来自 mcp 配置）；
- 自身不持连接，运行时从 ctx.services 取 McpManager；
- 图片结果既落盘（workspace/mcp/）又经 meta.images 回灌多模态附件。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import Tool, ToolContext, ToolResult

_B64_IMAGE_CAP = 20 * 1024 * 1024


class McpBridgeTool(Tool):
    risk = True

    def __init__(self, server_id: str, original_name: str,
                 bridge_name: str, spec: Dict[str, Any],
                 min_level: int = 0) -> None:
        self.server_id = server_id
        self.original_name = original_name
        self.name = bridge_name
        self.min_level = int(min_level or 0)
        desc = str(spec.get("description") or "").strip() \
            or "MCP 工具 %s" % original_name
        self.description = "[MCP:%s] %s" % (server_id, desc[:480])
        schema = spec.get("inputSchema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        self.parameters = schema

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        manager = ctx.services.get("mcp_manager")
        if manager is None or not getattr(
            getattr(manager, "settings", None), "enabled", False
        ):
            return ToolResult(
                ok=False,
                output="[MCP 未启用] 请管理员先在设置中开启 MCP 生态。",
            )
        try:
            result = await manager.call(
                ctx.uid, self.server_id, self.original_name, arguments or {}
            )
        except Exception as exc:  # 任何异常都不允许打断 agent loop
            return ToolResult(
                ok=False,
                output="[MCP 调用失败] %s: %s"
                       % (type(exc).__name__, exc),
            )
        if not result.get("ok"):
            return ToolResult(
                ok=False,
                output="[MCP 工具返回错误] %s"
                       % str(result.get("error") or "未知错误")[:1500],
            )

        meta: Dict[str, Any] = {}
        images = self._image_meta(result.get("content") or [])
        if images:
            meta["images"] = images
        media: List[Dict[str, Any]] = result.get("media") or []
        if media:
            meta["mcp_media"] = media

        parts: List[str] = []
        text = result.get("text") or ""
        if text:
            parts.append(text)
        for item in media:
            parts.append("（已保存%s：%s）"
                         % ("图片" if item.get("type") == "image" else "音频",
                            item.get("path", "")))
        output = "\n".join(parts) or "（MCP 工具执行完成，无文本返回）"
        return ToolResult(output=output, ok=True, meta=meta)

    @staticmethod
    def _image_meta(content: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image":
                continue
            data = block.get("data") or ""
            if not data or len(data) > _B64_IMAGE_CAP:
                continue
            out.append({
                "media_type": block.get("mimeType") or "image/png",
                "data": data,
            })
        return out
