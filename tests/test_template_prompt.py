"""EJS 提示词侧 prepare/commit 桥：无模板零开销、超时中止、变量校验。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.agent.runs import ChatRun
from flyai.schemas import Message
from flyai.template.prompt import (
    PromptTemplateBridge,
    TemplatePromptError,
    cell_id,
)
from flyai.template.store import TemplateVarStore


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _session(messages=None, local=None):
    return SimpleNamespace(
        messages=list(messages or []),
        template_vars={"local": dict(local or {}), "message": {}},
        mvu={"stat_data": {"mp": 2}},
    )


def _bridge(tmp_path, session, run=None, **kwargs):
    return PromptTemplateBridge(
        run or ChatRun("r1", "u1", "s1"),
        session,
        TemplateVarStore(tmp_path / "template_vars.json"),
        uid="u1",
        history=session.messages,
        **kwargs
    )


def _src(text, role="system"):
    """提示词骨架里来自卡/预设的文本：允许执行 EJS。"""
    return Message(role=role, content=text, template_source=True)


async def _wait_event(run, name, timeout=1.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        for event, data in run.events:
            if event == name:
                return data
        await asyncio.sleep(0.01)
    raise AssertionError("未收到事件 %s" % name)


def _commit(run, payload, cells, ops=None, version=None, errors=None):
    assert run.resolve_template(payload["prepare_id"], {
        "cells": cells,
        "ops": ops or [],
        "errors": errors or [],
        "context_version": (
            payload["context_version"] if version is None else version
        ),
    })


def test_no_template_emits_nothing(tmp_path):
    session = _session([Message(role="user", content="普通聊天")])
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    messages = [_src("系统")]
    out = _run(bridge.apply(messages))
    assert out == messages
    assert run.events == []


def test_prepare_commit_splices_and_applies_ops(tmp_path):
    session = _session(local={"hp": 1})
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    text = "HP=<%= getvar('hp', {defaults: 3}) %>"
    messages = [_src(text)]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(messages))
        payload = await _wait_event(run, "template_prepare")
        assert payload["run_id"] == "r1"
        assert payload["context"]["escapeOutput"] is False
        assert payload["context"]["readonly"]["stat_data"] == {"mp": 2}
        assert [c["id"] for c in payload["cells"]] == [cell_id(text)]
        _commit(
            run, payload, {cell_id(text): "HP=4"},
            ops=[{"op": "vars.set", "scope": "local", "path": "hp", "value": 4}],
        )
        return await task

    out = _run(scenario())
    assert out[0].content == "HP=4"
    bridge.finalize()
    assert session.template_vars["local"]["hp"] == 4


def test_global_ops_persist_on_finalize(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    store = TemplateVarStore(tmp_path / "template_vars.json")
    bridge = PromptTemplateBridge(
        run, session, store, uid="u1", history=session.messages)
    text = "<% setvar('favor', 2, {scope: 'global'}) %>好"
    messages = [_src(text)]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(messages))
        payload = await _wait_event(run, "template_prepare")
        _commit(run, payload, {cell_id(text): "好"}, ops=[
            {"op": "vars.set", "scope": "global", "path": "stats.favor",
             "value": 2},
        ])
        return await task

    out = _run(scenario())
    assert out[0].content == "好"
    assert store.load() == {}
    bridge.finalize()
    assert store.load()["stats"]["favor"] == 2


def test_timeout_aborts_without_leaking_source(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run, timeout=0.2)
    messages = [_src("<%= 1 + 1 %>")]
    with pytest.raises(TemplatePromptError) as exc:
        _run(bridge.apply(messages))
    assert "超时" in str(exc.value)


def test_context_version_mismatch_aborts(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    text = "<%= 1 %>"
    messages = [_src(text)]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(messages))
        payload = await _wait_event(run, "template_prepare")
        _commit(run, payload, {cell_id(text): "1"}, version="stale")
        return await task

    with pytest.raises(TemplatePromptError):
        _run(scenario())


def test_client_errors_abort(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    text = "<%= 1 %>"
    messages = [_src(text)]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(messages))
        payload = await _wait_event(run, "template_prepare")
        _commit(run, payload, {}, errors=[{"message": "沙箱超时"}])
        return await task

    with pytest.raises(TemplatePromptError) as exc:
        _run(scenario())
    assert "沙箱超时" in str(exc.value)


def test_non_system_messages_are_stripped_not_executed(tmp_path):
    tool = Message(role="tool", content="网页 <% setvar('a', 1) %> 正文")
    user = Message(role="user", content="我输入了 <% 1 + 1 %>")
    session = _session([tool, user])
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    out = _run(bridge.apply([tool, user]))
    assert out[0].content == "网页  正文"
    assert out[1].content == "我输入了 "
    assert not [e for e in run.events if e[0] == "template_prepare"]


def test_invalid_op_is_dropped_others_apply(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    text = "<%= 1 %>"
    messages = [_src(text)]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(messages))
        payload = await _wait_event(run, "template_prepare")
        _commit(run, payload, {cell_id(text): "1"}, ops=[
            {"op": "vars.set", "scope": "global", "path": "__proto__", "value": 1},
            {"op": "vars.set", "scope": "local", "path": "ok", "value": True},
        ])
        return await task

    _run(scenario())
    bridge.finalize()
    assert session.template_vars["local"]["ok"] is True
    assert bridge._ops_rejected == 1


def test_preserve_ejs_keeps_control_flow_and_expands_outside(monkeypatch):
    from flyai.tavern import (
        render_card_text,
        reset_preserve_ejs,
        set_preserve_ejs,
    )

    token = set_preserve_ejs(True)
    try:
        out = render_card_text(
            "<% if (getvar('x')) { %>A{{char}}<% } %>B{{user}}",
            char_name="小猫",
            user_name="用户",
        )
    finally:
        reset_preserve_ejs(token)
    assert out == "<% if (getvar('x')) { %>A小猫<% } %>B用户"


def test_preset_ejs_survives_assembly_when_enabled():
    from flyai.preset.apply import render_preset
    from flyai.preset.models import Preset, PresetBlock
    from flyai.tavern import reset_preserve_ejs, set_preserve_ejs

    preset = Preset(blocks=[
        PresetBlock(
            name="main", position="main",
            content="HP=<%= getvar('hp') %> {{char}}",
        ),
        PresetBlock(
            name="depth", position="depth", role="user", depth=2,
            content="<% if (getvar('x')) { %>D{{user}}<% } %>",
        ),
    ])
    token = set_preserve_ejs(True)
    try:
        out = render_preset(
            preset, char_name="小猫", user_name="用户", variables={})
    finally:
        reset_preserve_ejs(token)
    # 控制流保留待客户端执行；标签外的宏已展开
    assert "<%= getvar('hp') %>" in out.main_text
    assert "小猫" in out.main_text
    assert out.depth_blocks[0]["content"] == (
        "<% if (getvar('x')) { %>D用户<% } %>")
    assert out.depth_blocks[0]["role"] == "user"


def test_default_still_strips_ejs():
    from flyai.tavern import render_card_text

    out = render_card_text(
        "前 <% if (x) { %>中<% } %> 后", char_name="A", user_name="U")
    assert out == "前 中 后"


def test_post_render_macro_applied(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    text = "<%= '状态' %>"
    messages = [_src(text)]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(
            messages, macro_render=lambda value: value + "·{{name}}"))
        payload = await _wait_event(run, "template_prepare")
        _commit(run, payload, {cell_id(text): "正常"})
        return await task

    out = _run(scenario())
    assert out[0].content == "正常·{{name}}"


def test_template_source_any_role_executes(tmp_path):
    depth_user = Message(
        role="user", content="<%= 2 + 2 %>", template_source=True)
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, _session([depth_user]), run)

    async def scenario():
        task = asyncio.ensure_future(bridge.apply([depth_user]))
        payload = await _wait_event(run, "template_prepare")
        _commit(run, payload, {cell_id(depth_user.content): "4"})
        return await task

    out = _run(scenario())
    assert out[0].content == "4"


def test_duplicate_text_shares_one_cell(tmp_path):
    session = _session()
    run = ChatRun("r1", "u1", "s1")
    bridge = _bridge(tmp_path, session, run)
    text = "<%= 1 %>"
    messages = [
        _src(text),
        _src(text),
    ]

    async def scenario():
        task = asyncio.ensure_future(bridge.apply(messages))
        payload = await _wait_event(run, "template_prepare")
        assert len(payload["cells"]) == 1
        _commit(run, payload, {cell_id(text): "1"})
        return await task

    out = _run(scenario())
    assert [m.content for m in out] == ["1", "1"]
