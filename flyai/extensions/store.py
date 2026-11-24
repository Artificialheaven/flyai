"""扩展注册表、安装目录与用户状态的持久化。"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ids import InvalidName, check_safe_name, safe_child
from .models import CompatReport, ExtensionManifest, InstallRecord, ManifestError
from .manifest import manifest_from_dict

MAX_SETTINGS_BYTES = 1024 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Any]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


class ExtensionStore:
    def __init__(self, root: Path, user_root: Optional[Path] = None):
        self.root = Path(root)
        self.installed = self.root / "installed"
        self.tmp = self.root / "tmp"
        self.user_root = Path(user_root) if user_root else None
        self.installed.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ---------- 注册表 ----------
    def _registry_path(self) -> Path:
        return self.root / "registry.json"

    def registry(self) -> Dict[str, Any]:
        data = _read_json(self._registry_path())
        if not isinstance(data, dict):
            return {"version": 1, "extensions": {}}
        data.setdefault("version", 1)
        if not isinstance(data.get("extensions"), dict):
            data["extensions"] = {}
        return data

    def save_registry(self, data: Dict[str, Any]) -> None:
        with self._lock:
            _atomic_json(self._registry_path(), data)

    def records(self) -> List[InstallRecord]:
        out: List[InstallRecord] = []
        for name, item in self.registry().get("extensions", {}).items():
            if not isinstance(item, dict):
                continue
            out.append(InstallRecord(
                name=str(item.get("name") or name),
                display_name=str(item.get("display_name") or name),
                version=str(item.get("version") or "0.0.0"),
                sha256=str(item.get("sha256") or ""),
                installed_at=str(item.get("installed_at") or ""),
                source=str(item.get("source") or "zip"),
                compat=str(item.get("compat") or "L1"),
                manifest=dict(item.get("manifest") or {}),
            ))
        out.sort(key=lambda r: (r.installed_at, r.name))
        return out

    def record(self, name: str) -> Optional[InstallRecord]:
        name = check_safe_name(name, "扩展名称")
        for item in self.records():
            if item.name == name:
                return item
        return None

    def upsert_record(self, record: InstallRecord) -> None:
        with self._lock:
            data = self.registry()
            data["extensions"][record.name] = record.to_dict()
            self.save_registry(data)

    def remove_record(self, name: str) -> None:
        with self._lock:
            data = self.registry()
            data["extensions"].pop(name, None)
            self.save_registry(data)

    # ---------- 安装目录 ----------
    def install_dir(self, name: str) -> Path:
        return safe_child(self.installed, check_safe_name(name, "扩展名称"))

    def files_dir(self, name: str) -> Path:
        return self.install_dir(name) / "files"

    def manifest(self, name: str) -> Optional[ExtensionManifest]:
        path = self.install_dir(name) / "manifest.json"
        data = _read_json(path)
        if not isinstance(data, dict):
            return None
        try:
            return manifest_from_dict(data)
        except Exception:
            return None

    def compat(self, name: str) -> Optional[CompatReport]:
        data = _read_json(self.install_dir(name) / "compat-report.json")
        if not isinstance(data, dict):
            return None
        report = CompatReport(
            level=str(data.get("level") or "L1"),
            missing_modules=list(data.get("missingModules") or []),
            missing_symbols=list(data.get("missingSymbols") or []),
            modules=list(data.get("modules") or []),
            ui_risk=list(data.get("uiRisk") or []),
            files_scanned=int(data.get("filesScanned") or 0),
            declared_compat=bool(data.get("declaredCompat")),
        )
        return report

    def write_install(self, name: str, manifest: ExtensionManifest,
                      report: CompatReport) -> None:
        target = self.install_dir(name)
        target.mkdir(parents=True, exist_ok=True)
        _atomic_json(target / "manifest.json", manifest.to_dict())
        _atomic_json(target / "compat-report.json", report.to_dict())

    # ---------- 用户状态 ----------
    def _state_path(self, uid: str) -> Path:
        if self.user_root is None:
            raise RuntimeError("未配置用户状态目录")
        return self.user_root / check_safe_name(uid, "用户 id") / "extensions" / "state.json"

    def user_state(self, uid: str) -> Dict[str, Any]:
        data = _read_json(self._state_path(uid))
        if not isinstance(data, dict):
            data = {}
        data.setdefault("enabled", {})
        data.setdefault("order", {})
        data.setdefault("settings", {})
        for key in ("enabled", "order", "settings"):
            if not isinstance(data[key], dict):
                data[key] = {}
        return data

    def save_user_state(self, uid: str, data: Dict[str, Any]) -> None:
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("扩展状态超过 2MB")
        _atomic_json(self._state_path(uid), data)

    def set_enabled(self, uid: str, name: str, enabled: bool) -> None:
        name = check_safe_name(name, "扩展名称")
        with self._lock:
            state = self.user_state(uid)
            state["enabled"][name] = bool(enabled)
            state["order"].setdefault(name, 100)
            self.save_user_state(uid, state)

    def set_order(self, uid: str, order: List[str]) -> None:
        with self._lock:
            state = self.user_state(uid)
            for index, name in enumerate(order[:200]):
                state["order"][check_safe_name(name, "扩展名称")] = index * 10
            self.save_user_state(uid, state)

    def settings(self, uid: str, name: str) -> Dict[str, Any]:
        name = check_safe_name(name, "扩展名称")
        value = self.user_state(uid)["settings"].get(name)
        return dict(value) if isinstance(value, dict) else {}

    def set_settings(self, uid: str, name: str, value: Dict[str, Any]) -> None:
        name = check_safe_name(name, "扩展名称")
        if not isinstance(value, dict):
            raise ValueError("设置必须是对象")
        raw = json.dumps(value, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise ValueError("扩展设置超过 1MB")
        with self._lock:
            state = self.user_state(uid)
            state["settings"][name] = value
            self.save_user_state(uid, state)

    def clear_settings(self, uid: str, name: str) -> None:
        with self._lock:
            state = self.user_state(uid)
            state["settings"].pop(name, None)
            state["enabled"].pop(name, None)
            state["order"].pop(name, None)
            self.save_user_state(uid, state)

    def remove_install_dir(self, name: str) -> None:
        target = self.install_dir(name)
        if target.is_dir():
            for child in sorted(target.rglob("*"), reverse=True):
                try:
                    child.unlink() if child.is_file() or child.is_symlink() else child.rmdir()
                except OSError:
                    pass
            try:
                target.rmdir()
            except OSError:
                pass
