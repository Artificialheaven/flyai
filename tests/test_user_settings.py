"""用户个人设置：UserSettings 读写归一 + GET/PUT /api/my/settings。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_config import (
    MySettingsPayload,
    get_my_settings,
    put_my_settings,
)
from flyai.config import AppConfig
from flyai.user import (
    UserScope,
    UserSettings,
    load_user_settings,
    save_user_settings,
)
from flyai.auth_sessions import AuthSessionStore  # noqa: F401


def _scope(tmp_path, uid="u1"):
    return UserScope.build(uid, AppConfig(), tmp_path, None)


def _req(scope):
    state = SimpleNamespace(user=lambda _r: scope)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )


# ---- UserSettings 存取 ----

def test_default_when_file_missing(tmp_path):
    s = load_user_settings(tmp_path / "nouser" / "settings.json")
    assert s.keep_turns == 0


def test_save_and_reload(tmp_path):
    p = tmp_path / "u1" / "settings.json"
    save_user_settings(p, UserSettings(keep_turns=3))
    assert load_user_settings(p).keep_turns == 3


def test_corrupt_file_falls_back(tmp_path):
    p = tmp_path / "u1" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text("{broken", encoding="utf-8")
    assert load_user_settings(p).keep_turns == 0


def test_values_normalized():
    assert UserSettings.from_dict({"keep_turns": -5}).keep_turns == 0
    assert UserSettings.from_dict({"keep_turns": "x"}).keep_turns == 0
    assert UserSettings.from_dict({"keep_turns": "4"}).keep_turns == 4


# ---- API ----

def test_api_get_default(tmp_path):
    s = get_my_settings(_req(_scope(tmp_path)))
    assert s["keep_turns"] == 0
    assert s["auto_recall"] is False
    assert s["auto_recall_max"] == 5
    assert s["extract_every_turns"] == 10


def test_api_put_then_get_persists(tmp_path):
    scope = _scope(tmp_path)
    out = put_my_settings(
        MySettingsPayload(keep_turns=2, auto_recall=True,
                          auto_recall_max=8, extract_every_turns=5),
        _req(scope),
    )
    assert out["keep_turns"] == 2 and out["auto_recall"] is True
    assert out["auto_recall_max"] == 8 and out["extract_every_turns"] == 5
    # 已落盘：重新构建作用域后设置仍在
    rebuilt = UserScope.build("u1", AppConfig(), tmp_path, None)
    assert rebuilt.user_settings.keep_turns == 2
    assert rebuilt.user_settings.auto_recall is True


def test_api_negative_clamps_to_zero(tmp_path):
    scope = _scope(tmp_path)
    put_my_settings(MySettingsPayload(keep_turns=-9, auto_recall_max=-3),
                    _req(scope))
    assert scope.user_settings.keep_turns == 0
    assert scope.user_settings.auto_recall_max == 1  # 下限
    assert get_my_settings(_req(scope))["keep_turns"] == 0


def test_api_requires_login(tmp_path):
    def deny(_r):
        raise HTTPException(401, "未登录或登录已失效")

    state = SimpleNamespace(user=deny)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )
    with pytest.raises(HTTPException) as ei:
        get_my_settings(request)
    assert ei.value.status_code == 401
