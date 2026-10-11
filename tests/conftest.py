"""pytest 全局夹具。

部分测试用 asyncio.run（结束后主线程不再有 current event loop），
另一些同步测试在构造 ChatRun 时会立即创建 asyncio.Condition（3.9 在
__init__ 里 get_event_loop）。跨文件收集时后者会因无事件循环报
RuntimeError。每个测试前保证存在一个未关闭的事件循环。
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("event loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    yield
