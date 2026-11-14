"""用户级功能偏好：全局默认 + 用户覆盖的唯一合并收口处。

规则：data/{uid}/settings.json 中覆盖字段为 None（或缺键）时跟随管理员全局
默认；非 None 即用户显式覆盖。合并结果只在请求时计算，不落回配置文件。
"""

from __future__ import annotations

from typing import Any, Optional, Set

# 工具名 → 功能类别。base 类（内容表/记忆/属性/读图片/插件）随工具总开关放行；
# tts 不下放，永远只看全局；其余类别各有全局开关 + 用户覆盖。
TOOL_CATEGORIES = {
    # 联网搜索
    "web_search": "web",
    "web_fetch": "web",
    # AI 浏览器
    "browser_open": "browser",
    "browser_snapshot": "browser",
    "browser_click": "browser",
    "browser_type": "browser",
    "browser_back": "browser",
    "browser_get_text": "browser",
    "browser_close": "browser",
    # P 站搜图
    "search_pixiv": "pixiv",
    "pick_pixiv": "pixiv",
    # AI 生图
    "generate_image": "image",
    "edit_image": "image",
    # Galgame
    "galgame_draw_background": "galgame",
    "galgame_draw_sprite": "galgame",
    "galgame_compose_music": "galgame",
    "galgame_list_assets": "galgame",
    "galgame_set_scene": "galgame",
    "galgame_choice": "galgame",
    # 语音合成（仅全局，不在用户覆盖列表里，但过滤时要识别）
    "text_to_speech": "tts",
    # 会话工作区沙箱（开关即 config.sandbox.enabled，仅全局）
    "fs_list": "sandbox",
    "fs_read": "sandbox",
    "fs_write": "sandbox",
    "fs_append": "sandbox",
    "fs_delete": "sandbox",
    # AI 自定义工具管理五件套（仅全局；发布动作另有注册表 admin 执行期门禁）
    "custom_tool_validate": "custom_tools",
    "custom_tool_publish": "custom_tools",
    "custom_tool_list": "custom_tools",
    "custom_tool_toggle": "custom_tools",
    "custom_tool_remove": "custom_tools",
    # P2 长跑模式（仅全局；loop_finish 为请求级 extra，不在全局表中）
    "loop_request": "loop",
    "loop_start": "loop",
    # P3 第一人称自我记忆（仅全局，默认开）
    "remember_self": "self_memory",
    "recall_self": "self_memory",
    # P3 承诺/提醒（随主动行为总闸，默认关）
    "remember_commitment": "proactor",
    "close_commitment": "proactor",
    # P4 监测进程管理三件套（risk=admin，可见性随 mcp 生态总闸）
    "watcher_start": "mcp",
    "watcher_stop": "mcp",
    "watcher_list": "mcp",
    # 已上线的自定义函数工具名是动态的（custom__<name>），按前缀识别；
    # mcp__<server>__<tool> 同为请求级动态工具，也不进这张静态表
}

# 类别 → (配置段名, 缺段/缺字段时的默认值)；默认值与各 Settings 类一致
_GLOBAL_FLAGS = {
    "web": ("web_search", True),
    "browser": ("browser", False),
    "pixiv": ("pixiv", True),
    "image": ("image_generation", True),
    "galgame": ("galgame", True),
    "tts": ("tts", True),
    "sandbox": ("sandbox", False),
    "custom_tools": ("custom_tools", False),
    "loop": ("loop", False),
    "self_memory": ("self_memory", True),
    "proactor": ("proactor", False),
    "mcp": ("mcp", False),
}

# 类别 → UserSettings 上的覆盖字段名
_USER_FLAGS = {
    "web": "web_enabled",
    "browser": "browser_enabled",
    "pixiv": "pixiv_enabled",
    "image": "image_enabled",
    "galgame": "galgame_enabled",
}


def _us(user_settings: Any):
    """管理员/测试简易对象可能没有 user_settings：视为全跟随默认。"""
    return user_settings if user_settings is not None else None


def feature_enabled(category: str, cfg, user_settings) -> bool:
    """某功能类别对该用户是否有效：用户覆盖优先，否则取全局默认。

    测试桩的精简 config 可能缺整个配置段：按该段真实默认值兜底。
    """
    flag = _USER_FLAGS.get(category)
    if flag is not None:
        override = getattr(user_settings, flag, None)
        if override is not None:
            return bool(override)
    section, default = _GLOBAL_FLAGS[category]
    seg = getattr(cfg, section, None)
    if seg is None:
        return default
    return bool(getattr(seg, "enabled", default))


def effective_tool_settings(cfg, user_settings):
    """合并后的 ToolSettings（不修改全局对象）。"""
    tools = getattr(cfg, "tools", None)
    if tools is None:
        # 精简测试桩没有 tools 段：用代码默认值
        from .config import ToolSettings

        tools = ToolSettings()
    updates = {}
    v = getattr(user_settings, "tools_enabled", None)
    if v is not None:
        updates["enabled"] = bool(v)
    v = getattr(user_settings, "tool_max_iterations", None)
    if v is not None:
        updates["max_iterations"] = int(v)
    v = getattr(user_settings, "tool_timeout_seconds", None)
    if v is not None:
        updates["timeout_seconds"] = int(v)
    return tools.model_copy(update=updates) if updates else tools


def effective_roleplay(cfg, user_settings):
    """合并后的角色扮演设置（含嵌套 attributes.enabled）。"""
    rp = getattr(cfg, "roleplay", None)
    if rp is None:
        return None
    updates = {}
    if getattr(user_settings, "roleplay_format_enabled", None) is not None:
        updates["format_enabled"] = bool(
            user_settings.roleplay_format_enabled
        )
    for key in (
        "intro_chars",
        "background_chars",
        "world_chars",
        "mes_example_chars",
        "pinned_chars",
    ):
        v = getattr(user_settings, key, None)
        if v is not None:
            updates[key] = int(v)
    attr_on = getattr(user_settings, "roleplay_attributes_enabled", None)
    if attr_on is not None or updates:
        rp = rp.model_copy(deep=True)
        if attr_on is not None:
            rp.attributes = rp.attributes.model_copy(
                update={"enabled": bool(attr_on)}
            )
        if updates:
            rp = rp.model_copy(update=updates)
    return rp


def effective_galgame_loops(cfg, user_settings) -> int:
    v = getattr(user_settings, "galgame_max_loops", None)
    if v is not None:
        return int(v)
    seg = getattr(cfg, "galgame", None)
    return int(getattr(seg, "max_loops", 40)) if seg is not None else 40


def base_tool_names(registry) -> Set[str]:
    """仅 base 类工具（内容表/记忆/属性/读图片/插件）：记忆存档等内部动作使用，
    不暴露任何用户功能开关对应的工具。"""
    return {
        t.name for t in registry.all()
        if TOOL_CATEGORIES.get(t.name, "base") == "base"
    }


def allowed_tool_names(registry, cfg, user_settings, disabled=None,
                       extra_names: Optional[Set[str]] = None) -> Optional[Set[str]]:
    """该用户请求中允许暴露给模型的工具名集合。

    返回 None 表示不过滤（理论上不会走到：这里总是显式计算）。
    工具总开关（合并后的 tools.enabled）关闭时，只剩空集。
    disabled：本轮在用户快捷开关里手动关闭的工具名集合，只做收窄，
    不能突破类别级功能开关/权限。
    extra_names：请求级工具名（P1 自定义函数工具 custom__*），仅在
    custom_tools 类别开启时并入——它们不在全局注册表里，需要显式放行。
    """
    tools = effective_tool_settings(cfg, user_settings)
    if not tools.enabled:
        return set()

    on_categories = {"base"}
    for category in ("web", "browser", "pixiv", "image", "galgame", "tts",
                     "sandbox", "custom_tools", "loop", "self_memory",
                     "proactor", "mcp"):
        if feature_enabled(category, cfg, user_settings):
            on_categories.add(category)

    allowed = set()
    for tool in registry.all():
        category = TOOL_CATEGORIES.get(tool.name, "base")  # 插件等未知名归 base
        if category in on_categories:
            allowed.add(tool.name)
    if extra_names:
        # 请求级工具白名单：自定义函数只放行合法前缀；
        # loop_finish 仅在 loop 类别开启时按精确名放行；
        # mcp__ 桥接工具仅在 mcp 类别开启时按前缀放行
        if "custom_tools" in on_categories:
            allowed |= {n for n in extra_names
                        if isinstance(n, str) and n.startswith("custom__")}
        if "loop" in on_categories:
            allowed |= {n for n in extra_names if n == "loop_finish"}
        if "mcp" in on_categories:
            allowed |= {n for n in extra_names
                        if isinstance(n, str) and n.startswith("mcp__")}
    if disabled:
        allowed -= {str(n) for n in disabled if isinstance(n, str)}
    return allowed
