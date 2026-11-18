"""启动公告：公开读取接口（登录页也要可见）与仅管理员可改的配置段。"""

import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_app import get_announcement
from flyai.api.routes_config import ConfigPayload, get_config, put_config
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.config import AppConfig, load_config
from flyai.user import ADMIN_LEVEL, ADMIN_UID


class FakeUserStore:
    def __init__(self):
        self.users = {}

    def add(self, uid, name=None, level=0):
        u = SimpleNamespace(id=uid, name=name or uid, level=level)
        self.users[uid] = u
        return u

    def get_by_id(self, uid):
        return self.users.get(uid)


def _state(tmp_path):
    state = SimpleNamespace(
        config=AppConfig(),
        config_path=tmp_path / "config.json",
        user_store=FakeUserStore(),
        _scopes={},
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
        reload_providers=lambda: None,
        reload_tools=lambda: None,
    )
    state.user_store.add(ADMIN_UID, name="admin", level=ADMIN_LEVEL)
    state.user_store.add("u10", name="user", level=0)
    return state


def _req(state, uid=ADMIN_UID):
    is_admin = uid == ADMIN_UID
    token = state.auth_sessions.issue(
        uid, uid, "admin" if is_admin else "user", ADMIN_LEVEL if is_admin else 0
    )
    return SimpleNamespace(
        headers={"authorization": "Bearer " + token},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


def _public(state):
    """公告接口不鉴权，请求对象只需要能取到 app.state.flyai。"""
    resp = get_announcement(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    )
    assert resp.headers["cache-control"] == "no-store"
    return json.loads(resp.body.decode())


def test_announcement_disabled_by_default(tmp_path):
    state = _state(tmp_path)
    assert _public(state) == {"enabled": False, "title": "公告", "content": ""}


def test_announcement_enabled_is_public(tmp_path):
    state = _state(tmp_path)
    put_config(
        ConfigPayload(
            announcement={
                "enabled": True,
                "title": "  维护通知  ",
                "content": "今晚 2 点维护\n请提前保存",
            }
        ),
        _req(state),
    )
    assert _public(state) == {
        "enabled": True,
        "title": "维护通知",
        "content": "今晚 2 点维护\n请提前保存",
    }
    # 普通用户也能拿到展示内容（登录页在登录前就要弹出）
    assert state.config.announcement.enabled is True


def test_announcement_config_admin_only(tmp_path):
    state = _state(tmp_path)
    put_config(
        ConfigPayload(announcement={"enabled": True, "title": "t", "content": "c"}),
        _req(state),
    )
    admin_view = get_config(_req(state))
    assert admin_view["announcement"] == {"enabled": True, "title": "t", "content": "c"}
    assert "announcement" not in get_config(_req(state, "u10"))


def test_announcement_length_limits_and_persist(tmp_path):
    state = _state(tmp_path)
    put_config(
        ConfigPayload(
            announcement={"enabled": True, "title": "x" * 200, "content": "y" * 30000}
        ),
        _req(state),
    )
    assert len(state.config.announcement.title) == 120
    assert len(state.config.announcement.content) == 20000
    saved = load_config(tmp_path / "config.json")
    assert saved.announcement.enabled is True
    assert saved.announcement.title == "x" * 120
    assert len(saved.announcement.content) == 20000
