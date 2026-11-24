"""manifest.json 解析与校验。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..ids import InvalidName, check_safe_name
from .models import (
    MAX_CAPABILITIES,
    MAX_DISPLAY_NAME,
    MAX_VERSION,
    ExtensionManifest,
    ManifestError,
    safe_rel_path,
)

_HOOK_NAMES = ("install", "update", "delete", "activate", "enable", "disable", "clean")
_IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.*\-]+(?::\d{1,5})?$")


def _text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _string_list(value: Any, what: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError("%s 必须是数组" % what)
    out: List[str] = []
    for item in value:
        text = _text(item, 100)
        if text and text not in out:
            out.append(text)
    return out[:50]


def _parse_flyai(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError("flyai 必须是对象")
    out: Dict[str, Any] = {}
    raw_compat = _text(value.get("compat"), 16).lower()
    if raw_compat in ("logic", "panel", "ui"):
        out["compat"] = raw_compat
    try:
        host_api = int(value.get("host_api") or 1)
    except (TypeError, ValueError):
        host_api = 1
    out["host_api"] = max(1, min(host_api, 99))
    caps = value.get("capabilities")
    if caps is not None:
        if not isinstance(caps, list):
            raise ManifestError("flyai.capabilities 必须是数组")
        out["capabilities"] = [_text(item, 40) for item in caps][:MAX_CAPABILITIES]
    network = value.get("network")
    if isinstance(network, dict):
        hosts = network.get("allowed_hosts")
        if hosts is not None:
            if not isinstance(hosts, list):
                raise ManifestError("flyai.network.allowed_hosts 必须是数组")
            cleaned: List[str] = []
            for host in hosts[:32]:
                text = _text(host, 120).lower()
                if text and _HOST_RE.match(text) and text not in cleaned:
                    cleaned.append(text)
            out["network"] = {"allowed_hosts": cleaned}
    panel = value.get("panel")
    if isinstance(panel, dict):
        kind = _text(panel.get("type"), 16).lower()
        if kind in ("schema", "iframe"):
            entry: Dict[str, Any] = {"type": kind}
            target = panel.get("schema") if kind == "schema" else panel.get("html")
            if target:
                try:
                    entry["path"] = safe_rel_path(str(target), "flyai.panel 路径")
                except ManifestError:
                    entry["path"] = ""
            out["panel"] = entry
    return out


def parse_manifest(
    data: Dict[str, Any], fallback_name: str = ""
) -> ExtensionManifest:
    """把 manifest.json 字典转成受校验的 ExtensionManifest。"""
    if not isinstance(data, dict):
        raise ManifestError("manifest 必须是 JSON 对象")

    display_name = _text(data.get("display_name") or data.get("displayName"), MAX_DISPLAY_NAME)
    if not display_name:
        raise ManifestError("manifest 缺少 display_name")

    flyai = _parse_flyai(data.get("flyai"))
    raw_name = _text(flyai.get("name") or data.get("name") or fallback_name, 100)
    if not raw_name:
        raise ManifestError("缺少扩展名称（目录名或 flyai.name）")
    try:
        name = check_safe_name(raw_name, "扩展名称")
    except InvalidName as exc:
        raise ManifestError(str(exc))

    js_raw = data.get("js")
    if not js_raw:
        raise ManifestError("manifest 缺少 js 入口")
    js = safe_rel_path(str(js_raw), "js 入口")

    css = ""
    if data.get("css"):
        css = safe_rel_path(str(data.get("css")), "css 路径")

    version = _text(data.get("version"), MAX_VERSION) or "0.0.0"
    if not re.match(r"^\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.\-]+)?$", version):
        raise ManifestError("version 格式非法")

    try:
        loading_order = int(data.get("loading_order", 100))
    except (TypeError, ValueError):
        loading_order = 100
    loading_order = max(-10000, min(10000, loading_order))

    hooks: Dict[str, str] = {}
    raw_hooks = data.get("hooks")
    if raw_hooks is not None:
        if not isinstance(raw_hooks, dict):
            raise ManifestError("hooks 必须是对象")
        for key in _HOOK_NAMES:
            fn = _text(raw_hooks.get(key), 80)
            if not fn:
                continue
            if not _IDENT_RE.match(fn):
                raise ManifestError("hooks.%s 必须是合法函数名" % key)
            hooks[key] = fn

    minimum = _text(data.get("minimum_client_version"), 32)

    return ExtensionManifest(
        name=name,
        display_name=display_name,
        js=js,
        css=css,
        version=version,
        author=_text(data.get("author"), 120),
        home_page=_text(data.get("homePage") or data.get("home_page"), 300),
        loading_order=loading_order,
        requires=_string_list(data.get("requires"), "requires"),
        dependencies=_string_list(data.get("dependencies"), "dependencies"),
        hooks=hooks,
        minimum_client_version=minimum,
        flyai=flyai,
        raw=dict(data),
    )


def manifest_from_dict(data: Dict[str, Any]) -> ExtensionManifest:
    """注册表里已校验过的 manifest 重新装载。"""
    flyai = data.get("flyai") or {}
    try:
        name = check_safe_name(str(data.get("name") or ""), "扩展名称")
    except InvalidName:
        name = ""
    return ExtensionManifest(
        name=name,
        display_name=_text(data.get("display_name"), MAX_DISPLAY_NAME),
        js=_text(data.get("js"), 200) or "index.js",
        css=_text(data.get("css"), 200),
        version=_text(data.get("version"), MAX_VERSION) or "0.0.0",
        author=_text(data.get("author"), 120),
        home_page=_text(data.get("home_page"), 300),
        loading_order=int(data.get("loading_order") or 100),
        requires=list(data.get("requires") or []),
        dependencies=list(data.get("dependencies") or []),
        hooks=dict(data.get("hooks") or {}),
        minimum_client_version=_text(data.get("minimum_client_version"), 32),
        flyai=dict(flyai) if isinstance(flyai, dict) else {},
        raw=dict(data),
    )
