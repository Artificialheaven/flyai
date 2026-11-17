"""会话列表摘要：token_usage 不丢、message_count 按窗口正确统计。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_session import list_sessions
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread


def test_list_keeps_token_usage(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="用量会话")
    s.add_usage({"prompt_tokens": 100, "completion_tokens": 50,
                 "cached_tokens": 30})
    store.save(s)

    listed = store.list()
    assert len(listed) == 1
    assert listed[0].token_usage["prompt_tokens"] == 100
    assert listed[0].token_usage["completion_tokens"] == 50
    assert listed[0].token_usage["cached_tokens"] == 30
    assert listed[0].token_usage["total_tokens"] == 150


def test_room_message_count_sums_all_threads(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = Session(title="房间")
    me = RoomMember(name="你", is_user=True)
    ai = RoomMember(name="AI1", character_id="c1")
    s.members.extend([me, ai])
    dm = Thread(kind="dm", title="AI1", member_ids=[ai.id])
    dm.messages.extend([
        Message(role="user", content="hi"),
        Message(role="assistant", content="好"),
    ])
    group = Thread(kind="group", title="群", member_ids=[me.id, ai.id])
    group.messages.append(Message(role="assistant", content="在"))
    s.threads.extend([dm, group])
    store.save(s)

    listed = store.list()
    assert listed[0].message_count == 3


def test_legacy_top_level_messages_counted(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = Session(title="旧会话")
    s.messages.extend([
        Message(role="user", content="a"),
        Message(role="assistant", content="b"),
        Message(role="user", content="c"),
    ])
    store.save(s)

    assert store.list()[0].message_count == 3


def test_list_sessions_payload_has_message_count(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="房间")
    me = RoomMember(name="你", is_user=True)
    ai = RoomMember(name="AI1", character_id="c1")
    s.members.extend([me, ai])
    t = Thread(kind="dm", title="AI1", member_ids=[ai.id])
    t.messages.append(Message(role="user", content="hi"))
    s.threads.append(t)
    s.add_usage({"prompt_tokens": 7, "completion_tokens": 3})
    store.save(s)

    sc = SimpleNamespace(store=store)
    state = SimpleNamespace(user=lambda request: sc)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    payload = list_sessions(request)
    item = payload["sessions"][0]
    assert item["message_count"] == 1
    assert item["token_usage"]["prompt_tokens"] == 7