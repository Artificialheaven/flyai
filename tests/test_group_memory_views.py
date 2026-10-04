"""群聊记忆隔离：每个发言者只能看到别人的正文、看不到别人的工具调用与记忆，
因此后发言的角色也会各自保存自己的记忆。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.loop import _actor_view
from flyai.agent.runs import ChatRunManager
from flyai.api.routes_chat import _chat_in_thread
from flyai.lore.models import Character
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.tools.memory_tool import UpdateChatMemoryTool


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


def _group_room(store, n=3):
    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    chars = []
    for i in range(n):
        cid = "c%d" % (i + 1)
        ch = Character(id=cid, name="角色%d" % (i + 1))
        chars.append(ch)
        m = RoomMember(name=ch.name, character_id=cid)
        s.members.append(m)
        s.threads.append(Thread(kind="dm", title=m.name, member_ids=[m.id]))
    ais = list(s.members[1:])
    g = Thread(kind="group", title="群", member_ids=[m.id for m in ais])
    s.threads.append(g)
    store.save(s)
    return s, chars, g, ais


def _req(thread):
    return SimpleNamespace(
        message="hi", thread_id=thread,
        target_member_ids=[], shuffle_speakers=False,
    )


async def _drain(resp):
    async for _frame in resp.body_iterator:
        pass


# ---- _actor_view 单元测试 ----

def test_actor_view_keeps_own_and_legacy_hides_others_tools():
    a = Message(role="assistant", content=None,
                tool_calls=[ToolCall(id="x", name="update_chat_memory")])
    a.actor_id = "p1"
    t = Message(role="tool", tool_call_id="x", name="update_chat_memory",
                content="[记忆已更新] 秘密-p1")
    t.actor_id = "p1"
    fin = Message(role="assistant", content="p1 的发言")
    fin.actor_id = "p1"
    legacy = Message(role="assistant", content="旧消息")  # 无 actor 标记

    view = _actor_view([a, t, fin, legacy], "p2")
    assert view == [fin, legacy]  # 工具回合/工具结果被整条拿掉
    assert view[0].tool_calls is None

    # 自己视角：全部保留（含工具链，多轮工具回放必须完整）
    own = _actor_view([a, t, fin, legacy], "p1")
    assert own == [a, t, fin, legacy]

    # 无 actor：不过滤
    assert _actor_view([a, t, fin], "") == [a, t, fin]


def test_actor_view_hides_other_skip_and_tool_only_turns():
    skip = Message(role="assistant", content="略")
    skip.hidden = True
    skip.actor_id = "p1"
    only_tool = Message(role="assistant", content=None,
                        tool_calls=[ToolCall(id="y", name="web_search")])
    only_tool.actor_id = "p1"
    spoken = Message(role="assistant", content="嗯我在听")
    spoken.actor_id = "p1"

    assert _actor_view([skip, only_tool, spoken], "p2") == [spoken]


# ---- 集成：三位发言者都保存各自记忆 ----

class _MemoryProvider:
    """首轮回：调 update_chat_memory 写下自己的私事；第二轮：正常发言。"""

    def __init__(self, tag):
        self.tag = tag
        self.calls = 0
        self.seen = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls += 1
        self.seen.append("\n".join(m.content or "" for m in messages))
        if self.calls == 1:
            yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=[
                ToolCall(id="call-" + self.tag, name="update_chat_memory",
                         arguments={"add": "秘密-" + self.tag})
            ])
        else:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, "发言-" + self.tag)


def test_every_speaker_saves_own_memory(tmp_path):
    async def body():
        from flyai.config import RoleplaySettings, ToolSettings
        from flyai.tools.registry import ToolRegistry

        store = JsonSessionStore(tmp_path / "s")
        s, chars, g, ais = _group_room(store)
        provs = [_MemoryProvider("p%d" % (i + 1)) for i in range(3)]
        for spk, p in zip(ais, provs):
            spk.provider_id = p.tag

        state = SimpleNamespace()
        state.config = SimpleNamespace(
            roleplay=RoleplaySettings(format_example=""),
            tools=ToolSettings(enabled=True, max_iterations=3),
            image_models=[],
            providers=[
                SimpleNamespace(id=p.tag, owner="admin",
                                shared=True, min_level=0)
                for p in provs
            ],
        )
        registry = ToolRegistry()
        registry.register(UpdateChatMemoryTool())
        state.registry = registry
        state.run_manager = ChatRunManager()
        by_tag = {p.tag: p for p in provs}
        state.get_provider = lambda pid: by_tag[pid]
        sc = SimpleNamespace(
            store=store, lore=_FakeLore(chars), uid="admin", level=256
        )

        resp = await _chat_in_thread(state, sc, s, _req(g.id))
        await _drain(resp)

        s2 = store.get(s.id)
        members = {m.character_id: m for m in s2.members if not m.is_user}
        # 三个角色各自写了自己的记忆
        for i, ch in enumerate(chars):
            mem = members[ch.id].memory or ""
            assert "秘密-p%d" % (i + 1) in mem
        # 群里留下三段发言
        g2 = s2.thread(g.id)
        finals = [m.content for m in g2.messages
                  if m.role == "assistant" and (m.content or "").strip()]
        assert finals == ["发言-p1", "发言-p2", "发言-p3"]

        # 每个角色的请求里只出现过自己的私事与自己的工具结果，
        # 看不到别人的记忆（这正是修复前「只有一个人写记忆」的根因）
        for i, p in enumerate(provs):
            blob = "\n".join(p.seen)
            assert "秘密-p%d" % (i + 1) in blob
            for j, other in enumerate(provs):
                if other is not p:
                    assert "秘密-p%d" % (j + 1) not in blob

    _run(body())
