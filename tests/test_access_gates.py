"""功能权限门槛：工具可见集、工具清单标注、Galgame 建房/接口 403。"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi import HTTPException

from flyai.api import routes_galgame, routes_plugins, routes_room
from flyai.config import AccessSettings, AppConfig
from flyai.feature_prefs import TOOL_CATEGORIES, allowed_tool_names, required_level_for
from flyai.lore.models import Character
from flyai.sessions.json_store import JsonSessionStore
from flyai.sessions.models import RoomMember, Session, Thread
from flyai.tools.base import Tool, ToolResult
from flyai.tools.registry import ToolRegistry
from flyai.user import ADMIN_UID, UserSettings


class _Dummy(Tool):
    def __init__(self, name):
        self.name = name

    async def run(self, arguments, ctx):
        return ToolResult(output="x")


def _registry():
    reg = ToolRegistry()
    for name in TOOL_CATEGORIES:
        reg.register(_Dummy(name))
    reg.register(_Dummy("query_lore"))
    reg.register(_Dummy("some_plugin_tool"))
    return reg


def _all_on_cfg():
    cfg = AppConfig()
    for seg in (cfg.browser, cfg.sandbox, cfg.custom_tools, cfg.loop,
                cfg.proactor, cfg.mcp):
        seg.enabled = True
    return cfg


def _request(state, sc):
    state.user = lambda request: sc
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(flyai=state)))


class _Lore:
    def __init__(self, chars=()):
        self._chars = {c.id: c for c in chars}

    def get_character(self, cid):
        return self._chars.get(cid)

    def get_world(self, wid):
        return None


# ---- required_level_for ----

def test_required_level_for_maps_categories():
    cfg = AppConfig()
    cfg.access = AccessSettings(browser_min_level=10, pixiv_min_level=20,
                                galgame_min_level=30, agent_min_level=40)
    assert required_level_for("base", cfg) == 0
    assert required_level_for("browser", cfg) == 10
    assert required_level_for("pixiv", cfg) == 20
    assert required_level_for("galgame", cfg) == 30
    assert required_level_for("web", cfg) == 40
    assert required_level_for("sandbox", cfg) == 40
    assert required_level_for("mcp", cfg) == 40
    assert required_level_for("unknown", cfg) == 40
    # admin 恒 0，不依赖 level 数值
    assert required_level_for("browser", cfg, is_admin=True) == 0
    assert required_level_for("web", cfg, is_admin=True) == 0
    # 精简测试配置没有 access 段：全部不限
    assert required_level_for("web", SimpleNamespace(access=None)) == 0


# ---- allowed_tool_names 等级过滤 ----

def test_thresholds_gate_categories_by_level():
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(browser_min_level=10, pixiv_min_level=20,
                                galgame_min_level=30, agent_min_level=40)
    reg = _registry()
    us = UserSettings()
    gated = (
        "browser_open", "search_pixiv", "galgame_choice", "web_search",
        "text_to_speech", "fs_read", "custom_tool_list", "loop_request",
        "remember_self", "remember_commitment", "watcher_list",
    )

    at0 = allowed_tool_names(reg, cfg, us, level=0)
    assert all(name not in at0 for name in gated)
    # base 与插件工具始终可见
    assert "query_lore" in at0 and "some_plugin_tool" in at0

    at10 = allowed_tool_names(reg, cfg, us, level=10)
    assert "browser_open" in at10
    assert "search_pixiv" not in at10 and "web_search" not in at10

    at20 = allowed_tool_names(reg, cfg, us, level=20)
    assert "search_pixiv" in at20 and "galgame_choice" not in at20

    at30 = allowed_tool_names(reg, cfg, us, level=30)
    assert "galgame_choice" in at30 and "web_search" not in at30

    at40 = allowed_tool_names(reg, cfg, us, level=40)
    assert all(name in at40 for name in gated)


def test_special_thresholds_not_double_blocked_by_agent():
    """browser/pixiv/galgame 达到专属阈值即可，即使 agent 阈值更高。"""
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(browser_min_level=10, pixiv_min_level=10,
                                galgame_min_level=10, agent_min_level=255)
    reg = _registry()
    at10 = allowed_tool_names(reg, cfg, UserSettings(), level=10)
    assert {"browser_open", "search_pixiv", "galgame_choice"} <= at10
    assert "web_search" not in at10


def test_admin_ignores_all_thresholds():
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(browser_min_level=255, pixiv_min_level=255,
                                galgame_min_level=255, agent_min_level=255)
    reg = _registry()
    admin = allowed_tool_names(reg, cfg, UserSettings(), level=0, is_admin=True)
    full = allowed_tool_names(reg, cfg, UserSettings(), level=255)
    assert admin == full
    assert "browser_open" in admin and "fs_read" in admin


def test_base_tools_visible_at_zero_level_with_max_thresholds():
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(browser_min_level=255, pixiv_min_level=255,
                                galgame_min_level=255, agent_min_level=255)
    reg = _registry()
    allowed = allowed_tool_names(reg, cfg, UserSettings(), level=0)
    assert allowed == {"query_lore", "some_plugin_tool"}


def test_disabled_still_narrows_above_threshold():
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(agent_min_level=40)
    reg = _registry()
    allowed = allowed_tool_names(reg, cfg, UserSettings(), level=60,
                                 disabled={"web_search", "fs_read"})
    assert "web_search" not in allowed and "fs_read" not in allowed
    assert "web_fetch" in allowed and "fs_write" in allowed


def test_extra_names_gated_by_category_threshold():
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(agent_min_level=40)
    reg = _registry()
    extras = {"custom__hi", "loop_finish", "mcp__srv__tool", "mystery"}
    at0 = allowed_tool_names(reg, cfg, UserSettings(), level=0,
                             extra_names=extras)
    assert not ({"custom__hi", "loop_finish", "mcp__srv__tool"} & at0)
    at40 = allowed_tool_names(reg, cfg, UserSettings(), level=40,
                              extra_names=extras)
    assert {"custom__hi", "loop_finish", "mcp__srv__tool"} <= at40
    assert "mystery" not in at40
    narrowed = allowed_tool_names(reg, cfg, UserSettings(), level=40,
                                  extra_names=extras, disabled={"custom__hi"})
    assert "custom__hi" not in narrowed


# ---- GET /api/tools 标注 ----

def test_list_tools_marks_gated_tools_disallowed(tmp_path):
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(browser_min_level=20, agent_min_level=50)
    store = JsonSessionStore(tmp_path / "sessions")
    sc = SimpleNamespace(uid="u1", level=10, store=store, lore=_Lore(),
                         user_settings=UserSettings())
    state = SimpleNamespace(config=cfg, registry=_registry())
    request = _request(state, sc)

    by_name = {t["name"]: t for t in routes_plugins.list_tools(request)["tools"]}
    assert by_name["query_lore"]["allowed"] is True
    assert by_name["some_plugin_tool"]["allowed"] is True
    assert by_name["browser_open"]["allowed"] is False
    assert by_name["fs_read"]["allowed"] is False

    sc.level = 60
    by_name = {t["name"]: t for t in routes_plugins.list_tools(request)["tools"]}
    assert by_name["browser_open"]["allowed"] is True
    assert by_name["fs_read"]["allowed"] is True


def test_list_tools_admin_ignores_thresholds(tmp_path):
    cfg = _all_on_cfg()
    cfg.access = AccessSettings(browser_min_level=255, agent_min_level=255)
    sc = SimpleNamespace(uid=ADMIN_UID, level=0,
                         store=JsonSessionStore(tmp_path / "sessions"),
                         lore=_Lore(), user_settings=UserSettings())
    state = SimpleNamespace(config=cfg, registry=_registry())
    by_name = {t["name"]: t
               for t in routes_plugins.list_tools(_request(state, sc))["tools"]}
    assert by_name["browser_open"]["allowed"] is True
    assert by_name["fs_read"]["allowed"] is True


# ---- 建房与 Galgame 接口门槛 ----

def _galgame_cfg(required=30):
    cfg = AppConfig()
    cfg.access = AccessSettings(galgame_min_level=required)
    return cfg


def test_create_galgame_room_below_level_403(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    sc = SimpleNamespace(uid="u1", level=10, store=store,
                         lore=_Lore([Character(id="c1", name="凛")]),
                         user_settings=UserSettings())
    state = SimpleNamespace(config=_galgame_cfg())
    with pytest.raises(HTTPException) as ei:
        routes_room.create_room(
            routes_room.RoomCreate(character_ids=["c1"], mode="galgame"),
            _request(state, sc))
    assert ei.value.status_code == 403
    assert "Lv30" in ei.value.detail and "Lv10" in ei.value.detail
    assert store.list() == []  # 不留下空会话


def test_create_galgame_room_admin_passes_at_level_zero(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    sc = SimpleNamespace(uid=ADMIN_UID, level=0, store=store,
                         lore=_Lore([Character(id="c1", name="凛")]),
                         user_settings=UserSettings())
    state = SimpleNamespace(config=_galgame_cfg())
    out = routes_room.create_room(
        routes_room.RoomCreate(character_ids=["c1"], mode="galgame"),
        _request(state, sc))
    assert out["mode"] == "galgame"
    assert len(store.list()) == 1


def test_normal_room_not_gated_by_galgame_level(tmp_path):
    store = JsonSessionStore(tmp_path / "sessions")
    sc = SimpleNamespace(uid="u1", level=0, store=store,
                         lore=_Lore([Character(id="c1", name="凛")]),
                         user_settings=UserSettings())
    state = SimpleNamespace(config=_galgame_cfg(required=255))
    out = routes_room.create_room(
        routes_room.RoomCreate(character_ids=["c1"]), _request(state, sc))
    assert out.get("mode") != "galgame"


def _galgame_session_env(tmp_path, level, uid="u1"):
    store = JsonSessionStore(tmp_path / "sessions")
    s = Session(id="sid1", title="g", mode="galgame")
    s.members.append(RoomMember(name="你", is_user=True))
    store.save(s)
    sc = SimpleNamespace(uid=uid, level=level, store=store)
    state = SimpleNamespace(config=_galgame_cfg())
    return state, sc


def test_galgame_session_below_level_403(tmp_path):
    state, sc = _galgame_session_env(tmp_path, level=5)
    with pytest.raises(HTTPException) as ei:
        routes_galgame._galgame_session(state, sc, "sid1")
    assert ei.value.status_code == 403
    assert "Lv30" in ei.value.detail


def test_galgame_session_admin_or_high_level_passes(tmp_path):
    state, sc = _galgame_session_env(tmp_path, level=30)
    assert routes_galgame._galgame_session(state, sc, "sid1").id == "sid1"
    state, sc = _galgame_session_env(tmp_path, level=0, uid=ADMIN_UID)
    assert routes_galgame._galgame_session(state, sc, "sid1").id == "sid1"