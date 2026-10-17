"""记忆存档：房间所有 AI 各自回顾并写进跨房间角色记忆簿；
自有簿默认可读可写、挂别人的簿默认不可见；存档与回答互斥。"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.agent.runs import ChatRunManager
from flyai.api.routes_chat import (
    MemorySnapshotRequest,
    memory_snapshot,
    _snapshot_transcript,
)
from flyai.lore.character_memory import CharacterMemoryStore
from flyai.lore.models import Character
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.tools.base import ToolContext
from flyai.tools.memory_tool import (
    QueryChatMemoryTool,
    UpdateChatMemoryTool,
)


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


class _FakeLore:
    def __init__(self, chars):
        self._chars = {c.id: c for c in chars}

    def get_character(self, cid):
        return self._chars.get(cid)


def _room_with_history(tmp):
    """你 + 3 AI；群窗口与角色1私聊里各留几句对话。返回全套对象。"""
    store = JsonSessionStore(tmp / "s")
    s = Session(title="战前准备")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(3):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
    ais = list(s.members[1:])
    for m in ais:
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    g.messages.append(Message(role="user", content="明天一早出发去古堡"))
    g.messages.append(Message(role="assistant", name=ais[0].name,
                              content="我负责带地图"))
    g.messages.append(Message(role="assistant", name=ais[1].name,
                              content="我备好治疗药剂"))
    s.threads[0].messages.append(
        Message(role="user", content="私下告诉你，我怕黑"))
    store.save(s)
    return store, s, chars, ais, g


class _SnapshotProvider:
    """首轮回调 update_chat_memory，第二轮给确认语。"""

    def __init__(self, tag):
        self.tag = tag
        self.calls = 0

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=[
                ToolCall(id="call-" + self.tag, name="update_chat_memory",
                         arguments={"add": "存档-" + self.tag})
            ])
        else:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, "记下了")


def _wire_state(tmp, store, chars, provs):
    from flyai.config import RoleplaySettings, ToolSettings
    from flyai.tools.registry import ToolRegistry

    books = CharacterMemoryStore(tmp / "mem")
    registry = ToolRegistry()
    registry.register(UpdateChatMemoryTool())
    state = SimpleNamespace(
        config=SimpleNamespace(
            active_provider="",
            roleplay=RoleplaySettings(format_example=""),
            tools=ToolSettings(enabled=True, max_iterations=3),
            image_models=[],
            providers=[SimpleNamespace(id=p.tag, owner="admin",
                                       shared=True, min_level=0)
                       for p in provs],
        ),
        registry=registry,
        run_manager=ChatRunManager(),
        get_provider=lambda pid: {p.tag: p for p in provs}[pid],
    )
    sc = SimpleNamespace(store=store, lore=_FakeLore(chars),
                         uid="admin", level=256, char_memory=books)
    state.user = lambda request: sc
    request = SimpleNamespace(app=SimpleNamespace(
        state=SimpleNamespace(flyai=state)))
    return state, sc, request, books


async def _drain(resp):
    async for _frame in resp.body_iterator:
        pass


# ---- 存档端点：每个 AI 的记忆簿都被写入 ----

def test_snapshot_writes_every_characters_book(tmp_path):
    async def body():
        store, s, chars, ais, g = _room_with_history(tmp_path)
        provs = [_SnapshotProvider("p%d" % (i + 1)) for i in range(3)]
        for spk, p in zip(ais, provs):
            spk.provider_id = p.tag
        store.save(s)  # 座位 provider 在存档后赋值，端点会重新从磁盘读取
        state, sc, request, books = _wire_state(tmp_path, store, chars, provs)

        resp = await memory_snapshot(s.id, request, MemorySnapshotRequest())
        await _drain(resp)

        for i, ch in enumerate(chars):
            entries = books.load(ch.id)
            assert entries and any(
                "存档-p%d" % (i + 1) in e for e in entries
            ), "角色 %d 的记忆簿未写入" % (i + 1)

    _run(body())


def test_snapshot_409_while_answer_running(tmp_path):
    async def body():
        store, s, chars, ais, g = _room_with_history(tmp_path)
        state, sc, request, books = _wire_state(
            tmp_path, store, chars,
            [_SnapshotProvider("p%d" % (i + 1)) for i in range(3)])

        # 手工占住会话锁，模拟回答进行中
        run = state.run_manager.create(sc.uid, s.id, g.id)
        assert state.run_manager.try_lock_for(sc.uid, s.id, run)
        with pytest.raises(HTTPException) as ei:
            await memory_snapshot(s.id, request, MemorySnapshotRequest())
        assert ei.value.status_code == 409

    _run(body())


def test_snapshot_room_not_found(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        books = CharacterMemoryStore(tmp_path / "mem")
        state = SimpleNamespace(run_manager=ChatRunManager())
        sc = SimpleNamespace(store=store, uid="admin", char_memory=books)
        state.user = lambda request: sc
        request = SimpleNamespace(app=SimpleNamespace(
            state=SimpleNamespace(flyai=state)))
        with pytest.raises(HTTPException) as ei:
            await memory_snapshot("nope", request, MemorySnapshotRequest())
        assert ei.value.status_code == 404

    _run(body())


# ---- 回顾文本：只含该 AI 参与的窗口、新内容在上限内 ----

def test_snapshot_transcript_only_own_threads(tmp_path):
    store, s, chars, ais, g = _room_with_history(tmp_path)

    txt = _snapshot_transcript(s, ais[0])
    assert "窗口" in txt
    assert "古堡" in txt and "地图" in txt      # 群窗口内容
    assert "怕黑" in txt                        # 自己私聊
    # 其他 AI 的私聊（空窗口不出现标题）
    labels = [line for line in txt.splitlines() if line.startswith("【窗口")]
    assert len(labels) == 2

    # 没有参与任何窗口的角色 → 空
    outsider = RoomMember(name="路人", character_id="xx")
    s.members.append(outsider)
    assert _snapshot_transcript(s, outsider) == ""


# ---- 未挂载时：自有簿默认可读可写；挂别人的簿默认不可见不可写 ----

def test_default_readwrite_own_character_book(tmp_path):
    async def body():
        store, s, chars, ais, g = _room_with_history(tmp_path)
        books = CharacterMemoryStore(tmp_path / "mem")
        books.append(chars[0].id, "用户怕黑")
        member = ais[0]
        assert member.memory_mode == ""  # 未显式挂载

        ctx = ToolContext(services={
            "store": store, "session": s, "member": member,
            "char_memory": books,
        })
        res = await QueryChatMemoryTool().run({"topic": "怕黑"}, ctx)
        assert res.ok and "怕黑" in res.output

        # 自有卡默认读写：update 同时写本会话与跨会话记忆簿
        before = len(books.load(chars[0].id))
        res2 = await UpdateChatMemoryTool().run({"add": "新小事"}, ctx)
        assert res2.ok
        assert len(books.load(chars[0].id)) == before + 1
        assert any("新小事" in line for line in books.load(chars[0].id))

    _run(body())


def test_default_not_read_other_card_book(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s = Session(title="x")
        s.members.append(RoomMember(name="你", is_user=True))
        # 副本卡座位：未显式挂载时，不读根卡的簿
        member = RoomMember(name="副本", character_id="variant")
        member.memory_char_id = "root"
        s.members.append(member)
        books = CharacterMemoryStore(tmp_path / "mem")
        books.append("root", "根卡的秘密")
        t = Thread(kind="dm", title="副本私聊", member_ids=[member.id])
        s.threads.append(t)
        t.messages.append(Message(role="user", content="你好"))

        ctx = ToolContext(services={
            "store": store, "session": s, "member": member,
            "char_memory": books,
        })
        res = await QueryChatMemoryTool().run({"topic": "秘密"}, ctx)
        assert "根卡的秘密" not in res.output

    _run(body())


def test_force_write_on_unmounted_seat(tmp_path):
    async def body():
        store = JsonSessionStore(tmp_path / "s")
        s = Session(title="x")
        s.members.append(RoomMember(name="你", is_user=True))
        member = RoomMember(name="角色", character_id="c1")
        s.members.append(member)
        books = CharacterMemoryStore(tmp_path / "mem")
        ctx = ToolContext(services={
            "store": store, "session": s, "member": member,
            "char_memory": books, "book_force_write": True,
        })
        res = await UpdateChatMemoryTool().run({"add": "强制存档内容"}, ctx)
        assert res.ok and "已同步" in res.output
        assert any("强制存档内容" in e for e in books.load("c1"))

    _run(body())
