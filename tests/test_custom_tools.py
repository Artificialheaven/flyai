# -*- coding: utf-8 -*-
"""P1 AI 自定义工具：DAL、隔离子进程、运行时生命周期、管理工具授权、
请求级 extras 注入与 agent loop 端到端。"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agentdb import custom_tools as ct
from flyai.agentdb import open_agent_db
from flyai.agentdb.custom_tools import SCOPE_USER, session_scope
from flyai.agent.loop import run_agent_loop
from flyai.config import AppConfig, CustomToolSettings, ToolSettings
from flyai.feature_prefs import allowed_tool_names
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.tools.base import ToolContext
from flyai.tools.custom_tool_manage import (
    CustomFunctionTool,
    CustomToolList,
    MANAGE_TOOLS,
)
from flyai.tools.custom_tool_runtime import CustomToolRuntime
from flyai.tools.registry import ToolRegistry

SCHEMA = '{"type":"object","properties":{"x":{"type":"string"}}}'
ECHO_CODE = (
    "def tool(args, ctx):\n"
    "    return 'echo:' + str(args.get('x', '')) + ':' + ctx['uid']\n"
)


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _drain(gen):
    async def go():
        out = []
        async for ev in gen:
            out.append(ev)
        return out

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


def _rt(tmp_path, **kw):
    kw.setdefault("enabled", True)
    kw.setdefault("validate_timeout", 5.0)
    kw.setdefault("call_timeout", 10.0)
    return CustomToolRuntime(tmp_path, CustomToolSettings(**kw))


def _ctx(uid="u1", sid="s1", is_admin=False, level=0, runtime=None):
    return ToolContext(
        uid=uid, session_id=sid, is_admin=is_admin, level=level,
        services={"custom_runtime": runtime} if runtime is not None else {},
    )


def _call(tool_name, **arguments):
    return ToolCall(id="c1", name=tool_name, arguments=arguments)


# ---------- DAL ----------

def test_dal_upsert_list_get(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    tid = ct.upsert_tool(
        conn, scope=SCOPE_USER, name="echo", title="回声",
        description="d", params_schema=SCHEMA, code=ECHO_CODE,
        owner_only=False, created_by="u1",
    )
    assert tid >= 1
    rows = ct.list_all(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "echo" and row["enabled"] is True
    assert row["owner_only"] is False and row["scope"] == "user"
    assert ct.get_by_id(conn, tid)["id"] == tid
    assert ct.count_for_scope(conn, SCOPE_USER) == 1
    conn.close()


def test_dal_republish_preserves_id_and_disabled_state(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    tid = ct.upsert_tool(
        conn, scope=SCOPE_USER, name="echo", title="", description="d",
        params_schema=SCHEMA, code=ECHO_CODE, owner_only=False, created_by="u1",
    )
    ct.set_enabled(conn, tid, False)
    # 同名重新发布：id 与 enabled 状态保留，代码更新
    new_id = ct.upsert_tool(
        conn, scope=SCOPE_USER, name="echo", title="t", description="d2",
        params_schema=SCHEMA, code="def tool(args, ctx):\n    return 'v2'\n",
        owner_only=False, created_by="u1",
    )
    assert new_id == tid
    row = ct.get_by_id(conn, tid)
    assert row["enabled"] is False and row["description"] == "d2"
    assert "v2" in row["code"]
    conn.close()


def test_dal_session_scope_overrides_user_scope(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    ct.upsert_tool(conn, scope=SCOPE_USER, name="echo", title="",
                   description="user 级", params_schema=SCHEMA,
                   code="def tool(args, ctx):\n    return 'user'\n",
                   owner_only=False, created_by="u1")
    ct.upsert_tool(conn, scope=session_scope("s1"), name="echo", title="",
                   description="会话级", params_schema=SCHEMA,
                   code="def tool(args, ctx):\n    return 'sess'\n",
                   owner_only=False, created_by="u1")
    # 同名会话级覆盖用户级：视图按名去重后只剩会话行
    s1 = ct.list_enabled_for_session(conn, "s1")
    assert len(s1) == 1
    assert s1[0]["scope"] == session_scope("s1")
    assert s1[0]["description"] == "会话级"
    # s2 看不到 s1 的会话级工具，回落到用户级
    s2 = ct.list_enabled_for_session(conn, "s2")
    assert len(s2) == 1 and s2[0]["scope"] == SCOPE_USER
    # 两行都停用后会话视图为空
    for r in ct.list_all(conn):
        ct.set_enabled(conn, r["id"], False)
    assert ct.list_enabled_for_session(conn, "s1") == []
    conn.close()


def test_dal_toggle_and_remove(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    tid = ct.upsert_tool(conn, scope=SCOPE_USER, name="echo", title="",
                         description="d", params_schema=SCHEMA, code=ECHO_CODE,
                         owner_only=False, created_by="u1")
    assert ct.set_enabled(conn, tid, False) is True
    assert ct.get_by_id(conn, tid)["enabled"] is False
    assert ct.delete_by_id(conn, tid) is True
    assert ct.get_by_id(conn, tid) is None
    assert ct.delete_by_id(conn, 999) is False
    conn.close()


# ---------- 静态校验 ----------

def test_static_check_rules(tmp_path):
    sc = CustomToolRuntime.static_check
    schema = '{"type":"object","properties":{}}'
    assert sc("BadName", "d", schema, "x", 8000)
    assert sc("1ok", "d", schema, "x", 8000)
    assert sc("ok", "  ", schema, "x", 8000)
    assert sc("ok", "d" * 501, schema, "x", 8000)
    assert sc("ok", "d", "not-json", "x", 8000)
    assert sc("ok", "d", "{}", "x", 8000)  # 缺 type=object
    assert sc("ok", "d", schema, "   ", 8000)
    assert sc("ok", "d", schema, "x" * 8001, 8000)
    assert sc("ok", "d", schema, "def tool(args, ctx)\n  pass", 8000)
    assert sc("ok", "d", schema, "x = 1\n", 8000) == ""


# ---------- 子进程 worker ----------

def test_worker_happy_call_and_ctx(tmp_path):
    rt = _rt(tmp_path)
    res = rt.run_worker(
        {"code": ECHO_CODE, "mode": "call", "args": {"x": "hi"},
         "ctx": {"uid": "u1", "session_id": "s1", "dry_run": False},
         "lib_dirs": []},
        timeout=5.0,
    )
    assert res["ok"] is True
    assert res["result"] == "echo:hi:u1"


def test_worker_exception_becomes_error_payload(tmp_path):
    rt = _rt(tmp_path)
    res = rt.run_worker(
        {"code": "def tool(args, ctx):\n    raise RuntimeError('boom')\n",
         "mode": "call", "args": {}, "ctx": {}, "lib_dirs": []},
        timeout=5.0,
    )
    assert res["ok"] is False and "RuntimeError" in res["error"]
    assert "boom" in res["error"]


def test_worker_missing_contract(tmp_path):
    rt = _rt(tmp_path)
    res = rt.run_worker(
        {"code": "x = 1\n", "mode": "call", "args": {},
         "ctx": {}, "lib_dirs": []},
        timeout=5.0,
    )
    assert res["ok"] is False and "tool" in res["error"]


def test_worker_crash_returns_crash_stage(tmp_path):
    rt = _rt(tmp_path)
    # 加载期直接退出，结果文件来不及写
    res = rt.run_worker(
        {"code": "import os\nos._exit(7)\n", "mode": "validate",
         "test_args": None, "ctx": {"dry_run": True}, "lib_dirs": []},
        timeout=5.0,
    )
    assert res["ok"] is False and res["stage"] == "crash"


def test_worker_timeout_kills_process_tree(tmp_path):
    rt = _rt(tmp_path)
    t0 = time.time()
    res = rt.run_worker(
        {"code": "import time\ndef tool(args, ctx):\n    time.sleep(8)\n",
         "mode": "call", "args": {}, "ctx": {}, "lib_dirs": []},
        timeout=1.0,
    )
    elapsed = time.time() - t0
    assert res["ok"] is False and res["stage"] == "timeout"
    assert elapsed < 6.0  # 必须真的杀掉了，而不是等满 8 秒


def test_worker_env_secrets_stripped(tmp_path, monkeypatch):
    monkeypatch.setenv("FLAI_CONFIG", "C:/secret.json")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_TOKEN", "tok")
    monkeypatch.setenv("HTTP_AUTHORIZATION", "Bearer x")
    monkeypatch.setenv("FLYAI_KEYSTORE_PASS", "pw")
    monkeypatch.setenv("KEEP_VAR", "keep")
    code = (
        "import os\n"
        "def tool(args, ctx):\n"
        "    return '|'.join('%s=%s' % (k, os.environ.get(k, '-')) "
        "for k in ('FLAI_CONFIG','OPENAI_API_KEY','MY_TOKEN',"
        "'HTTP_AUTHORIZATION','FLYAI_KEYSTORE_PASS','KEEP_VAR'))\n"
    )
    rt = _rt(tmp_path)
    res = rt.run_worker(
        {"code": code, "mode": "call", "args": {}, "ctx": {}, "lib_dirs": []},
        timeout=5.0,
    )
    assert res["ok"] is True
    out = res["result"]
    for secret in ("FLAI_CONFIG=-", "OPENAI_API_KEY=-", "MY_TOKEN=-",
                   "HTTP_AUTHORIZATION=-", "FLYAI_KEYSTORE_PASS=-"):
        assert secret in out
    assert "KEEP_VAR=keep" in out


# ---------- Runtime 高层操作 ----------

def test_validate_happy_and_failure(tmp_path):
    rt = _rt(tmp_path)
    ok = _run(rt.validate("u1", "s1", {
        "name": "echo", "description": "回声", "params_schema": SCHEMA,
        "code": ECHO_CODE, "test_args": '{"x":"hi"}',
    }))
    assert ok.startswith("✅") and "echo:hi:u1" in ok

    bad = _run(rt.validate("u1", "s1", {
        "name": "boom", "description": "炸", "params_schema": SCHEMA,
        "code": "def tool(args, ctx):\n    return 1/0\n",
        "test_args": "{}",
    }))
    assert bad.startswith("❌") and "ZeroDivisionError" in bad

    bad_args = _run(rt.validate("u1", "s1", {
        "name": "echo", "description": "回声", "params_schema": SCHEMA,
        "code": ECHO_CODE, "test_args": "{not json",
    }))
    assert bad_args.startswith("❌") and "test_args" in bad_args


def test_publish_call_list_toggle_remove_lifecycle(tmp_path):
    rt = _rt(tmp_path)
    pub = _run(rt.publish("u1", "s1", {
        "name": "echo", "title": "回声", "description": "回声工具",
        "params_schema": SCHEMA, "code": ECHO_CODE,
    }))
    assert pub.startswith("✅") and "custom__echo" in pub and "id=1" in pub

    tools = rt.tools_for("u1", "s1")
    assert len(tools) == 1 and tools[0].name == "custom__echo"
    out = _run(tools[0].run({"x": "hi"}, _ctx("u1", "s1", runtime=rt)))
    assert out.ok and out.output == "echo:hi:u1"

    listing = _run(rt.list_text("u1", "s1"))
    assert "custom__echo" in listing and "启用" in listing

    toggled = _run(rt.toggle("u1", {"id": 1, "enabled": False}))
    assert "停用" in toggled and rt.tools_for("u1", "s1") == []

    removed = _run(rt.remove("u1", {"id": 1}))
    assert "删除" in removed
    assert _run(rt.list_text("u1", "s1")).startswith("当前没有自定义工具")


def test_publish_cap_and_bad_id_handling(tmp_path):
    rt = _rt(tmp_path, max_per_scope=1)
    r1 = _run(rt.publish("u1", "s1", {
        "name": "one", "description": "d1", "params_schema": SCHEMA,
        "code": "def tool(args, ctx):\n    return '1'\n",
    }))
    assert r1.startswith("✅")
    r2 = _run(rt.publish("u1", "s1", {
        "name": "two", "description": "d2", "params_schema": SCHEMA,
        "code": "def tool(args, ctx):\n    return '2'\n",
    }))
    assert r2.startswith("❌") and "上限" in r2
    # 同名更新不占新名额
    r3 = _run(rt.publish("u1", "s1", {
        "name": "one", "description": "d1v2", "params_schema": SCHEMA,
        "code": "def tool(args, ctx):\n    return '1v2'\n",
    }))
    assert r3.startswith("✅")
    assert _run(rt.toggle("u1", {"id": "x", "enabled": True})).startswith("❌")
    assert _run(rt.remove("u1", {"id": 99})).startswith("❌")


def test_failed_republish_keeps_old_version(tmp_path):
    rt = _rt(tmp_path)
    _run(rt.publish("u1", "s1", {
        "name": "echo", "description": "d", "params_schema": SCHEMA,
        "code": ECHO_CODE,
    }))
    # 同名发布能过语法编译、但加载期抛异常的代码：子进程验证失败，
    # 走「加载验证失败」分支，库里旧版原样保留且仍可调用
    bad = _run(rt.publish("u1", "s1", {
        "name": "echo", "description": "d", "params_schema": SCHEMA,
        "code": "x = 1 / 0\n\ndef tool(args, ctx):\n    return 'boom'\n",
    }))
    assert bad.startswith("❌") and "旧版原样保留" in bad
    tools = rt.tools_for("u1", "s1")
    assert len(tools) == 1
    out = _run(tools[0].run({"x": "keep"}, _ctx("u1", runtime=rt)))
    assert out.ok and out.output == "echo:keep:u1"


def test_session_scope_and_user_isolation(tmp_path):
    rt = _rt(tmp_path)
    r = _run(rt.publish("u1", "s1", {
        "name": "local", "scope": "session", "description": "仅本会话",
        "params_schema": SCHEMA,
        "code": "def tool(args, ctx):\n    return 'sess:' + ctx['session_id']\n",
    }))
    assert r.startswith("✅") and "仅本会话" in r
    assert len(rt.tools_for("u1", "s1")) == 1
    assert rt.tools_for("u1", "s2") == []
    # 每用户独立 agent.db：u2 完全看不到 u1 的工具
    assert rt.tools_for("u2", "s1") == []


def test_session_scope_requires_sid(tmp_path):
    rt = _rt(tmp_path)
    r = _run(rt.publish("u1", "", {
        "name": "local", "scope": "session", "description": "d",
        "params_schema": SCHEMA, "code": ECHO_CODE,
    }))
    assert r.startswith("❌") and "session_id" in r


def test_owner_only_sets_risk_and_admin_gate(tmp_path):
    rt = _rt(tmp_path)
    _run(rt.publish("u1", "s1", {
        "name": "secret", "description": "管理员专属", "params_schema": SCHEMA,
        "code": "def tool(args, ctx):\n    return 'topsecret'\n",
        "owner_only": True,
    }))
    tool = rt.tools_for("u1", "s1")[0]
    assert tool.risk is True and tool.min_level == 0
    reg = ToolRegistry()
    denied = _run(reg.execute(
        _call("custom__secret"), _ctx("u1", level=99, runtime=rt),
        extras=[tool],
    ))
    assert not denied.ok and "权限不足" in denied.output
    allowed = _run(reg.execute(
        _call("custom__secret"), _ctx("u1", is_admin=True, runtime=rt),
        extras=[tool],
    ))
    assert allowed.ok and allowed.output == "topsecret"


def test_disabled_runtime_yields_no_tools(tmp_path):
    rt = _rt(tmp_path, enabled=False)
    conn = open_agent_db(tmp_path, "u1")
    ct.upsert_tool(conn, scope=SCOPE_USER, name="echo", title="",
                   description="d", params_schema=SCHEMA, code=ECHO_CODE,
                   owner_only=False, created_by="u1")
    conn.close()
    assert rt.tools_for("u1", "s1") == []


# ---------- 管理工具（注册表执行期授权） ----------

def test_management_tools_admin_gate_and_disabled_runtime(tmp_path):
    rt = _rt(tmp_path)
    reg = ToolRegistry()
    for cls in MANAGE_TOOLS:
        reg.register(cls())
    # 普通用户：risk 门禁直接拦截，run() 不会执行
    denied = _run(reg.execute(
        _call("custom_tool_list"), _ctx("u1", level=10, runtime=rt)
    ))
    assert not denied.ok and "权限不足" in denied.output
    # 管理员：可列出（空库提示）
    ok = _run(reg.execute(
        _call("custom_tool_list"), _ctx("admin", is_admin=True, runtime=rt)
    ))
    assert ok.ok and "当前没有自定义工具" in ok.output
    # 管理员通过管理工具完整发布一次
    pub = _run(reg.execute(
        _call("custom_tool_publish", name="echo", description="回声",
              params_schema=SCHEMA, code=ECHO_CODE),
        _ctx("admin", is_admin=True, runtime=rt),
    ))
    assert pub.ok and "已上线" in pub.output
    # 运行时未启用：管理工具返回失败而不是抛异常
    rt.settings.enabled = False
    off = _run(reg.execute(
        _call("custom_tool_list"), _ctx("admin", is_admin=True, runtime=rt)
    ))
    assert not off.ok and "未启用" in off.output


# ---------- Registry extras 语义 ----------

def test_registry_specs_and_execute_with_extras(tmp_path):
    rt = _rt(tmp_path)
    _run(rt.publish("u1", "s1", {
        "name": "echo", "description": "回声", "params_schema": SCHEMA,
        "code": ECHO_CODE,
    }))
    extra = rt.tools_for("u1", "s1")[0]
    reg = ToolRegistry()
    # 白名单外不暴露
    assert reg.specs(set(), extras=[extra]) == []
    specs = reg.specs({"custom__echo"}, extras=[extra])
    assert len(specs) == 1
    assert specs[0]["function"]["name"] == "custom__echo"
    assert specs[0]["function"]["description"].startswith("[自定义工具]")
    # execute 能解析 extras 中的工具
    out = _run(reg.execute(
        _call("custom__echo", x="z"), _ctx("u1", runtime=rt), extras=[extra]
    ))
    assert out.ok and out.output == "echo:z:u1"
    # 未知名仍失败
    unknown = _run(reg.execute(
        _call("custom__nope"), _ctx("u1", runtime=rt), extras=[extra]
    ))
    assert not unknown.ok and "未知工具" in unknown.output


# ---------- feature_prefs 集成 ----------

def test_feature_prefs_custom_tools_category(tmp_path):
    cfg = AppConfig()
    reg = ToolRegistry()
    reg.register(CustomToolList())
    # 默认关闭：管理工具与 custom__* 都不在白名单
    names = allowed_tool_names(reg, cfg, None,
                               extra_names={"custom__x", "evil"})
    assert "custom_tool_list" not in names
    assert "custom__x" not in names and "evil" not in names
    # 开启后：管理工具可见，custom__ 前缀放行，非法前缀不收
    cfg.custom_tools.enabled = True
    names = allowed_tool_names(reg, cfg, None,
                               extra_names={"custom__x", "evil"})
    assert "custom_tool_list" in names
    assert "custom__x" in names and "evil" not in names
    # disabled 只做收窄
    names = allowed_tool_names(
        reg, cfg, None, extra_names={"custom__x"},
        disabled={"custom__x"},
    )
    assert "custom__x" not in names
    # 工具总开关关闭 → 空集
    cfg.tools.enabled = False
    assert allowed_tool_names(reg, cfg, None,
                              extra_names={"custom__x"}) == set()


# ---------- agent loop 端到端 ----------

class _ScriptedProvider:
    """第一轮发起 custom__echo 工具调用，第二轮给最终回复。"""

    def __init__(self, tool_name):
        self.calls = 0
        self.specs_seen = None
        self._tool_name = tool_name

    async def stream_chat(self, messages, tool_specs, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.specs_seen = tool_specs
            yield StreamEvent(
                StreamEventType.TOOL_CALLS,
                tool_calls=[ToolCall(id="c1", name=self._tool_name,
                                     arguments={"x": "hi"})],
            )
        else:
            yield StreamEvent(StreamEventType.CONTENT_DELTA, text="完成")


def test_loop_extra_tools_end_to_end(tmp_path):
    rt = _rt(tmp_path)
    _run(rt.publish("u1", "s1", {
        "name": "echo", "description": "回声", "params_schema": SCHEMA,
        "code": ECHO_CODE,
    }))
    extras = rt.tools_for("u1", "s1")
    cfg = AppConfig()
    cfg.custom_tools.enabled = True
    reg = ToolRegistry()
    allowed = allowed_tool_names(reg, cfg, None,
                                 extra_names={t.name for t in extras})
    provider = _ScriptedProvider("custom__echo")
    settings = ToolSettings(enabled=True, max_iterations=5,
                            timeout_seconds=30, allowed_read_dirs=[])
    history = [Message(role="user", content="调用回声工具")]
    events = _drain(run_agent_loop(
        history, provider, reg, settings,
        system_prompt="", session_id="s1",
        services={"custom_runtime": rt},
        uid="u1", is_admin=True,
        allowed_tools=allowed, extra_tools=extras,
    ))
    # 模型第一轮确实看到了自定义工具 spec
    seen = {s["function"]["name"] for s in provider.specs_seen}
    assert "custom__echo" in seen
    # 工具真的在隔离子进程里跑通，结果回灌进历史
    tool_ends = [d for ev, d in events if ev == "tool_end"]
    assert tool_ends and tool_ends[0]["ok"] is True
    tool_msgs = [m for m in history if m.role == "tool"]
    assert tool_msgs and "echo:hi:u1" in (tool_msgs[0].content or "")
    assert history[-1].role == "assistant" and history[-1].content == "完成"


def test_loop_extras_hidden_when_tools_disabled(tmp_path):
    rt = _rt(tmp_path)
    _run(rt.publish("u1", "s1", {
        "name": "echo", "description": "回声", "params_schema": SCHEMA,
        "code": ECHO_CODE,
    }))
    extras = rt.tools_for("u1", "s1")
    provider = _ScriptedProvider("custom__echo")
    settings = ToolSettings(enabled=False, allowed_read_dirs=[])
    _drain(run_agent_loop(
        [Message(role="user", content="x")], provider, ToolRegistry(),
        settings, system_prompt="", session_id="s1",
        services={"custom_runtime": rt}, uid="u1",
        extra_tools=extras,
    ))
    # 工具总开关关闭：specs 整体为空，模型看不到任何工具
    assert provider.specs_seen == []


# ---------- 配置兼容 ----------

def test_legacy_config_without_section_uses_safe_defaults():
    cfg = AppConfig.parse_obj({})
    assert cfg.custom_tools.enabled is False
    assert cfg.custom_tools.code_max_chars == 8000
    assert cfg.custom_tools.max_per_scope == 20
    cfg2 = AppConfig.parse_obj({"custom_tools": {"enabled": True}})
    assert cfg2.custom_tools.enabled is True
    assert cfg2.custom_tools.call_timeout == 20.0


def test_custom_tool_settings_clamps():
    s = CustomToolSettings(code_max_chars=0, max_per_scope=-5,
                           validate_timeout=99999, call_timeout=0.2)
    assert s.code_max_chars >= 1 and s.max_per_scope >= 1
    assert s.validate_timeout == 300.0 and s.call_timeout == 1.0
