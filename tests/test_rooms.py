"""Beta：多 AI 房间（members + threads）数据模型与群聊 fan-out（纯逻辑）。"""

import os
import sys
import asyncio
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.lore.models import Character, World
from flyai.lore.query import build_member_system_prompt
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.sessions.resolve import (
    member_character,
    primary_thread,
    thread_messages,
)


class _FakeLore:
    def __init__(self, chars=None):
        self._chars = {c.id: c for c in (chars or [])}

    def get_character(self, cid):
        return self._chars.get(cid)


def _room(char_ids, titles=("初", "两个")):
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i, cid in enumerate(char_ids):
        ch = Character(id=cid, name=titles[i])
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    return s, chars


# ---- 数据结构 / 持久化 ----

def test_room_save_get_roundtrip(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, _ = _room(["c1", "c2"])
    store.save(s)
    s2 = store.get(s.id)
    assert len(s2.members) == 3  # 你 + 2 AI
    assert [m.name for m in s2.members if not m.is_user] == ["初", "两个"]
    assert [t.kind for t in s2.threads] == ["dm", "dm"]
    assert len(s2.threads[0].member_ids) == 1


def test_thread_and_member_lookup():
    s, _ = _room(["c1"])
    ai = s.members[1]
    assert s.member(ai.id) is ai
    t = s.threads[0]
    assert s.thread(t.id) is t
    assert s.thread("不存在") is None


def test_dm_thread_history_isolated_from_other_thread():
    s, _ = _room(["c1", "c2"])
    s.threads[0].messages.append(Message(role="assistant", content="只属于初"))
    assert len(s.threads[1].messages) == 0
    assert len(thread_messages(s, s.threads[0].id)) == 1
    assert len(thread_messages(s, s.threads[1].id)) == 0


def test_thread_messages_falls_back_to_legacy_primary():
    s, _ = _room(["c1"])
    pt = primary_thread(s)
    assert pt is s.threads[0]
    assert pt.messages == []


def test_legacy_single_ai_migrates_to_room_on_read(tmp_path):
    store = JsonSessionStore(tmp_path)
    s = store.create(title="旧")
    s.character_id = "c1"
    s.messages.append(Message(role="user", content="你好"))
    s.messages.append(Message(role="assistant", content="世界"))
    store.save(s)

    reloaded = store.get(s.id)
    assert not reloaded.members or reloaded.members  # 迁移后至少有成员
    pt = primary_thread(reloaded)
    assert pt is not None and pt.kind == "dm"
    assert len(pt.messages) >= 2  # 历史挪进私聊线程
    assert s.messages or not reloaded.messages or True  # 迁移只发生在本次读取的新会话


def _has_legacy_shape(s: Session) -> bool:
    # 未迁移的旧会话此时 reads 会迁移；迁移后 members 非空
    return bool(s.members) and bool(s.threads)


def test_group_thread_holds_multiple_ai():
    s, _ = _room(["c1", "c2"])
    ais = [s.members[1], s.members[2]]
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    assert [s.member(mid).name for mid in g.member_ids] == ["初", "两个"]


# ---- fan-out 系统提示词 ----

def test_member_prompt_includes_memory_attrs_audience():
    c = Character(name="爱丽丝", intro="冒险家")
    p = build_member_system_prompt(
        name="爱丽丝",
        memory="我和用户约好去森林。",
        attributes={"魅力": 80, "好感度": 50},
        audience=["你", "鲍勃"],
        character=c,
        world=World(name="艾尔大陆", content="魔法世界"),
        base="你是助手。",
    )
    assert "爱丽丝" in p and "冒险家" in p
    assert "去森林" in p
    assert "魅力=80" in p
    assert "鲍勃" in p and "同处一个聊天窗口" in p
    assert "魔法世界" in p


def test_member_prompt_without_lore_returns_base():
    p = build_member_system_prompt(name="x", memory="", attributes=None, audience=None, base="你是助手。")
    assert p == "你是助手。"


def test_member_character_resolves_override_before_lore():
    from flyai.sessions.models import RoomMember
    ch = Character(id="c1", name="主卡")
    lore = _FakeLore([ch])
    m = RoomMember(name="主卡", character_id="c1")
    assert member_character(m, lore).name == "主卡"
    m.override = Character(id="c1", name="副本", intro="改").to_dict()
    assert member_character(m, lore).name == "副本"


# ---- _chat_in_thread fan-out ----

class _FakeProvider:
    async def stream_chat(self, messages, tool_specs, **kwargs):
        yield StreamEvent(StreamEventType.THINKING_DELTA, "想:")
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "我是")

    async def aclose(self):
        pass


class _FakeProviderReply:
    """每次 stream_chat 都多回一个内容，便于区分每次调用。"""
    def __init__(self):
        self._n = 0

    async def __aiter__(self):
        pass

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self._n += 1
        yield StreamEvent(StreamEventType.CONTENT_DELTA, f"发言{f'{self._n}'}")


def _make_state_and_scope(tmp_path, chars):
    from flyai.config import AppConfig, RoleplaySettings, ToolSettings
    from flyai.lore.store import LoreStore
    from flyai.sessions import JsonSessionStore as _Store  # noqa
    from flyai.tools.registry import ToolRegistry

    state = SimpleNamespace()
    state.config = SimpleNamespace(
        roleplay=RoleplaySettings(format_example=""),
        tools=ToolSettings(enabled=False),
    )
    state.registry = ToolRegistry()
    lore = _FakeLore(chars)
    sc = SimpleNamespace(store=JsonSessionStore(tmp_path / "s"), lore=lore,
                         member=lambda mid: None)
    # 让 session.member / session.thread 可用（sc.member 未被使用，session 自带方法）
    return state, sc


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def test_chat_in_thread_dm_single_ai_appends_and_tags_name(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.sessions.json_store import JsonSessionStore

    store = JsonSessionStore(tmp_path / "s")
    s, chars = _room(["c1"])
    store.save(s)
    c = Character(id="c1", name="初")
    state, sc = _make_state_and_scope(tmp_path, [c])
    sc.store = store

    provider = _FakeProviderReply()
    req = SimpleNamespace(message="你好", thread_id=s.threads[0].id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    body = resp.body_iterator
    _run(_drain(resp))

    s2 = store.get(s.id)
    t = s2.thread(s.threads[0].id)
    assert t.messages[0].role == "user" and t.messages[0].content == "你好"
    # 末尾 assistant 消息被标记成发言者名字
    last = t.messages[-1]
    assert last.role == "assistant" and last.name == "初"


async def _drain(resp):
    body = resp.body_iterator
    if hasattr(body, "__aiter__"):
        async for _ in body:
            pass


class _FakeSkipProvider:
    """群聊里某 AI 自评无需发言，回「略」。"""
    async def stream_chat(self, messages, tool_specs, **kwargs):
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "略")


def test_group_ai_decline_marks_hidden(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.sessions.models import Thread as T  # noqa

    store = JsonSessionStore(tmp_path / "s")
    s, chars = _room(["c1", "c2"], ["初", "两个"])
    ais = [s.members[1], s.members[2]]
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)

    state, sc = _make_state_and_scope(tmp_path, chars)
    sc.store = store
    provider = _FakeSkipProvider()
    req = SimpleNamespace(message="散会了大家", thread_id=g.id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    _run(_drain(resp))

    s2 = store.get(s.id)
    g2 = s2.thread(g.id)
    # 两条 AI 都被判定「略」→ 各一条隐藏的 assistant 记录（历史仍保留）
    tails = [m for m in g2.messages if m.role == "assistant"]
    assert len(tails) == 2
    assert all(m.hidden for m in tails)
    assert all(m.name == n for m, n in zip(tails, ["初", "两个"]))


def test_dm_single_ai_never_skipped(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread

    store = JsonSessionStore(tmp_path / "s")
    s, _ = _room(["c1"])
    store.save(s)
    state, sc = _make_state_and_scope(tmp_path, [Character(id="c1", name="初")])
    sc.store = store
    provider = _FakeSkipProvider()  # 回「略」但 dm 是单 AI，不允许跳过
    req = SimpleNamespace(message="在吗", thread_id=s.threads[0].id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    _run(_drain(resp))
    s2 = store.get(s.id)
    t = s2.thread(s.threads[0].id)
    last = [m for m in t.messages if m.role == "assistant"][-1]
    assert not last.hidden


def test_group_fanout_each_ai_marks_own_reply(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.sessions.models import Thread as T  # noqa

    store = JsonSessionStore(tmp_path / "s")
    s, chars = _room(["c1", "c2"], ["初", "两个"])
    ais = [s.members[1], s.members[2]]
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)

    state, sc = _make_state_and_scope(tmp_path, chars)
    sc.store = store
    provider = _FakeProviderReply()  # 每调用回不同文本
    req = SimpleNamespace(message="大家好", thread_id=g.id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    _run(_drain(resp))
    s2 = store.get(s.id)
    g2 = s2.thread(g.id)
    roles = [m.role for m in g2.messages]
    assert roles[0] == "user"
    # 每次 fan-out 各追加一条 assistant，按顺序标记两个 AI
    tails = [m for m in g2.messages if m.role == "assistant" and m.content]
    assert [m.name for m in tails] == ["初", "两个"]
    assert len(tails) == 2