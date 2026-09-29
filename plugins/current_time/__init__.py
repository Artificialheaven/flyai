"""current_time 插件：注册获取当前时间的工具。"""

from __future__ import annotations

from .plugin import CurrentTimeTool


def setup(registry, plugin_config):
    registry.register(CurrentTimeTool())
