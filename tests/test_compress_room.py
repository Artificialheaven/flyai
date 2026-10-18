"""房间压缩：消息脚本文本、compress_room（monkeypatch complete）、apply_room_compression。"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.lore.gen import compress_room, messages_to_script
from flyai.schemas import Message, ToolCall
from flyai.sessions.resolve import apply_room_compression


class _FakeLore:
    def __init__(self, chars=None):
        self._chars = {c.id: c for c in (chars or [])}

    def get_character(self, cid):
        return self._chars.get(cid)


def _room(char_ids, titles=("初", "两个")):
    from flyai.lore.models import Character
    from flyai.sessions.models import RoomMember, Session, Thread

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


# ---- messages_to_script ----

def test_messages_to_script_includes_tools_names_and_skips_hidden():
    msgs = [
        Message(role="user", content="你好"),
        Message(
            role="assistant", content="回复内容", name="初",
            tool_calls=[ToolCall(id="t1", name="query_lore", arguments={"keyword": "x"})],
        ),
        Message(role="tool", content="查到资料了", name="query_lore", tool_call_id="t1"),
        Message(role="assistant", content="不应出现", name="初", hidden=True),
    ]
    script = messages_to_script(msgs, with_names=True)

    assert "user: 你好" in script
    assert "[初] 回复内容" in script
    assert "[初] [调用工具 query_lore(" in script and '"keyword": "x"' in script
    assert "[工具结果(query_lore) 查到资料了]" in script
    assert "不应出现" not in script

    # 不带名字前缀
    plain = messages_to_script(msgs)
    assert "[初]" not in plain and "回复内容" in plain


def test_messages_to_script_empty():
    assert messages_to_script([]) == "（本对话暂无消息）"


# ---- compress_room ----

@pytest.mark.asyncio
async def test_compress_room_builds_thread_script_and_returns_json(monkeypatch):
    s, chars = _room(["c1", "c2"])
    s.threads[0].messages.append(Message(role="user", content="你好"))
    lore = _FakeLore(chars)

    captured = {}

    async def fake_complete(provider, messages, system_prompt="", **kwargs):
        captured["user"] = messages[-1].content
        captured["system"] = system_prompt
        return json.dumps({
            "members": [
                {"member_id": s.members[1].id, "character": {"intro": "初的新简介"}},
                {"member_id": s.members[2].id, "character": {"background": "两个的新经历"}},
            ],
            "world": {"content": "共享世界变化"},
        })

    monkeypatch.setattr("flyai.lore.gen.complete", fake_complete)

    state = SimpleNamespace(get_provider=lambda: object())
    data = await compress_room(state, s, lore)

    assert len(data["members"]) == 2
    assert data["world"]["content"] == "共享世界变化"
    # 给模型的文本里：参与者资料 + 按窗口分段（带发言者名）+ ID 清单
    prompt = captured["user"]
    assert "参与者 member_id=" in prompt
    assert "【窗口 初】" in prompt and "你好" in prompt
    assert s.members[1].id in prompt and s.members[2].id in prompt
    assert "member_id" in captured["system"]


# ---- apply_room_compression ----

def test_apply_room_compression_writes_member_overrides_and_world():
    s, chars = _room(["c1"])
    lore = _FakeLore(chars)
    ai = s.members[1]

    data = {
        "members": [
            {
                "member_id": ai.id,
                "character": {
                    "intro": "更新的简介",
                    "table": [{"title": "关键设定", "content": "内容值"}],
                },
            }
        ],
        "world": {"content": "世界变化", "table": [{"title": "规则", "content": "一条"}]},
    }
    out = apply_room_compression(s, data, lore)

    # base（库卡）与 patch 合并：名称保留、简介更新、条目按标题合并写入
    assert ai.override["name"] == "初"
    assert ai.override["intro"] == "更新的简介"
    _entry_full = {"pinned": False, "keys": [], "position": "system",
                   "depth": 4, "insert_order": 100, "role": "system",
                   "probability": 100}
    assert ai.override["table"] == [
        {"title": "关键设定", "content": "内容值", **_entry_full}
    ]
    assert s.world_override["content"] == "世界变化"
    assert s.world_override["table"] == [
        {"title": "规则", "content": "一条", **_entry_full}
    ]

    assert out["members"][ai.id] is ai.override
    assert out["world"]["content"] == "世界变化"


def test_apply_room_compression_merges_table_by_title_and_keeps_pinned():
    """AI 只返回部分标题：其余条目原样保留，同标题只更新 content，pinned 不丢。"""
    from flyai.lore.models import Character, LoreEntry

    s, _ = _room(["c1"])
    base = Character(
        id="c1",
        name="初",
        table=[
            LoreEntry(title="旧设定", content="旧内容", pinned=True),
            LoreEntry(title="待更新", content="原内容", keys=["触发词A", "触发词B"]),
        ],
    )
    lore = _FakeLore([base])
    ai = s.members[1]

    data = {
        "members": [
            {
                "member_id": ai.id,
                "character": {
                    "table": [
                        {"title": "待更新", "content": "新内容"},
                        {"title": "新设定", "content": "追加内容"},
                    ]
                },
            }
        ]
    }
    apply_room_compression(s, data, lore)

    table = ai.override["table"]
    assert [t["title"] for t in table] == ["旧设定", "待更新", "新设定"]
    assert table[0]["content"] == "旧内容" and table[0]["pinned"] is True
    assert table[1]["content"] == "新内容"
    # 同标题只更新正文/pinned：用户配的触发词不能被压缩抹掉
    assert table[1]["keys"] == ["触发词A", "触发词B"]
    assert table[2]["content"] == "追加内容"


def test_apply_room_compression_ignores_unknown_member():
    s, chars = _room(["c1"])
    out = apply_room_compression(
        s, {"members": [{"member_id": "ghost", "character": {"intro": "x"}}]}, _FakeLore(chars)
    )
    assert out["members"] == {}
    assert s.members[1].override is None


def test_apply_room_compression_rejects_cross_member_patch():
    """AI 把 B 角色的资料写到 A 的 member_id 下（补丁 name 与座位不符）：拒绝该补丁。"""
    from flyai.sessions.resolve import plan_room_compression

    s, chars = _room(["c1", "c2"])  # 座位名：初、两个
    lore = _FakeLore(chars)
    # 给座位 c1 的 member_id 塞了名字是另一位角色「两个」的补丁
    data = {
        "members": [
            {"member_id": s.members[1].id,
             "character": {"name": "两个", "intro": "我其实是另一个人"}},
            {"member_id": s.members[2].id,
             "character": {"name": "两个", "intro": "正确归属的更新"}},
        ]
    }
    planned = plan_room_compression(s, data, lore)
    assert s.members[1].id not in planned["members"]  # 张冠李戴，拒绝
    assert s.members[2].id in planned["members"]
    assert planned["members"][s.members[2].id]["intro"] == "正确归属的更新"


def test_apply_room_compression_accepts_patch_without_name():
    """补丁省略 name（旧提示词输出）：不阻断，照常合并。"""
    s, chars = _room(["c1"])
    out = apply_room_compression(
        s, {"members": [{"member_id": s.members[1].id,
                         "character": {"intro": "新简介"}}]},
        _FakeLore(chars),
    )
    assert s.members[1].id in out["members"]
