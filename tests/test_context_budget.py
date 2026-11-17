"""上下文护栏：窗口字数预警只提示一次，超过硬上限时请求侧静默截断。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import _message_char_size, _truncate_context
from flyai.api.routes_chat import _emit_context_warning
from flyai.schemas import Message, ToolCall
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Session


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _FakeRun:
    def __init__(self):
        self.events = []

    async def emit(self, event, data):
        self.events.append((event, data))


def _turn(index, size=10):
    return [
        Message(role="user", content="u%d" % index + "x" * size),
        Message(role="assistant", content="a%d" % index + "y" * size),
    ]


# ---------- 请求侧截断 ----------

def test_under_limit_is_noop_and_returns_new_list():
    messages = _turn(0) + _turn(1)
    out = _truncate_context(messages, limit=1000)
    assert out == messages
    assert out is not messages


def test_truncates_to_recent_complete_turns():
    messages = _turn(0) + _turn(1)
    # 每轮 2 * 12 字，limit 30 只放得下最后一轮
    out = _truncate_context(messages, limit=30)
    assert out == messages[2:]
    assert out[0].role == "user"


def test_never_starts_in_middle_of_tool_round():
    messages = [
        Message(role="user", content="u" * 20),
        Message(role="assistant", content=None,
                tool_calls=[ToolCall(id="t1", name="f", arguments={"q": "x" * 20})]),
        Message(role="tool", content="r" * 20, tool_call_id="t1"),
        Message(role="assistant", content="done" * 5),
        Message(role="user", content="latest" * 5),
        Message(role="assistant", content="reply" * 5),
    ]
    out = _truncate_context(messages, limit=60)
    assert out[0].role == "user"
    assert out == messages[4:]


def test_keeps_latest_turn_even_if_it_alone_exceeds_limit():
    messages = [Message(role="user", content="u" * 100),
                Message(role="assistant", content="a" * 100)]
    assert _truncate_context(messages, limit=50) == messages


def test_no_user_message_keeps_everything():
    messages = [Message(role="assistant", content="a" * 100)]
    assert _truncate_context(messages, limit=50) == messages


def test_does_not_mutate_input():
    messages = _turn(0) + _turn(1)
    original = list(messages)
    _truncate_context(messages, limit=30)
    assert messages == original


def test_message_size_counts_tool_arguments():
    bare = Message(role="assistant", content="x" * 5)
    with_call = Message(
        role="assistant",
        content="x" * 5,
        tool_calls=[ToolCall(id="t", name="f", arguments={"q": "y" * 10})],
    )
    assert _message_char_size(with_call) > _message_char_size(bare)


# ---------- 阈值提示 ----------

def test_warns_once_per_threshold_and_persists():
    session = Session(id="s1")
    run = _FakeRun()
    history = [Message(role="user", content="x" * 210_000)]

    _run(_emit_context_warning(run, session, "t1", history))
    assert len(run.events) == 1
    event, data = run.events[0]
    assert event == "context_warning"
    assert data["threshold"] == 200_000
    assert data["chars"] == 210_000
    assert session.context_warned["t1"] == [200_000]

    # 同一窗口同一阈值不再重复提示
    _run(_emit_context_warning(run, session, "t1", history))
    assert len(run.events) == 1

    # 跨过下一个阈值时只补提示新的那个
    history = [Message(role="user", content="x" * 450_000)]
    _run(_emit_context_warning(run, session, "t1", history))
    assert len(run.events) == 2
    assert run.events[1][1]["threshold"] == 400_000
    assert session.context_warned["t1"] == [200_000, 400_000]


def test_warning_is_scoped_per_window():
    session = Session(id="s1")
    run = _FakeRun()
    history = [Message(role="user", content="x" * 210_000)]
    _run(_emit_context_warning(run, session, "t1", history))
    _run(_emit_context_warning(run, session, "t2", history))
    assert len(run.events) == 2
    assert session.context_warned["t1"] == [200_000]
    assert session.context_warned["t2"] == [200_000]


def test_jumping_thresholds_reports_highest():
    session = Session(id="s1")
    run = _FakeRun()
    history = [Message(role="user", content="x" * 650_000)]
    _run(_emit_context_warning(run, session, "t1", history))
    assert len(run.events) == 1
    data = run.events[0][1]
    assert data["threshold"] == 600_000
    assert data["thresholds"] == [200_000, 400_000, 600_000]


def test_session_json_roundtrip_keeps_context_warned(tmp_path):
    store = JsonSessionStore(tmp_path)
    session = Session(id="s1", context_warned={"t1": [200_000, 400_000]})
    store.save(session)
    loaded = store.get("s1")
    assert loaded.context_warned == {"t1": [200_000, 400_000]}
