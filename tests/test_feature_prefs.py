"""用户功能偏好：全局默认 + 用户覆盖合并、工具类别过滤。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import AppConfig
from flyai.feature_prefs import (
    TOOL_CATEGORIES,
    allowed_tool_names,
    base_tool_names,
    effective_galgame_loops,
    effective_roleplay,
    effective_tool_settings,
    feature_enabled,
)
from flyai.tools.base import Tool, ToolContext, ToolResult
from flyai.tools.registry import ToolRegistry
from flyai.user import UserSettings


class _Dummy(Tool):
    """最小工具实现，只用于占名注册。"""

    def __init__(self, name):
        self.name = name

    async def run(self, arguments, ctx):
        return ToolResult(output="x")


def _registry():
    reg = ToolRegistry()
    # 每个真实功能名 + 一个未知名（模拟插件工具）
    for name in TOOL_CATEGORIES:
        reg.register(_Dummy(name))
    reg.register(_Dummy("query_lore"))
    reg.register(_Dummy("some_plugin_tool"))
    return reg


def test_all_none_follows_globals():
    cfg = AppConfig()
    us = UserSettings()
    assert effective_tool_settings(cfg, us) is cfg.tools
    rp = effective_roleplay(cfg, us)
    assert rp.format_enabled == cfg.roleplay.format_enabled
    assert feature_enabled("web", cfg, us) is True
    assert feature_enabled("browser", cfg, us) is False  # 全局默认关
    assert effective_galgame_loops(cfg, us) == cfg.galgame.max_loops


def test_overrides_win_and_global_object_untouched():
    cfg = AppConfig()
    cfg.tools.enabled = False
    cfg.web_search.enabled = True
    us = UserSettings(
        tools_enabled=True,
        tool_max_iterations=20,
        web_enabled=False,
        browser_enabled=True,
    )
    tools = effective_tool_settings(cfg, us)
    assert tools.enabled is True and tools.max_iterations == 20
    # 不修改全局对象
    assert cfg.tools.enabled is False
    assert feature_enabled("web", cfg, us) is False
    assert feature_enabled("browser", cfg, us) is True


def test_roleplay_overrides_including_nested_attributes():
    cfg = AppConfig()
    us = UserSettings(
        roleplay_format_enabled=False,
        roleplay_attributes_enabled=False,
        intro_chars=1234,
    )
    rp = effective_roleplay(cfg, us)
    assert rp.format_enabled is False
    assert rp.intro_chars == 1234
    assert rp.attributes.enabled is False
    # 全局未被改动
    assert cfg.roleplay.format_enabled is True
    assert cfg.roleplay.attributes.enabled is True
    # 未覆盖的预算保持全局值
    assert rp.background_chars == cfg.roleplay.background_chars


def test_allowed_names_filters_categories():
    cfg = AppConfig()
    reg = _registry()
    us = UserSettings(web_enabled=False, pixiv_enabled=False, image_enabled=False,
                      browser_enabled=False, galgame_enabled=False)
    allowed = allowed_tool_names(reg, cfg, us)
    assert "web_search" not in allowed
    assert "search_pixiv" not in allowed
    assert "generate_image" not in allowed
    assert "browser_open" not in allowed
    assert "galgame_choice" not in allowed
    # base 与未知名（插件）放行；tts 随全局默认
    assert "query_lore" in allowed and "some_plugin_tool" in allowed
    assert "text_to_speech" in allowed  # AppConfig 默认 tts enabled


def test_tools_master_switch_kills_everything():
    cfg = AppConfig()
    reg = _registry()
    us = UserSettings(tools_enabled=False)
    assert allowed_tool_names(reg, cfg, us) == set()


def test_enabled_category_exposes_tools():
    cfg = AppConfig()  # browser 默认关
    reg = _registry()
    assert "browser_open" not in allowed_tool_names(reg, cfg, UserSettings())
    us = UserSettings(browser_enabled=True)
    assert "browser_open" in allowed_tool_names(reg, cfg, us)


def test_request_disabled_only_narrows():
    """输入框快捷开关只能收窄：关掉已暴露的工具生效，无法借 disabled 增加工具。"""
    cfg = AppConfig()
    reg = _registry()
    us = UserSettings(browser_enabled=False)
    allowed = allowed_tool_names(
        reg, cfg, us, disabled={"web_search", "browser_open", "ghost"}
    )
    assert "web_search" not in allowed  # 手动关闭
    assert "web_fetch" in allowed       # 同类其他工具不受影响
    assert "browser_open" not in allowed  # 本来就关，再关也无变化
    assert "query_lore" in allowed      # 未列入禁用，照常暴露
    assert "ghost" not in allowed      # 不存在的名字不会凭空出现
    # None/空：结果与不传一致
    assert allowed_tool_names(reg, cfg, us, disabled=set()) == \
        allowed_tool_names(reg, cfg, us)


def test_base_tool_names_excludes_features():
    reg = _registry()
    base = base_tool_names(reg)
    assert "query_lore" in base and "some_plugin_tool" in base
    assert "web_search" not in base and "generate_image" not in base
    assert "text_to_speech" not in base


def test_registry_specs_filters_by_name():
    reg = _registry()
    assert len(reg.specs()) == len(reg.all())
    picked = reg.specs({"web_search"})
    assert len(picked) == 1
    assert picked[0]["function"]["name"] == "web_search"
    assert reg.specs(set()) == []


def test_galgame_loops_override():
    cfg = AppConfig()
    assert effective_galgame_loops(cfg, UserSettings(galgame_max_loops=77)) == 77


def test_category_map_covers_registered_feature_tools():
    """防漏网：app 注册的全部功能工具名都必须在类别表里（tts 除外也行，已登记）。"""
    from flyai.app import AppState

    cfg = AppConfig()
    cfg.tools.plugin_dirs = []  # 干净环境：不扫仓库里可能存在的插件目录
    state = AppState(config=cfg)
    state.reload_tools()
    registered = {t.name for t in state.registry.all()}
    missing = registered - set(TOOL_CATEGORIES) - {
        # base 类（恒注册的内置工具）
        "query_lore", "query_chat_memory", "update_chat_memory",
        "update_attributes", "read_image",
    }
    # 插件目录可能额外注册；干净环境下应为空
    assert missing == set(), "未分类工具名: %s" % missing
