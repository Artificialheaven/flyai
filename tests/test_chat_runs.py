"""断线可续聊天运行：ChatRun 事件缓存/回放、ChatRunManager 生命周期、重连端点。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.runs import ChatRun, ChatRunManager
from flyai.api.routes_chat import (
    cancel_chat_run,
    chat_run_events,
    chat_run_status,
)
from flyai.api.routes_chat import _spawn_run


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# ---- ChatRun 事件回放 ----

def test_stream_replays_all_events_with_seq():
    async def body():
        run = ChatRun("r1", "admin", "s1")
        await run.emit("content_delta", {"text": "甲"})
        await run.emit("content_delta", {"text": "乙"})
        await run.finish("done")
        return [f async for f in run.stream(0)]

    frames = _run(body())
    assert len(frames) == 2
    assert "id: 1" in frames[0] and "id: 2" in frames[1]
    assert "甲" in frames[0]


def test_stream_after_cursor_skips_old_events():
    async def body():
        run = ChatRun("r1", "admin", "s1")
        await run.emit("content_delta", {"text": "甲"})
        await run.emit("content_delta", {"text": "乙"})
        await run.finish("done")
        return [f async for f in run.stream(1)]

    frames = _run(body())
    assert len(frames) == 1
    assert "id: 2" in frames[0] and "乙" in frames[0]


def test_stream_live_follows_new_events():
    async def body():
        run = ChatRun("r1", "admin", "s1")

        async def producer():
            await asyncio.sleep(0.02)
            await run.emit("content_delta", {"text": "新"})
            await asyncio.sleep(0.02)
            await run.emit("done", {"reason": "stop"})

        pt = asyncio.ensure_future(producer())
        frames = [f async for f in run.stream(0)]
        await pt
        return frames

    frames = _run(body())
    assert len(frames) == 2
    assert "新" in frames[0]


def test_done_terminal_ends_stream():
    async def body():
        run = ChatRun("r1", "admin", "s1")
        await run.emit("done", {"reason": "stop"})
        return [f async for f in run.stream(0)]

    assert len(_run(body())) == 1


def test_intermediate_done_not_terminal():
    # 群聊 fan-out：terminal=False 的 done 不结束流，最后一个 done 才结束
    async def body():
        run = ChatRun("r1", "admin", "s1", "t1")
        await run.emit("done", {"reason": "stop", "terminal": False})
        await run.emit("done", {"reason": "stop", "terminal": True})
        return [f async for f in run.stream(0)]

    assert len(_run(body())) == 2


def test_fatal_error_ends_stream_nonfatal_continues():
    async def body():
        run = ChatRun("r1", "admin", "s1")
        await run.emit("error", {"message": "提示", "fatal": False})
        await run.emit("error", {"message": "崩了", "fatal": True})
        return [f async for f in run.stream(0)]

    assert len(_run(body())) == 2


def test_cancelled_run_ends_after_replay():
    async def body():
        run = ChatRun("r1", "admin", "s1")
        await run.emit("content_delta", {"text": "半截"})
        await run.finish("cancelled")
        return [f async for f in run.stream(0)]

    frames = _run(body())
    assert len(frames) == 1
    assert "半截" in frames[0]


# ---- ChatRunManager ----

def test_manager_create_get_ownership():
    mgr = ChatRunManager()
    r = mgr.create("admin", "s1", "t1")
    assert mgr.get(r.id, "admin") is r
    assert mgr.get(r.id, "other") is None
    assert mgr.get("missing", "admin") is None


def test_manager_active_for():
    async def body():
        mgr = ChatRunManager()
        r = mgr.create("admin", "s1", "t1")
        assert mgr.active_for("admin", "s1", "t1") is r
        assert mgr.active_for("admin", "s1", "t2") is None
        assert mgr.active_for("other", "s1", "t1") is None
        await r.finish("done")
        assert mgr.active_for("admin", "s1", "t1") is None

    _run(body())


def test_manager_prunes_expired():
    async def body():
        mgr = ChatRunManager(ttl=0)
        r = mgr.create("admin", "s1")
        await r.finish("done")
        mgr.create("admin", "s2")  # prune: r 已过期
        assert mgr.get(r.id, "admin") is None

    _run(body())


def test_manager_capacity_evicts_oldest_finished():
    async def body():
        mgr = ChatRunManager(ttl=100000, max_runs=3)
        runs = [mgr.create("admin", "s%d" % i) for i in range(3)]
        await runs[0].finish("done")
        mgr.create("admin", "s3")
        assert mgr.get(runs[0].id, "admin") is None
        assert mgr.get(runs[1].id, "admin") is not None
        assert mgr.get(runs[2].id, "admin") is not None

    _run(body())


# ---- _spawn_run 后台任务 ----

def test_spawn_run_success():
    async def body():
        run = ChatRun("r1", "admin", "s1")

        async def drive():
            await run.emit("done", {"reason": "stop"})
            return "done"

        _spawn_run(run, drive, ChatRunManager())
        await run.task
        return run.status

    assert _run(body()) == "done"


def test_spawn_run_exception_publishes_error():
    async def body():
        run = ChatRun("r1", "admin", "s1")

        async def drive():
            raise RuntimeError("boom")

        _spawn_run(run, drive, ChatRunManager())
        await run.task
        return run

    run = _run(body())
    assert run.status == "error"
    assert run.events[-1][0] == "error"
    assert "boom" in run.events[-1][1]["message"]


def test_spawn_run_cancel_marks_cancelled():
    async def body():
        run = ChatRun("r1", "admin", "s1")

        async def drive():
            await asyncio.sleep(100)

        _spawn_run(run, drive, ChatRunManager())
        await asyncio.sleep(0.02)
        run.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run.task
        return run.status

    assert _run(body()) == "cancelled"


# ---- 重连端点（直接调路由函数）----

def _env():
    mgr = ChatRunManager()
    state = SimpleNamespace(run_manager=mgr)
    sc = SimpleNamespace(uid="admin")
    state.user = lambda request: sc
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return mgr, sc, request


def test_status_endpoint_missing_404():
    _mgr, _sc, request = _env()
    with pytest.raises(Exception) as exc:
        _run(chat_run_status("nope", request))
    assert getattr(exc.value, "status_code", 0) == 404


def test_status_endpoint_other_owner_404():
    mgr, sc, request = _env()
    r = mgr.create("bob", "s1")
    with pytest.raises(Exception) as exc:
        _run(chat_run_status(r.id, request))
    assert getattr(exc.value, "status_code", 0) == 404


def test_status_endpoint_ok():
    mgr, _sc, request = _env()
    r = mgr.create("admin", "s1", "t1")
    out = _run(chat_run_status(r.id, request))
    assert out["status"] == "running" and out["session_id"] == "s1"
    assert out["thread_id"] == "t1"


def test_events_endpoint_returns_streaming_response():
    mgr, _sc, request = _env()
    r = mgr.create("admin", "s1")
    resp = _run(chat_run_events(r.id, request, after=2))
    assert resp.media_type == "text/event-stream"


def test_cancel_endpoint_without_task():
    mgr, _sc, request = _env()
    r = mgr.create("admin", "s1")
    out = _run(cancel_chat_run(r.id, request))
    assert out["cancelled"] is True


def test_cancel_endpoint_cancels_task():
    async def body():
        mgr = ChatRunManager()
        state = SimpleNamespace(run_manager=mgr)
        state.user = lambda request: SimpleNamespace(uid="admin")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
        r = mgr.create("admin", "s1")

        async def long_drive():
            await asyncio.sleep(100)

        _spawn_run(r, long_drive, mgr)
        await asyncio.sleep(0.02)
        out = await cancel_chat_run(r.id, request)
        with pytest.raises(asyncio.CancelledError):
            await r.task
        return out, r.status

    out, status = _run(body())
    assert out["cancelled"] is True
    assert status == "cancelled"
