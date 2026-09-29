"""query_lore 工具：角色/世界观内容表统一查询。

通过 ToolContext.services['lore']（LoreQuerier）访问当前会话绑定的活动角色/世界观。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..providers.embedding import dot, normalize
from .base import Tool, ToolContext, ToolResult

_RAG_MAX_EMBED = 300   # 语义检索最多向量化的条目数
_EMBED_TEXT = 1000     # 每条目用于向量化的文本截断长度


class QueryLoreTool(Tool):
    name = "query_lore"
    description = (
        "查询角色或世界观的内容记录表，返回与主题最相关的记录内容。"
        "kind 传 'character' 查角色资料表，传 'world' 查世界设定表；"
        "keyword 填要查询的主题/关键词（为空则列出全部条目标题）。"
        "配置了语义检索时会按内容含义匹配，而不只是标题。"
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
        # 有关键词 + 配置了 embeddings → 语义检索；服务出错静默回退标题关键词
        if keyword and table and services.get("embedder") is not None:
            self._rag_entries = None
            if await self._rag_search(services.get("embedder"), table, keyword):
                matches = self._rag_entries
        if matches is None:
            # 关键词检索（标题模糊）或没配 RAG 时的原逻辑
            matches = querier.search(kind, keyword)

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

    async def _rag_search(self, embedder, table, keyword: str):
        """对内容表条目做语义检索，按相关度取 top-k。返回 True（成功并存到 self._rag_entries）／None（失败回退）。"""
        entries = table[:_RAG_MAX_EMBED]
        if not entries:
            return None
        texts = [((e.title or "") + "\n" + (e.content or ""))[:_EMBED_TEXT] for e in entries]
        try:
            qv = (await embedder.embed([keyword]))[0]
            vecs = await embedder.embed(texts)
        except Exception:
            return None
        qn = normalize(qv)
        scored = []
        for e, txt, v in zip(entries, texts, vecs):
            scored.append((dot(qn, normalize(v)), e, txt))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = int(getattr(embedder.cfg, "top_k", 4) or 4)
        self._rag_entries = [e for _s, e, _t in scored[:top_k]]
        return True