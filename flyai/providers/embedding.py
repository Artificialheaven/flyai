"""远程 Embeddings 客户端（OpenAI 兼容 POST /embeddings）。

用于 RAG 语义检索：
  - 把消息/查询向量化
  - 支持向量缓存（进程内 + 可选磁盘），避免重复调用 API
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from ..config import EmbeddingsConfig
from ..errors import ProviderError

_MAX_INPUTS = 512            # 单次请求最多向量化的文本数
_CACHE_MAX = 20000           # 磁盘缓存最多保留的条目数


class EmbeddingClient:
    """绑定一个 OpenAI 兼容 embeddings 端点的客户端；会话级单例使用。"""

    def __init__(self, cfg: EmbeddingsConfig):
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=60.0)
        )
        # 进程内 memo：sha256(text) -> vector
        self._memo: Dict[str, List[float]] = {}
        self._dirty = False
        self._lock = asyncio.Lock()
        self._cache_path = self._resolve_cache_path()
        self._load_cache()

    def _resolve_cache_path(self) -> Optional[Path]:
        p = (self.cfg.cache_file or "").strip()
        if not p:
            return None
        path = Path(p)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._memo = {k: v for k, v in data.items()}
        except Exception:
            self._memo = {}

    def _flush_cache(self) -> None:
        if self._cache_path is None or not self._dirty:
            return
        if len(self._memo) > _CACHE_MAX:
            # 简单裁剪：保留最近加入的，丢最旧的
            self._memo = dict(list(self._memo.items())[-_CACHE_MAX:])
        tmp = self._cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._memo, f, ensure_ascii=False)
        os.replace(tmp, self._cache_path)
        self._dirty = False

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """把文本批量向量化，返回与输入等长的向量列表。已有缓存命中则跳过 API。"""
        if not texts:
            return []
        results: Dict[int, List[float]] = {}
        to_fetch: List[Tuple[int, str]] = []
        for idx, text in enumerate(texts):
            key = self._digest(text)
            cached = self._memo.get(key)
            if cached is not None:
                results[idx] = cached
            else:
                to_fetch.append((idx, text))

        # 分块调用，块间串行（共享同一 async client 与限流更稳）
        for start in range(0, len(to_fetch), _MAX_INPUTS):
            chunk = to_fetch[start:start + _MAX_INPUTS]
            vectors = await self._call_remote([t for _i, t in chunk])
            for (idx, text), vec in zip(chunk, vectors):
                results[idx] = vec
                self._memo[self._digest(text)] = vec
                self._dirty = True
                await asyncio.sleep(0)  # 让出事件循环，避免阻塞其它 SSE 流

        self._flush_cache()
        return [results[i] for i in range(len(texts))]

    async def _call_remote(self, texts: List[str]) -> List[List[float]]:
        self._assert_key_ascii()
        url = self.cfg.base_url.rstrip("/") + "/embeddings"
        body = {"model": self.cfg.model, "input": texts}
        headers = {}
        if (self.cfg.api_key or "").strip():
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        async with self._lock:
            try:
                resp = await self._client.post(url, json=body, headers=headers)
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                    raise ProviderError(
                        f"嵌入服务返回 HTTP {resp.status_code}", status_code=resp.status_code, detail=detail
                    )
                obj = resp.json()
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"嵌入请求失败: {type(exc).__name__}: {exc}") from exc

        data = obj.get("data") or []
        data.sort(key=lambda d: d.get("index", 0))
        return [d.get("embedding") for d in data]

    def _assert_key_ascii(self) -> None:
        if any(ord(c) > 127 for c in self.cfg.api_key):
            raise ProviderError("API Key 含非 ASCII 字符，请检查嵌入服务配置", status_code=400)

    async def aclose(self) -> None:
        self._flush_cache()
        await self._client.aclose()


def dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def normalize(vec: List[float]) -> List[float]:
    norm = dot(vec, vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]