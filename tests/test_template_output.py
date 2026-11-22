# -*- coding: utf-8 -*-
"""EJS 模板（方案 B）：消息标记、输出侧提交校验与变量落盘。"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api import routes_template
from flyai.api.routes_template import (
    TemplateOutputBody,
    TemplatePromptCommitBody,
)
from flyai.agent.runs import ChatRun
from flyai.config import PromptTemplateSettings
from flyai.schemas import Message
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import Session, Thread
from flyai.template import policy
from flyai.template.output import (
    apply_output_commit,
    mark_pending_output,
    message_hash,
    strip_template_markers,
)
from flyai.template.store import TemplateVarStore
from flyai.template.vars import TemplateOpError, apply_ops


def _session(content="正文\n<% setvar('hp', 10) %>\n"):
    session = Session(id="s1", title="t")
    thread = Thread(id="t1", kind="dm", member_ids=["m1"])
    thread.messages.append(Message(role="user", id="u1", content="你好"))
    thread.messages.append(Message(role="assistant", id="a1", content=content))
    session.threads = [thread]
    return session


def _message(session):
    return session.threads[0].messages[1]


def test_mark_pending_only_when_enabled_and_template():
    session = _session()
    assert mark_pending_output(session, False) == 0
    assert _message(session).raw_content is None
    assert mark_pending_output(session, True) == 1
    message = _message(session)
    assert message.raw_content == message.content
    assert message.template_rendered is False
    # 重复标记不覆盖
    assert mark_pending_output(session, True) == 0


def test_mark_pending_skips_plain_text_and_users():
    session = _session("普通回复")
    session.threads[0].messages[0].content = "<% user template %>"
    assert mark_pending_output(session, True) == 0


def test_apply_output_commit_sets_content_and_vars():
    session = _session()
    message = _message(session)
    mark_pending_output(session, True)
    raw = message.raw_content
    result = apply_output_commit(
        session,
        message,
        {
            "content": "正文\n10\n",
            "raw_hash": message_hash(message),
            "render_id": "r1",
            "ops": [
                {"op": "vars.set", "scope": "message", "path": "hp", "value": 10},
                {"op": "vars.inc", "scope": "local", "path": "count", "delta": 2},
            ],
        },
        global_vars={"g": 1},
    )
    assert result["ok"] and not result["idempotent"]
    assert message.content == "正文\n10\n"
    assert message.template_rendered is True
    assert message.template_render_id == "r1"
    assert session.template_vars["message"]["a1"] == {"hp": 10}
    assert session.template_vars["local"] == {"count": 2}
    assert message.raw_content == raw


def test_apply_output_commit_idempotent_and_hash_guard():
    session = _session()
    message = _message(session)
    mark_pending_output(session, True)
    payload = {
        "content": "ok",
        "raw_hash": message_hash(message),
        "render_id": "r1",
        "ops": [],
    }
    apply_output_commit(session, message, payload, global_vars={})
    again = apply_output_commit(session, message, payload, global_vars={})
    assert again["idempotent"] is True

    other = _session()
    target = _message(other)
    mark_pending_output(other, True)
    with pytest.raises(Exception) as exc:
        apply_output_commit(
            other,
            target,
            {"content": "x", "raw_hash": "bad", "render_id": "r2", "ops": []},
            global_vars={},
        )
    assert getattr(exc.value, "status", 0) == 409


def test_apply_output_commit_rejects_injection_ops():
    session = _session()
    message = _message(session)
    mark_pending_output(session, True)
    with pytest.raises(Exception):
        apply_output_commit(
            session,
            message,
            {
                "content": "x",
                "raw_hash": message_hash(message),
                "ops": [{"op": "injectPrompt", "path": "x"}],
            },
            global_vars={},
        )


def test_apply_ops_rejects_bad_paths_and_sizes():
    with pytest.raises(TemplateOpError):
        apply_ops(
            [{"op": "vars.set", "path": "__proto__.x", "value": 1}],
            local={}, message={}, global_vars={},
        )
    with pytest.raises(TemplateOpError):
        apply_ops(
            [{"op": "vars.set", "path": "x", "value": "a" * (70 * 1024)}],
            local={}, message={}, global_vars={},
        )
    with pytest.raises(TemplateOpError):
        apply_ops(
            [{"op": "vars.inc", "path": "x", "delta": "nope"}],
            local={}, message={}, global_vars={},
        )


def test_strip_template_markers():
    assert strip_template_markers("a<% setvar('x') %>b") == "ab"
    assert strip_template_markers("a<%= x %>b") == "ab"
    assert strip_template_markers("没有模板") == "没有模板"
    assert strip_template_markers("前<% 未闭合") == "前"


def test_policy_requires_all_gates():
    cfg = SimpleNamespace(prompt_template=PromptTemplateSettings(
        enabled=True, min_level=10, allow_output_side=True))
    us = SimpleNamespace(template_enabled=True, template_output_enabled=True)
    assert policy.prompt_enabled(cfg, us, 10, False) is True
    assert policy.prompt_enabled(cfg, us, 9, False) is False
    assert policy.prompt_enabled(cfg, us, 0, True) is True
    assert policy.output_enabled(cfg, us, 10, False) is True
    us.template_output_enabled = False
    assert policy.output_enabled(cfg, us, 10, False) is False
    cfg.prompt_template.allow_output_side = False
    assert policy.output_enabled(cfg, us, 10, False) is False


def _env(tmp_path):
    store = JsonSessionStore(tmp_path)
    session = _session()
    mark_pending_output(session, True)
    store.save(session)
    us = SimpleNamespace(template_enabled=True, template_output_enabled=True)
    sc = SimpleNamespace(
        uid="u1", level=10, store=store, user_settings=us,
        settings_path=tmp_path / "u1" / "settings.json",
    )
    cfg = SimpleNamespace(prompt_template=PromptTemplateSettings(
        enabled=True, min_level=0, allow_output_side=True))
    state = SimpleNamespace(
        user=lambda request: sc, config=cfg, data_dir=tmp_path,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return request, store


def test_route_commit_output_and_global_vars(tmp_path):
    request, store = _env(tmp_path)
    session = store.get("s1")
    message = session.threads[0].messages[1]
    body = TemplateOutputBody(
        content="渲染后",
        raw_hash=message_hash(message),
        render_id="r1",
        ops=[{"op": "vars.set", "scope": "global", "path": "g", "value": 1}],
    )
    result = routes_template.commit_template_output("s1", "a1", body, request)
    assert result["ok"] is True
    saved = store.get("s1")
    assert saved.threads[0].messages[1].content == "渲染后"
    assert saved.threads[0].messages[1].template_rendered is True
    global_store = TemplateVarStore(tmp_path / "template_vars" / "u1.json")
    assert global_store.load() == {"g": 1}


def test_route_prompt_commit_resolves_waiter(tmp_path):
    request, _store = _env(tmp_path)
    run = ChatRun("deadbeef", "u1", "s1")
    prepare_id = "a" * 24
    future = asyncio.get_event_loop().create_future()
    run.template_waiters[prepare_id] = future
    request.app.state.flyai.run_manager = SimpleNamespace(
        get=lambda rid, uid: run if (rid == run.id and uid == "u1") else None
    )
    body = TemplatePromptCommitBody(
        run_id=run.id, prepare_id=prepare_id, context_version="v1",
        cells={"p1": "渲染"}, ops=[], errors=[],
    )
    result = routes_template.commit_template_prompt(body, request)
    assert result == {"ok": True, "accepted": 1}
    assert future.result()["cells"] == {"p1": "渲染"}
    assert future.result()["context_version"] == "v1"

    with pytest.raises(HTTPException) as exc:
        routes_template.commit_template_prompt(body, request)
    assert exc.value.status_code == 409


def test_route_prompt_commit_rejects_foreign_run(tmp_path):
    request, _store = _env(tmp_path)
    run = ChatRun("deadbeef", "u1", "s1")
    prepare_id = "a" * 24
    run.template_waiters[prepare_id] = asyncio.get_event_loop().create_future()
    request.app.state.flyai.run_manager = SimpleNamespace(
        get=lambda rid, uid: None
    )
    body = TemplatePromptCommitBody(
        run_id=run.id, prepare_id=prepare_id, cells={"p1": "x"},
    )
    with pytest.raises(HTTPException) as exc:
        routes_template.commit_template_prompt(body, request)
    assert exc.value.status_code == 409


def test_route_commit_requires_enabled(tmp_path):
    request, _store = _env(tmp_path)
    request.app.state.flyai.config.prompt_template.allow_output_side = False
    session = request.app.state.flyai.user(request).store.get("s1")
    message = session.threads[0].messages[1]
    with pytest.raises(HTTPException) as exc:
        routes_template.commit_template_output(
            "s1", "a1",
            TemplateOutputBody(content="x", raw_hash=message_hash(message)),
            request,
        )
    assert exc.value.status_code == 403
