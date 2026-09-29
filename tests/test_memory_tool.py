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