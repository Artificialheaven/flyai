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
    fin = Message(role="assistant", content="p1 的发言", name="小红")
    fin.actor_id = "p1"
    legacy = Message(role="assistant", content="旧消息")  # 无 actor 标记、无 name

    view = _actor_view([a, t, fin, legacy], "p2")
    # 工具回合/工具结果被整条拿掉；别人的正文保留但加「名字：」前缀防串人设
    assert len(view) == 2
    assert view[0].tool_calls is None
    assert view[0].content == "小红：p1 的发言"
    assert view[0].name == "小红"
    assert view[1] is legacy  # 无归属旧消息原样保留

    # 自己视角：全部保留且不加前缀（含工具链，多轮工具回放必须完整）
    own = _actor_view([a, t, fin, legacy], "p1", "小红")
    assert own == [a, t, fin, legacy]
    assert own[2].content == "p1 的发言"

    # 无 actor：不过滤
    assert _actor_view([a, t, fin], "") == [a, t, fin]


def test_actor_view_legacy_messages_attributed_by_name():
    """actor_id 缺失的旧数据：按持久化 name 与当前座位名比对兜底归属。"""
    other = Message(role="assistant", content="小明的旧台词", name="小明")
    mine = Message(role="assistant", content="我自己的旧台词", name="小红")
    anon = Message(role="assistant", content="远古无主消息")

    view = _actor_view([other, mine, anon], "seat-hong", "小红")
    assert [m.content for m in view] == [
        "小明：小明的旧台词", "我自己的旧台词", "远古无主消息",
    ]
    # 认出是别人的消息时复制成干净消息（不带原 id 上的任何工具字段）
    assert view[0].name == "小明"


def test_actor_view_legacy_tool_turn_kept_intact():
    """无 actor_id 的旧工具回合不能拆：拆了会产生孤立 tool 消息触发提供商 400。"""
    a = Message(role="assistant", name="小明",
                tool_calls=[ToolCall(id="z", name="query_lore")])
    t = Message(role="tool", tool_call_id="z", name="query_lore",
                content="结果")
    view = _actor_view([a, t], "seat-hong", "小红")
    assert view == [a, t]


def test_actor_view_prefix_is_idempotent():
    m = Message(role="assistant", content="小红：我已经说过了", name="小红")
    m.actor_id = "p1"
    view = _actor_view([m], "p2", "我")
    assert view[0].content == "小红：我已经说过了"


def test_message_actor_id_roundtrip():
    m = Message(role="assistant", content="x", actor_id="seat-7")
    d = m.to_dict()
    assert d["actor_id"] == "seat-7"
    assert Message.from_dict(d).actor_id == "seat-7"
    # 旧数据没有该字段：from_dict 得到 None 而非报错
    legacy = {k: v for k, v in d.items() if k != "actor_id"}
    assert Message.from_dict(legacy).actor_id is None


def test_actor_view_hides_other_skip_and_tool_only_turns():
    skip = Message(role="assistant", content="略")
    skip.hidden = True
    skip.actor_id = "p1"
    only_tool = Message(role="assistant", content=None,
                        tool_calls=[ToolCall(id="y", name="web_search")])
    only_tool.actor_id = "p1"
    spoken = Message(role="assistant", content="嗯我在听", name="小明")
    spoken.actor_id = "p1"

    view = _actor_view([skip, only_tool, spoken], "p2")
    assert len(view) == 1
    assert view[0].content == "小明：嗯我在听"


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


# ---- 同卡多座位：窗口按座位精确匹配 ----

def test_candidate_sets_filter_by_member_id():
    from flyai.tools.memory_tool import QueryChatMemoryTool

    s = Session(title="房间")
    s.members.append(RoomMember(name="你", is_user=True))
    # 同一张卡 c1 挂两个座位（不同副本），各自有私聊窗口
    a1 = RoomMember(name="本体", character_id="c1")
    a2 = RoomMember(name="分身", character_id="c1")
    s.members.extend([a1, a2])
    dm1 = Thread(kind="dm", title="本体私聊", member_ids=[a1.id])
    dm1.messages.append(Message(role="assistant", content="本体的私事", name="本体"))
    dm2 = Thread(kind="dm", title="分身私聊", member_ids=[a2.id])
    dm2.messages.append(Message(role="assistant", content="分身的私事", name="分身"))
    g = Thread(kind="group", title="群", member_ids=[a1.id, a2.id])
    g.messages.append(Message(role="assistant", content="群里共同经历", name="本体"))
    s.threads.extend([dm1, dm2, g])

    # 旧行为（按 char_id）：两个座位的窗口互相可见
    by_char = {tid for tid, _l, _m in QueryChatMemoryTool._candidate_sets(s, "c1")}
    assert {dm1.id, dm2.id, g.id} <= by_char

    # 按座位：a1 只见自己的 dm + 共同群聊，不见 a2 的私聊
    by_seat = {tid for tid, _l, _m in
               QueryChatMemoryTool._candidate_sets(s, "c1", a1.id)}
    assert by_seat == {dm1.id, g.id}
    texts = " ".join(txt for _tid, _l, msgs in
                     QueryChatMemoryTool._candidate_sets(s, "c1", a1.id)
                     for _w, txt, _i in msgs)
    assert "分身的私事" not in texts


# ---- 旧会话加载时惰性补 actor_id ----

def test_legacy_session_get_migrates_actor_ids(tmp_path):
    import json

    store = JsonSessionStore(tmp_path / "s")
    s, chars, g, ais = _group_room(store, n=2)
    # 旧数据：所有消息都没有 actor_id，只靠 assistant.name 区分座位
    a1 = Message(role="assistant", name="角色1",
                 tool_calls=[ToolCall(id="old-call", name="query_lore")])
    t1 = Message(role="tool", tool_call_id="old-call",
                 name="query_lore", content="世界书结果")
    f1 = Message(role="assistant", name="角色1", content="我查过了")
    f2 = Message(role="assistant", name="角色2", content="我是第二个角色")
    u = Message(role="user", content="你们好")
    anon = Message(role="assistant", content="名字对不上座位的旧消息", name="路人甲")
    for m in (a1, t1, f1, f2, u, anon):
        g.messages.append(m)
    store.save(s)

    loaded = store.get(s.id)
    gm = loaded.thread(g.id).messages
    by = {(m.role, m.tool_call_id or "", m.content or ""): m.actor_id
          for m in gm}
    assert gm[0].actor_id == ais[0].id          # 工具调用按 name 归属
    assert gm[1].actor_id == ais[0].id          # tool 结果跟随 tool_call_id
    assert by[("assistant", "", "我查过了")] == ais[0].id
    assert by[("assistant", "", "我是第二个角色")] == ais[1].id
    assert by[("user", "", "你们好")] is None   # 用户消息不归属任何座位
    assert by[("assistant", "", "名字对不上座位的旧消息")] is None

    # 已落盘：直接读 JSON 也能看到归属（迁移只跑一次）
    raw = json.loads((tmp_path / "s" / (s.id + ".json")).read_text(
        encoding="utf-8"))
    group_raw = next(t for t in raw["threads"] if t["id"] == g.id)
    assert group_raw["messages"][0]["actor_id"] == ais[0].id
    assert group_raw["messages"][1]["actor_id"] == ais[0].id
    # 再加载一遍不报错、归属不变
    again = store.get(s.id).thread(g.id).messages
    assert again[0].actor_id == ais[0].id and again[1].actor_id == ais[0].id


# ---- heal 补造的 tool 消息继承座位归属 ----

def test_heal_dangling_tools_inherit_actor():
    from flyai.api.routes_chat import _heal_dangling_tool_calls

    a = Message(role="assistant",
                tool_calls=[ToolCall(id="q1", name="query_lore")])
    a.actor_id = "seat-9"
    out = _heal_dangling_tool_calls([a])
    assert len(out) == 2 and out[1].role == "tool"
    assert out[1].actor_id == "seat-9"
