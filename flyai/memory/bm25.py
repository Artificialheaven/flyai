"""CJK 感知分词 + BM25（无外部依赖）。

分词：拉丁文/数字按词；中日韩字符取 unigram + bigram（无空格中文也能命中），
不引入 jieba 等第三方分词库。
"""

from __future__ import annotations

import math
import re
from typing import List

_LATIN_RE = re.compile(r"[a-z0-9]+")


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 统一表意
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
        or 0x3040 <= code <= 0x30FF   # 平/片假名
        or 0xAC00 <= code <= 0xD7AF   # 韩文音节
        or 0xF900 <= code <= 0xFAFF   # 兼容表意
    )


def tokenize(text: str) -> List[str]:
    """文本 → token 列表（latin 词 + CJK unigram/bigram）。"""
    low = (text or "").lower()
    toks: List[str] = _LATIN_RE.findall(low)
    cjk = [ch for ch in low if _is_cjk(ch)]
    toks.extend(cjk)
    for i in range(len(cjk) - 1):
        toks.append(cjk[i] + cjk[i + 1])
    return toks


class BM25:
    """标准 BM25（k1=1.5, b=0.75），文档为预分 token 列表。"""

    def __init__(self, docs_tokens, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs = [list(d) for d in docs_tokens]
        self.n = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) if self.n else 0.0
        self.tf: List[dict] = []
        self.df: dict = {}
        for d in self.docs:
            freq: dict = {}
            for t in d:
                freq[t] = freq.get(t, 0) + 1
            self.tf.append(freq)
            for t in freq:
                self.df[t] = self.df.get(t, 0) + 1
        # BM25+ 风格 idf（非负）
        self.idf = {
            t: math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))
            for t, df in self.df.items()
        }

    def scores(self, query_tokens) -> List[float]:
        """返回查询对每篇文档的 BM25 打分。"""
        out: List[float] = []
        for i, freq in enumerate(self.tf):
            dl = len(self.docs[i])
            norm = (1 - self.b + self.b * dl / self.avgdl) if self.avgdl > 0 else 1.0
            score = 0.0
            for t in query_tokens:
                f = freq.get(t)
                if not f:
                    continue
                score += (
                    self.idf.get(t, 0.0)
                    * (f * (self.k1 + 1))
                    / (f + self.k1 * norm)
                )
            out.append(score)
        return out
