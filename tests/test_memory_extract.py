"""结构化批量抽取：条目冲突消解 + 节奏触发（P4）。"""

import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.lore.character_memory import CharacterMemoryStore
from flyai.memory.entries import EntryBook, MemoryEntry
from flyai.memory.extract import (
    apply_entry_ops,
    maybe_structured_extract,
)
from flyai.providers.base import StreamEventType
from flyai.schemas import Message


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_apply_entry_ops_add():
    book = EntryBook()
    report = apply_entry_ops(book, [
        {"text": "用户是杂货铺老板娘", "op": "add",
         "entities": ["用户"], "importance": 0.8},
    ])
    assert report == {"added": 1, "updated": 0}
    assert book.entries[0].source == "extract"
    assert book.entries[0].importance == 0.8


def test_apply_entry_ops_update_matches_entity():
    book = EntryBook(entries=[
        MemoryEntry(text="银钥匙在用户手中", entities=["银钥匙"]),
    ])
    report = apply_entry_ops(book, [
        {"text": "银钥匙转交给了老板娘保管", "op": "update",
         "entities": ["银钥匙"]},
    ])
    assert report["updated"] == 1 and report["added"] == 0
    assert book.entries[0].text == "银钥匙转交给了老板娘保管"
    assert len(book.entries[0].history) == 1
    assert "用户手中" in book.entries[0].history[0]["text"]


def test_apply_entry_ops_strong_similarity_inplace():
    book = EntryBook(entries=[
        MemoryEntry(text="用户特别怕水这件事"),
    ])
    # 无 op 标注但高度相似 → 视为更新
    report = apply_entry_ops(book, [
        {"text": "用户特别怕水这件事情"},
    ])
    assert report["updated"] == 1


def test_apply_entry_ops_medium_similarity_supersedes():
    book = EntryBook(entries=[
        MemoryEntry(text="银钥匙被放在了吧台抽屉的最里面那一层"),
    ])
    report = apply_entry_ops(book, [
        {"text": "银钥匙其实已经被老板娘收进了保险柜"},
    ])
    # 既不是强相似也没有实体交集 → 新增（旧条目继续保留）
    assert report["added"] == 1
    assert len(book.entries) == 2


def test_apply_entry_ops_update_without_match_becomes_add():
    book = EntryBook()
    report = apply_entry_ops(book, [
        {"text": "全新事实", "op": "update"},
    ])
    assert report["added"] == 1


def test_apply_entry_ops_ignores_empty():
    book = EntryBook()
    report = apply_entry_ops(book, [{"text": "  ", "op": "add"}])
    assert report["added"] == 0 and book.entries == []


class _JsonProvider:
    """无工具 provider：把预置 JSON 作为正文流式返回。"""

    def __init__(self, payload):
        self._text = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    async def stream_chat(self, messages, tool_specs=None):
        self.calls += 1
        yield SimpleNamespace(
            type=StreamEventType.CONTENT_DELTA, text=self._text
        )


def _turns(n):
    msgs = []
    for i in range(n):
        msgs.append(Message(role="user", content="第{0}轮问题".format(i)))
        msgs.append(Message(role="assistant", content="第{0}轮回答".format(i)))
    return msgs


def test_maybe_extract_runs_at_cadence_and_marks(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    payload = {
        "entries": [{"text": "用户怕水", "op": "add",
                     "entities": ["用户"]}],
        "ledger": [{"kind": "item", "name": "银钥匙",
                    "state": "用户手中", "op": "upsert"}],
    }
    provider = _JsonProvider(payload)

    # 3 轮、节奏 5 → 不触发
    r1 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="sid-1",
        history=_turns(3), provider=provider, every=5,
    ))
    assert r1 is None and provider.calls == 0

    # 5 轮 → 触发
    r2 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="sid-1",
        history=_turns(5), provider=provider, every=5,
    ))
    assert r2 is not None
    assert r2["entries"]["added"] == 1
    assert r2["ledger"]["added"] == 1
    assert provider.calls == 1

    book = store.load_book("TA")
    assert book.extract_markers["sid-1"] == 5
    assert any(e.text == "用户怕水" for e in book.entries)
    assert store.load_ledger("TA")["item"][0].name == "银钥匙"

    # 再发同样 5 轮（marker 已记 5，未见新增）→ 不重复触发
    r3 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="sid-1",
        history=_turns(5), provider=provider, every=5,
    ))
    assert r3 is None and provider.calls == 1


def test_maybe_extract_increments_after_marker(tmp_path):
    store = CharacterMemoryStore(tmp_path)
    provider = _JsonProvider({"entries": [], "ledger": []})
    # 预置 marker=3
    book = store.load_book("TA")
    book.extract_markers["sid-1"] = 3
    store.save_book("TA", book)

    # 总共 5 轮：新增 2 轮，未达节奏 3 → 不触发
    r1 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="sid-1",
        history=_turns(5), provider=provider, every=3,
    ))
    assert r1 is None

    # 总共 6 轮：新增 3 轮 → 触发
    r2 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="sid-1",
        history=_turns(6), provider=provider, every=3,
    ))
    assert r2 is not None
    assert store.load_book("TA").extract_markers["sid-1"] == 6


class _CapturingProvider:
    """记录每次请求收到的消息文本。"""

    def __init__(self, payload):
        self._text = json.dumps(payload, ensure_ascii=False)
        self.seen = []

    async def stream_chat(self, messages, tool_specs=None):
        self.seen.append("\n".join(str(getattr(m, "content", "")) for m in messages))
        yield SimpleNamespace(
            type=StreamEventType.CONTENT_DELTA, text=self._text
        )


def test_extract_script_keeps_speaker_names(tmp_path):
    """群聊历史抽取：assistant 的名字必须进脚本，否则会把别人说的话记成自己的。"""
    store = CharacterMemoryStore(tmp_path)
    provider = _CapturingProvider({"entries": [], "ledger": []})
    history = [
        Message(role="user", content="发生了什么"),
        Message(role="assistant", content="小明做的事", name="小明"),
        Message(role="assistant", content="小红做的事", name="小红"),
    ]
    _run(maybe_structured_extract(
        char_store=store, char_id="c-xiao", marker_key="sid",
        history=history, provider=provider, every=1,
    ))
    assert provider.seen and "[小明] 小明做的事" in provider.seen[0]
    assert "[小红] 小红做的事" in provider.seen[0]


def test_schedule_extraction_writes_mounted_book(tmp_path):
    """rw 显式挂载别人的记忆簿（memory_char_id）时，自动抽取写挂载簿，
    与 update_chat_memory 工具归属一致，不能写回座位自己的簿。"""
    from flyai.api.routes_chat import _schedule_extraction
    from flyai.sessions.models import RoomMember

    store = CharacterMemoryStore(tmp_path)
    settings = SimpleNamespace(extract_every_turns=1)
    sc = SimpleNamespace(user_settings=settings, char_memory=store, uid="u1")
    member = RoomMember(
        name="分身", character_id="own-card",
        memory_mode="rw", memory_char_id="mounted-card",
    )
    payload = {"entries": [{"text": "只属于挂载簿的事实", "op": "add",
                            "entities": ["事实"]}], "ledger": []}
    provider = _JsonProvider(payload)
    history = [
        Message(role="user", content="第一轮"),
        Message(role="assistant", content="回答", name="分身"),
    ]

    async def scenario():
        # create_task 要求运行中的 loop（生产环境在 async drive 内调用）
        _schedule_extraction(
            SimpleNamespace(recall_logs=None), sc, char_id="own-card",
            marker_key="t-1", history=history, member=member,
            provider=provider,
        )
        for _ in range(20):  # 等后台抽取任务完成
            await asyncio.sleep(0.05)
            if store.load_book("mounted-card").entries:
                break

    _run(scenario())
    assert any(e.text == "只属于挂载簿的事实"
               for e in store.load_book("mounted-card").entries)
    assert store.load_book("own-card").entries == []


def test_schedule_extraction_default_own_book_rw(tmp_path):
    """未显式挂载的自有卡座位：默认可写自己的记忆簿（自动整理默认生效）。"""
    from flyai.api.routes_chat import _schedule_extraction
    from flyai.sessions.models import RoomMember

    store = CharacterMemoryStore(tmp_path)
    settings = SimpleNamespace(extract_every_turns=1)
    sc = SimpleNamespace(user_settings=settings, char_memory=store, uid="u1")
    member = RoomMember(name="AI", character_id="own-card")  # memory_mode=""
    provider = _JsonProvider(
        {"entries": [{"text": "默认写进自有簿", "op": "add"}], "ledger": []}
    )
    history = [
        Message(role="user", content="第一轮"),
        Message(role="assistant", content="回答", name="AI"),
    ]

    async def scenario():
        _schedule_extraction(
            SimpleNamespace(recall_logs=None), sc, char_id="own-card",
            marker_key="t-1", history=history, member=member,
            provider=provider,
        )
        for _ in range(20):
            await asyncio.sleep(0.05)
            if store.load_book("own-card").entries:
                break

    _run(scenario())
    assert any(e.text == "默认写进自有簿"
               for e in store.load_book("own-card").entries)


def test_schedule_extraction_explicit_ro_skipped(tmp_path):
    """显式只读挂载的座位：不触发抽取。"""
    from flyai.api.routes_chat import _schedule_extraction
    from flyai.sessions.models import RoomMember

    store = CharacterMemoryStore(tmp_path)
    settings = SimpleNamespace(extract_every_turns=1)
    sc = SimpleNamespace(user_settings=settings, char_memory=store, uid="u1")
    member = RoomMember(name="AI", character_id="own-card", memory_mode="ro")
    provider = _JsonProvider(
        {"entries": [{"text": "不应写入", "op": "add"}], "ledger": []}
    )
    history = [
        Message(role="user", content="第一轮"),
        Message(role="assistant", content="回答"),
    ]
    _schedule_extraction(
        SimpleNamespace(recall_logs=None), sc, char_id="own-card",
        marker_key="t-1", history=history, member=member, provider=provider,
    )
    assert store.load_book("own-card").entries == []


def test_schedule_extraction_other_book_without_mount_skipped(tmp_path):
    """指定了别人的 memory_char_id 但未显式 rw：默认不可写，不触发抽取。"""
    from flyai.api.routes_chat import _schedule_extraction
    from flyai.sessions.models import RoomMember

    store = CharacterMemoryStore(tmp_path)
    settings = SimpleNamespace(extract_every_turns=1)
    sc = SimpleNamespace(user_settings=settings, char_memory=store, uid="u1")
    member = RoomMember(
        name="分身", character_id="own-card", memory_char_id="other-card",
    )
    provider = _JsonProvider(
        {"entries": [{"text": "不应写入", "op": "add"}], "ledger": []}
    )
    history = [
        Message(role="user", content="第一轮"),
        Message(role="assistant", content="回答"),
    ]
    _schedule_extraction(
        SimpleNamespace(recall_logs=None), sc, char_id="own-card",
        marker_key="t-1", history=history, member=member, provider=provider,
    )
    assert store.load_book("other-card").entries == []
    assert store.load_book("own-card").entries == []


def test_extract_markers_isolated_per_window(tmp_path):
    """房间多窗口：marker 按窗口 id 隔离，A 窗口抽取后 B 窗口仍应触发。"""
    store = CharacterMemoryStore(tmp_path)
    provider = _JsonProvider({"entries": [], "ledger": []})

    r1 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="thread-a",
        history=_turns(3), provider=provider, every=2,
    ))
    assert r1 is not None
    # 另一个窗口有自己的历史与标记，不受 thread-a 影响
    r2 = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="thread-b",
        history=_turns(2), provider=provider, every=2,
    ))
    assert r2 is not None
    book = store.load_book("TA")
    assert book.extract_markers["thread-a"] == 3
    assert book.extract_markers["thread-b"] == 2


def test_extract_marker_ahead_of_history_resets(tmp_path):
    """标记领先于当前历史（旧键复用/形态迁移）：按 0 重抽而不是永久卡住。"""
    store = CharacterMemoryStore(tmp_path)
    book = store.load_book("TA")
    book.extract_markers["sid"] = 99
    store.save_book("TA", book)
    provider = _JsonProvider({"entries": [], "ledger": []})

    r = _run(maybe_structured_extract(
        char_store=store, char_id="TA", marker_key="sid",
        history=_turns(1), provider=provider, every=1,
    ))
    assert r is not None
    assert store.load_book("TA").extract_markers["sid"] == 1
