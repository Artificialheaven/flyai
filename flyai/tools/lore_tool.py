"""query_lore 工具：角色/世界观内容表统一查询。

通过 ToolContext.services['lore']（LoreQuerier）访问当前会话绑定的活动角色/世界观。
检索顺序：语义检索（配了 embedder 时，含相似度阈值）→ 关键词检索（标题+正文打分）。
"""

from __future__ import annotations

from typing import Any, Dict

from ..lore.query import _KEYWORD_LIMIT_DEFAULT
from .base import Tool, ToolContext, ToolResult


class QueryLoreTool(Tool):
    name = "query_lore"
    description = (
        "查询角色或世界观的内容记录表，返回与主题最相关的记录内容。"
        "kind 传 'character' 查角色资料表，传 'world' 查世界设定表；"
        "keyword 填要查询的主题/关键词（为空则列出全部条目标题）。"
        "检索同时匹配标题与正文并按相关度返回（有数量上限）；标记为常驻的条目已直接注入，无需查询。"
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
                "description": "主题/关键词，匹配标题与正文；留空返回该表全部条目标题",
            },
        },
        "required": ["kind"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        kind = str(arguments.get("kind", "character"))
        keyword = str(arguments.get("keyword", "") or "").strip()

        services = ctx.services or {}
        querier = services.get("lore")
        if querier is None:
            return ToolResult(ok=False, output="[工具错误] 当前会话未绑定角色或世界观，无法查询")

        if kind not in querier.available_kinds():
            return ToolResult(
                ok=False,
                output=f"[查询失败] 当前没有可用的 {kind} 内容表。可用范围: {querier.available_kinds() or '无'}",
            )

        table = querier._table(kind)
        matches = None
        embedder = services.get("embedder")
        # 有关键词 + 配置了 embeddings → 语义检索；服务出错/全不达标静默回退关键词
        if keyword and table and embedder is not None:
            top_k = int(getattr(embedder.cfg, "top_k", 4) or 4)
            threshold = float(getattr(embedder.cfg, "score_threshold", 0.55))
            limit = int(getattr(embedder.cfg, "keyword_limit", _KEYWORD_LIMIT_DEFAULT))
            rag = await querier.semantic_search(kind, keyword, embedder, top_k, threshold)
            if rag:
                matches = [e for _s, e in rag]
            else:
                matches = querier.search(kind, keyword, limit)
        if matches is None:
            limit = _KEYWORD_LIMIT_DEFAULT
            if embedder is not None:
                limit = int(getattr(embedder.cfg, "keyword_limit", _KEYWORD_LIMIT_DEFAULT))
            matches = querier.search(kind, keyword, limit)

        if not matches:
            titles = "、".join(querier.list_titles(kind)) or "（空）"
            return ToolResult(
                ok=False,
                output=f"[未找到] 内容表中没有匹配「{keyword}」的条目。当前可用标题: {titles}",
            )
        if not keyword:
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
