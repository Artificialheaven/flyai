"""file_reader 插件：白名单安全读取本地文件。"""

from .plugin import ReadFileTool


def setup(registry, plugin_config):
    registry.register(ReadFileTool())