"""混合召回：向量余弦 + BM25 双路，RRF 融合，重要度/近因加权。

调用方负责用候选消息/条目构建 Candidate 列表（按时间顺序：旧 → 新）。
embedding 不可用或失败时自动退化为 BM25 单路。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from ..providers.embedding import dot, normalize
from .bm25 import BM25, tokenize

RRF_K = 60
RECENCY_HALF_LIFE = 50.0      # 按消息位置计的半衰期
# 权重：向量 / BM25 / 重要度 / 近因
DEFAULT_WEIGHTS = (0.45, 0.30, 0.15, 0.10)


@dataclass
class Candidate:
    """一个参与排序的记忆单元。"""

    text: str
    kind: str                       # book | room | message
    position: int                   # 全局顺序（旧=0 → 新=n-1）
    importance: float = 0.5
    label: str = ""                 # 窗口标题（message）
    who: str = ""                   # 发言者
    entry_id: str = ""              # book 条目 id
    identity: Any = None            # 向量缓存身份
    obj: Any = None                 # 关联对象（MemoryEntry）


def _ranks(scores: List[float]) -> List[Optional[int]]:
    """正分条目按降序排名（1 起）；非正分不排名。"""
    order = sorted(
        ((i, s) for i, s in enumerate(scores) if s > 0),
        key=lambda x: x[1], reverse=True,
    )
    ranks: List[Optional[int]] = [None] * len(scores)
    for r, (i, _s) in enumerate(order, start=1):
        ranks[i] = r
    return ranks


def _road_components(ranks: List[Optional[int]]) -> List[float]:
    return [1.0 / (RRF_K + r) if r is not None else 0.0 for r in ranks]


def _normalize(parts: List[float]) -> List[float]:
    m = max(parts) if parts else 0.0
    if m <= 0:
        return list(parts)
    return [p / m for p in parts]


async def hybrid_rank(
    candidates: List[Candidate],
    query: str,
    embedder: Any = None,
    vec_cache: Any = None,
    cache_key: Any = None,
    weights: Tuple[float, float, float, float] = DEFAULT_WEIGHTS,
    half_life: float = RECENCY_HALF_LIFE,
) -> List[Tuple[Candidate, float]]:
    """返回 (candidate, final_score) 按分数降序；零分条目不返回。"""
    n = len(candidates)
    if n == 0:
        return []
    q_tokens = tokenize(query)

    # ---- BM25 路（始终可用）----
    bm25 = BM25([tokenize(c.text) for c in candidates])
    bm25_parts = _normalize(_road_components(_ranks(bm25.scores(q_tokens))))

    # ---- 向量路（可选，失败即退化）----
    vec_parts: List[float] = [0.0] * n
    if embedder is not None:
        try:
            qv = (await embedder.embed([query]))[0]
            if vec_cache is not None and cache_key is not None:
                desired = [(c.identity, c.text) for c in candidates]
                vecs = await vec_cache.vectors_for(cache_key, desired, embedder)
            else:
                vecs = await embedder.embed([c.text for c in candidates])
            cos = [dot(normalize(qv), normalize(v)) for v in vecs]
            vec_parts = _normalize(_road_components(_ranks(cos)))
        except Exception:
            vec_parts = [0.0] * n

    w_vec, w_bm25, w_imp, w_rec = weights
    scored: List[Tuple[Candidate, float]] = []
    for i, c in enumerate(candidates):
        recency = math.exp(-(n - 1 - c.position) / max(1.0, half_life))
        score = (
            w_vec * vec_parts[i]
            + w_bm25 * bm25_parts[i]
            + w_imp * max(0.0, min(1.0, c.importance))
            + w_rec * recency
        )
        # 两路都没命中时只有「常备分量」（imp/recency），会让无关条目常驻——
        # 无任何命中证据的条目不输出
        hit = bm25_parts[i] > 0 or vec_parts[i] > 0
        if hit and score > 0:
            scored.append((c, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
