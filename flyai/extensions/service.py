"""扩展服务层：列表、安装、启停、设置与运行快照。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ids import check_safe_name
from .installer import InstallError, InstallLimits, InstallResult, install_zip
from .scanner import is_loadable
from .store import ExtensionStore


class ExtensionService:
    def __init__(self, data_dir: Path, settings: Any = None, user_root: Optional[Path] = None):
        self.settings = settings
        self.store = ExtensionStore(
            Path(data_dir) / "extensions", user_root=Path(user_root) if user_root else None
        )

    # ---------- 查询 ----------
    def _limits(self) -> InstallLimits:
        cfg = self.settings
        return InstallLimits(
            max_zip_bytes=int(getattr(cfg, "max_total_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024),
            max_total_bytes=int(getattr(cfg, "max_total_bytes", 20 * 1024 * 1024) or 20 * 1024 * 1024),
            max_files=int(getattr(cfg, "max_files", 500) or 500),
            max_file_bytes=5 * 1024 * 1024,
        )

    def list_for(self, uid: str) -> List[Dict[str, Any]]:
        state = self.store.user_state(uid)
        out: List[Dict[str, Any]] = []
        for record in self.store.records():
            manifest = self.store.manifest(record.name)
            report = self.store.compat(record.name)
            if manifest is None:
                continue
            out.append({
                **manifest.summary(),
                "installed_at": record.installed_at,
                "sha256": record.sha256,
                "enabled": bool(state["enabled"].get(record.name, False)),
                "order": int(state["order"].get(record.name, manifest.loading_order) or manifest.loading_order),
                "loadable": bool(report and is_loadable(manifest, report)),
                "compat_level": report.level if report else record.compat,
                "missing_modules": len(report.missing_modules) if report else 0,
                "missing_symbols": len(report.missing_symbols) if report else 0,
                "ui_risk": list(report.ui_risk) if report else [],
            })
        out.sort(key=lambda item: (item["order"], item["name"]))
        return out

    def detail(self, uid: str, name: str) -> Optional[Dict[str, Any]]:
        name = check_safe_name(name, "扩展名称")
        record = self.store.record(name)
        manifest = self.store.manifest(name)
        if record is None or manifest is None:
            return None
        report = self.store.compat(name)
        state = self.store.user_state(uid)
        return {
            "manifest": manifest.to_dict(),
            "summary": manifest.summary(),
            "record": record.to_dict(),
            "compat": report.to_dict() if report else None,
            "settings": self.store.settings(uid, name),
            "enabled": bool(state["enabled"].get(name, False)),
            "order": int(state["order"].get(name, manifest.loading_order) or manifest.loading_order),
        }

    # ---------- 安装 ----------
    def install(self, data: bytes, fallback_name: str = "", update: bool = False) -> InstallResult:
        return install_zip(self.store, data, fallback_name, self._limits(), update=update)

    def uninstall(self, name: str, purge_settings_uid: str = "") -> Dict[str, Any]:
        name = check_safe_name(name, "扩展名称")
        record = self.store.record(name)
        if record is None:
            raise InstallError("扩展不存在")
        self.store.remove_install_dir(name)
        self.store.remove_record(name)
        if purge_settings_uid:
            self.store.clear_settings(purge_settings_uid, name)
        return {"ok": True, "name": name}

    # ---------- 启停 / 顺序 ----------
    def set_enabled(self, uid: str, name: str, enabled: bool) -> Dict[str, Any]:
        name = check_safe_name(name, "扩展名称")
        manifest = self.store.manifest(name)
        if manifest is None:
            raise InstallError("扩展不存在")
        report = self.store.compat(name)
        if enabled and report is not None and not is_loadable(manifest, report):
            raise InstallError("兼容等级 %s，不能启用" % report.level)
        self.store.set_enabled(uid, name, enabled)
        return {"ok": True, "name": name, "enabled": bool(enabled)}

    def set_order(self, uid: str, order: List[str]) -> Dict[str, Any]:
        self.store.set_order(uid, order)
        return {"ok": True}

    # ---------- 设置 ----------
    def get_settings(self, uid: str, name: str) -> Dict[str, Any]:
        return self.store.settings(uid, name)

    def put_settings(self, uid: str, name: str, value: Dict[str, Any]) -> Dict[str, Any]:
        if self.store.manifest(name) is None:
            raise InstallError("扩展不存在")
        self.store.set_settings(uid, name, value)
        return {"ok": True}

    # ---------- 运行快照 ----------
    def host_state(self, uid: str, name: str) -> Optional[Dict[str, Any]]:
        detail = self.detail(uid, name)
        if detail is None:
            return None
        manifest = detail["manifest"]
        return {
            "name": name,
            "baseUrl": "/scripts/extensions/third-party/%s/" % name,
            "js": manifest["js"],
            "css": manifest["css"],
            "manifest": manifest,
            "settings": detail["settings"],
            "capabilities": manifest["flyai"].get("capabilities") or [],
            "order": detail["order"],
        }

    def panel_state(self, name: str) -> Optional[Dict[str, Any]]:
        """L2 面板：返回 schema（JSON）或 panel.html 的 URL；文件必须存在且受限。"""
        name = check_safe_name(name, "扩展名称")
        manifest = self.store.manifest(name)
        if manifest is None:
            return None
        panel = manifest.flyai.get("panel") if isinstance(manifest.flyai, dict) else None
        if not isinstance(panel, dict) or not panel.get("type"):
            return None
        kind = str(panel.get("type"))
        rel = str(panel.get("path") or "")
        if kind not in ("schema", "iframe") or not rel:
            return None
        files = self.store.files_dir(name)
        target = files.joinpath(*[p for p in rel.split("/") if p])
        try:
            resolved = target.resolve()
            root = files.resolve()
        except OSError:
            return None
        if root not in resolved.parents and resolved != root:
            return None
        if not resolved.is_file() or resolved.stat().st_size > 512 * 1024:
            return None
        base = "/scripts/extensions/third-party/%s/" % name
        out: Dict[str, Any] = {"type": kind, "path": rel, "url": base + rel}
        if kind == "schema":
            import json
            try:
                schema = json.loads(resolved.read_text(encoding="utf-8"))
            except Exception:
                return None
            if not isinstance(schema, dict):
                return None
            out["schema"] = schema
        return out

    def enabled_for(self, uid: str) -> List[Dict[str, Any]]:
        """按顺序返回可加载的扩展快照；依赖缺失/等级不符的写入 errors。"""
        state = self.store.user_state(uid)
        installed = {item["name"]: item for item in self.list_for(uid)}
        out: List[Dict[str, Any]] = []
        for name, enabled in state["enabled"].items():
            if not enabled:
                continue
            item = installed.get(name)
            if item is None:
                continue
            if not item["loadable"]:
                continue
            manifest = self.store.manifest(name)
            if manifest is None:
                continue
            missing_deps = [dep for dep in manifest.dependencies if dep not in installed]
            if missing_deps:
                out.append({
                    "name": name,
                    "error": "缺少依赖：%s" % "、".join(missing_deps),
                })
                continue
            snap = self.host_state(uid, name)
            if snap:
                out.append(snap)
        out.sort(key=lambda item: (item.get("order", 100), item.get("name", "")))
        return out
