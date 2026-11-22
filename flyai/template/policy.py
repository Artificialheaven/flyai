"""EJS 模板权限判定：管理员总开关 + 最低等级 + 用户个人开关。"""

from __future__ import annotations

from typing import Any


def _feature(cfg: Any) -> Any:
    return getattr(cfg, "prompt_template", None)


def _user_flag(us: Any, name: str) -> bool:
    return bool(getattr(us, name, False))


def available(cfg: Any, level: int, is_admin: bool) -> bool:
    """管理员总开关与最低权限等级。"""
    feature = _feature(cfg)
    if feature is None or not bool(getattr(feature, "enabled", False)):
        return False
    if is_admin:
        return True
    return int(level or 0) >= int(getattr(feature, "min_level", 0) or 0)


def prompt_enabled(cfg: Any, us: Any, level: int, is_admin: bool) -> bool:
    return available(cfg, level, is_admin) and _user_flag(us, "template_enabled")


def output_enabled(cfg: Any, us: Any, level: int, is_admin: bool) -> bool:
    feature = _feature(cfg)
    if feature is None or not bool(getattr(feature, "allow_output_side", False)):
        return False
    return available(cfg, level, is_admin) and _user_flag(
        us, "template_output_enabled"
    )
