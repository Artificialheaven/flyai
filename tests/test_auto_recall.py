"""自动召回注入：开关、保留窗去重、条数上限（P3）。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_chat import _attach_recall, build_auto_recall_block
from flyai.config import AppConfig
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


def test_recall_from_book(tmp_path):
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
    assert "记忆簿" in block and "码头" in block
    assert any(it["source"] == "记忆簿" for it in items)
