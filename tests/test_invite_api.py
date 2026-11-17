"""邀请码接口：个人查看/重新生成、管理员自定义/列表/删除、删除用户联动。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_auth import delete_user
from flyai.api.routes_invite import (
    AdminInvitePayload,
    delete_invite,
    get_my_invite,
    list_invites,
    regenerate_my_invite,
    set_admin_invite,
)
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig
from flyai.invites import InviteStore
from flyai.user import ADMIN_LEVEL, ADMIN_UID, UserStore


def _state(tmp_path):
    cfg = AppConfig()
    cfg.auth.admin.username = "root"
    state = SimpleNamespace(
        config=cfg,
        user_store=UserStore(tmp_path),
        invite_store=InviteStore(tmp_path),
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
        reload_providers=lambda: None,
        reload_tools=lambda: None,
        _scopes={},
    )

    def user(request):
        from flyai.auth_sessions import resolve_uid

        uid = resolve_uid(state, request)
        if uid is None:
            raise HTTPException(401, "未登录或登录已失效")
        return SimpleNamespace(uid=uid)

    state.user = user
    return state


def _req(state, uid=ADMIN_UID):
    role = "admin" if uid == ADMIN_UID else "user"
    level = ADMIN_LEVEL if uid == ADMIN_UID else 0
    token = state.auth_sessions.issue(uid, uid, role, level)
    return SimpleNamespace(
        headers={"authorization": "Bearer " + token},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def test_user_generate_and_regenerate(tmp_path):
    state = _state(tmp_path)
    u = state.user_store.create("alice", "pw")
    req = _req(state, u.id)

    assert get_my_invite(req)["invite"] is None
    first = regenerate_my_invite(req)["invite"]
    assert first["owner"] == u.id and first["max_uses"] == 5
    assert get_my_invite(req)["invite"]["code"] == first["code"]

    second = regenerate_my_invite(req)["invite"]
    assert second["code"] != first["code"]
    assert state.invite_store.check(first["code"]) is None


def test_admin_custom_code_and_max_uses(tmp_path):
    state = _state(tmp_path)
    view = set_admin_invite(
        AdminInvitePayload(code="my-invite", max_uses=9), _req(state))["invite"]
    assert view["code"] == "MY-INVITE" and view["max_uses"] == 9
    assert view["owner"] == ADMIN_UID

    # 空码=随机生成，可保留自定义人数
    random_view = set_admin_invite(
        AdminInvitePayload(code="", max_uses=7), _req(state))["invite"]
    assert random_view["code"] != "MY-INVITE"
    assert random_view["max_uses"] == 7
    assert state.invite_store.check("MY-INVITE") is None


def test_admin_endpoints_forbidden_for_user(tmp_path):
    state = _state(tmp_path)
    u = state.user_store.create("alice", "pw")
    req = _req(state, u.id)
    for fn, payload in [
        (set_admin_invite, AdminInvitePayload(code="ABCD-EFGH")),
        (list_invites, None),
        (delete_invite, None),
    ]:
        with pytest.raises(HTTPException) as ei:
            if fn is delete_invite:
                fn("ABCD-EFGH", req)
            elif payload is None:
                fn(req)
            else:
                fn(payload, req)
        assert ei.value.status_code == 403


def test_admin_list_attaches_owner_name_and_missing(tmp_path):
    state = _state(tmp_path)
    alice = state.user_store.create("alice", "pw")
    state.invite_store.generate(alice.id)
    gone = state.user_store.create("gone", "pw")
    state.invite_store.generate(gone.id)
    state.user_store.remove(gone.id)
    state.invite_store.generate(ADMIN_UID)

    invites = {v["owner"]: v for v in list_invites(_req(state))["invites"]}
    assert invites[alice.id]["owner_name"] == "alice"
    assert invites[alice.id]["owner_missing"] is False
    assert invites[ADMIN_UID]["owner_name"] == "root"
    assert invites[gone.id]["owner_name"] == ""
    assert invites[gone.id]["owner_missing"] is True


def test_delete_invite_and_404(tmp_path):
    state = _state(tmp_path)
    code = state.invite_store.generate("u1")["code"]
    assert delete_invite(code, _req(state))["ok"] is True
    with pytest.raises(HTTPException) as ei:
        delete_invite(code, _req(state))
    assert ei.value.status_code == 404
    assert ei.value.detail == "邀请码不存在"


def test_delete_user_cleans_invite(tmp_path):
    state = _state(tmp_path)
    alice = state.user_store.create("alice", "pw")
    code = state.invite_store.generate(alice.id)["code"]
    delete_user(alice.id, _req(state))
    assert state.invite_store.check(code) is None


def test_store_missing_returns_503(tmp_path):
    state = _state(tmp_path)
    state.invite_store = None
    with pytest.raises(HTTPException) as ei:
        get_my_invite(_req(state))
    assert ei.value.status_code == 503
