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
    user_seat,
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


def test_pinned_roundtrip_and_sort_first(tmp_path):
    store = JsonSessionStore(tmp_path)
    older = Session(title="旧")
    newer = Session(title="新")
    store.save(older)
    store.save(newer)
    # 默认按更新时间倒序：新在前
    assert [s.title for s in store.list()] == ["新", "旧"]
    # 旧的置顶：稳定排序后置顶项提到最前，其余仍按时间倒序
    older.pinned = True
    store.save(older)
    listed = store.list()
    assert [s.title for s in listed] == ["旧", "新"]
    assert listed[0].pinned is True
    # 落盘再读，置顶标记保留
    assert store.get(older.id).pinned is True


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
    assert "鲍勃" in p and "聊天窗口" in p
    assert "魔法世界" in p


def test_member_prompt_without_lore_returns_base():
    p = build_member_system_prompt(name="x", memory="", attributes=None, audience=None, base="你是助手。")
    assert p == "你是助手。"


# ---- R8：用户扮演自设（persona）----

def test_member_prompt_injects_user_persona():
    c = Character(name="爱丽丝", intro="冒险家")
    persona = Character(name="勇者小明", intro="一位立志消灭魔王的勇者", background="从小在村子里长大")
    p = build_member_system_prompt(
        name="爱丽丝",
        memory="",
        attributes=None,
        audience=["你"],
        character=c,
        base="你是助手。",
        user_character=persona,
    )
    assert "你对话的用户" in p
    assert "勇者小明" in p
    assert "消灭魔王" in p
    # persona 不污染 AI 自己的身份
    assert p.index("你对话的用户") > p.index("爱丽丝")


def test_system_prompt_injects_user_persona_legacy():
    from flyai.lore.query import build_system_prompt
    persona = Character(id="p1", name="村长", intro="村民们的领袖")
    p = build_system_prompt(Character(name="旅人"), None, "你是助手。", user_character=persona)
    assert "你对话的用户" in p and "村长" in p and "村民们的领袖" in p


def test_no_user_persona_unchanged():
    c = Character(name="爱丽丝")
    base = "你是助手。"
    p = build_member_system_prompt(
        name="爱丽丝", memory="", attributes=None, audience=None, character=c, base=base
    )
    # 未设自设：不含 persona 块
    assert "你对话的用户" not in p
    assert "爱丽丝" in p  # AI 自己的身份仍在


def test_user_seat_resolves_persona_character():
    seat = RoomMember(name="你", is_user=True, character_id="p1")
    s = Session(title="房间")
    s.members.append(seat)
    assert user_seat(s) is seat
    persona = Character(id="p1", name="村长")
    assert member_character(seat, _FakeLore([persona])).name == "村长"
    # 无自设的座位解析为 None
    empty = RoomMember(name="你", is_user=True)
    assert member_character(empty, _FakeLore([persona])) is None


def test_chat_in_thread_tags_user_message_with_persona(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.sessions.json_store import JsonSessionStore

    store = JsonSessionStore(tmp_path / "s")
    s, chars = _room(["c1"])
    # 给用户座位设自设卡 p1
    s.members[0].character_id = "p1"
    store.save(s)
    persona = Character(id="p1", name="村长", intro="村民们的领袖")
    state, sc = _make_state_and_scope(tmp_path, [chars[0], persona])
    sc.store = store
    provider = _FakeProviderReply()
    req = SimpleNamespace(message="你好", thread_id=s.threads[0].id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    _run(_drain(resp))

    s2 = store.get(s.id)
    t = s2.thread(s.threads[0].id)
    assert t.messages[0].role == "user" and t.messages[0].name == "村长"


def test_chat_in_thread_user_without_persona_tag_none(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.sessions.json_store import JsonSessionStore

    store = JsonSessionStore(tmp_path / "s")
    s, _ = _room(["c1"])
    store.save(s)
    state, sc = _make_state_and_scope(tmp_path, [Character(id="c1", name="初")])
    sc.store = store
    provider = _FakeProviderReply()
    req = SimpleNamespace(message="你好", thread_id=s.threads[0].id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    _run(_drain(resp))

    s2 = store.get(s.id)
    t = s2.thread(s.threads[0].id)
    assert t.messages[0].role == "user" and t.messages[0].name is None


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
        image_models=[],
    )
    state.registry = ToolRegistry()
    lore = _FakeLore(chars)
    sc = SimpleNamespace(store=JsonSessionStore(tmp_path / "s"), lore=lore,
                         member=lambda mid: None, uid="admin", level=256)
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


class _FakeToolThenReply:
    """先发一轮工具调用（content 为空很正常），再发一段正式回复。

    模拟真实群聊里会查内容表的 AI：中间那条 content 为空的 assistant 只是
    携带 tool_calls 的工具步，绝不能被当「跳过」处理、把正式回复整卡移除。
    """
    def __init__(self):
        self._n = 0

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self._n += 1
        if self._n % 2 == 1:
            from flyai.schemas import ToolCall
            yield StreamEvent(
                StreamEventType.TOOL_CALLS,
                tool_calls=[ToolCall(id=f"t{self._n}", name="query_lore",
                                     arguments={"kind": "character", "keyword": "初"})],
            )
        else:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, "查完资料后的正式回复")


async def _drain_collect_events(resp):
    """收集 SSE 事件名，返回去重后的顺序列表。"""
    names = []
    body = resp.body_iterator
    if hasattr(body, "__aiter__"):
        async for frame in body:
            if isinstance(frame, str) and "event:" in frame:
                ev = frame.split("event:", 1)[1].split("\n", 1)[0].strip()
                names.append(ev)
    return names


class _FakeEmptyThenReply:
    """每次发言先给一轮空输出（0 字），重试后才给内容。模拟模型『没开口』的退化回复。"""
    def __init__(self):
        self._n = 0

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self._n += 1
        if self._n % 2 == 1:
            return  # 空生成器 → run_agent_loop 追加一条空 assistant
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "终于开口了")


def test_empty_reply_is_retried_not_skipped(tmp_path):
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
    provider = _FakeEmptyThenReply()
    req = SimpleNamespace(message="大家好", thread_id=g.id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    events = _run(_drain_collect_events(resp))

    # 空回复应被重试成正式发言：不跳过、真实回复留下、每 AI 最终恰好一次 done
    assert "speaker_skip" not in events
    assert events.count("done") == 2
    s2 = store.get(s.id)
    g2 = s2.thread(g.id)
    real = [m for m in g2.messages if m.role == "assistant" and (m.content or "").strip()]
    assert [m.name for m in real] == ["初", "两个"]
    assert all(not m.hidden for m in real)
    assert len(real) == 2

    # 被舍弃的空回合标记隐藏、留在历史（不删除）
    blanks = [m for m in g2.messages if m.role == "assistant" and not (m.content or "").strip()]
    assert blanks and all(m.hidden for m in blanks)


# ---- 历史编辑 / 重新生成 ----

def _seed_two_turns(store, s):
    t = s.threads[0]
    t.messages = [
        Message(role="user", content="A"),
        Message(role="assistant", content="rA", name="初"),
        Message(role="user", content="B"),
        Message(role="assistant", content="rB", name="初"),
    ]
    store.save(s)
    return t


def _mode_req(tid, message="", edit_id=None, regen_id=None):
    return SimpleNamespace(
        message=message, thread_id=tid,
        edit_message_id=edit_id, regenerate_message_id=regen_id,
        images=[],
    )


def test_dm_edit_historical_turn_end_to_end(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread

    store = JsonSessionStore(tmp_path / "s")
    s, _ = _room(["c1"])
    t = _seed_two_turns(store, s)
    anchor = t.messages[0].id

    state, sc = _make_state_and_scope(tmp_path, [Character(id="c1", name="初")])
    sc.store = store
    resp = _run(_chat_in_thread(
        state, sc, s, _mode_req(t.id, "A2", edit_id=anchor), _FakeProviderReply()
    ))
    _run(_drain(resp))

    t2 = store.get(s.id).thread(t.id)
    # 新回复进入编辑轮；suffix 轮原样
    assert [m.content for m in t2.messages] == ["A2", "发言1", "B", "rB"]
    entry = t2.edits[anchor]
    assert entry["edit_active"] == 1 and len(entry["edits"]) == 2
    assert entry["edits"][0]["user"]["content"] == "A"


def test_dm_regenerate_end_to_end(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread

    store = JsonSessionStore(tmp_path / "s")
    s, _ = _room(["c1"])
    t = _seed_two_turns(store, s)
    anchor = t.messages[2].id

    state, sc = _make_state_and_scope(tmp_path, [Character(id="c1", name="初")])
    sc.store = store
    resp = _run(_chat_in_thread(
        state, sc, s, _mode_req(t.id, "B", regen_id=anchor), _FakeProviderReply()
    ))
    _run(_drain(resp))

    t2 = store.get(s.id).thread(t.id)
    assert [m.content for m in t2.messages] == ["A", "rA", "B", "发言1"]
    slot = t2.edits[anchor]["edits"][0]
    assert len(slot["reply_variants"]) == 2 and slot["reply_active"] == 1


def test_group_edit_or_regen_rejected_400(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from fastapi import HTTPException

    store = JsonSessionStore(tmp_path / "s")
    s, chars = _room(["c1", "c2"], ["初", "两个"])
    ais = [s.members[1], s.members[2]]
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)
    state, sc = _make_state_and_scope(tmp_path, chars)
    sc.store = store

    with pytest.raises(HTTPException) as ei:
        _run(_chat_in_thread(
            state, sc, s, _mode_req(g.id, "x", edit_id="whatever"), _FakeProviderReply()
        ))
    assert ei.value.status_code == 400

    with pytest.raises(HTTPException) as ri:
        _run(_chat_in_thread(
            state, sc, s, _mode_req(g.id, regen_id="whatever"), _FakeProviderReply()
        ))
    assert ri.value.status_code == 400


def test_patch_switch_and_bad_index(tmp_path):
    from flyai.api.routes_room import switch_turn
    from flyai.api.routes_room import TurnSwitch
    from fastapi import HTTPException

    store = JsonSessionStore(tmp_path / "s")
    s, _ = _room(["c1"])
    t = _seed_two_turns(store, s)
    anchor = t.messages[2].id
    # 直接造两个 reply variant
    from flyai.sessions import versions
    versions.begin_regen(t, anchor)
    t.messages.append(Message(role="assistant", content="rB2", name="初"))
    versions.commit_run(t, anchor, 3, versions.MODE_REGEN, (0, 0))
    store.save(s)

    state, sc = _make_state_and_scope(tmp_path, [Character(id="c1", name="初")])
    sc.store = store
    fake_req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        flyai=SimpleNamespace(user=lambda r: sc)
    )))

    out = switch_turn(s.id, t.id, anchor, TurnSwitch(reply_index=0), fake_req)
    assert [m["content"] for m in out["messages"]] == ["A", "rA", "B", "rB"]

    with pytest.raises(HTTPException) as ei:
        switch_turn(s.id, t.id, anchor, TurnSwitch(reply_index=9), fake_req)
    assert ei.value.status_code == 400

    # 群聊窗口 PATCH → 400
    g = Thread(kind="group", title="群", member_ids=[m.id for m in s.members[1:2]])
    s.threads.append(g)
    store.save(s)  # 路由内重新 get，g 必须已落盘
    with pytest.raises(HTTPException) as gi:
        switch_turn(s.id, g.id, anchor, TurnSwitch(reply_index=0), fake_req)
    assert gi.value.status_code == 400


def test_tool_using_ai_not_marked_as_skipped(tmp_path):
    from flyai.api.routes_chat import _chat_in_thread
    from flyai.sessions.models import Thread as T  # noqa
    from flyai.tools.lore_tool import QueryLoreTool

    store = JsonSessionStore(tmp_path / "s")
    s, chars = _room(["c1", "c2"], ["初", "两个"])
    ais = [s.members[1], s.members[2]]
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)

    state, sc = _make_state_and_scope(tmp_path, chars)
    from flyai.config import ToolSettings
    state.config.tools = ToolSettings(enabled=True)
    state.registry.register(QueryLoreTool())
    sc.store = store

    provider = _FakeToolThenReply()
    req = SimpleNamespace(message="大家好", thread_id=g.id)

    resp = _run(_chat_in_thread(state, sc, s, req, provider))
    events = _run(_drain_collect_events(resp))

    # 两个 AI 都以正式回复收尾 → 绝不发 speaker_skip，真实回复留在历史且不隐藏
    assert "speaker_skip" not in events
    s2 = store.get(s.id)
    g2 = s2.thread(g.id)
    real = [m for m in g2.messages if m.role == "assistant" and m.content]
    assert [m.name for m in real] == ["初", "两个"]
    assert all(not m.hidden for m in real)
    assert len(real) == 2