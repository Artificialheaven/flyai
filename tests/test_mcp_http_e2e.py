# -*- coding: utf-8 -*-
"""P4 HTTP 层端到端：真实 FastAPI 路由 + 序列化 + 鉴权门禁。

不进 lifespan（data_dir 硬编码仓库 data/，不能被测试触碰）：手工构造 AppState
并把 tmp 目录上的真实 P4 服务挂到 app.state.flyai，user() 用测试桩替换。
覆盖 /api/agentdb/* 管理门禁与 /api/ext/trigger 的完整状态码矩阵。
"""

import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.app import AppState, create_app
from flyai.config import AppConfig, McpSettings, WatcherSettings
from flyai.mcp_client.manager import McpManager
from flyai.proactor import runner
from flyai.proactor.prompt_registry import PROMPT_PARTITIONS
from flyai.proactor.trigger import MAX_BODY_BYTES, TriggerCooldown
from flyai.proactor.watchers import WatcherManager
from flyai.user import ADMIN_UID


@pytest.fixture()
def client(tmp_path):
    app = create_app()
    config = AppConfig()
    config.ext_trigger.enabled = True
    config.ext_trigger.token = "secret-token"
    config.mcp = McpSettings(enabled=True)

    state = AppState(config=config)
    state.data_dir = tmp_path
    state.trigger_cooldown = TriggerCooldown(30)
    state.mcp_manager = McpManager(tmp_path, config.mcp)
    from flyai.proactor.prompt_registry import PromptRegistry

    state.prompt_registry = PromptRegistry(tmp_path)
    user_store = SimpleNamespace(
        list=lambda: [SimpleNamespace(id=ADMIN_UID)],
        get_by_id=lambda uid: SimpleNamespace(id=uid) if uid == "u1" else None,
    )
    state.user_store = user_store
    admin_scope = SimpleNamespace(
        uid=ADMIN_UID,
        store=SimpleNamespace(
            get=lambda sid: {"id": sid} if sid == "s1" else None),
    )
    user_scope = SimpleNamespace(uid="u1", store=SimpleNamespace(get=lambda sid: None))
    state._scopes = {ADMIN_UID: admin_scope, "u1": user_scope}
    state.watcher_manager = WatcherManager(
        tmp_path, WatcherSettings(), config.ext_trigger, user_store,
    )

    def fake_user(request):
        uid = request.headers.get("x-test-uid", "")
        scope = state._scopes.get(uid)
        if scope is None:
            raise HTTPException(401, "未登录或登录已失效")
        return scope

    state.user = fake_user
    app.state.flyai = state
    return TestClient(app), state


def _auth(uid):
    return {"x-test-uid": uid}


class TestAgentdbAdminGate:
    def test_mcp_list_forbidden_for_normal_user(self, client):
        c, _ = client
        r = c.get("/api/agentdb/mcp/servers", headers=_auth("u1"))
        assert r.status_code == 403

    def test_mcp_server_crud(self, client):
        c, state = client
        r = c.get("/api/agentdb/mcp/servers", headers=_auth(ADMIN_UID))
        assert r.status_code == 200 and r.json()["items"] == []

        r = c.post("/api/agentdb/mcp/servers", headers=_auth(ADMIN_UID), json={
            "server_id": "demo", "command": "python",
            "args": ["x.py"], "env": {"K": "V"}, "enabled": True,
        })
        assert r.status_code == 200 and r.json()["item"]["status"] == "new"

        r = c.put("/api/agentdb/mcp/servers/demo", headers=_auth(ADMIN_UID), json={
            "command": "python3",
        })
        assert r.status_code == 200 and r.json()["item"]["command"] == "python3"

        r = c.post("/api/agentdb/mcp/servers/demo/enabled",
                   headers=_auth(ADMIN_UID), json={"enabled": False})
        assert r.status_code == 200 and r.json()["item"]["enabled"] == 0

        r = c.delete("/api/agentdb/mcp/servers/demo", headers=_auth(ADMIN_UID))
        assert r.status_code == 200 and r.json()["ok"] is True
        r = c.delete("/api/agentdb/mcp/servers/demo", headers=_auth(ADMIN_UID))
        assert r.status_code == 404

    def test_mcp_create_bad_id_rejected(self, client):
        c, _ = client
        r = c.post("/api/agentdb/mcp/servers", headers=_auth(ADMIN_UID), json={
            "server_id": "Bad Id", "command": "python",
        })
        assert r.status_code == 422

    def test_prompt_partitions(self, client):
        c, _ = client
        r = c.get("/api/agentdb/prompts", headers=_auth(ADMIN_UID))
        items = r.json()["items"]
        assert len(items) == len(PROMPT_PARTITIONS)
        assert all(x["override"] == "" for x in items)

        r = c.put("/api/agentdb/prompts/compact", headers=_auth(ADMIN_UID),
                  json={"text": "自定义压缩指令"})
        assert r.status_code == 200
        items = c.get("/api/agentdb/prompts",
                      headers=_auth(ADMIN_UID)).json()["items"]
        compact = next(x for x in items if x["key"] == "compact")
        assert compact["override"] == "自定义压缩指令"

        r = c.delete("/api/agentdb/prompts/compact", headers=_auth(ADMIN_UID))
        assert r.status_code == 200 and r.json()["reset"] is True
        r = c.put("/api/agentdb/prompts/nope", headers=_auth(ADMIN_UID),
                  json={"text": "x"})
        assert r.status_code == 404

    def test_prompt_admin_only(self, client):
        c, _ = client
        r = c.get("/api/agentdb/prompts", headers=_auth("u1"))
        assert r.status_code == 403

    def test_watcher_list_and_reconcile(self, client):
        c, _ = client
        r = c.get("/api/agentdb/watchers", headers=_auth(ADMIN_UID))
        assert r.status_code == 200 and r.json()["items"] == []
        r = c.post("/api/agentdb/watchers/reconcile", headers=_auth(ADMIN_UID))
        assert r.status_code == 200 and r.json()["stopped"] == 0
        # 普通用户不能触发全量 reconcile
        r = c.post("/api/agentdb/watchers/reconcile", headers=_auth("u1"))
        assert r.status_code == 403


class TestExtTriggerHttp:
    def test_endpoint_hides_as_404_when_disabled(self, client):
        c, state = client
        state.config.ext_trigger.enabled = False
        r = c.post("/api/ext/trigger",
                   headers={"Authorization": "Bearer secret-token"},
                   json={"session_id": "s1", "source": "s", "text": "t"})
        assert r.status_code == 404

    def test_auth_matrix(self, client):
        c, _ = client
        payload = {"session_id": "s1", "source": "s", "text": "t"}
        assert c.post("/api/ext/trigger", json=payload).status_code == 401
        r = c.post("/api/ext/trigger",
                   headers={"Authorization": "Bearer wrong"}, json=payload)
        assert r.status_code == 401

    def test_bad_json_and_oversize(self, client):
        c, _ = client
        headers = {"Authorization": "Bearer secret-token"}
        r = c.post("/api/ext/trigger", headers=headers, content=b"{bad")
        assert r.status_code == 400
        r = c.post("/api/ext/trigger", headers=headers,
                   content=b"x" * (MAX_BODY_BYTES + 1))
        assert r.status_code == 413

    def test_owner_missing_404(self, client):
        c, _ = client
        r = c.post("/api/ext/trigger",
                   headers={"Authorization": "Bearer secret-token"},
                   json={"session_id": "ghost", "source": "s", "text": "t"})
        assert r.status_code == 404

    def test_enqueue_202_then_cooldown_429(self, client, monkeypatch):
        c, state = client
        calls = []

        async def fake_fire(st, uid, sid, **kw):
            calls.append((uid, sid, kw))
            return {"ok": True, "run_id": "run-http-1"}

        monkeypatch.setattr(runner, "fire", fake_fire)
        headers = {"Authorization": "Bearer secret-token"}
        body = {"session_id": "s1", "source": "price-bot", "text": "跌破 100"}
        r = c.post("/api/ext/trigger", headers=headers, json=body)
        assert r.status_code == 202
        assert r.json() == {"ok": True, "run_id": "run-http-1"}
        assert len(calls) == 1 and calls[0][2]["kind"] == "ext-trigger"

        # 同（会话, 来源）立即再触发 → 429，且不再入队
        r = c.post("/api/ext/trigger", headers=headers, json=body)
        assert r.status_code == 429
        assert r.json()["retry_after"] >= 1
        assert len(calls) == 1

    def test_fire_failure_503_and_cooldown_rollback(self, client, monkeypatch):
        c, state = client

        async def bad_fire(st, uid, sid, **kw):
            return {"ok": False, "reason": "locked"}

        monkeypatch.setattr(runner, "fire", bad_fire)
        headers = {"Authorization": "Bearer secret-token"}
        body = {"session_id": "s1", "source": "src", "text": "t"}
        r = c.post("/api/ext/trigger", headers=headers, json=body)
        assert r.status_code == 503 and r.json()["reason"] == "locked"
        # 冷却已回滚：换新来源仍可立即尝试（同来源也不再 429）
        assert state.trigger_cooldown.remaining("s1", "src") == 0.0
