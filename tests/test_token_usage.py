"""会话级累计 token：模型累加、JSON 持久化、agent loop 用量上报。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import run_agent_loop
from flyai.config import ToolSettings
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Session
from flyai.tools.registry import ToolRegistry
from flyai.api.routes_session import _session_dict


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# ---- Session.add_usage ----

def test_add_usage_accumulates_across_turns():
    s = Session()
    s.add_usage({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    s.add_usage({"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280})
    assert s.token_usage == {"prompt_tokens": 300, "completion_tokens": 130,
                             "total_tokens": 430, "cached_tokens": 0}


def test_add_usage_accumulates_cached_tokens():
    s = Session()
    s.add_usage({"prompt_tokens": 100, "completion_tokens": 10,
                 "cached_tokens": 60})
    s.add_usage({"prompt_tokens": 50, "cached_tokens": 20})
    assert s.token_usage["cached_tokens"] == 80
    assert s.token_usage["prompt_tokens"] == 150


def test_add_usage_derives_total_when_missing():
    s = Session()
    s.add_usage({"prompt_tokens": 12, "completion_tokens": 8})  # 无 total_tokens
    assert s.token_usage["total_tokens"] == 20


def test_add_usage_ignores_none_and_empty():
    s = Session()
    s.add_usage(None)
    s.add_usage({})
    s.add_usage({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    assert s.token_usage == {}


# ---- JSON 持久化 ----

def test_json_store_roundtrips_token_usage(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="用量会话")
    s.add_usage({"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500})
    store.save(s)

    loaded = store.get(s.id)
    assert loaded.token_usage == {"prompt_tokens": 1000, "completion_tokens": 500,
                                  "total_tokens": 1500, "cached_tokens": 0}


def test_old_session_without_token_usage_loads_empty(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create()
    assert store.get(s.id).token_usage == {}


# ---- API 序列化 ----

def test_session_dict_contains_token_usage():
    s = Session()
    s.add_usage({"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
    d = _session_dict(s)
    assert d["token_usage"] == {"prompt_tokens": 5, "completion_tokens": 2,
                                "total_tokens": 7, "cached_tokens": 0}


# ---- agent loop done 带 usage ----

class _FakeProvider:
    def __init__(self, events):
        self._events = events

    async def stream_chat(self, messages, tool_specs, **kwargs):
        for ev in self._events:
            yield ev


def test_agent_loop_done_payload_carries_usage():
    provider = _FakeProvider([
        StreamEvent(
            StreamEventType.USAGE,
            usage={"prompt_tokens": 30, "completion_tokens": 10},
        ),
        StreamEvent(StreamEventType.CONTENT_DELTA, "你好"),
    ])
    history = [Message(role="user", content="hi")]
    settings = ToolSettings(enabled=False)

    async def collect():
        out = []
        async for ev, data in run_agent_loop(
            history, provider, ToolRegistry(), settings, system_prompt="sys"
        ):
            out.append((ev, data))
        return out

    events = _run(collect())
    done = [d for ev, d in events if ev == "done"][-1]
    assert done["usage"] == {"prompt_tokens": 30, "completion_tokens": 10,
                             "total_tokens": 40, "cached_tokens": 0}
