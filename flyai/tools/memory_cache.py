"""记忆消息向量的增量缓存（batch 3）。

背景：memory_tool._rag_search 原本每次查询把窗口消息重新 embed 全量向量。
嵌入提供商自身有文本 memo（不产生重复 API 费用），但仍有全量列表重建与
normalize 的 CPU 开销。这里按 (会话, 角色) 缓存消息向量，只 embed 新增消息。

item 身份：(thread_id, 消息在窗口内的下标, sha1(文本)[:12])。
早先消息被编辑/重生成替换时，共同前缀从该点截断、其后重建。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Dict, List, Tuple


def item_identity(thread_id: str, index: int, text: str) -> Tuple[str, int, str]:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return (thread_id, index, h)


class MemoryVectorCache:
    def __init__(self) -> None:
        # key=(sid, char_id) -> {"keys": [ident], "texts": [...], "vecs": [...]}
        self._store: Dict[Any, Dict[str, Any]] = {}
        self._locks: Dict[Any, asyncio.Lock] = {}

    def _lock(self, key) -> asyncio.Lock:
        lk = self._locks.get(key)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[key] = lk
        return lk

    async def vectors_for(self, key, desired: List[Tuple[Tuple, str]], embedder):
        """desired: [(ident, text)]（调用方已做最新 N 条截断）；返回对齐的向量。"""
        async with self._lock(key):
            cached = self._store.get(key) or {}
            ckeys = cached.get("keys", [])
            ctexts = cached.get("texts", [])
            cvecs = cached.get("vecs", [])

            # 共同前缀：身份与文本都一致
            n = 0
            lim = min(len(ckeys), len(desired))
            while n < lim and ckeys[n] == desired[n][0] and ctexts[n] == desired[n][1]:
                n += 1

            if n < len(ckeys):
                # 早先消息有变动：前缀之后的旧缓存全部作废
                keep_keys, keep_texts, keep_vecs = ckeys[:n], ctexts[:n], cvecs[:n]
            else:
                keep_keys, keep_texts, keep_vecs = ckeys, ctexts, cvecs

            new_items = desired[n:]
            new_texts = [t for _ident, t in new_items]
            new_vecs = await embedder.embed(new_texts) if new_texts else []

            keys = list(keep_keys) + [ident for ident, _t in new_items]
            texts = list(keep_texts) + new_texts
            vecs = list(keep_vecs) + list(new_vecs)
            self._store[key] = {"keys": keys, "texts": texts, "vecs": vecs}
            return vecs
