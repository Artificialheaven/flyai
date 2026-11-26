"""管理员终端 WebSocket：令牌校验、非管理员拒绝、ready 握手。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from flyai.api import routes_shell
from flyai.auth_sessions import AuthSessionStore
from flyai.user import ADMIN_LEVEL, ADMIN_UID


class FakeState:
    def __init__(self):
        self.auth_sessions = AuthSessionStore()

    def token(self, uid=ADMIN_UID, role="admin", level=ADMIN_LEVEL):
        return self.auth_sessions.issue(uid, uid, role, level)


def _client(state):
    app = FastAPI()
    app.include_router(routes_shell.router)
    app.state.flyai = state
    return TestClient(app)


def test_shell_rejects_bad_token():
    client = _client(FakeState())
    with client.websocket_connect("/api/shell") as ws:
        ws.send_text('{"type":"auth","token":"nope"}')
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
        assert ei.value.code == 4401


def test_shell_rejects_non_admin():
    state = FakeState()
    token = state.token(uid="u1", role="user", level=0)
    client = _client(state)
    with client.websocket_connect("/api/shell") as ws:
        ws.send_text('{"type":"auth","token":"%s"}' % token)
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
        assert ei.value.code == 4401


def test_shell_rejects_non_auth_first_message():
    client = _client(FakeState())
    with client.websocket_connect("/api/shell") as ws:
        ws.send_text('{"type":"input","data":"ls\\n"}')
        with pytest.raises(WebSocketDisconnect) as ei:
            ws.receive_text()
        assert ei.value.code == 4401


def test_shell_admin_gets_ready():
    state = FakeState()
    token = state.token()
    client = _client(state)
    with client.websocket_connect("/api/shell") as ws:
        ws.send_text('{"type":"auth","token":"%s"}' % token)
        assert ws.receive_text() == '{"type": "ready"}'
    # 退出上下文即断开，路由会清理子进程
