# -*- coding: utf-8 -*-
"""P2 长跑模式：纯逻辑、DAL 状态机、InterruptHub、引擎四保护、工具授权、HTTP 路由。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.interrupt import InterruptHub
from flyai.agent.loop import _IDLE_FINISH_TEXT, _REPORT_NUDGE, run_agent_loop
from flyai.agent.loopmode import (
    LoopContext,
    compact_cut_point,
    estimate_tokens,
    history_char_size,
    parse_context_overflow,
    render_head_for_summary,
    render_interrupt_notice,
    should_compact,
)
from flyai.agentdb import loop_store, open_agent_db
from flyai.agentdb.loop_store import (
    LOOP_DONE,
    LOOP_IDLE,
    LOOP_INTERRUPTED,
    LOOP_RUNNING,
    LOOP_STOPPED,
    REQ_APPROVED,
    REQ_PENDING,
    REQ_REJECTED,
)
from flyai.agent.loopmode import LoopManager
from flyai.config import AppConfig, LoopSettings, ToolSettings
from flyai.feature_prefs import allowed_tool_names
from flyai.providers.base import StreamEvent, StreamEventType
from flyai.schemas import Message, ToolCall
from flyai.tools.base import Tool, ToolContext, ToolResult
from flyai.tools.loop_tools import (
    LOOP_TOOLS,
    LoopFinishTool,
    LoopRequestTool,
    LoopStartTool,
)
from flyai.tools.registry import ToolRegistry


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


def _ls(**kw):
    return LoopSettings(**kw)


def _ctx_obj(loop_id=1):
    return LoopContext(task="长任务", loop_db_id=loop_id)


# ---------- 纯逻辑 ----------

def test_history_size_and_token_estimate():
    history = [
        Message(role="user", content="甲" * 100),
        Message(role="assistant", content=None,
                tool_calls=[ToolCall(id="c1", name="t", arguments={"k": "v" * 50})]),
    ]
    size = history_char_size(history)
    assert size >= 150
    assert estimate_tokens(size) >= 1
    assert estimate_tokens(0) == 1


def test_parse_context_overflow_variants():
    assert parse_context_overflow(Exception("context length 8192 tokens exceeded")) == 8192
    assert parse_context_overflow(RuntimeError("上下文长度超出，上限 32768")) == 32768
    # 命中文案但无数字 → 0（触发压缩但不学习窗口）
    assert parse_context_overflow(Exception("context window too long")) == 0
    # 不相关异常 → None
    assert parse_context_overflow(ValueError("bad request")) is None


def test_should_compact_thresholds():
    ctx = _ctx_obj()
    # 配置校验把字符阈值夹到 ≥1000
    settings = _ls(compact_char_threshold=1000, context_window=1000,
                   compact_token_ratio=0.8)
    assert should_compact([Message(role="user", content="x" * 1000)], ctx, settings)
    # 字符不够、无窗口信息 → 不压缩
    small = [Message(role="user", content="x" * 10)]
    ctx2 = _ctx_obj()
    assert not should_compact(small, ctx2, settings)
    # 学到窗口后按 token 比例触发（预算 = 1000-30000 <0 → 不触发，边界保护）
    ctx3 = _ctx_obj()
    ctx3.context_limit = 100000
    assert should_compact(small, ctx3, settings) is False
    ctx4 = _ctx_obj()
    ctx4.context_limit = 100000
    big = [Message(role="user", content="x" * 60000)]
    assert should_compact(big, ctx4, settings) is True


def test_compact_cut_point_keeps_recent_tool_rounds():
    history = [Message(role="system", content="s")]
    for i in range(6):
        history.append(Message(
            role="assistant",
            tool_calls=[ToolCall(id="c%d" % i, name="t", arguments={})],
        ))
        history.append(Message(role="tool", tool_call_id="c%d" % i,
                               name="t", content="r%d" % i))
    history.append(Message(role="assistant", content="终稿"))
    cut = compact_cut_point(history, keep_rounds=4)
    # 切点必须是 assistant 边界
    assert history[cut].role == "assistant" and history[cut].tool_calls
    tail = history[cut:]
    tail_call_ids = {tc.id for m in tail if m.tool_calls for tc in m.tool_calls}
    # 恰好保留最近 4 个工具轮（c2..c5），c0/c1 进摘要头部
    assert tail_call_ids == {"c2", "c3", "c4", "c5"}
    # 工具轮不足 → 回退保留最旧一轮（切点在该轮 assistant）
    sparse = [Message(role="user", content="u"),
              Message(role="assistant",
                      tool_calls=[ToolCall(id="z1", name="t", arguments={})]),
              Message(role="tool", tool_call_id="z1", name="t", content="r")]
    assert compact_cut_point(sparse, 4) == 1
    # 完全没有工具轮 → 无可压缩
    assert compact_cut_point([Message(role="user", content="x")], 4) == 1


def test_render_helpers():
    text = render_head_for_summary(
        [Message(role="user", content="你好"),
         Message(role="assistant", tool_calls=[
             ToolCall(id="c1", name="do_work", arguments={"x": 1})])],
        2,
    )
    assert "你好" in text and "do_work" in text
    notice = render_interrupt_notice([
        {"name": "小明", "text": "先查日志"},
        {"name": "", "text": "再重启"},
    ])
    assert "先查日志" in notice and "再重启" in notice and "小明" in notice


def test_loop_context_terminal_reason():
    ctx = _ctx_obj()
    assert ctx.terminal_reason() == LOOP_INTERRUPTED
    ctx.stop_status = LOOP_STOPPED
    assert ctx.terminal_reason() == LOOP_STOPPED
    ctx.finished = True
    assert ctx.terminal_reason() == LOOP_DONE


# ---------- DAL：申请审批 ----------

def test_dal_request_lifecycle(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    rid = loop_store.create_request(
        conn, session_id="s1", task="整理日志", reason="轮次多",
        requester_uid="u1",
    )
    row = loop_store.get_request(conn, rid)
    assert row["status"] == REQ_PENDING and row["task"] == "整理日志"
    # 重复裁决无效
    decided = loop_store.decide_request(conn, rid, True, "admin")
    assert decided["status"] == REQ_APPROVED
    assert loop_store.decide_request(conn, rid, False, "admin") is None
    # 列表过滤
    loop_store.create_request(conn, session_id="s1", task="二", reason="",
                              requester_uid="u1")
    assert len(loop_store.list_requests(conn, REQ_PENDING)) == 1
    assert len(loop_store.list_requests(conn)) == 2
    conn.close()


def test_dal_request_reject(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    rid = loop_store.create_request(
        conn, session_id="s", task="t", reason="", requester_uid="u1"
    )
    row = loop_store.decide_request(conn, rid, False, "admin")
    assert row["status"] == REQ_REJECTED and row["decided_by"] == "admin"
    conn.close()


# ---------- DAL：运行状态机 ----------

def test_dal_session_progress_finish_stop(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    sid = loop_store.create_session(conn, session_id="s1", task="t")
    assert loop_store.running_session_for(conn, "s1")["id"] == sid
    loop_store.update_progress(conn, sid, rounds=3, compacts=1, compact_note="n")
    row = loop_store.get_session(conn, sid)
    assert row["rounds"] == 3 and row["compacts"] == 1 and row["status"] == LOOP_RUNNING
    # 终态后不能再 finish（只更新仍在 running 的行）
    assert loop_store.finish_session(conn, sid, LOOP_DONE, result="成品") is True
    assert loop_store.finish_session(conn, sid, LOOP_IDLE) is False
    row = loop_store.get_session(conn, sid)
    assert row["status"] == LOOP_DONE and row["result"] == "成品"
    assert loop_store.running_session_for(conn, "s1") is None
    # 非法终态被拒
    sid2 = loop_store.create_session(conn, session_id="s2", task="t2")
    with pytest.raises(ValueError):
        loop_store.finish_session(conn, sid2, LOOP_RUNNING)
    conn.close()


def test_dal_stop_and_reconcile(tmp_path):
    conn = open_agent_db(tmp_path, "u1")
    sid = loop_store.create_session(conn, session_id="s1", task="t")
    assert loop_store.stop_session(conn, sid) is True
    assert loop_store.get_session(conn, sid)["status"] == LOOP_STOPPED
    # 已停止再停 → False
    assert loop_store.stop_session(conn, sid) is False
    # 两个遗留 running，reconcile 全部置 interrupted
    loop_store.create_session(conn, session_id="a", task="t")
    loop_store.create_session(conn, session_id="b", task="t")
    assert loop_store.reconcile_interrupted(conn) == 2
    assert loop_store.reconcile_interrupted(conn) == 0
    conn.close()


# ---------- LoopManager ----------

def test_manager_lifecycle_and_refresh(tmp_path):
    mgr = LoopManager(tmp_path, _ls())
    rid = mgr.request_create("u1", "s1", "任务", "理由")
    assert mgr.request_get("u1", rid)["status"] == REQ_PENDING
    assert mgr.decide("u1", rid, True, "admin")["status"] == REQ_APPROVED
    lid = mgr.session_create("u1", "s1", "任务", request_id=rid)
    ctx = LoopContext(task="任务", loop_db_id=lid)
    ctx.total_rounds = 2
    mgr.refresh("u1", ctx)  # 仍 running，不写 stop_status
    assert ctx.stop_status == ""
    assert mgr.running_for("u1", "s1")["id"] == lid
    assert mgr.stop("u1", lid) is True
    mgr.refresh("u1", ctx)
    assert ctx.stop_status == LOOP_STOPPED


def test_manager_reconcile_all_scans_user_dirs(tmp_path):
    mgr = LoopManager(tmp_path, _ls())
    mgr.session_create("u1", "s1", "t")
    mgr.session_create("u2", "s9", "t")
    # 空目录/坏目录不阻断
    (tmp_path / "nodb").mkdir()
    assert mgr.reconcile_all() == 2
    assert mgr.running_for("u1", "s1") is None


# ---------- InterruptHub ----------

def test_interrupt_hub_drain_only_at_safety_point():
    hub = InterruptHub(max_per_session=3)
    hub.push("s1", {"text": "a"})
    hub.push("s1", {"text": "b"})
    assert hub.pending("s1") == 2
    items = hub.drain("s1")
    assert [i["text"] for i in items] == ["a", "b"]
    # 排空后队列消失
    assert hub.pending("s1") == 0 and hub.drain("s1") == []
    # 满队丢最旧
    for ch in "abcd":
        hub.push("s2", {"text": ch})
    assert [i["text"] for i in hub.drain("s2")] == ["b", "c", "d"]
    hub.push("s3", {"text": "x"})
    hub.clear("s3")
    assert hub.pending("s3") == 0


# ---------- 引擎 fakes ----------

class _WorkTool(Tool):
    name = "do_work"
    description = "干活"
    parameters = {"type": "object", "properties": {}}

    async def run(self, arguments, ctx):
        return ToolResult(output="work done")


class _ScriptedProvider:
    """按脚本逐次返回：("call", [(name, args)]) / ("text", s) / ("raise", exc)。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # (specs, messages, kwargs)

    async def stream_chat(self, messages, tool_specs, **kwargs):
        kind, payload = self.script.pop(0)
        self.calls.append((list(tool_specs), list(messages), kwargs))
        if kind == "call":
            yield StreamEvent(
                StreamEventType.TOOL_CALLS,
                tool_calls=[
                    ToolCall(id="c%d" % len(self.calls), name=n, arguments=a)
                    for n, a in payload
                ],
            )
        elif kind == "text":
            yield StreamEvent(StreamEventType.CONTENT_DELTA, text=payload)
        else:
            raise payload


def _engine(history, provider, settings=None, services=None, *,
            max_iterations=8, extras=None, allowed=None):
    reg = ToolRegistry()
    reg.register(_WorkTool())
    if allowed is None:
        allowed = {"do_work", "loop_finish"}
    return _drain(run_agent_loop(
        history, provider, reg,
        settings or ToolSettings(enabled=True, max_iterations=max_iterations,
                                 timeout_seconds=30, allowed_read_dirs=[]),
        system_prompt="", session_id="s1",
        services=services or {}, uid="u1", is_admin=True,
        allowed_tools=allowed, extra_tools=extras,
        loop_ctx=_ctx_obj(),
    ))


def _tool_round_history():
    """一条完整旧工具轮，供压缩切点/超长重试使用。"""
    return [
        Message(role="user", content="开工"),
        Message(role="assistant",
                tool_calls=[ToolCall(id="old1", name="do_work", arguments={})]),
        Message(role="tool", tool_call_id="old1", name="do_work",
                content="旧结果"),
        Message(role="user", content="继续"),
    ]


# ---------- 引擎：预算穿透 ----------

def test_engine_loop_bypasses_budget_caps():
    provider = _ScriptedProvider([
        ("call", [("do_work", {})]),
        ("call", [("do_work", {})]),
        ("call", [("loop_finish", {"summary": "全部完成"})]),
    ])
    settings = ToolSettings(enabled=True, max_iterations=1,
                            timeout_seconds=30, allowed_read_dirs=[])
    events = _engine([Message(role="user", content="长跑")], provider,
                     settings=settings, extras=[LoopFinishTool()])
    done = [d for ev, d in events if ev == "done"]
    assert done and done[-1]["reason"] == "loop_finish"
    # 三轮工具都执行了（非长跑模式第一轮后就会被 max_iterations 拦）
    assert len([e for e in events if e[0] == "tool_end"]) == 3


def test_engine_degrades_without_hooks():
    provider = _ScriptedProvider([
        ("call", [("loop_finish", {"summary": "ok"})]),
    ])
    events = _engine([Message(role="user", content="x")], provider,
                     services={}, extras=[LoopFinishTool()])
    assert any(ev == "done" and d["reason"] == "loop_finish"
               for ev, d in events)


# ---------- 引擎：强制汇报 ----------

def test_engine_forced_report_uses_no_tools_and_skips_history():
    provider = _ScriptedProvider([
        ("call", [("do_work", {})]),
        ("call", [("do_work", {})]),
        ("text", "例行汇报：进展顺利"),
        ("call", [("loop_finish", {"summary": "收工"})]),
    ])
    services = {"loop_settings": _ls(every_rounds=2)}
    events = _engine([Message(role="user", content="x")], provider,
                     services=services, extras=[LoopFinishTool()])
    # 第 3 轮开头先做一次无工具补问：共 4 次 provider 调用
    assert len(provider.calls) == 4
    report_specs, report_msgs, _ = provider.calls[2]
    assert report_specs == []
    assert any(_REPORT_NUDGE in (m.content or "") for m in report_msgs)
    assert any(ev == "loop_report" for ev, _d in events)
    # 最终仍正常收尾（临时指令不入库由下一个用例校验）
    assert any(ev == "done" and d["reason"] == "loop_finish"
               for ev, d in events)


def test_engine_report_content_landed_in_history():
    provider = _ScriptedProvider([
        ("call", [("do_work", {})]),
        ("call", [("do_work", {})]),
        ("text", "例行汇报：进展顺利"),
        ("call", [("loop_finish", {"summary": "收工"})]),
    ])
    history = [Message(role="user", content="x")]
    services = {"loop_settings": _ls(every_rounds=2)}
    _engine(history, provider, services=services, extras=[LoopFinishTool()])
    # 汇报正文在历史，临时 nudge 不在
    assert any(m.role == "assistant" and "例行汇报" in (m.content or "")
               for m in history)
    assert all(_REPORT_NUDGE not in (m.content or "") for m in history)


# ---------- 引擎：空转自停 ----------

def test_engine_idle_stop_after_limit():
    provider = _ScriptedProvider([("text", "嗯"), ("text", "好了")])
    services = {"loop_settings": _ls(idle_turn_limit=2)}
    events = _engine([Message(role="user", content="x")], provider,
                     services=services)
    done = [d for ev, d in events if ev == "done"]
    assert done and done[-1]["reason"] == "idle"
    assert done[-1]["finish_text"] == _IDLE_FINISH_TEXT
    assert len(provider.calls) == 2  # 第一次空转引导后又试了一轮


def test_engine_idle_recovers_with_tool_then_finish():
    provider = _ScriptedProvider([
        ("text", "让我想想"),
        ("call", [("loop_finish", {"summary": "完成"})]),
    ])
    services = {"loop_settings": _ls(idle_turn_limit=2)}
    events = _engine([Message(role="user", content="x")], provider,
                     services=services, extras=[LoopFinishTool()])
    assert any(ev == "done" and d["reason"] == "loop_finish"
               for ev, d in events)


# ---------- 引擎：滚动压缩 ----------

def test_engine_threshold_compact_rebuilds_history():
    history = _tool_round_history()
    history[0].content = "开工" + ("字" * 1200)  # 撑过字符阈值（下限 1000）
    provider = _ScriptedProvider([
        ("call", [("loop_finish", {"summary": "完工"})]),
    ])

    async def compact_hook(head_text):
        assert "开工" in head_text
        return "纪要-甲"

    services = {
        "loop_settings": _ls(compact_char_threshold=1000, keep_rounds=4),
        "loop_compact": compact_hook,
    }
    events = _engine(history, provider, services=services,
                     extras=[LoopFinishTool()])
    assert any(ev == "loop_compact" and d["reason"] == "threshold"
               for ev, d in events)
    assert "纪要-甲" in (history[0].content or "")
    # 尾部工作轮仍在
    assert any(m.role == "tool" and m.content == "旧结果" for m in history)


def test_engine_overflow_error_triggers_compact_and_retry():
    history = _tool_round_history()
    provider = _ScriptedProvider([
        ("raise", RuntimeError("context length 8192 tokens exceeded")),
        ("text", "重试成功"),
    ])
    services = {
        "loop_settings": _ls(idle_turn_limit=1, compact_char_threshold=999999),
        "loop_compact": lambda text: _async_note("纪要-乙"),
    }
    events = _engine(history, provider, services=services)
    assert any(ev == "loop_compact" and d["reason"] == "overflow"
               for ev, d in events)
    assert any(ev == "done" and d["reason"] == "idle" for ev, d in events)
    # 从报错学到窗口
    assert len(provider.calls) == 2


async def _async_note(text):
    return text


def test_engine_non_overflow_exception_propagates():
    provider = _ScriptedProvider([("raise", ValueError("400 bad"))])
    with pytest.raises(ValueError):
        _engine([Message(role="user", content="x")], provider,
                services={"loop_settings": _ls()})


# ---------- 引擎：安全点打断与干净停止 ----------

def test_engine_interrupt_drained_at_safety_point():
    hub = InterruptHub()
    hub.push("s1", {"text": "先别画图", "name": "小明"})
    provider = _ScriptedProvider([("text", "收到")])
    services = {
        "loop_settings": _ls(idle_turn_limit=1),
        "interrupt_hub": hub,
    }
    events = _engine([Message(role="user", content="x")], provider,
                     services=services)
    assert any(ev == "loop_interrupt" and "先别画图" in d["text"]
               for ev, d in events)
    # 用户原话进历史，系统通知只在请求里
    assert any(m.role == "user" and "先别画图" in (m.content or "")
               for m in [mm for _, msgs, _ in provider.calls for mm in msgs])
    assert hub.pending("s1") == 0


def test_engine_clean_stop_polls_db_state():
    provider = _ScriptedProvider([
        ("call", [("do_work", {})]),
        ("text", "不会跑到这里"),
    ])
    ctx = _ctx_obj()
    ticks = {"n": 0}

    async def state_hook(c):
        ticks["n"] += 1
        if ticks["n"] == 2:
            c.stop_status = LOOP_STOPPED

    reg = ToolRegistry()
    reg.register(_WorkTool())
    events = _drain(run_agent_loop(
        [Message(role="user", content="x")], provider, reg,
        ToolSettings(enabled=True, max_iterations=8, timeout_seconds=30,
                     allowed_read_dirs=[]),
        system_prompt="", session_id="s1",
        services={"loop_settings": _ls(), "loop_state": state_hook},
        uid="u1", is_admin=True,
        allowed_tools={"do_work"}, loop_ctx=ctx,
    ))
    done = [d for ev, d in events if ev == "done"]
    assert done and done[-1]["reason"] == LOOP_STOPPED
    assert len(provider.calls) == 1  # 第二轮安全点退出，未再请求模型


# ---------- 工具：授权与服务 ----------

def test_loop_request_tool_writes_pending_row(tmp_path):
    mgr = LoopManager(tmp_path, _ls(enabled=True))
    ctx = ToolContext(uid="u1", session_id="s1", services={
        "loop_manager": mgr, "loop_settings": SimpleNamespace(enabled=True),
    })
    res = _run(LoopRequestTool().run({"task": "整理一周日志", "reason": "长"}, ctx))
    assert res.ok and "#1" in res.output
    row = mgr.request_get("u1", 1)
    assert row["task"] == "整理一周日志" and row["status"] == REQ_PENDING
    # 未启用 → 失败但不抛
    ctx2 = ToolContext(services={"loop_settings": SimpleNamespace(enabled=False)})
    res2 = _run(LoopRequestTool().run({"task": "t"}, ctx2))
    assert not res2.ok
    # 空 task
    ctx3 = ToolContext(services={
        "loop_manager": mgr, "loop_settings": SimpleNamespace(enabled=True)})
    assert not _run(LoopRequestTool().run({"task": "  "}, ctx3)).ok


def test_loop_request_registry_gate_for_non_admin(tmp_path):
    mgr = LoopManager(tmp_path, _ls(enabled=True, request_min_level=0))
    reg = ToolRegistry()
    tool = LoopRequestTool()
    tool.min_level = 0
    reg.register(tool)
    ctx = ToolContext(uid="u1", level=0, is_admin=False, services={
        "loop_manager": mgr, "loop_settings": SimpleNamespace(enabled=True)})
    res = _run(reg.execute(
        ToolCall(id="c1", name="loop_request",
                 arguments={"task": "t", "reason": ""}), ctx))
    assert not res.ok and "权限" in res.output
    # 等级达标放行
    tool.min_level = 10
    ctx10 = ToolContext(uid="u2", level=10, is_admin=False, services={
        "loop_manager": mgr, "loop_settings": SimpleNamespace(enabled=True)})
    res10 = _run(reg.execute(
        ToolCall(id="c2", name="loop_request", arguments={"task": "t"}), ctx10))
    assert res10.ok


def test_loop_start_tool_uses_starter_service():
    async def starter(uid, sid, task, reason=""):
        return {"ok": True, "loop_id": 7, "run_id": "r1"}

    ctx = ToolContext(uid="admin", session_id="s1", is_admin=True, services={
        "loop_settings": SimpleNamespace(enabled=True),
        "loop_starter": starter,
    })
    res = _run(LoopStartTool().run({"task": "扫日志"}, ctx))
    assert res.ok and "loop_id=7" in res.output
    # 缺 starter
    ctx2 = ToolContext(services={
        "loop_settings": SimpleNamespace(enabled=True)})
    assert not _run(LoopStartTool().run({"task": "t"}, ctx2)).ok


def test_loop_finish_tool_echoes_summary():
    res = _run(LoopFinishTool().run({"summary": "结论如下"}, ToolContext()))
    assert res.ok and res.output == "结论如下"
    assert not _run(LoopFinishTool().run({"summary": " "}, ToolContext())).ok
    # 不在全局工具集里
    assert "loop_finish" not in {c.__name__ for c in LOOP_TOOLS}


# ---------- feature_prefs ----------

def test_feature_prefs_loop_category():
    cfg = AppConfig()
    reg = ToolRegistry()
    reg.register(LoopRequestTool())
    reg.register(LoopStartTool())
    # 默认关闭
    names = allowed_tool_names(reg, cfg, None,
                               extra_names={"loop_finish", "evil"})
    assert "loop_request" not in names and "loop_finish" not in names
    # 开启后全局工具可见，extra 仅精确放行 loop_finish
    cfg.loop.enabled = True
    names = allowed_tool_names(reg, cfg, None,
                               extra_names={"loop_finish", "evil"})
    assert {"loop_request", "loop_start", "loop_finish"} <= names
    assert "evil" not in names


# ---------- HTTP 路由（直接调路由函数 + 简易 state/request）----------

class _FakeStore:
    def __init__(self, sessions):
        self._sessions = sessions

    def get(self, sid):
        return self._sessions.get(sid)


def _http_env(tmp_path, *, enabled=True):
    from flyai.api import routes_loop

    mgr = LoopManager(tmp_path, _ls(enabled=enabled))
    hub = InterruptHub()
    admin_sc = SimpleNamespace(
        uid="admin", store=_FakeStore({"s1": object(), "s2": object()}))
    user_sc = SimpleNamespace(uid="u1", store=_FakeStore({"s1": object()}))
    state = SimpleNamespace(
        config=SimpleNamespace(loop=SimpleNamespace(enabled=enabled)),
        loop_manager=mgr,
        interrupt_hub=hub,
        user_store=SimpleNamespace(get_by_id=lambda uid: None),
        _scopes={"admin": admin_sc, "u1": user_sc},
    )

    def user(request):
        return state._scopes[request.uid]

    state.user = user
    req = SimpleNamespace(uid="admin",
                          app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return routes_loop, state, req, admin_sc, user_sc, mgr, hub


def _http(fn, *args):
    return _run(fn(*args))


def test_http_disabled_returns_400(tmp_path):
    routes_loop, state, req, *_ = _http_env(tmp_path, enabled=False)
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_requests, req)
    assert ei.value.status_code == 400


def test_http_requests_list_and_decide(tmp_path):
    routes_loop, state, req, admin_sc, user_sc, mgr, hub = _http_env(tmp_path)
    rid = mgr.request_create("u1", "s1", "任务甲", "理由")
    # 普通用户不能看别人的
    req_u = SimpleNamespace(uid="u1",
                            app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_requests, req_u, None, "admin")
    assert ei.value.status_code == 403
    # 管理员列表
    out = _http(routes_loop.loop_requests, req, None, "u1")
    assert len(out["items"]) == 1 and out["items"][0]["id"] == rid
    # 驳回
    body = routes_loop.LoopDecideBody(uid="u1", approve=False)
    out = _http(routes_loop.loop_decide, rid, body, req)
    assert out["status"] == REQ_REJECTED
    # 重复裁决 409
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_decide, rid, body, req)
    assert ei.value.status_code == 409


def test_http_decide_approve_missing_session_404(tmp_path):
    routes_loop, state, req, *_ = _http_env(tmp_path)
    rid = LoopManager(tmp_path, _ls()).request_create("u1", "ghost", "t", "")
    body = routes_loop.LoopDecideBody(uid="u1", approve=True)
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_decide, rid, body, req)
    assert ei.value.status_code == 404


def test_http_start_authorization_and_validation(tmp_path):
    routes_loop, state, req, admin_sc, user_sc, mgr, hub = _http_env(tmp_path)
    # 非管理员 403
    req_u = SimpleNamespace(uid="u1",
                            app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    body = routes_loop.LoopStartBody(session_id="s1", task="t")
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_start, body, req_u)
    assert ei.value.status_code == 403
    # 空 task 400
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_start,
              routes_loop.LoopStartBody(session_id="s1", task=" "), req)
    assert ei.value.status_code == 400
    # 会话不存在 404
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_start,
              routes_loop.LoopStartBody(session_id="nope", task="t"), req)
    assert ei.value.status_code == 404


def test_http_stop_lifecycle(tmp_path):
    routes_loop, state, req, admin_sc, user_sc, mgr, hub = _http_env(tmp_path)
    lid = mgr.session_create("u1", "s1", "t")
    # 所有者可停自己的
    req_u = SimpleNamespace(uid="u1",
                            app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    out = _http(routes_loop.loop_stop, lid,
                routes_loop.LoopStopBody(uid=None), req_u)
    assert out["status"] == "stopped"
    # 再停 409
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_stop, lid, None, req_u)
    assert ei.value.status_code == 409


def test_http_interrupt_routes_to_hub(tmp_path):
    routes_loop, state, req, admin_sc, user_sc, mgr, hub = _http_env(tmp_path)
    lid = mgr.session_create("u1", "s1", "t")
    req_u = SimpleNamespace(uid="u1",
                            app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    body = routes_loop.LoopInterruptBody(session_id="s1", text="先查错误日志")
    out = _http(routes_loop.loop_interrupt, body, req_u)
    assert out["pending"] == 1 and out["loop_id"] == lid
    assert hub.drain("s1")[0]["text"] == "先查错误日志"
    # 无运行中长跑 → 409
    mgr.finish("u1", _ctx_obj(lid), LOOP_DONE, result="")
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_interrupt, body, req_u)
    assert ei.value.status_code == 409
    # 非会话成员 → 404
    other = routes_loop.LoopInterruptBody(session_id="s2", text="x")
    with pytest.raises(Exception) as ei:
        _http(routes_loop.loop_interrupt, other, req_u)
    assert ei.value.status_code == 404
    # pending 查询
    hub.push("s1", {"text": "z"})
    mgr.session_create("u1", "s1", "t2")
    out = _http(routes_loop.loop_hub_pending, "s1", req_u)
    assert out["pending"] == 1


def test_http_sessions_list_ownership(tmp_path):
    routes_loop, state, req, admin_sc, user_sc, mgr, hub = _http_env(tmp_path)
    mgr.session_create("u1", "s1", "t")
    out = _http(routes_loop.loop_sessions, req, LOOP_RUNNING, "u1")
    assert len(out["items"]) == 1
    req_u = SimpleNamespace(uid="u1",
                            app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    out = _http(routes_loop.loop_sessions, req_u, None, None)
    assert len(out["items"]) == 1
