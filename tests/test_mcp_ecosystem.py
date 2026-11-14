# -*- coding: utf-8 -*-
"""P4 MCP 生态：stdio JSON-RPC 客户端、连接管理器、三个 DAL、提示词注册表、
桥接工具门禁、feature_prefs 类别、routes_agentdb 管理路由。

假 MCP server 是 tmp 目录里的真实 Python 进程（换行分帧 JSON-RPC），
不 mock 子系统；全部 async 用例经 _run 事件循环包装，无 sleep 赌运气。
"""

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agentdb.mcp_store import (
    STATUS_FAILED,
    STATUS_NEW,
    STATUS_READY,
    McpServerStore,
)
from flyai.agentdb.prompt_store import PromptOverrideStore
from flyai.agentdb.watcher_store import STATUS_RUNNING, WatcherStore
from flyai.api import routes_agentdb as ra
from flyai.config import McpSettings
from flyai.feature_prefs import allowed_tool_names
from flyai.mcp_client.manager import McpManager
from flyai.mcp_client.stdio import (
    McpEOF,
    McpError,
    McpProtocolError,
    StdioMcpClient,
    sanitize_tool_name,
    valid_server_id,
)
from flyai.proactor.prompt_registry import (
    DEFAULT_COMPACT,
    DEFAULT_MOOD_WRAP,
    DEFAULT_SELF_MEMORY_HEADER,
    KEY_MOOD_WRAP,
    PROMPT_PARTITIONS,
    PromptRegistry,
    render_text,
)
from flyai.tools.base import ToolContext
from flyai.tools.mcp_tool import McpBridgeTool
from flyai.tools.registry import ToolRegistry
from flyai.user import ADMIN_UID

FAKE_SERVER = r'''
import base64
import json
import os
import sys
import time

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def reply(msg_id, result):
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "result": result},
        ensure_ascii=False) + "\n")
    sys.stdout.flush()


def error(msg_id, code, message):
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": msg_id,
         "error": {"code": code, "message": message}}) + "\n")
    sys.stdout.flush()


def main():
    mode = os.environ.get("FAKE_MCP_MODE", "ok")
    call_count = 0
    for raw in sys.stdin:
        if not raw.strip():
            continue
        msg = json.loads(raw)
        method = msg.get("method")
        if method == "initialize":
            if mode == "notify":
                sys.stdout.write(json.dumps(
                    {"jsonrpc": "2.0",
                     "method": "notifications/progress"}) + "\n")
                sys.stdout.flush()
            reply(msg["id"], {"protocolVersion": "2024-11-05",
                              "capabilities": {},
                              "serverInfo": {"name": "fake", "version": "1"}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            if mode == "hang":
                time.sleep(30)
            reply(msg["id"], {"tools": [
                {"name": "echo", "description": "echo back",
                 "inputSchema": {"type": "object",
                                 "properties": {"text": {"type": "string"}}}},
                {"name": "draw", "description": "make image",
                 "inputSchema": {"type": "object", "properties": {}}},
                {"name": "Weird Name!", "description": "sanitize me",
                 "inputSchema": {"type": "object"}},
            ]})
        elif method == "tools/call":
            call_count += 1
            params = msg.get("params") or {}
            name = params.get("name")
            # Windows 下「回复后立刻退出」会让 proactor 丢最后一帧，
            # 这里改为第二次调用直接不回复就退出，确定性制造 EOF
            if mode == "die_on_second" and call_count >= 2:
                sys.stdout.flush()
                os._exit(0)
            if name == "draw":
                reply(msg["id"], {"content": [
                    {"type": "image", "data": PNG, "mimeType": "image/png"}],
                    "isError": False})
            elif name == "boom":
                reply(msg["id"], {"content": [
                    {"type": "text", "text": "failed"}], "isError": True})
            else:
                args = params.get("arguments") or {}
                reply(msg["id"], {"content": [
                    {"type": "text",
                     "text": "echo:%s" % args.get("text", "")}],
                    "isError": False})
        else:
            error(msg["id"], -32601, "method not found")


main()
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


def _write_server(tmp_path, mode="ok"):
    path = tmp_path / ("fake_mcp_%s.py" % mode)
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def _settings(**kw):
    kw.setdefault("enabled", True)
    kw.setdefault("timeout", 10.0)
    return McpSettings(**kw)


def _make_manager(tmp_path, **kw):
    return McpManager(tmp_path, _settings(**kw))


# ---------------- stdio 纯函数 ----------------

class TestStdioHelpers:
    def test_server_id_rule(self):
        assert valid_server_id("demo_1")
        assert not valid_server_id("Demo")
        assert not valid_server_id("1demo")
        assert not valid_server_id("a" * 33)
        assert not valid_server_id("")

    def test_sanitize_tool_name(self):
        assert sanitize_tool_name("Hello World") == "hello_world"
        assert sanitize_tool_name("a-b.c") == "a_b_c"
        assert sanitize_tool_name("!!!") == "tool"
        assert sanitize_tool_name("  Mix_Case ") == "mix_case"


# ---------------- stdio 客户端（真实子进程） ----------------

class TestStdioClient:
    def test_initialize_list_and_call(self, tmp_path):
        script = _write_server(tmp_path)
        client = StdioMcpClient(sys.executable, [str(script)], timeout=10)

        async def scenario():
            await client.start()
            assert client.connected
            tools = await client.list_tools()
            assert {t["name"] for t in tools} == {"echo", "draw", "Weird Name!"}
            res = await client.call_tool("echo", {"text": "hi"})
            assert res["ok"] is True
            assert res["text"] == "echo:hi"
            await client.close()
            assert not client.connected

        _run(scenario())

    def test_notification_frame_skipped(self, tmp_path):
        script = _write_server(tmp_path, "notify")
        client = StdioMcpClient(sys.executable, [str(script)], timeout=10)

        async def scenario():
            await client.start()  # 通知帧在 initialize 响应前
            tools = await client.list_tools()
            assert len(tools) == 3
            await client.close()

        _run(scenario())

    def test_protocol_error_surfaced(self, tmp_path):
        script = _write_server(tmp_path)
        client = StdioMcpClient(sys.executable, [str(script)], timeout=10)

        async def scenario():
            await client.start()
            with pytest.raises(McpProtocolError):
                await client._request("does/not/exist", {})
            await client.close()

        _run(scenario())

    def test_bad_command_raises_mcp_error(self, tmp_path):
        client = StdioMcpClient(
            str(tmp_path / "no_such_binary_xyz"), [], timeout=5
        )

        async def scenario():
            with pytest.raises(McpError):
                await client.start()
            assert not client.connected

        _run(scenario())

    def test_timeout_wrapped_as_mcp_error(self, tmp_path):
        script = _write_server(tmp_path, "hang")
        client = StdioMcpClient(
            sys.executable, [str(script)],
            env=dict(os.environ, FAKE_MCP_MODE="hang"), timeout=0.5,
        )

        async def scenario():
            await client.start()
            with pytest.raises(McpError):
                await client.list_tools()
            await client.close()

        _run(scenario())

    def test_eof_when_server_exits(self, tmp_path):
        script = _write_server(tmp_path, "die_on_second")
        client = StdioMcpClient(
            sys.executable, [str(script)],
            env=dict(os.environ, FAKE_MCP_MODE="die_on_second"), timeout=5,
        )

        async def scenario():
            await client.start()
            res = await client.call_tool("echo", {"text": "x"})
            assert res["ok"] is True
            # 第二次调用时进程不回复直接退出：读帧得到 EOF
            with pytest.raises(McpEOF):
                await client.call_tool("echo", {"text": "y"})
            await client.close()

        _run(scenario())


# ---------------- McpServerStore DAL ----------------

class TestMcpServerStore:
    def test_crud_and_status_machine(self, tmp_path):
        store = McpServerStore(tmp_path)
        row = store.create(ADMIN_UID, "demo", sys.executable,
                           ["a.py"], {"K": "V"})
        assert row["status"] == STATUS_NEW
        assert row["args"] == ["a.py"]
        assert row["env"] == {"K": "V"}
        assert row["tools"] == []
        with pytest.raises(ValueError):
            store.create(ADMIN_UID, "demo", "x")

        store.update_connection(
            ADMIN_UID, "demo", STATUS_READY,
            tools=[{"name": "echo", "inputSchema": {}}],
        )
        row = store.get(ADMIN_UID, "demo")
        assert row["status"] == STATUS_READY and len(row["tools"]) == 1

        # 改连接参数 → 状态回 new，缓存保留
        row = store.update(ADMIN_UID, "demo", command="python3")
        assert row["status"] == STATUS_NEW
        assert len(row["tools"]) == 1
        assert row["command"] == "python3"

        # 失败更新不带 tools → 旧缓存不丢
        store.update_connection(ADMIN_UID, "demo", STATUS_FAILED,
                                last_error="boom")
        row = store.get(ADMIN_UID, "demo")
        assert row["status"] == STATUS_FAILED
        assert row["last_error"] == "boom"
        assert len(row["tools"]) == 1

        assert not store.set_enabled(ADMIN_UID, "demo", False)["enabled"]
        assert len(store.list_enabled(ADMIN_UID)) == 0
        assert store.delete(ADMIN_UID, "demo") is True
        assert store.delete(ADMIN_UID, "demo") is False

    def test_update_unknown_raises_keyerror(self, tmp_path):
        store = McpServerStore(tmp_path)
        with pytest.raises(KeyError):
            store.update(ADMIN_UID, "ghost", command="x")


# ---------------- McpManager ----------------

class TestMcpManager:
    def _seed(self, manager, tmp_path, server_id="demo", mode="ok"):
        script = _write_server(tmp_path, mode)
        manager.store.create(
            ADMIN_UID, server_id, sys.executable, [str(script)],
            {"FAKE_MCP_MODE": mode},
        )

    def test_test_connection_caches_tools(self, tmp_path):
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path)
        result = _run(manager.test_connection("demo"))
        assert result["ok"] is True
        assert len(result["tools"]) == 3
        row = manager.store.get(ADMIN_UID, "demo")
        assert row["status"] == STATUS_READY
        _run(manager.aclose())

    def test_test_connection_failure_marks_failed(self, tmp_path):
        manager = _make_manager(tmp_path)
        manager.store.create(ADMIN_UID, "bad",
                             str(tmp_path / "missing_bin"), [])
        result = _run(manager.test_connection("bad"))
        assert result["ok"] is False
        row = manager.store.get(ADMIN_UID, "bad")
        assert row["status"] == STATUS_FAILED and row["last_error"]

    def test_call_happy_path_and_media_saved(self, tmp_path):
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path)
        _run(manager.test_connection("demo"))
        res = _run(manager.call(ADMIN_UID, "demo", "echo", {"text": "yo"}))
        assert res["ok"] is True and res["text"] == "echo:yo"

        res = _run(manager.call(ADMIN_UID, "demo", "draw", {}))
        assert res["ok"] is True
        assert len(res["media"]) == 1
        media = res["media"][0]
        assert media["type"] == "image"
        assert os.path.isfile(media["path"])
        _run(manager.aclose())

    def test_is_error_result_propagates(self, tmp_path):
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path)
        _run(manager.test_connection("demo"))
        res = _run(manager.call(ADMIN_UID, "demo", "boom", {}))
        assert res["ok"] is False
        _run(manager.aclose())

    def test_disabled_server_rejected(self, tmp_path):
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path)
        manager.store.set_enabled(ADMIN_UID, "demo", False)
        res = _run(manager.call(ADMIN_UID, "demo", "echo", {}))
        assert res["ok"] is False and "停用" in res["error"]

    def test_reconnect_once_on_dead_connection(self, tmp_path):
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path, mode="ok")

        class _DeadClient:
            connected = True

            async def call_tool(self, name, arguments):
                raise McpEOF("test: connection dead")

            async def start(self):
                return None

            async def close(self):
                return None

        manager._clients["demo"] = _DeadClient()
        res = _run(manager.call(ADMIN_UID, "demo", "echo", {"text": "r"}))
        assert res["ok"] is True
        assert res["text"] == "echo:r"
        # 重连成功后状态翻回 ready
        assert manager.store.get(ADMIN_UID, "demo")["status"] == STATUS_READY
        _run(manager.aclose())

    def test_retry_still_fails_marks_failed(self, tmp_path):
        manager = _make_manager(tmp_path)
        manager.store.create(ADMIN_UID, "broken",
                             str(tmp_path / "missing_bin"), [])

        class _DeadClient:
            connected = True

            async def call_tool(self, name, arguments):
                raise McpEOF("dead")

            async def start(self):
                return None

            async def close(self):
                return None

        manager._clients["broken"] = _DeadClient()
        res = _run(manager.call(ADMIN_UID, "broken", "echo", {}))
        assert res["ok"] is False and "重连" in res["error"]
        assert manager.store.get(ADMIN_UID, "broken")["status"] == STATUS_FAILED

    def test_lazy_reconnect_after_process_killed(self, tmp_path):
        # 不经过异常路径：调用前发现 returncode 已设置 → ensure 直接重建
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path, mode="ok")
        r1 = _run(manager.call(ADMIN_UID, "demo", "echo", {"text": "1"}))
        assert r1["ok"] is True
        proc = manager._clients["demo"]._proc
        assert proc is not None
        proc.kill()
        _run(proc.wait())
        assert not manager._clients["demo"].connected
        r2 = _run(manager.call(ADMIN_UID, "demo", "echo", {"text": "2"}))
        assert r2["ok"] is True and r2["text"] == "echo:2"
        _run(manager.aclose())

    def test_reconnect_on_real_eof_happy_path(self, tmp_path):
        # 真实子进程 E2E：第二次调用 EOF → manager 重连一次 → 新进程应答成功
        manager = _make_manager(tmp_path)
        self._seed(manager, tmp_path, mode="die_on_second")
        r1 = _run(manager.call(ADMIN_UID, "demo", "echo", {"text": "1"}))
        assert r1["ok"] is True
        first_client = manager._clients["demo"]
        r2 = _run(manager.call(ADMIN_UID, "demo", "echo", {"text": "2"}))
        assert r2["ok"] is True and r2["text"] == "echo:2"
        assert manager._clients["demo"] is not first_client
        assert manager.store.get(ADMIN_UID, "demo")["status"] == STATUS_READY
        _run(manager.aclose())

    def test_bridge_tools_naming_and_gate(self, tmp_path):
        manager = _make_manager(tmp_path, call_min_level=10)
        self._seed(manager, tmp_path)
        _run(manager.test_connection("demo"))
        tools = manager.bridge_tools()
        names = {t.name for t in tools}
        assert "mcp__demo__echo" in names
        assert "mcp__demo__draw" in names
        assert "mcp__demo__weird_name" in names
        echo = next(t for t in tools if t.name == "mcp__demo__echo")
        assert echo.risk is True and echo.min_level == 10
        assert echo.parameters["type"] == "object"
        _run(manager.aclose())

    def test_bridge_tools_empty_when_disabled(self, tmp_path):
        manager = _make_manager(tmp_path, enabled=False)
        assert manager.bridge_tools() == []

    def test_bridge_name_collision_gets_suffix(self, tmp_path):
        manager = _make_manager(tmp_path)
        manager.store.create(ADMIN_UID, "s1", sys.executable)
        manager.store.update_connection(ADMIN_UID, "s1", STATUS_READY, tools=[
            {"name": "Same Tool", "inputSchema": {"type": "object"}},
            {"name": "same-tool", "inputSchema": {"type": "object"}},
        ])
        names = [t.name for t in manager.bridge_tools()]
        assert names == ["mcp__s1__same_tool", "mcp__s1__same_tool_2"]

    def test_sweep_media_respects_ttl(self, tmp_path):
        manager = _make_manager(tmp_path)
        mcp_dir = tmp_path / ADMIN_UID / "workspace" / "mcp"
        mcp_dir.mkdir(parents=True)
        old = mcp_dir / "old.png"
        new = mcp_dir / "new.png"
        old.write_bytes(b"x")
        new.write_bytes(b"y")
        old_at = time.time() - 7 * 3600
        os.utime(str(old), (old_at, old_at))
        removed = manager.sweep_media(ttl_seconds=6 * 3600)
        assert removed == 1
        assert not old.exists() and new.exists()


# ---------------- WatcherStore / PromptOverrideStore DAL ----------------

class TestExtraDals:
    def test_watcher_store_lifecycle(self, tmp_path):
        store = WatcherStore(tmp_path)
        row = store.register(ADMIN_UID, "s1", "w.py", 1234, "w.log")
        assert row["status"] == STATUS_RUNNING and row["pid"] == 1234
        assert store.count_running(ADMIN_UID, "s1") == 1
        assert store.count_running(ADMIN_UID, "other") == 0
        assert store.mark_stopped(ADMIN_UID, row["id"]) is True
        # 幂等：第二次停不掉已停止的行
        assert store.mark_stopped(ADMIN_UID, row["id"]) is False
        assert len(store.list_running(ADMIN_UID)) == 0
        assert store.get(ADMIN_UID, row["id"])["status"] == "stopped"

    def test_prompt_override_store(self, tmp_path):
        store = PromptOverrideStore(tmp_path)
        assert store.get(ADMIN_UID, "compact") is None
        store.set(ADMIN_UID, "compact", "自定义压缩指令")
        assert store.get(ADMIN_UID, "compact") == "自定义压缩指令"
        assert store.all(ADMIN_UID) == {"compact": "自定义压缩指令"}
        assert store.delete(ADMIN_UID, "compact") is True
        assert store.delete(ADMIN_UID, "compact") is False


# ---------------- 提示词注册表 ----------------

class TestPromptRegistry:
    def test_seven_partitions_complete(self):
        assert set(PROMPT_PARTITIONS) == {
            "mood_wrap", "wake_notice", "speakup_notice", "loop_start",
            "loop_continue", "compact", "self_memory_header",
        }

    def test_render_whitelist(self):
        tpl = "A={allowed} B={unknown} C={allowed}"
        out = render_text(tpl, ("allowed",), {"allowed": "X"})
        assert out == "A=X B={unknown} C=X"
        # 缺值占位符原样保留
        assert render_text("{x}", ("x",), {}) == "{x}"

    def test_defaults_match_legacy_hardcode(self):
        assert DEFAULT_MOOD_WRAP == (
            "（内心独白·只宣布一次）刚刚的互动让你心情{direction}：{reason}"
        )
        assert "{task}" in PROMPT_PARTITIONS["loop_start"].default
        assert "{notice}" in PROMPT_PARTITIONS["loop_continue"].default
        assert DEFAULT_COMPACT.startswith("你是长跑任务的上下文压缩器")
        assert DEFAULT_SELF_MEMORY_HEADER == "【我的自我记忆】"

    def test_override_and_reset(self, tmp_path):
        registry = PromptRegistry(tmp_path)
        rendered = registry.render(KEY_MOOD_WRAP, direction="低落",
                                   reason="被批评")
        assert rendered.endswith("低落：被批评")
        registry.set_override(KEY_MOOD_WRAP, "自定义：{direction}")
        assert registry.render(KEY_MOOD_WRAP, direction="上扬") == "自定义：上扬"
        info = next(x for x in registry.describe() if x["key"] == KEY_MOOD_WRAP)
        assert info["override"] == "自定义：{direction}"
        assert info["placeholders"] == ["direction", "reason"]
        assert registry.reset_override(KEY_MOOD_WRAP) is True
        assert registry.render(KEY_MOOD_WRAP, direction="上扬",
                               reason="x").startswith("（内心独白")

    def test_unknown_key(self, tmp_path):
        registry = PromptRegistry(tmp_path)
        with pytest.raises(KeyError):
            registry.render("nope")
        with pytest.raises(KeyError):
            registry.set_override("nope", "x")


# ---------------- 桥接工具与注册表门禁 ----------------

class TestBridgeToolGate:
    def _ctx(self, manager, uid=ADMIN_UID, is_admin=True, level=0):
        return ToolContext(
            services={"mcp_manager": manager},
            uid=uid, is_admin=is_admin, level=level,
        )

    def test_non_admin_blocked_by_registry(self, tmp_path):
        manager = _make_manager(tmp_path)
        tool = McpBridgeTool("demo", "echo", "mcp__demo__echo",
                             {"name": "echo",
                              "inputSchema": {"type": "object"}},
                             min_level=0)
        registry = ToolRegistry()
        ctx = self._ctx(manager, uid="u1", is_admin=False, level=0)
        result = _run(registry.execute(
            SimpleNamespace(name="mcp__demo__echo", arguments={}),
            ctx, extras=[tool],
        ))
        assert result.ok is False and "权限" in result.output

    def test_level_gate_then_pass(self, tmp_path):
        manager = _make_manager(tmp_path)

        class _StubManager:
            settings = SimpleNamespace(enabled=True)

            async def call(self, uid, sid, name, args):
                return {"ok": True, "text": "stub-ok", "content": [],
                        "media": []}

        tool = McpBridgeTool("demo", "echo", "mcp__demo__echo",
                             {"name": "echo",
                              "inputSchema": {"type": "object"}},
                             min_level=10)
        registry = ToolRegistry()
        low = ToolContext(services={"mcp_manager": _StubManager()},
                          uid="u1", is_admin=False, level=5)
        blocked = _run(registry.execute(
            SimpleNamespace(name="mcp__demo__echo", arguments={}),
            low, extras=[tool],
        ))
        assert blocked.ok is False
        high = ToolContext(services={"mcp_manager": _StubManager()},
                           uid="u2", is_admin=False, level=10)
        passed = _run(registry.execute(
            SimpleNamespace(name="mcp__demo__echo", arguments={}),
            high, extras=[tool],
        ))
        assert passed.ok is True and passed.output == "stub-ok"

    def test_disabled_service_returns_failure(self, tmp_path):
        manager = _make_manager(tmp_path, enabled=False)
        tool = McpBridgeTool("demo", "echo", "mcp__demo__echo",
                             {"name": "echo",
                              "inputSchema": {"type": "object"}})
        result = _run(tool.run({}, self._ctx(manager)))
        assert result.ok is False and "MCP 未启用" in result.output


# ---------------- feature_prefs mcp 类别 ----------------

class TestFeaturePrefsMcp:
    def _registry(self):
        registry = ToolRegistry()

        class _Watch:
            def __init__(self, name):
                self.name = name
                self.risk = True
                self.min_level = 0

        for n in ("watcher_start", "watcher_stop", "watcher_list"):
            registry.register(_Watch(n))
        return registry

    def test_mcp_prefix_gated_by_category(self):
        cfg = SimpleNamespace(
            tools=SimpleNamespace(enabled=True),
            mcp=SimpleNamespace(enabled=False),
        )
        registry = self._registry()
        allowed = allowed_tool_names(
            registry, cfg, None,
            extra_names={"mcp__a__t", "custom__x", "watcher_start"},
        )
        assert "mcp__a__t" not in allowed
        assert "watcher_start" not in allowed
        cfg.mcp.enabled = True
        allowed = allowed_tool_names(
            registry, cfg, None,
            extra_names={"mcp__a__t", "custom__x"},
        )
        assert "mcp__a__t" in allowed
        assert "custom__x" not in allowed  # custom_tools 类别仍关着


# ---------------- routes_agentdb 管理路由 ----------------

class TestAgentdbRoutes:
    def _env(self, tmp_path):
        manager = _make_manager(tmp_path)
        registry = PromptRegistry(tmp_path)
        state = SimpleNamespace(
            mcp_manager=manager, prompt_registry=registry,
            watcher_manager=SimpleNamespace(
                list=lambda uid, sid=None: [],
                stop=lambda uid, wid: {"id": wid, "status": "stopped"},
                reconcile_all=lambda: 0,
            ),
        )
        admin_sc = SimpleNamespace(uid=ADMIN_UID)
        user_sc = SimpleNamespace(uid="u1")
        state._scopes = {"admin": admin_sc, "u1": user_sc}
        state.user = lambda request: state._scopes[request.uid]
        return state, admin_sc, user_sc

    @staticmethod
    def _req(uid):
        return SimpleNamespace(
            uid=uid,
            app=SimpleNamespace(state=SimpleNamespace(flyai=None)),
        )

    def test_mcp_crud_admin_only(self, tmp_path):
        state, admin_sc, user_sc = self._env(tmp_path)
        req = self._req("u1")
        req.app.state.flyai = state
        with pytest.raises(Exception) as exc:
            _run(ra.mcp_list(req))
        assert exc.value.status_code == 403

        areq = self._req("admin")
        areq.app.state.flyai = state
        assert _run(ra.mcp_list(areq))["items"] == []
        body = ra.McpServerBody(
            server_id="demo", command=sys.executable,
            args=[], env={}, enabled=True,
        )
        created = _run(ra.mcp_create(body, areq))
        assert created["item"]["server_id"] == "demo"
        assert _run(ra.mcp_delete("demo", areq))["ok"] is True
        with pytest.raises(Exception) as exc:
            _run(ra.mcp_delete("ghost", areq))
        assert exc.value.status_code == 404

    def test_mcp_create_bad_id_rejected(self):
        with pytest.raises(Exception):
            ra.McpServerBody(server_id="Bad Id", command="x")

    def test_prompt_routes(self, tmp_path):
        state, _admin, user_sc = self._env(tmp_path)
        areq = self._req("admin")
        areq.app.state.flyai = state
        items = _run(ra.prompt_list(areq))["items"]
        assert len(items) == 7 and all(x["override"] == "" for x in items)

        _run(ra.prompt_set("compact", ra.PromptBody(text="自定义"), areq))
        items = _run(ra.prompt_list(areq))["items"]
        compact = next(x for x in items if x["key"] == "compact")
        assert compact["override"] == "自定义"
        reset = _run(ra.prompt_reset("compact", areq))
        assert reset["reset"] is True

        with pytest.raises(Exception) as exc:
            _run(ra.prompt_set("nope", ra.PromptBody(text="x"), areq))
        assert exc.value.status_code == 404

    def test_watcher_stop_admin_proxy(self, tmp_path):
        state, admin_sc, _ = self._env(tmp_path)
        areq = self._req("admin")
        areq.app.state.flyai = state
        out = _run(ra.watcher_stop(ra.WatcherStopBody(id=3, uid=None), areq))
        assert out["item"]["id"] == 3 and out["item"]["status"] == "stopped"
        rec = _run(ra.watcher_reconcile(areq))
        assert rec == {"ok": True, "stopped": 0}
