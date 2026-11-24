"""酒馆扩展（第三方前端扩展）的数据模型。

字段命名尽量保留 ST 原字段，`flyai` 段是本项目的能力声明。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..ids import InvalidName, check_safe_name

MAX_DISPLAY_NAME = 120
MAX_VERSION = 32
MAX_JS_PATH = 200
MAX_CAPABILITIES = 32


class ManifestError(ValueError):
    """manifest 非法（安装接口应转 400）。"""


_SAFE_REL_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff /]+$")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.\-]+)?$")

KNOWN_CAPABILITIES = {
    "chat.read",
    "chat.write",
    "variables.read",
    "variables.write",
    "worldbook.read",
    "worldbook.write",
    "generate.quiet",
    "generate.raw",
    "prompt.inject",
    "settings",
    "storage",
    "net",
    "slash.register",
    "slash.execute",
    "ui",
}


def safe_rel_path(value: str, what: str = "路径") -> str:
    """包内相对路径校验：禁止绝对路径、..、反斜杠、协议前缀与控制字符。"""
    raw = str(value or "").strip()
    if not raw:
        raise ManifestError("%s 为空" % what)
    if len(raw) > MAX_JS_PATH:
        raise ManifestError("%s 过长" % what)
    if raw.startswith("/") or raw.startswith("\\"):
        raise ManifestError("%s 不能是绝对路径" % what)
    if "\\" in raw or ".." in raw.split("/") or "://" in raw or raw.startswith("data:"):
        raise ManifestError("%s 含非法字符" % what)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ManifestError("%s 含控制字符" % what)
    if not _SAFE_REL_RE.match(raw):
        raise ManifestError("%s 含不支持的字符" % what)
    return raw


@dataclass
class ExtensionManifest:
    name: str
    display_name: str
    js: str = "index.js"
    css: str = ""
    version: str = "0.0.0"
    author: str = ""
    home_page: str = ""
    loading_order: int = 100
    requires: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    hooks: Dict[str, str] = field(default_factory=dict)
    minimum_client_version: str = ""
    flyai: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def compat(self) -> str:
        value = str(self.flyai.get("compat") or "logic").strip().lower()
        return value if value in ("logic", "panel", "ui") else "logic"

    @property
    def host_api(self) -> int:
        try:
            return max(1, int(self.flyai.get("host_api") or 1))
        except (TypeError, ValueError):
            return 1

    @property
    def capabilities(self) -> List[str]:
        raw = self.flyai.get("capabilities")
        if not isinstance(raw, list):
            raw = []
        out: List[str] = []
        for item in raw:
            value = str(item or "").strip()
            if value and value in KNOWN_CAPABILITIES and value not in out:
                out.append(value)
        return out[:MAX_CAPABILITIES]

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "js": self.js,
            "css": self.css,
            "version": self.version,
            "author": self.author,
            "home_page": self.home_page,
            "loading_order": self.loading_order,
            "requires": list(self.requires),
            "dependencies": list(self.dependencies),
            "hooks": dict(self.hooks),
            "minimum_client_version": self.minimum_client_version,
            "flyai": dict(self.flyai),
        }

    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "author": self.author,
            "loading_order": self.loading_order,
            "compat": self.compat,
            "capabilities": self.capabilities,
            "hooks": sorted(self.hooks.keys()),
        }


@dataclass
class CompatReport:
    level: str = "L1"
    missing_modules: List[str] = field(default_factory=list)
    missing_symbols: List[Dict[str, str]] = field(default_factory=list)
    modules: List[Dict[str, Any]] = field(default_factory=list)
    ui_risk: List[str] = field(default_factory=list)
    files_scanned: int = 0
    declared_compat: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "missingModules": list(self.missing_modules),
            "missingSymbols": list(self.missing_symbols),
            "modules": list(self.modules),
            "uiRisk": list(self.ui_risk),
            "filesScanned": self.files_scanned,
            "declaredCompat": self.declared_compat,
        }


@dataclass
class InstallRecord:
    name: str
    display_name: str
    version: str
    sha256: str
    installed_at: str
    source: str = "zip"
    compat: str = "L1"
    manifest: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "sha256": self.sha256,
            "installed_at": self.installed_at,
            "source": self.source,
            "compat": self.compat,
            "manifest": dict(self.manifest),
        }
