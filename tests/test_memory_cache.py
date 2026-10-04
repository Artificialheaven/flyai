"""MemoryVectorCache：增量向量缓存（只 embed 新增；前缀变动截尾重建）。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.tools.memory_cache import MemoryVectorCache, item_identity


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _FakeEmbedder:
    def __init__(self):
        self.batches = []  # 每次 embed 的文本批次

    async def embed(self, texts):
        self.batches.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


def _items(tid, texts):
    return [(item_identity(tid, i, t), t) for i, t in enumerate(texts)]


def test_incremental_only_embeds_new_items():
    async def body():
        cache = MemoryVectorCache()
        emb = _FakeEmbedder()
        key = ("s1", "c1", "t1")

        vecs = await cache.vectors_for(key, _items("t1", ["甲", "乙"]), emb)
        assert len(vecs) == 2
        assert emb.batches == [["甲", "乙"]]

        # 追加一条：只 embed 新文本，旧向量复用
        vecs = await cache.vectors_for(key, _items("t1", ["甲", "乙", "丙"]), emb)
        assert len(vecs) == 3
        assert emb.batches[-1] == ["丙"]
        assert vecs[0] == [1.0, 1.0] and vecs[2] == [1.0, 1.0]  # 旧向量原样复用

    _run(body())


def test_edit_middle_discards_suffix_and_rebuilds():
    async def body():
        cache = MemoryVectorCache()
        emb = _FakeEmbedder()
        key = ("s1", "c1", "t1")

        await cache.vectors_for(key, _items("t1", ["甲", "乙", "丙"]), emb)
        # 第二条被编辑（身份变化）：共同前缀只有第一条，其余全部重建
        vecs = await cache.vectors_for(
            key, _items("t1", ["甲", "乙改", "丙"]), emb
        )
        assert emb.batches[-1] == ["乙改", "丙"]
        assert len(vecs) == 3

    _run(body())


def test_distinct_keys_have_independent_caches():
    async def body():
        cache = MemoryVectorCache()
        emb = _FakeEmbedder()
        await cache.vectors_for(("s1", "c1", "t1"), _items("t1", ["甲"]), emb)
        await cache.vectors_for(("s2", "c1", "t1"), _items("t1", ["甲"]), emb)
        assert emb.batches == [["甲"], ["甲"]]  # 不同 key 不共享

    _run(body())
