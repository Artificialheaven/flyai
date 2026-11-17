"""自动召回注入：开关、保留窗去重、条数上限（P3）。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_chat import (
    _attach_recall,
    _belongs_to_speaker,
    build_auto_recall_block,
)
from flyai.config import AppConfig
from flyai.sessions.models import RoomMember
from flyai.user import UserScope
from flyai.schemas import Message


def _env(tmp_path):
    scope = UserScope.build("u1", AppConfig(), tmp_path, None)
    state = SimpleNamespace(
        config=AppConfig(),
        get_embedder=lambda: None,
        memory_vec_cache=None,
        recall_logs=None,
    )
    return scope, state


def _turn(n, topic):
    return [
        Message(role="user", content="第{0}轮：{1}".format(n, topic)),
        Message(role="assistant", content="第{0}轮回答".format(n)),
    ]


def test_disabled_returns_empty(tmp_path):
    scope, state = _env(tmp_path)
    history = _turn(0, "我们以前约好去看星星")
    block, items = __import__("asyncio").get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="TA", history=history,
            member=None, query="星星", cache_namespace="sid",
        )
    )
    assert block == "" and items == []


def test_recalls_old_message_outside_window(tmp_path):
    import asyncio

    scope, state = _env(tmp_path)
    scope.user_settings.auto_recall = True
    scope.user_settings.keep_turns = 2
    history = (
        _turn(0, "我们以前约好去码头")
        + _turn(1, "别的事")
        + _turn(2, "又是别的事")
    )
    block, items = asyncio.get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="TA", history=history,
            member=None, query="码头", cache_namespace="sid",
        )
    )
    assert "自动回忆" in block and "码头" in block
    assert items and all("text" in it and "source" in it for it in items)


def test_skips_fact_inside_keep_window(tmp_path):
    import asyncio

    scope, state = _env(tmp_path)
    scope.user_settings.auto_recall = True
    scope.user_settings.keep_turns = 3
    # 三条轮次全部在保留窗内 → 召回为空
    history = (
        _turn(0, "我们约好去码头")
        + _turn(1, "别的事")
        + _turn(2, "又是别的事")
    )
    block, items = asyncio.get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="TA", history=history,
            member=None, query="码头", cache_namespace="sid",
        )
    )
    assert block == "" and items == []


def test_recall_max_cap(tmp_path):
    import asyncio

    scope, state = _env(tmp_path)
    scope.user_settings.auto_recall = True
    scope.user_settings.auto_recall_max = 2
    scope.user_settings.keep_turns = 2
    history = []
    for i in range(6):
        history += _turn(i, "码头相关的事情%02d" % i)
    block, items = asyncio.get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="TA", history=history,
            member=None, query="码头", cache_namespace="sid",
        )
    )
    assert block.count("\n- ") <= 2 and len(items) <= 2


def test_attach_recall_to_last_visible_assistant():
    items = [{"source": "auto", "text": "去码头"}]
    history = [
        Message(role="user", content="码头"),
        Message(role="assistant", content=None, tool_calls=[{"id": "c1", "name": "x", "arguments": {}}]),
        Message(role="tool", content="结果", tool_call_id="c1", name="x"),
        Message(role="assistant", content="最终回答"),
    ]
    _attach_recall(history, 0, "", items)
    assert history[-1].recall == items
    assert history[1].recall is None  # 工具调用中的那条 assistant 不挂
    # 挂载的是拷贝，后续修改入参不影响已存消息
    items[0]["text"] = "改过"
    assert history[-1].recall[0]["text"] == "去码头"


def test_attach_recall_skips_hidden_and_other_actor():
    items = [{"source": "auto", "text": "x"}]
    # 最后一条 assistant 是 hidden（skip）→ 本轮不挂
    ma = Message(role="assistant", content="可见")
    ma.actor_id = "A"
    mb = Message(role="assistant", content="", hidden=True)
    mb.actor_id = "B"
    history = [ma, mb]
    _attach_recall(history, 0, "B", items)
    assert history[0].recall is None and history[1].recall is None
    # actor 匹配到同发言者的可见消息
    _attach_recall(history, 0, "A", items)
    assert history[0].recall == items


def test_attach_recall_empty_items_noop():
    history = [Message(role="assistant", content="x")]
    _attach_recall(history, 0, "", [])
    assert history[0].recall is None


# ---- 按角色 id 隔离：群聊别家台词 / 换卡后旧卡台词不进候选池 ----

def test_belongs_to_speaker_group_isolates_other_actors():
    a = RoomMember(name="爱琳", character_id="CA")
    b = RoomMember(name="小玥", character_id="CB")
    mine = Message(role="assistant", content="x", actor_id=b.id, name="小玥")
    theirs = Message(role="assistant", content="y", actor_id=a.id, name="爱琳")
    user = Message(role="user", content="z")
    assert _belongs_to_speaker(mine, b, "CB")
    assert not _belongs_to_speaker(theirs, b, "CB")
    # 用户消息恒算（用户是同一个人）
    assert _belongs_to_speaker(user, b, "CB")


def test_belongs_to_speaker_card_id_beats_actor():
    seat = RoomMember(name="小玥", character_id="CB")
    old = Message(role="assistant", content="旧卡台词", actor_id=seat.id,
                  character_id="CA")
    new = Message(role="assistant", content="新卡台词", actor_id=seat.id,
                  character_id="CB")
    # 同一座位换卡：消息级角色卡 id 优先于座位 actor_id
    assert not _belongs_to_speaker(old, seat, "CB")
    assert _belongs_to_speaker(new, seat, "CB")
    # 旧卡自己回看历史时仍能看到自己的台词
    assert _belongs_to_speaker(old, seat, "CA")


def test_auto_recall_excludes_other_characters(tmp_path):
    import asyncio

    scope, state = _env(tmp_path)
    scope.user_settings.auto_recall = True
    scope.user_settings.keep_turns = 1
    a = RoomMember(name="爱琳", character_id="CA")
    b = RoomMember(name="小玥", character_id="CB")
    history = [
        Message(role="user", content="我们以前在码头碰过头"),
        Message(role="assistant", content="码头暗号只有我知道",
                actor_id=a.id, name="爱琳"),
        Message(role="assistant", content="码头海风很大",
                actor_id=b.id, name="小玥"),
        Message(role="user", content="今天随便聊聊"),
        Message(role="assistant", content="好的呀",
                actor_id=b.id, name="小玥"),
    ]
    block, items = asyncio.get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="CB", history=history,
            member=b, query="码头", cache_namespace="tid",
        )
    )
    # 别家角色的台词不进入候选池
    assert "暗号" not in block
    assert all("暗号" not in it["text"] for it in items)
    assert not any(it["source"] == "旧对话·爱琳" for it in items)
    # 用户消息与本角色旧台词照常召回
    assert "码头海风很大" in block


def test_auto_recall_respects_message_card_id(tmp_path):
    import asyncio

    scope, state = _env(tmp_path)
    scope.user_settings.auto_recall = True
    scope.user_settings.keep_turns = 1
    seat = RoomMember(name="小玥", character_id="NEW")
    history = [
        Message(role="assistant", content="旧卡约定在码头接头",
                actor_id=seat.id, character_id="OLD"),
        Message(role="assistant", content="新卡知道码头有船",
                actor_id=seat.id, character_id="NEW"),
        Message(role="user", content="今天聊别的"),
        Message(role="assistant", content="好", actor_id=seat.id,
                character_id="NEW"),
        Message(role="user", content="再说点"),
        Message(role="assistant", content="行", actor_id=seat.id,
                character_id="NEW"),
    ]
    block, items = asyncio.get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="NEW", history=history,
            member=seat, query="码头", cache_namespace="tid",
        )
    )
    assert "接头" not in block
    assert "新卡知道码头有船" in block


def test_auto_recall_ignores_book(tmp_path):
    """自动回忆只取当前窗口消息：跨会话记忆簿不进入候选池。"""
    import asyncio

    scope, state = _env(tmp_path)
    scope.user_settings.auto_recall = True
    scope.char_memory.append("TA", "我们跨会话约好一起去码头")
    block, items = asyncio.get_event_loop().run_until_complete(
        build_auto_recall_block(
            state=state, sc=scope, char_id="TA", history=[],
            member=None, query="码头", cache_namespace="sid",
        )
    )
    assert block == "" and items == []
