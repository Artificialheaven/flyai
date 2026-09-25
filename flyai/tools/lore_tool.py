"""query_lore 工具：角色/世界观内容表统一查询。

通过 ToolContext.services['lore']（LoreQuerier）访问当前会话绑定的活动角色/世界观。
"""

from __future__ import annotations

from typing import Any, Dict

from .base import Tool, ToolContext, ToolResult


class QueryLoreTool(Tool):
    name = "query_lore"
    description = (
        "查询角色或世界观的内容记录表，返回匹配标题的记录内容。"
        "kind 传 'character' 查角色资料表，传 'world' 查世界设定表；"
        "keyword 填要查询的主题/关键词（为空则列出全部条目标题）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["character", "world"],
                "description": "查询范围：character=角色内容表，world=世界观内容表",
            },
            "keyword": {
                "type": "string",
                "description": "主题/关键词，按标题模糊匹配；留空返回该表全部条目标题",
            },
        },
        "required": ["kind"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        kind = str(arguments.get("kind", "character"))
        keyword = str(arguments.get("keyword", "") or "")

        querier = (ctx.services or {}).get("lore")
        if querier is None:
            return ToolResult(ok=False, output="[工具错误] 当前会话未绑定角色或世界观，无法查询")

        if kind not in querier.available_kinds():
            return ToolResult(
                ok=False,
                output=f"[查询失败] 当前没有可用的 {kind} 内容表。可用范围: {querier.available_kinds() or '无'}",
            )

        matches = querier.search(kind, keyword)
        if not matches:
            titles = "、".join(querier.list_titles(kind)) or "（空）"
            return ToolResult(
                ok=False,
                output=f"[未找到] 内容表中没有匹配「{keyword}」的条目。当前可用标题: {titles}",
            )
        if not keyword:
            # 列出全部标题
            titles = "、".join(e.title for e in matches) or "（空）"
            return ToolResult(ok=True, output=f"[内容表] 当前有 {len(matches)} 条记录:\n{titles}")

        blocks = []
        for e in matches:
            title = e.title or "(无标题)"
            content = e.content or "(空内容)"
            blocks.append(f"『{title}』\n{content}")
        return ToolResult(
            ok=True,
            output="\n\n".join(blocks),
            meta={"kind": kind, "keyword": keyword, "count": len(matches)},
        )