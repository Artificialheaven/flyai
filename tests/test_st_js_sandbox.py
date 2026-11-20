"""脚本沙箱开关：管理员默认开、普通用户默认关，个人设置可显式覆盖。"""

from __future__ import annotations

from types import SimpleNamespace

from flyai.api.routes_session import _st_payload
from flyai.user import UserSettings


def _session():
    return SimpleNamespace(members=[], character_id="", mvu={})


def _js_sandbox(user_settings, is_admin):
    payload = _st_payload(_session(), None, None, user_settings, cfg=None, is_admin=is_admin)
    return payload["flags"]["js_sandbox"]


def test_admin_defaults_on():
    assert _js_sandbox(None, is_admin=True) is True
    assert _js_sandbox(UserSettings(), is_admin=True) is True


def test_regular_user_defaults_off():
    assert _js_sandbox(None, is_admin=False) is False
    assert _js_sandbox(UserSettings(), is_admin=False) is False


def test_user_override_wins_over_default():
    off = UserSettings(st_js_sandbox_enabled=False)
    on = UserSettings(st_js_sandbox_enabled=True)
    assert _js_sandbox(off, is_admin=True) is False
    assert _js_sandbox(on, is_admin=False) is True


def test_override_survives_roundtrip():
    settings = UserSettings(st_js_sandbox_enabled=True)
    restored = UserSettings.from_dict(settings.to_dict())
    assert restored.st_js_sandbox_enabled is True
