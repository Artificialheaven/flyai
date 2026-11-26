"""出站 HTTP 的瞬时网络错误重试（DNS 解析失败 / 连接失败 / 连接超时）。

只重试「请求还没拿到响应头」阶段的错误——此时请求尚未被上游处理（或至少
没有产生可观测输出），重发是安全的。流式响应一旦开始读取就不再重试，避免
正文被重复输出；非幂等的 POST 也只会在连接建立失败时重发。

触发条件覆盖 POC / 弱网下常见的 ``Temporary failure in name resolution``
（httpx 会把 getaddrinfo 的 EAI_AGAIN 包成 ``ConnectError``）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)

# 连接尚未建立/响应头未到达时的瞬时错误；不含 ReadTimeout（请求可能已被处理）
RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# 首次 + 2 次重试
ATTEMPTS = 3
# 指数退避基数（秒）：0.4 / 0.8
BASE_DELAY = 0.4


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, RETRYABLE)


async def _backoff(attempt: int, base_delay: float) -> None:
    """第 attempt 次失败（0 起）后的等待：指数退避 + 少量抖动。"""
    delay = base_delay * (2 ** attempt)
    if delay > 0:
        await asyncio.sleep(delay + delay * 0.1 * (attempt % 2))


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = ATTEMPTS,
    base_delay: float = BASE_DELAY,
    **kwargs: Any,
) -> httpx.Response:
    """``client.request`` 的瞬时网络错误重试版本（非流式）。"""
    for attempt in range(attempts):
        try:
            return await client.request(method, url, **kwargs)
        except RETRYABLE as exc:
            if attempt + 1 >= attempts:
                raise
            logger.warning(
                "出站请求 %s %s 失败（%s: %s），重试 %d/%d",
                method, url, type(exc).__name__, exc, attempt + 1, attempts - 1,
            )
            await _backoff(attempt, base_delay)
    raise AssertionError("unreachable")


async def send_with_retry(
    client: httpx.AsyncClient,
    make_request: Callable[[], httpx.Request],
    *,
    attempts: int = ATTEMPTS,
    base_delay: float = BASE_DELAY,
) -> httpx.Response:
    """``client.send`` 的重试版本；每次重试都重新构建请求（便于重新钉 IP）。"""
    for attempt in range(attempts):
        try:
            return await client.send(make_request())
        except RETRYABLE as exc:
            if attempt + 1 >= attempts:
                raise
            logger.warning(
                "出站请求失败（%s: %s），重试 %d/%d",
                type(exc).__name__, exc, attempt + 1, attempts - 1,
            )
            await _backoff(attempt, base_delay)
    raise AssertionError("unreachable")


@asynccontextmanager
async def stream_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = ATTEMPTS,
    base_delay: float = BASE_DELAY,
    **kwargs: Any,
):
    """``client.stream`` 的重试版本：仅重试到响应头返回为止。

    yield 之后（响应体读取阶段）的中断不会再重试，异常原样抛出。
    """
    cm = None  # type: Optional[Any]
    resp = None  # type: Optional[httpx.Response]
    for attempt in range(attempts):
        cm = client.stream(method, url, **kwargs)
        try:
            resp = await cm.__aenter__()
            break
        except RETRYABLE as exc:
            cm = None
            if attempt + 1 >= attempts:
                raise
            logger.warning(
                "流式请求 %s %s 失败（%s: %s），重试 %d/%d",
                method, url, type(exc).__name__, exc, attempt + 1, attempts - 1,
            )
            await _backoff(attempt, base_delay)
    try:
        yield resp
    finally:
        if cm is not None:
            await cm.__aexit__(None, None, None)
