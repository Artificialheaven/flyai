"""BM25 + CJK 分词 + hybrid_rank 基础行为测试（P1）。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.memory.bm25 import BM25, tokenize
from flyai.memory.recall import Candidate, hybrid_rank


def test_tokenize_latin_and_cjk():
    toks = tokenize("Hello World 你好")
    assert "hello" in toks and "world" in toks
    assert "你" in toks and "好" in toks
    assert "你好" in toks  # CJK bigram


def test_tokenize_empty():
    assert tokenize("") == []


def test_bm25_ranks_cjk_hit_above_miss():
    docs = [
        tokenize("我们明天一起去码头看船"),
        tokenize("今晚的星星很亮"),
        tokenize("一起在码头吃晚饭"),
    ]
    bm = BM25(docs)
    scores = bm.scores(tokenize("码头"))
    assert scores[0] > 0 and scores[2] > 0
    assert scores[1] == 0  # 无任何 token 命中
    assert scores[0] > scores[2] or scores[2] > scores[0]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _msg_cands(texts):
    return [
        Candidate(text=t, kind="message", position=i)
        for i, t in enumerate(texts)
    ]


def test_hybrid_rank_bm25_only():
    ranked = _run(hybrid_rank(
        _msg_cands(["我们约好明晚看星星", "群里聊到一起去码头"]),
        "星星",
    ))
    assert len(ranked) == 1
    assert ranked[0][0].text == "我们约好明晚看星星"


def test_hybrid_rank_embedder_failure_degrades():
    class _Boom:
        async def embed(self, texts):
            raise RuntimeError("嵌入服务崩溃")

    ranked = _run(hybrid_rank(
        _msg_cands(["一起去看星星", "无关的一句话"]),
        "星星",
        embedder=_Boom(),
    ))
    assert len(ranked) == 1
    assert "星星" in ranked[0][0].text


def test_hybrid_rank_no_hit_returns_empty():
    ranked = _run(hybrid_rank(
        _msg_cands(["基地护盾一直开着"]),
        "晚餐吃什么",
    ))
    assert ranked == []
