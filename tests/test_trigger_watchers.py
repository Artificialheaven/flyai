# -*- coding: utf-8 -*-
"""P4 外部触发与 watcher 进程：冷却记账、dispatch/handle 状态码矩阵、
detached 脚本启停/探活/reconcile、触发环境变量注入与密钥剥离、AI 工具门禁。

watcher 用例启动的是真实 detached Python 进程；全部 async 用例经 _run 包装。
"""

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agentdb.watcher_store import STATUS_RUNNING
from flyai.api.routes_ext import _extract_bearer, handle_trigger
from flyai.config import WatcherSettings
from flyai.proactor import runner
from flyai.proactor.trigger import (
    MAX_BODY_BYTES,
    MAX_TEXT_LEN,
    TriggerCooldown,
    dispatch_trigger,
    valid_source,
)
from flyai.proactor.watchers import (
    WatcherError,
    WatcherManager,
    _kill_tree,
    is_process_alive,
)
from flyai.tools.base import ToolContext
from flyai.tools.registry import ToolRegistry
from flyai.tools.watcher_tools import (
    WATCHER_TOOLS,
    WatcherListTool,
    WatcherStartTool,
)
from flyai.user import ADMIN_UID

WATCHER_SCRIPT = r'''
import os
import time

with open("env_dump.txt", "w", encoding="utf-8") as fh:
    for k in ("TRIGGER_URL", "TRIGGER_TOKEN",
              "TRIGGER_SESSION", "TRIGGER_SOURCE"):
        fh.write("%s=%s\n" % (k, os.environ.get(k, "")))

while True:
    time.sleep(0.1)
'''


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return loop.run_until_complete(coro)


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------- 纯函数与冷却 ----------------

class TestTriggerPrimitives:
    def test_valid_source(self):
        assert valid_source("watcher.abc-1_2")
        assert not valid_source("")
        assert not valid_source("a b")
        assert not valid_source("a" * 65)
        assert not valid_source("a/b")

    def test_bearer_extract(self):
        assert _extract_bearer("Bearer abc") == "abc"
        assert _extract_bearer("bearer xyz") == "xyz"
        assert _extract_bearer("Token abc") == ""
        assert _extract_bearer("Bearer") == ""
        assert _extract_bearer(None) == ""

    def test_cooldown_record_remaining_rollback(self):
        cd = TriggerCooldown(30)
        now = 1000.0
        assert cd.remaining("s1", "src", now=now) == 0.0
        cd.record("s1", "src", now=now)
        assert cd.remaining("s1", "src", now=now + 10) == 20.0
        # 不同键互不影响
        assert cd.remaining("s1", "other", now=now) == 0.0
        cd.rollback("s1", "src")
        assert cd.remaining("s1", "src", now=now) == 0.0

    def test_cooldown_prune(self):
        cd = TriggerCooldown(30)
        cd.record("s1", "a", now=100.0)
        cd.record("s2", "b", now=1000.0)
        cd.prune(now=1000.0)
        assert cd.remaining("s1", "a", now=1000.0) == 0.0
        assert cd.remaining("s2", "b", now=1000.0) == 30.0


# ---------------- dispatch_trigger 状态矩阵 ----------------

def _trigger_state(tmp_path, enabled=True, token="secret-token", cooldown=30):
    ext = SimpleNamespace(
        enabled=enabled, token=token,
        cooldown_seconds=cooldown, base_url="https://h.example",
    )
    scope = SimpleNamespace(store=SimpleNamespace(
        get=lambda sid: {"id": sid} if sid == "s1" else None,
    ))
    return SimpleNamespace(
        config=SimpleNamespace(ext_trigger=ext),
        trigger_cooldown=TriggerCooldown(cooldown),
        data_dir=tmp_path,
        user_store=SimpleNamespace(
            list=lambda: [SimpleNamespace(id=ADMIN_UID)],
        ),
        _scopes={ADMIN_UID: scope},
    )


class TestDispatchTrigger:
    def test_400_validation_matrix(self, tmp_path):
        state = _trigger_state(tmp_path)
        cases = [
            {"session_id": "", "source": "src", "text": "x"},
            {"session_id": "s1", "source": "bad source", "text": "x"},
            {"session_id": "s1", "source": "src", "text": "  "},
            {"session_id": "s1", "source": "src", "text": "x" * (MAX_TEXT_LEN + 1)},
        ]
        for payload in cases:
            out = _run(dispatch_trigger(state, payload))
            assert out["status"] == 400, payload

    def test_404_owner_missing(self, tmp_path):
        state = _trigger_state(tmp_path)
        out = _run(dispatch_trigger(state, {
            "session_id": "ghost", "source": "src", "text": "hi",
        }))
        assert out["status"] == 404

    def test_429_cooldown_and_retry_after(self, tmp_path):
        state = _trigger_state(tmp_path)
        state.trigger_cooldown.record("s1", "src", now=time.time())
        out = _run(dispatch_trigger(state, {
            "session_id": "s1", "source": "src", "text": "hi",
        }))
        assert out["status"] == 429
        assert out["body"]["retry_after"] >= 1

    def test_202_enqueues_and_records_cooldown(self, tmp_path, monkeypatch):
        state = _trigger_state(tmp_path)
        calls = []

        async def fake_fire(st, uid, sid, **kw):
            calls.append((uid, sid, kw))
            return {"ok": True, "run_id": "run-9"}

        monkeypatch.setattr(runner, "fire", fake_fire)
        out = _run(dispatch_trigger(state, {
            "session_id": "s1", "source": "price-bot", "text": "跌破 100",
        }))
        assert out["status"] == 202
        assert out["body"] == {"ok": True, "run_id": "run-9"}
        assert len(calls) == 1
        uid, sid, kw = calls[0]
        assert uid == ADMIN_UID and sid == "s1"
        assert kw["kind"] == "ext-trigger"
        assert kw["brief"] == "【外部事件：price-bot】跌破 100"
        assert kw["allow_silent"] is False and kw["with_history"] is True
        # 冷却已记账
        assert state.trigger_cooldown.remaining("s1", "price-bot") > 0

    def test_503_fire_failure_rolls_back_cooldown(self, tmp_path, monkeypatch):
        state = _trigger_state(tmp_path)

        async def bad_fire(st, uid, sid, **kw):
            return {"ok": False, "reason": "locked"}

        monkeypatch.setattr(runner, "fire", bad_fire)
        out = _run(dispatch_trigger(state, {
            "session_id": "s1", "source": "src", "text": "hi",
        }))
        assert out["status"] == 503
        assert out["body"]["reason"] == "locked"
        assert state.trigger_cooldown.remaining("s1", "src") == 0.0

    def test_503_exception_rolls_back_cooldown(self, tmp_path, monkeypatch):
        state = _trigger_state(tmp_path)

        async def boom(st, uid, sid, **kw):
            raise RuntimeError("queue down")

        monkeypatch.setattr(runner, "fire", boom)
        out = _run(dispatch_trigger(state, {
            "session_id": "s1", "source": "src", "text": "hi",
        }))
        assert out["status"] == 503
        assert "queue down" in out["body"]["error"]
        assert state.trigger_cooldown.remaining("s1", "src") == 0.0


# ---------------- handle_trigger 传输层 ----------------

class TestHandleTrigger:
    def test_404_when_disabled(self, tmp_path):
        state = _trigger_state(tmp_path, enabled=False)
        status, body = _run(handle_trigger(
            state, b'{"session_id":"s1"}', "Bearer secret-token"))
        assert status == 404 and body["ok"] is False

    def test_401_auth_matrix(self, tmp_path):
        state = _trigger_state(tmp_path)
        payload = b'{"session_id":"s1","source":"s","text":"t"}'
        for header in (None, "", "Bearer wrong-token", "Basic secret-token"):
            status, _ = _run(handle_trigger(state, payload, header))
            assert status == 401, header

    def test_413_oversize_body(self, tmp_path):
        state = _trigger_state(tmp_path)
        status, body = _run(handle_trigger(
            state, b"x" * (MAX_BODY_BYTES + 1), "Bearer secret-token"))
        assert status == 413 and str(MAX_BODY_BYTES) in body["error"]

    def test_400_bad_json_and_non_object(self, tmp_path):
        state = _trigger_state(tmp_path)
        status, _ = _run(handle_trigger(
            state, b"{not json", "Bearer secret-token"))
        assert status == 400
        status, _ = _run(handle_trigger(
            state, b"[1,2,3]", "Bearer secret-token"))
        assert status == 400

    def test_happy_path_202(self, tmp_path, monkeypatch):
        state = _trigger_state(tmp_path)

        async def fake_fire(st, uid, sid, **kw):
            return {"ok": True, "run_id": "r-1"}

        monkeypatch.setattr(runner, "fire", fake_fire)
        payload = json.dumps(
            {"session_id": "s1", "source": "s", "text": "t"}).encode()
        status, body = _run(handle_trigger(
            state, payload, "Bearer secret-token"))
        assert status == 202 and body["run_id"] == "r-1"


# ---------------- WatcherManager ----------------

def _watcher_env(tmp_path, max_per_session=5):
    return SimpleNamespace(
        enabled=True, token="tok-123", base_url="https://h.example/",
    ), WatcherSettings(max_per_session=max_per_session)


def _make_manager(tmp_path, max_per_session=5):
    ext, settings = _watcher_env(tmp_path, max_per_session)
    user_store = SimpleNamespace(
        list=lambda: [SimpleNamespace(id=ADMIN_UID)],
    )
    return WatcherManager(tmp_path, settings, ext, user_store)


def _session_dir(manager, sid="s1"):
    root = manager.session_root(ADMIN_UID, sid)
    root.mkdir(parents=True, exist_ok=True)
    return root


class TestWatcherValidation:
    def test_validation_matrix(self, tmp_path):
        manager = _make_manager(tmp_path)
        root = _session_dir(manager)
        (root / "ok.py").write_text("print('ok')", encoding="utf-8")
        good = manager._validate_script(ADMIN_UID, "s1", "ok.py")
        assert good.is_file()
        bad_paths = [
            "", "x.txt", "/abs/x.py", "C:\\abs.py", "http://h/x.py",
            "../escape.py", "public/x.py", ".watchers/x.py", "missing.py",
        ]
        for bad in bad_paths:
            with pytest.raises(WatcherError):
                manager._validate_script(ADMIN_UID, "s1", bad)

    def test_start_requires_ext_enabled(self, tmp_path):
        manager = _make_manager(tmp_path)
        root = _session_dir(manager)
        (root / "ok.py").write_text("print(1)", encoding="utf-8")
        manager.ext_settings.enabled = False
        with pytest.raises(WatcherError):
            manager.start(ADMIN_UID, "s1", "ok.py")

    def test_start_rejects_bad_source(self, tmp_path):
        manager = _make_manager(tmp_path)
        root = _session_dir(manager)
        (root / "ok.py").write_text("print(1)", encoding="utf-8")
        with pytest.raises(WatcherError):
            manager.start(ADMIN_UID, "s1", "ok.py", source="bad source")


class TestWatcherProcess:
    def test_start_list_stop_lifecycle(self, tmp_path):
        manager = _make_manager(tmp_path)
        root = _session_dir(manager)
        (root / "w.py").write_text(WATCHER_SCRIPT, encoding="utf-8")

        row = manager.start(ADMIN_UID, "s1", "w.py", source="price-bot")
        assert row["status"] == STATUS_RUNNING
        pid = row["pid"]
        assert is_process_alive(pid)
        # detached 进程写出环境变量快照
        dump = root / "env_dump.txt"
        assert _wait_until(dump.is_file)
        text = dump.read_text(encoding="utf-8")
        assert "TRIGGER_URL=https://h.example/api/ext/trigger" in text
        assert "TRIGGER_TOKEN=tok-123" in text
        assert "TRIGGER_SESSION=s1" in text
        assert "TRIGGER_SOURCE=price-bot" in text

        rows = manager.list(ADMIN_UID, "s1")
        assert len(rows) == 1 and rows[0]["alive"] is True
        assert os.path.isfile(rows[0]["log_path"])

        stopped = manager.stop(ADMIN_UID, row["id"])
        assert stopped["status"] == "stopped"
        assert _wait_until(lambda: not is_process_alive(pid))

    def test_cap_enforced(self, tmp_path):
        manager = _make_manager(tmp_path, max_per_session=1)
        root = _session_dir(manager)
        (root / "w.py").write_text(WATCHER_SCRIPT, encoding="utf-8")
        first = manager.start(ADMIN_UID, "s1", "w.py")
        try:
            with pytest.raises(WatcherError):
                manager.start(ADMIN_UID, "s1", "w.py")
        finally:
            manager.stop(ADMIN_UID, first["id"])

    def test_reconcile_marks_dead_no_restart(self, tmp_path):
        manager = _make_manager(tmp_path)
        root = _session_dir(manager)
        (root / "w.py").write_text(WATCHER_SCRIPT, encoding="utf-8")
        row = manager.start(ADMIN_UID, "s1", "w.py")
        pid = row["pid"]
        assert _wait_until((root / "env_dump.txt").is_file)

        _kill_tree(pid)
        assert _wait_until(lambda: not is_process_alive(pid))
        changed = manager.reconcile(ADMIN_UID)
        assert changed == 1
        # 不自动重启：登记仍是同一个 pid，且已 stopped
        again = manager.store.get(ADMIN_UID, row["id"])
        assert again["status"] == "stopped" and again["pid"] == pid
        assert manager.reconcile(ADMIN_UID) == 0

    def test_reconcile_all_scans_users(self, tmp_path):
        manager = _make_manager(tmp_path)
        # 无运行 watcher：两个用户库都扫一遍，返回 0 且不抛
        assert manager.reconcile_all() == 0

    def test_trigger_env_strips_secrets(self, tmp_path, monkeypatch):
        manager = _make_manager(tmp_path)
        monkeypatch.setenv("FOO_API_KEY", "leak-me")
        monkeypatch.setenv("FLAI_CONFIG", "C:/secret/config.json")
        monkeypatch.setenv("MY_PASSWORD", "hunter2")
        monkeypatch.setenv("NORMAL_VAR", "keep")
        env = manager._trigger_env("s1", "src")
        assert "FOO_API_KEY" not in env
        assert "FLAI_CONFIG" not in env
        assert "MY_PASSWORD" not in env
        assert env.get("NORMAL_VAR") == "keep"
        assert env["TRIGGER_URL"] == "https://h.example/api/ext/trigger"
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_is_process_alive_bad_pid(self):
        assert is_process_alive(0) is False
        assert is_process_alive(-1) is False
        # 一个几乎不可能存在的 pid
        assert is_process_alive(2 ** 22) is False


# ---------------- AI 工具层 ----------------

class TestWatcherTools:
    def test_service_missing_failure(self):
        tool = WatcherListTool()
        ctx = ToolContext(services={}, uid=ADMIN_UID, is_admin=True)
        result = _run(tool.run({}, ctx))
        assert result.ok is False and "未就绪" in result.output

    def test_non_admin_blocked_by_registry(self, tmp_path):
        manager = _make_manager(tmp_path)
        registry = ToolRegistry()
        for tool_cls in WATCHER_TOOLS:
            registry.register(tool_cls())
        ctx = ToolContext(
            services={"watcher_manager": manager},
            uid="u1", is_admin=False, level=0, session_id="s1",
        )
        call = SimpleNamespace(name="watcher_start",
                               arguments={"script_path": "w.py"})
        result = _run(registry.execute(call, ctx))
        assert result.ok is False and "权限" in result.output

    def test_admin_tool_e2e(self, tmp_path):
        manager = _make_manager(tmp_path)
        root = _session_dir(manager)
        (root / "w.py").write_text(WATCHER_SCRIPT, encoding="utf-8")
        ctx = ToolContext(
            services={"watcher_manager": manager},
            uid=ADMIN_UID, is_admin=True, session_id="s1",
        )
        start_tool = WatcherStartTool()
        started = _run(start_tool.run({"script_path": "w.py"}, ctx))
        assert started.ok and "已启动" in started.output

        listed = _run(WatcherListTool().run({}, ctx))
        assert listed.ok and "[1]" in listed.output and "存活" in listed.output

        rows = manager.list(ADMIN_UID, "s1")
        stop_call = SimpleNamespace(
            name="watcher_stop", arguments={"id": rows[0]["id"]})
        registry = ToolRegistry()
        registry.register(WATCHER_TOOLS[1]())
        stopped = _run(registry.execute(stop_call, ctx))
        assert stopped.ok and "已停止" in stopped.output
