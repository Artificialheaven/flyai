"""会话全文搜索：标题 + 消息正文（摘要里没有 messages，不能只搜标题）。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_session import search_sessions
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore


def _env(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    sc = SimpleNamespace(store=store)
    state = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return store, request


def test_search_matches_message_body_and_name(tmp_path):
    store, request = _env(tmp_path)
    s = store.create("无关标题")
    s.messages.append(Message(role="assistant", content="今天去海边看烟火。"))
    s.messages.append(Message(role="user", name="阿虚", content="收到。"))
    store.save(s)

    out = search_sessions(request, q="烟火", limit=10)
    assert [r["id"] for r in out["results"]] == [s.id]
    assert out["results"][0]["match_count"] == 1
    assert "烟火" in out["results"][0]["snippet"]

    out = search_sessions(request, q="阿虚", limit=10)
    assert [r["id"] for r in out["results"]] == [s.id]


def test_search_matches_thread_messages(tmp_path):
    store, request = _env(tmp_path)
    s = store.create("群聊")
    from flyai.sessions.models import Thread

    thread = Thread(kind="group", title="主线", member_ids=[])
    thread.messages.append(Message(role="assistant", content="月光下的约定。"))
    s.threads.append(thread)
    store.save(s)

    out = search_sessions(request, q="月光", limit=10)
    assert [r["id"] for r in out["results"]] == [s.id]


def test_search_title_still_hits(tmp_path):
    store, request = _env(tmp_path)
    s = store.create("烟火大会")
    store.save(s)

    out = search_sessions(request, q="烟火", limit=10)
    assert [r["id"] for r in out["results"]] == [s.id]
    assert out["results"][0]["match_count"] == 1


def test_search_empty_query(tmp_path):
    _store, request = _env(tmp_path)
    assert search_sessions(request, q="   ", limit=10)["results"] == []


def test_search_cache_invalidated_after_save(tmp_path):
    store, request = _env(tmp_path)
    s = store.create("标题")
    s.messages.append(Message(role="assistant", content="第一次提到星尘。"))
    store.save(s)
    assert search_sessions(request, q="星尘", limit=10)["results"]

    s.messages[0].content = "内容已经改掉了。"
    store.save(s)
    assert search_sessions(request, q="星尘", limit=10)["results"] == []
    assert search_sessions(request, q="改掉", limit=10)["results"]
