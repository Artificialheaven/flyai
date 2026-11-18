"""用户个人设置：UserSettings 读写归一 + GET/PUT /api/my/settings。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_config import (
    MyBackgroundPayload,
    MySettingsPayload,
    get_my_background,
    get_my_settings,
    put_my_background,
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
    # 新用户默认开启自动回忆与内容表触发词注入；max_tokens 默认跟随模型
    assert s["auto_recall"] is True
    assert s["lore_auto_inject"] is True
    assert s["max_tokens"] is None
    assert s["auto_recall_max"] == 5
    assert s["extract_every_turns"] == 10


def test_api_put_then_get_persists(tmp_path):
    scope = _scope(tmp_path)
    out = put_my_settings(
        MySettingsPayload(keep_turns=2, auto_recall=True,
                          auto_recall_max=8, extract_every_turns=5,
                          lore_auto_inject=False, max_tokens=2048),
        _req(scope),
    )
    assert out["keep_turns"] == 2 and out["auto_recall"] is True
    assert out["auto_recall_max"] == 8 and out["extract_every_turns"] == 5
    assert out["lore_auto_inject"] is False and out["max_tokens"] == 2048
    # 已落盘：重新构建作用域后设置仍在
    rebuilt = UserScope.build("u1", AppConfig(), tmp_path, None)
    assert rebuilt.user_settings.keep_turns == 2
    assert rebuilt.user_settings.auto_recall is True
    assert rebuilt.user_settings.lore_auto_inject is False
    assert rebuilt.user_settings.max_tokens == 2048


def test_api_max_tokens_zero_means_follow_model(tmp_path):
    scope = _scope(tmp_path)
    out = put_my_settings(MySettingsPayload(max_tokens=0), _req(scope))
    assert out["max_tokens"] is None  # 0/空归一化为 None = 跟随提供商配置
    assert scope.user_settings.max_tokens is None


def test_api_max_tokens_clamped(tmp_path):
    scope = _scope(tmp_path)
    out = put_my_settings(MySettingsPayload(max_tokens=10 ** 9), _req(scope))
    assert out["max_tokens"] == 100000


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


# ---- 应用背景 ----

def _bg(scope, device="desktop"):
    return get_my_background(_req(scope))[device]


def _put_bg(scope, **kw):
    return put_my_background(MyBackgroundPayload(**kw), _req(scope))


def test_background_defaults(tmp_path):
    scope = _scope(tmp_path)
    assert _bg(scope) == {"mode": "theme", "color": "", "image": "",
                          "opacity": 100}
    assert _bg(scope, "mobile") == {"mode": "theme", "color": "", "image": "",
                                    "opacity": 100}
    s = scope.user_settings
    assert (s.bg_desktop_mode, s.bg_desktop_color, s.bg_desktop_image,
            s.bg_desktop_opacity) == ("theme", "", "", 100)
    assert (s.bg_mobile_mode, s.bg_mobile_color, s.bg_mobile_image,
            s.bg_mobile_opacity) == ("theme", "", "", 100)


def test_background_save_and_reload(tmp_path):
    scope = _scope(tmp_path)
    _put_bg(scope, device="desktop", mode="solid", color="#112233", opacity=60)
    _put_bg(scope, device="mobile", mode="solid", color="#AABBCC", opacity=30)
    rebuilt = UserScope.build("u1", AppConfig(), tmp_path, None)
    us = rebuilt.user_settings
    assert us.bg_desktop_mode == "solid" and us.bg_desktop_color == "#112233"
    assert us.bg_desktop_opacity == 60
    assert us.bg_mobile_mode == "solid" and us.bg_mobile_color == "#aabbcc"
    assert us.bg_mobile_opacity == 30


def test_background_normalize_from_dict():
    us = UserSettings.from_dict({
        "bg_desktop_mode": "nope", "bg_desktop_color": "#123456",
        "bg_desktop_image": "/uploads/u1/x.jpg", "bg_desktop_opacity": 140,
        "bg_mobile_mode": "solid", "bg_mobile_color": "red",
        "bg_mobile_image": "/uploads/u1/y.jpg", "bg_mobile_opacity": -5,
    })
    assert us.bg_desktop_mode == "theme"  # 非法 mode → theme，素材原样保留
    assert us.bg_desktop_image == "/uploads/u1/x.jpg"
    assert us.bg_desktop_opacity == 100   # 越界钳制到 0-100
    assert us.bg_mobile_mode == "theme"   # 非法颜色 → solid 无颜色回落 theme
    assert us.bg_mobile_image == "/uploads/u1/y.jpg"
    assert us.bg_mobile_opacity == 0
    bad = UserSettings.from_dict({"bg_desktop_opacity": "abc"})
    assert bad.bg_desktop_opacity == 100  # 非数字回默认


def test_background_partial_put_keeps_other_fields(tmp_path):
    scope = _scope(tmp_path)
    _put_bg(scope, device="desktop", mode="solid", color="#123456")
    out = _put_bg(scope, device="desktop", mode="theme")
    assert out["desktop"]["color"] == "#123456"  # theme 不清颜色
    assert out["desktop"]["mode"] == "theme"
    # 只改颜色，模式与透明度保持
    _put_bg(scope, device="desktop", mode="solid", color="#abcdef", opacity=45)
    out = _put_bg(scope, device="desktop", color="#000000")
    assert out["desktop"] == {"mode": "solid", "color": "#000000",
                              "image": "", "opacity": 45}
    # 只改透明度，其余保持
    out = _put_bg(scope, device="desktop", opacity=0)
    assert out["desktop"] == {"mode": "solid", "color": "#000000",
                              "image": "", "opacity": 0}


def test_background_clear_color_falls_back_to_theme(tmp_path):
    scope = _scope(tmp_path)
    _put_bg(scope, device="desktop", mode="solid", color="#123456")
    out = _put_bg(scope, device="desktop", color="")
    assert out["desktop"]["mode"] == "theme"
    assert out["desktop"]["color"] == ""


def test_background_uploaded_image_owned(tmp_path):
    scope = _scope(tmp_path)
    (scope.upload_dir / "bg.jpg").write_bytes(b"x")
    out = _put_bg(scope, device="mobile", mode="image",
                  image="/uploads/u1/bg.jpg")
    assert out["mobile"]["mode"] == "image"
    assert out["mobile"]["image"] == "/uploads/u1/bg.jpg"
    # 清空图片 → 自动回落 theme
    out = _put_bg(scope, device="mobile", image="")
    assert out["mobile"]["mode"] == "theme" and out["mobile"]["image"] == ""


@pytest.mark.parametrize("kw", [
    {"device": "tablet"},
    {"device": "desktop", "mode": "video"},
    {"device": "desktop", "color": "red"},
    {"device": "desktop", "color": "#12345"},
    {"device": "desktop", "opacity": 101},
    {"device": "desktop", "opacity": -1},
    {"device": "desktop", "image": "/uploads/u2/bg.jpg"},   # 他人目录
    {"device": "desktop", "image": "/uploads/u1/missing.jpg"},
    {"device": "desktop", "image": "/uploads/u1/../u2/bg.jpg"},
    {"device": "desktop", "image": "/uploads/u1/a/b.jpg"},
    {"device": "desktop", "image": "/uploads/u1/bg.exe"},
])
def test_background_put_rejects_invalid(tmp_path, kw):
    scope = _scope(tmp_path)
    (scope.upload_dir / "bg.jpg").write_bytes(b"x")
    with pytest.raises(HTTPException) as ei:
        _put_bg(scope, **kw)
    assert ei.value.status_code == 400


def test_background_survives_full_settings_put(tmp_path):
    scope = _scope(tmp_path)
    _put_bg(scope, device="desktop", mode="solid", color="#123456", opacity=55)
    # 管理面板/旧前端整体保存设置，不应清空背景
    put_my_settings(MySettingsPayload(keep_turns=1), _req(scope))
    out = get_my_background(_req(scope))
    assert out["desktop"] == {"mode": "solid", "color": "#123456",
                              "image": "", "opacity": 55}


def test_background_requires_login(tmp_path):
    def deny(_r):
        raise HTTPException(401, "未登录或登录已失效")

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            flyai=SimpleNamespace(user=deny)))
    )
    with pytest.raises(HTTPException) as ei:
        get_my_background(request)
    assert ei.value.status_code == 401
