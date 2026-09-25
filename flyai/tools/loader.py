"""插件加载器：扫描目录、importlib 加载、调用 setup。

插件约定（两种等价方式）：
1. 目录含 __init__.py，其中定义 def setup(registry, plugin_config)，在 setup 里注册工具。
2. 或插件导入时直接调用 flyai.tools 的注册函数自注册（自动被收集）。

启动时执行一次，v1 不做热重载。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import Tool
from .registry import ToolRegistry

logger = logging.getLogger("flyai.plugins")


@dataclass
class PluginInfo:
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    enabled: bool = True
    loaded: bool = False
    error: Optional[str] = None
    tools: List[str] = field(default_factory=list)


class PluginLoader:
    """基于目录的插件加载器。"""

    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.infos: Dict[str, PluginInfo] = {}

    def load_all(self, plugin_dirs: List[str]) -> List[PluginInfo]:
        """加载所有启用的插件，返回状态汇总。单插件异常不影响其他插件。"""
        self.infos.clear()
        for dir_str in plugin_dirs:
            base = Path(dir_str)
            if not base.exists() or not base.is_dir():
                continue
            for sub in sorted(base.iterdir()):
                if not sub.is_dir():
                    continue
                name = sub.name
                init_path = sub / "__init__.py"
                manifest = self._read_manifest(sub)
                info = PluginInfo(
                    name=name,
                    version=manifest.get("version", ""),
                    description=manifest.get("description", ""),
                    author=manifest.get("author", ""),
                )
                self.infos[name] = info
                self._load_one(name, init_path, manifest)
        return self.list_status()

    def _read_manifest(self, plugin_dir: Path) -> Dict[str, Any]:
        mf = plugin_dir / "manifest.json"
        if mf.exists():
            try:
                return json.loads(mf.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("插件 %s manifest 解析失败: %s", plugin_dir.name, exc)
        return {}

    def _load_one(self, name: str, init_path: Path, manifest: Dict[str, Any]) -> None:
        """加载单插件。插件目录内的相对导入依赖 __init__ 作为包入口。"""
        info = self.infos[name]
        if not init_path.exists():
            info.loaded = False
            info.error = "缺少 __init__.py"
            return
        before_tools = set(self.registry.all())
        try:
            mod_name = f"flyai_plugin_{name}"
            spec = importlib.util.spec_from_file_location(
                mod_name,
                init_path,
                submodule_search_locations=[str(init_path.parent)],
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("无法创建加载 spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)

            plugin_config: Dict[str, Any] = {}
            setup = getattr(module, "setup", None)
            if callable(setup):
                setup(self.registry, plugin_config)
            info.loaded = True
        except Exception as exc:
            logger.exception("插件 %s 加载失败", name)
            info.loaded = False
            info.error = f"{type(exc).__name__}: {exc}"

        # 归属：本次加载新增的工具
        new_tools = set(t.name for t in self.registry.all()) - before_tools
        info.tools = sorted(new_tools)

    def list_status(self) -> List[PluginInfo]:
        return list(self.infos.values())