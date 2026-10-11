"""用户功能开关覆盖：PUT /api/my/settings 的覆盖/继承/范围钳制与落盘。

规则（见 flyai/feature_prefs.py）：覆盖字段为 null=跟随管理员全局默认；
非 null=用户显式值，只影响该用户自己；管理员全局对象不被修改。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from pydantic import ValidationError

from flyai.api.routes_config import MySettingsPayload, get_my_settings, put_my_settings
from flyai.config import AppConfig
from flyai.user import UserScope
from flyai import feature_prefs


def _scope(tmp_path, uid="u1"):
    return UserScope.build(uid, AppConfig(), tmp_path, None)


def _req(scope):
    state = SimpleNamespace(user=lambda _r: scope)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state))
    )


def _all_overrides():
    return MySettingsPayload(
        tools_enabled=True,
        tool_max_iterations=20,
        tool_timeout_seconds=90,
        roleplay_format_enabled=False,
        roleplay_attributes_enabled=False,
        intro_chars=1234,
        background_chars=567,
        world_chars=89,
        mes_example_chars=10,
        pinned_chars=11,
        web_enabled=False,
        browser_enabled=True,
        pixiv_enabled=False,
        image_enabled=False,
        galgame_enabled=False,
        galgame_max_loops=77,
    )


def test_override_roundtrip_and_persist(tmp_path):
    scope = _scope(tmp_path)
    out = put_my_settings(_all_overrides(), _req(scope))
    # 响应即落盘内容
    assert out["tools_enabled"] is True
    assert out["tool_max_iterations"] == 20
    assert out["tool_timeout_seconds"] == 90
    assert out["roleplay_format_enabled"] is False
    assert out["roleplay_attributes_enabled"] is False
    assert out["intro_chars"] == 1234 and out["background_chars"] == 567
    assert out["world_chars"] == 89 and out["mes_example_chars"] == 10
    assert out["pinned_chars"] == 11
    assert out["web_enabled"] is False
    assert out["browser_enabled"] is True
    assert out["pixiv_enabled"] is False
    assert out["image_enabled"] is False
    assert out["galgame_enabled"] is False
    assert out["galgame_max_loops"] == 77

    rebuilt = UserScope.build("u1", AppConfig(), tmp_path, None)
    assert rebuilt.user_settings.tools_enabled is True
    assert rebuilt.user_settings.intro_chars == 1234
    assert rebuilt.user_settings.galgame_max_loops == 77


def test_overrides_take_effect_without_global_mutation(tmp_path):
    cfg = AppConfig()
    scope = UserScope.build("u1", cfg, tmp_path, None)
    put_my_settings(_all_overrides(), _req(scope))

    us = scope.user_settings
    # 用户覆盖值生效
    assert feature_prefs.feature_enabled("web", cfg, us) is False
    assert feature_prefs.feature_enabled("browser", cfg, us) is True
    assert feature_prefs.feature_enabled("galgame", cfg, us) is False
    tools = feature_prefs.effective_tool_settings(cfg, us)
    assert tools.enabled is True and tools.max_iterations == 20
    assert tools.timeout_seconds == 90
    rp = feature_prefs.effective_roleplay(cfg, us)
    assert rp.format_enabled is False
    assert rp.attributes.enabled is False
    assert rp.intro_chars == 1234
    assert feature_prefs.effective_galgame_loops(cfg, us) == 77
    # 全局默认原封不动
    assert cfg.tools.enabled is True and cfg.tools.max_iterations == 8
    assert cfg.roleplay.format_enabled is True
    assert cfg.roleplay.attributes.enabled is True
    assert cfg.web_search.enabled is True
    assert cfg.browser.enabled is False
    assert cfg.galgame.max_loops == 40


def test_null_restores_global_default(tmp_path):
    cfg = AppConfig()
    scope = UserScope.build("u1", cfg, tmp_path, None)
    put_my_settings(_all_overrides(), _req(scope))
    # 全 null（前端"跟随全局默认"勾选后提交）
    put_my_settings(MySettingsPayload(), _req(scope))
    view = get_my_settings(_req(scope))
    for key in (
        "tools_enabled", "tool_max_iterations", "tool_timeout_seconds",
        "roleplay_format_enabled", "roleplay_attributes_enabled",
        "intro_chars", "background_chars", "world_chars",
        "mes_example_chars", "pinned_chars",
        "web_enabled", "browser_enabled", "pixiv_enabled",
        "image_enabled", "galgame_enabled", "galgame_max_loops",
    ):
        assert view[key] is None
    us = scope.user_settings
    assert feature_prefs.feature_enabled("web", cfg, us) is True
    assert feature_prefs.feature_enabled("browser", cfg, us) is False
    assert feature_prefs.effective_galgame_loops(cfg, us) == 40
    # 工具设置返回全局对象本身（无覆盖）
    assert feature_prefs.effective_tool_settings(cfg, us) is cfg.tools


def test_ranges_clamped(tmp_path):
    scope = _scope(tmp_path)
    put_my_settings(MySettingsPayload(
        tool_max_iterations=99,
        tool_timeout_seconds=1,
        intro_chars=99999,
        galgame_max_loops=0,
    ), _req(scope))
    us = scope.user_settings
    assert us.tool_max_iterations == 40
    assert us.tool_timeout_seconds == 5
    assert us.intro_chars == 50000
    assert us.galgame_max_loops == 1

    put_my_settings(MySettingsPayload(
        tool_max_iterations=-5,
        tool_timeout_seconds=99999,
        galgame_max_loops=999,
    ), _req(scope))
    assert us.tool_max_iterations == 0
    assert us.tool_timeout_seconds == 600
    assert us.galgame_max_loops == 100


def test_bools_coerced(tmp_path):
    scope = _scope(tmp_path)
    put_my_settings(MySettingsPayload(
        web_enabled=1, browser_enabled=0, tools_enabled="false",
    ), _req(scope))
    us = scope.user_settings
    assert us.web_enabled is True
    assert us.browser_enabled is False
    assert us.tools_enabled is False


def test_non_integer_rejected():
    with pytest.raises(ValidationError):
        MySettingsPayload(tool_max_iterations="abc")
    with pytest.raises(ValidationError):
        MySettingsPayload(galgame_max_loops="x")


def test_other_user_unaffected(tmp_path):
    """覆盖只写本人 settings.json，别的用户仍跟随全局。"""
    cfg = AppConfig()
    scope_a = UserScope.build("ua", cfg, tmp_path, None)
    scope_b = UserScope.build("ub", cfg, tmp_path, None)
    put_my_settings(MySettingsPayload(web_enabled=False), _req(scope_a))
    assert feature_prefs.feature_enabled(
        "web", cfg, scope_b.user_settings) is True
