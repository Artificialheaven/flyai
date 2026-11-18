"""首轮自动标题：后台让 AI 拟标题，仅在用户未改过标题时覆盖。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.runs import ChatRun, ChatRunManager
from flyai.api.routes_chat import (
    _chat_in_thread,
    _clean_auto_title,
    _count_user_messages,
    _is_default_title,
    _schedule_auto_title,
)
from flyai.config import ProviderConfig, RoleplaySettings, ToolSettings
from flyai.errors import ProviderError
from flyai.lore.models import Character
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.schemas import Message
from flyai.tools.registry import ToolRegistry
from flyai.user import UserStore


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _TitleProvider:
    """max_tokens==64 的调用视为标题请求，其余是正常对话。"""

    def __init__(self, title="京都旅行", fail_title=False,
                 title_delay=0.0, reply_delay=0.0):
        self.title = title
        self.fail_title = fail_title
        self.title_delay = title_delay
        self.reply_delay = reply_delay
        self.title_calls = []

    async def stream_chat(self, messages, tool_specs, **kwargs):
        if kwargs.get("max_tokens") == 64:
            self.title_calls.append(list(messages))
            if self.title_delay:
                await asyncio.sleep(self.title_delay)
            if self.fail_title:
                raise ProviderError("标题接口 500")
            yield StreamEvent(StreamEventType.CONTENT_DELTA, self.title)
            return
        if self.reply_delay:
            await asyncio.sleep(self.reply_delay)
        yield StreamEvent(StreamEventType.CONTENT_DELTA, "好的")


def _setup(tmp_path, title):
    store = JsonSessionStore(tmp_path / "sessions")
    cfg = ProviderConfig(
        id="p0", base_url="https://example.com", model="m",
        owner="admin", shared=True, min_level=0, rate=0.0,
    )
    state = SimpleNamespace()
    state.config = SimpleNamespace(
        providers=[cfg],
        active_provider=cfg.id,
        tools=ToolSettings(enabled=False),
        roleplay=RoleplaySettings(format_example=""),
        image_models=[],
    )
    state.registry = ToolRegistry()
    state.run_manager = ChatRunManager()
    state.user_store = UserStore(tmp_path)
    user = state.user_store.create("bob", "", level=0)
    state.user_store.set_quota_total(user.id, 100)
    provider = _TitleProvider()
    state.get_provider = lambda pid: provider
    ch = Character(id="c1", name="AI1")
    s = Session(title=title)
    s.members.append(RoomMember(name="你", is_user=True))
    m = RoomMember(name=ch.name, character_id=ch.id)
    s.members.append(m)
    dm = Thread(kind="dm", title=m.name, member_ids=[m.id])
    s.threads.append(dm)
    store.save(s)
    sc = SimpleNamespace(
        uid=user.id, level=0, store=store,
        lore=SimpleNamespace(get_character=lambda cid: ch),
    )
    return state, sc, store, s, dm, m, provider


def _req(thread_id, message="帮我规划京都行程"):
    return SimpleNamespace(
        message=message, thread_id=thread_id, provider_id=None,
        target_member_ids=[], shuffle_speakers=False,
        regenerate_message_id=None, edit_message_id=None,
        image_model_id=None, image_engine="", galgame_resume=None,
        disabled_tools=[],
    )


async def _drain(resp):
    frames = []
    async for frame in resp.body_iterator:
        frames.append(frame)
    return frames


def _turn(state, sc, s, dm, provider, message="帮我规划京都行程"):
    async def body():
        resp = await _chat_in_thread(
            state, sc, s, _req(dm.id, message), provider
        )
        await _drain(resp)
        # 标题是并发后台任务：多让出一拍确保它跑完
        await asyncio.sleep(0.05)
        return state.run_manager, s

    return _run(body())


# ---- 纯函数 ----

def test_clean_title_strips_wrappers_and_tail():
    assert _clean_auto_title("「京都旅行」") == "京都旅行"
    assert _clean_auto_title("标题：京都三日游。") == "京都三日游"
    assert _clean_auto_title("  ## 京都旅行 \n第二行") == "## 京都旅行"
    assert _clean_auto_title("") == ""
    assert _clean_auto_title("   \n  ") == ""


def test_clean_title_truncates_long_output():
    assert len(_clean_auto_title("一" * 50)) == 20


def test_default_title_detection():
    assert _is_default_title("")
    assert _is_default_title("新会话")
    assert _is_default_title("群聊·AI1、AI2")
    assert not _is_default_title("我的房间")
    assert not _is_default_title("京都旅行")


def test_count_user_messages_ignores_hidden():
    s = Session(title="新会话")
    s.members.append(RoomMember(name="你", is_user=True))
    t = Thread(kind="dm", title="x", member_ids=[])
    s.threads.append(t)
    t.messages.append(Message(role="user", content="系统通知", hidden=True))
    assert _count_user_messages(s) == 0
    t.messages.append(Message(role="user", content="你好"))
    assert _count_user_messages(s) == 1


# ---- 调度器本身 ----

def test_schedule_replaces_default_title_and_emits(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s = Session(title="新会话")
    store.save(s)
    sc = SimpleNamespace(store=store)
    provider = _TitleProvider()
    run = ChatRun("r1", "u1", s.id)

    async def body():
        _schedule_auto_title(
            sc=sc, run=run, session_id=s.id, provider=provider,
            first_text="帮我规划京都行程", expected_title="新会话",
        )
        await asyncio.sleep(0.05)

    _run(body())
    assert store.get(s.id).title == "京都旅行"
    assert [ev for ev, _ in run.events] == ["session_title"]
    assert provider.title_calls[0][-1].content == "帮我规划京都行程"


def test_schedule_does_not_clobber_user_rename(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    s = Session(title="新会话")
    store.save(s)
    sc = SimpleNamespace(store=store)
    provider = _TitleProvider()

    async def body():
        _schedule_auto_title(
            sc=sc, run=None, session_id=s.id, provider=provider,
            first_text="你好", expected_title="新会话",
        )
        # 生成期间用户改了标题（同步读改写，早于后台任务醒来）
        fresh = store.get(s.id)
        fresh.set_title("用户起的名字")
        store.save(fresh)
        await asyncio.sleep(0.05)

    _run(body())
    assert store.get(s.id).title == "用户起的名字"


# ---- 房间路径端到端 ----

def test_first_room_turn_sets_ai_title(tmp_path):
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "新会话")
    _turn(state, sc, s, dm, provider)
    assert store.get(s.id).title == "京都旅行"
    assert len(provider.title_calls) == 1


def test_second_turn_does_not_retitle(tmp_path):
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "新会话")
    _turn(state, sc, s, dm, provider)
    _turn(state, sc, s, dm, provider, message="第二天呢")
    assert len(provider.title_calls) == 1


def test_custom_room_title_never_touched(tmp_path):
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "我的房间")
    _turn(state, sc, s, dm, provider)
    assert store.get(s.id).title == "我的房间"
    assert provider.title_calls == []


def test_hidden_message_does_not_block_first_title(tmp_path):
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "新会话")
    dm.messages.append(Message(role="user", content="后台触发", hidden=True))
    store.save(s)
    _turn(state, sc, s, dm, provider)
    assert store.get(s.id).title == "京都旅行"


def test_title_failure_keeps_default_title(tmp_path):
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "新会话")
    provider.fail_title = True
    _turn(state, sc, s, dm, provider)
    assert store.get(s.id).title == "新会话"
    # 主回复不受影响
    t = store.get(s.id).thread(dm.id)
    assert any(m.role == "assistant" and m.content == "好的" for m in t.messages)


def _parse_sse(frames):
    """把 SSE 文本帧解析成 (event, data) 列表。"""
    events = []
    for frame in frames:
        text = frame.decode("utf-8") if isinstance(frame, bytes) else frame
        name = None
        data = None
        for line in text.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):].strip()
            elif line.startswith("data: "):
                raw = line[len("data: "):].strip()
                data = json.loads(raw) if raw else None
        if name:
            events.append((name, data))
    return events


def test_title_pending_flag_when_title_loses_the_race(tmp_path):
    """回复先结束、标题还在生成时，终态 done 带 title_pending，让前端收尾后补取。"""
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "新会话")
    provider.title_delay = 0.3

    async def body():
        resp = await _chat_in_thread(state, sc, s, _req(dm.id), provider)
        frames = await _drain(resp)
        events = _parse_sse(frames)
        # 收尾时标题任务还没跑完，标题事件不可能出现在流里
        assert not any(name == "session_title" for name, _ in events)
        done = [data for name, data in events if name == "done"]
        assert len(done) == 1
        assert done[0]["terminal"] is True
        assert done[0]["title_pending"] is True
        # 后台任务仍在继续，跑完会落库
        await asyncio.sleep(0.4)
        return store.get(s.id).title

    assert _run(body()) == "京都旅行"


def test_no_title_pending_when_title_already_emitted(tmp_path):
    """标题先于回复完成时，done 不带 title_pending（客户端无需轮询）。"""
    state, sc, store, s, dm, _m, provider = _setup(tmp_path, "新会话")
    # 回复慢于标题，标题事件赶在 done 之前送达
    provider.reply_delay = 0.2

    async def body():
        resp = await _chat_in_thread(state, sc, s, _req(dm.id), provider)
        frames = await _drain(resp)
        return _parse_sse(frames)

    events = _run(body())
    assert any(name == "session_title" for name, _ in events)
    done = [data for name, data in events if name == "done"]
    assert len(done) == 1
    assert "title_pending" not in done[0]
