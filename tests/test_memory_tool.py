"""记忆工具测试：query_chat_memory（只查当前会话内包含自己的窗口）+ update_chat_memory（追加并持久化）。"""

import asyncio
import os
import sys
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.tools.base import ToolContext
from flyai.tools.memory_tool import QueryChatMemoryTool, UpdateChatMemoryTool


def _fsession(store, sid, charids):
    """建一个包含【你 + 若干 AI 角色】的会话；返回会话与该会话的 AI 成员列表。"""
    s = store.create(title=sid)
    s.members.append(RoomMember(name="你", is_user=True))
    ais = []
    for cid in charids:
        m = RoomMember(name="角色" + cid, character_id=cid)
        s.members.append(m)
        ais.append(m)
    return s, ais


def _thread(s, ais, kind, title):
    t = Thread(kind=kind, title=title, member_ids=[a.id for a in ais])
    s.threads.append(t)
    return t


def test_query_memory_only_looks_inside_current_session(tmp_path):
    asyncio.run(_r_1(tmp_path))

async def _r_1(tmp_path):
    store = JsonSessionStore(tmp_path)

    # 当前会话：TA 有私聊窗口 + TA/TB 群聊窗口 + TB 专属私聊窗口
    s, [_ta, _tb] = _fsession(store, "S", ["TA", "TB"])
    ta, tb = _ta, _tb
    dm_ta = _thread(s, [ta], "dm", "TA私聊")
    dm_ta.messages.append(Message(role="assistant", name="TA", content="我们约好明晚看星星"))
    dm_tb = _thread(s, [tb], "dm", "TB私聊")
    dm_tb.messages.append(Message(role="user", content="这是TB的星星约定，与TA无关"))
    group = _thread(s, [ta, tb], "group", "TA与TB的群")
    group.messages.append(Message(role="user", content="群里聊到一起去码头"))
    store.save(s)

    member = ta
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": member})
    res = await (QueryChatMemoryTool()).run({"topic": "星星"}, ctx)
    assert res.ok
    assert "TA私聊" in res.output and "看星星" in res.output
    assert "TB私聊" not in res.output  # 不含 TA 的窗口不检索
    assert "码头" not in res.output  # 关键词过滤生效（群窗未命中「星星」）


async def _r_2(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    t = _thread(s, [ta], "dm", "TA私聊")
    t.messages.append(Message(role="user", content="第一次见面"))
    t.messages.append(Message(role="assistant", name="TA", content="很高兴认识你"))
    store.save(s)

    member = ta
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": member})
    res = await (QueryChatMemoryTool()).run({}, ctx)
    assert res.ok and "很高兴认识你" in res.output


def test_query_memory_topic_empty_returns_recent(tmp_path):
    asyncio.run(_r_2(tmp_path))


def test_query_memory_skips_hidden_and_tool_rolls(tmp_path):
    asyncio.run(_r_3(tmp_path))

async def _r_3(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    t = _thread(s, [ta], "dm", "TA私聊")
    t.messages.append(Message(role="user", content="秘密"))
    t.messages.append(Message(role="tool", content="查询结果"))
    t.messages.append(Message(role="assistant", name="TA", content="被跳过的空内容", hidden=True))
    t.messages.append(Message(role="assistant", name="TA", content="这句要留下"))
    store.save(s)

    member = ta
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": member})
    res = await (QueryChatMemoryTool()).run({}, ctx)
    assert res.ok and "这句要留下" in res.output
    assert "秘密" in res.output
    assert "查询结果" not in res.output  # tool 消息不展示
    assert "被跳过的空内容" not in res.output  # hidden 不展示


def test_update_memory_appends_and_persists(tmp_path):
    asyncio.run(_r_4(tmp_path))

async def _r_4(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, ais = _fsession(store, "A", ["CA"])
    store.save(s)
    member = ais[0]
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": member})
    tool = UpdateChatMemoryTool()

    res = await tool.run({"add": "用户是杂货铺老板娘"}, ctx)
    assert res.ok and "老板娘" in member.memory

    await tool.run({"add": "约定明天一起去码头"}, ctx)
    assert member.memory.count("- [") == 2  # 追加而非覆盖

    # 持久化生效
    reloaded = store.get(s.id)
    rm = reloaded.member(member.id)
    assert "老板娘" in rm.memory and "码头" in rm.memory


class _FakeEmbedder:
    """确定性向量：含「码头」的特征1、含「星星」的特征2，外加偏置，保证相似度可区分。"""
    def __init__(self, top_k=1, fail=False):
        self.cfg = SimpleNamespace(top_k=top_k)
        self.fail = fail

    async def embed(self, texts):
        if self.fail:
            raise RuntimeError("嵌入服务崩溃")
        rows = []
        for t in texts:
            row = [0.0, 0.0, 1.0]
            if "码头" in t:
                row[0] += 1
            if "星星" in t:
                row[1] += 1
            rows.append(row)
        return rows


def test_query_memory_rag_ranks_semantically(tmp_path):
    asyncio.run(_r_rag(tmp_path))

async def _r_rag(tmp_path):
    from flyai.sessions.json_store import JsonSessionStore
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    t = _thread(s, [ta], "dm", "TA私聊")
    t.messages.append(Message(role="user", content="我们聊过星星的事"))
    t.messages.append(Message(role="user", content="一起坐船去码头"))
    store.save(s)

    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": ta, "embedder": _FakeEmbedder(top_k=1)})
    res = await (QueryChatMemoryTool()).run({"topic": "码头"}, ctx)
    assert res.ok
    assert "码头" in res.output and "星星" not in res.output  # 只返回最相关的 top-1


def test_query_memory_rag_falls_back_to_keyword_on_error(tmp_path):
    asyncio.run(_r_rag_fallback(tmp_path))

async def _r_rag_fallback(tmp_path):
    from flyai.sessions.json_store import JsonSessionStore
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    t = _thread(s, [ta], "dm", "TA私聊")
    t.messages.append(Message(role="user", content="一起去看星星"))
    store.save(s)

    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": ta, "embedder": _FakeEmbedder(fail=True)})
    res = await (QueryChatMemoryTool()).run({"topic": "星星"}, ctx)
    assert res.ok and "看星星" in res.output  # 嵌入失败 → 回退关键词检索


# ---- 角色级跨会话记忆簿 ----

def _book_ctx(tmp_path, mode, owner="TA"):
    """单 AI 房间 + 挂载角色记忆簿；返回 (tool ctx, 记忆簿存储)。"""
    from flyai.lore.character_memory import CharacterMemoryStore

    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    t = _thread(s, [ta], "dm", "TA私聊")
    store.save(s)

    books = CharacterMemoryStore(tmp_path / "mem")
    ta.memory_mode = mode
    services = {"store": store, "session": s, "member": ta, "char_memory": books}
    return ToolContext(session_id=s.id, services=services), books, ta, t


def test_query_includes_mounted_character_book(tmp_path):
    ctx, books, ta, t = _book_ctx(tmp_path, "ro")
    books.append("TA", "用户特别怕水")

    res = asyncio.run(QueryChatMemoryTool().run({"topic": "怕水"}, ctx))
    assert res.ok and "怕水" in res.output
    assert "角色记忆簿" in res.output and "只读" in res.output


def test_query_book_no_topic_returns_recent(tmp_path):
    ctx, books, ta, t = _book_ctx(tmp_path, "rw")
    books.append("TA", "跨会话的老事实")
    res = asyncio.run(QueryChatMemoryTool().run({}, ctx))
    assert res.ok and "跨会话的老事实" in res.output


def test_update_rw_appends_to_character_book(tmp_path):
    ctx, books, ta, t = _book_ctx(tmp_path, "rw")
    res = asyncio.run(UpdateChatMemoryTool().run({"add": "用户养了三只猫"}, ctx))
    assert res.ok and "已同步" in res.output
    assert any("三只猫" in e for e in books.load("TA"))


def test_update_ro_does_not_append_book(tmp_path):
    ctx, books, ta, t = _book_ctx(tmp_path, "ro")
    res = asyncio.run(UpdateChatMemoryTool().run({"add": "只读不该写入簿"}, ctx))
    assert res.ok  # 会话记忆仍可写
    assert "只读不该写入簿" in ta.memory
    assert books.load("TA") == []  # 只读：记忆簿不变


def test_update_mounted_memory_char_id_owner(tmp_path):
    """memory_char_id 指定挂的是别的卡的记忆簿：写入落在那张卡名下。"""
    ctx, books, ta, t = _book_ctx(tmp_path, "rw")
    ta.memory_char_id = "ROOT"
    books.append("ROOT", "根卡旧记忆")

    res = asyncio.run(UpdateChatMemoryTool().run({"add": "新事实"}, ctx))
    assert res.ok
    assert any("新事实" in e for e in books.load("ROOT"))
    assert books.load("TA") == []


def test_cardless_seat_never_uses_anonymous_book(tmp_path):
    """未绑定角色卡的座位不读也不写匿名 ".json" 公共簿（含强制存档）。"""
    from flyai.lore.character_memory import CharacterMemoryStore
    from flyai.tools.memory_tool import _book_read_mode, _book_write_mode

    store = JsonSessionStore(tmp_path)
    s = store.create(title="S")
    s.members.append(RoomMember(name="你", is_user=True))
    seat = RoomMember(name="AI")
    s.members.append(seat)
    store.save(s)
    books = CharacterMemoryStore(tmp_path / "mem")
    books.append("", "匿名簿里的旧事实")

    assert _book_read_mode(seat) == "" and _book_write_mode(seat) == ""

    services = {"store": store, "session": s, "member": seat, "char_memory": books}
    ctx = ToolContext(session_id=s.id, services=services)
    # 无卡座位本来就不能检索跨窗对话；关键是绝不能从匿名簿里捞到内容
    res = asyncio.run(QueryChatMemoryTool().run({"topic": "旧事实"}, ctx))
    assert "匿名簿里的旧事实" not in res.output

    res = asyncio.run(UpdateChatMemoryTool().run({"add": "只写会话"}, ctx))
    assert res.ok and "只写会话" in seat.memory
    assert not any("只写会话" in e for e in books.load(""))

    forced = ToolContext(
        session_id=s.id, services=dict(services, book_force_write=True)
    )
    res = asyncio.run(UpdateChatMemoryTool().run({"add": "强制也不写"}, forced))
    assert res.ok
    assert not any("强制也不写" in e for e in books.load(""))


# ---- 本房间记忆（member.memory）也能被 query 检索到 ----

def test_query_includes_room_memory_on_topic(tmp_path):
    asyncio.run(_r_room_1(tmp_path))

async def _r_room_1(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    ta.memory = (
        "- [2026-10-02] AAS 基地有独立电力系统和两个备份\n"
        "- [2026-10-02] 用户在地下室养了三只猫"
    )
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": ta})
    res = await QueryChatMemoryTool().run({"topic": "aas"}, ctx)
    assert res.ok
    assert "本房间记忆" in res.output
    assert "独立电力" in res.output
    assert "三只猫" not in res.output  # topic 过滤：只回命中的条目
    assert res.output.index("本房间记忆")  # 块存在即可


def test_query_room_memory_no_topic_returns_tail(tmp_path):
    asyncio.run(_r_room_2(tmp_path))

async def _r_room_2(tmp_path):
    from flyai.tools.memory_tool import _ROOM_MEM_MAX_LINES

    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    n = _ROOM_MEM_MAX_LINES + 3
    ta.memory = "\n".join("- 第{0}条记忆".format(i) for i in range(n))
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": ta})
    res = await QueryChatMemoryTool().run({}, ctx)
    assert res.ok and "本房间记忆" in res.output
    assert "第{0}条记忆".format(n - 1) in res.output  # 最新的在
    assert "第0条记忆" not in res.output  # 只取末尾 _ROOM_MEM_MAX_LINES 行


def test_query_multi_keyword_topic_uses_or_match(tmp_path):
    """复现真实报障参数：topic「用户身份 自我介绍 约定」整句不会出现在条目里，
    拆词后「约定」应命中（记忆簿/本房间记忆同样适用）。"""
    asyncio.run(_r_room_5(tmp_path))

async def _r_room_5(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    ta.memory = (
        "- [2026-10-02] AAS 降级后是否还记得幸存者与我们的约定，待查\n"
        "- [2026-10-02] 基地电力剩余未知天数"
    )
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": ta})
    res = await QueryChatMemoryTool().run({"topic": "用户身份 自我介绍 约定"}, ctx)
    assert res.ok
    assert "本房间记忆" in res.output
    assert "约定" in res.output
    assert "电力剩余" not in res.output  # 只回命中词的条目


def test_query_room_memory_topic_miss_omits_block(tmp_path):
    asyncio.run(_r_room_3(tmp_path))

async def _r_room_3(tmp_path):
    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    ta.memory = "- 基地护盾一直开着"
    ctx = ToolContext(session_id=s.id, services={"store": store, "session": s, "member": ta})
    res = await QueryChatMemoryTool().run({"topic": "晚餐吃什么"}, ctx)
    assert res.ok
    assert "本房间记忆" not in res.output  # 无命中不塞块
    assert "没有找到" in res.output


def test_query_room_and_book_share_budget_before_windows(tmp_path):
    """记忆块先占位：窗口片段超出剩余总预算时不再追加（budget_start 记账）。"""
    asyncio.run(_r_room_4(tmp_path))

async def _r_room_4(tmp_path):
    from flyai.lore.character_memory import CharacterMemoryStore

    store = JsonSessionStore(tmp_path)
    s, [_ta] = _fsession(store, "S", ["TA"])
    ta = _ta
    t = _thread(s, [ta], "dm", "TA私聊")
    # 窗口里 8 条长消息：单个 block 远超剩余预算
    for i in range(8):
        t.messages.append(Message(role="user", content="窗口长消息%03d" % i + "x" * 200))
    # 记忆簿 + 本房间记忆各 10 行 ×120 截断 ≈ 2400 字符先占
    books = CharacterMemoryStore(tmp_path / "mem")
    for i in range(10):
        books.append("TA", "记忆簿条目%02d" % i + "y" * 200)
    ta.memory_mode = "rw"
    ta.memory = "\n".join("- 房间条目%02d" % i + "z" * 200 for i in range(10))
    store.save(s)

    services = {"store": store, "session": s, "member": ta, "char_memory": books}
    res = await QueryChatMemoryTool().run({}, ToolContext(session_id=s.id, services=services))
    assert res.ok
    assert "角色记忆簿" in res.output and "本房间记忆" in res.output
    assert "窗口长消息" not in res.output  # 剩余预算装不下整块窗口 → 不追加


def test_memory_tools_guard_missing_context():
    asyncio.run(_r_5())

async def _r_5():
    ctx = ToolContext(session_id="x", services={})
    assert not (await (QueryChatMemoryTool()).run({"topic": "hi"}, ctx)).ok
    assert not (await (UpdateChatMemoryTool()).run({"add": "hi"}, ctx)).ok

    # 用户座位不可写记忆
    user_member = RoomMember(name="你", is_user=True, character_id="")
    uc = ToolContext(session_id="x", services={"store": JsonSessionStore, "session": None, "member": user_member})
    assert not (await (UpdateChatMemoryTool()).run({"add": "hi"}, uc)).ok