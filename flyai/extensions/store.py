"""扩展注册表、安装目录与用户状态的持久化。

可写数据（registry / InstallRecord / 用户 state）落在扩展根目录下的
``extensions.db``（SQLite）；扩展安装目录里的 ``manifest.json``、
``compat-report.json`` 与代码文件属于「安装包资产」，继续留在文件系统原路径。
旧 ``registry.json`` 与 ``<user_root>/<uid>/extensions/state.json`` 在首次打开
时自动导入并改名 ``.bak-<时间戳>``。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ids import InvalidName, check_safe_name, safe_child
from ..sqlite_store import JsonTable, SqliteDB, migrate_legacy_file
from .models import CompatReport, ExtensionManifest, InstallRecord, ManifestError
from .manifest import manifest_from_dict

MAX_SETTINGS_BYTES = 1024 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024


def _atomic_json(path: Path, data: Any) -> None:
    """安装包资产（manifest / compat-report）的原子写。"""
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


def _coerce_version(value: Any) -> int:
    try:
        return int(value or 1)
    except (TypeError, ValueError):
        return 1


class ExtensionStore:
    def __init__(self, root: Path, user_root: Optional[Path] = None):
        self.root = Path(root)
        self.installed = self.root / "installed"
        self.tmp = self.root / "tmp"
        self.user_root = Path(user_root) if user_root else None
        self.installed.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = SqliteDB(self.root / "extensions.db")
        # registry：只存全局元信息（version）；安装记录一条一行。
        self._registry = JsonTable(self.db, "registry")
        self._records = JsonTable(self.db, "records")
        self._states = JsonTable(self.db, "user_state")
        self._migrate_legacy()

    # ---------- 旧数据迁移 ----------
    def _migrate_legacy(self) -> None:
        """导入旧 registry.json / 各用户 state.json；重复启动不会重复导入。"""
        with self._lock:
            migrate_legacy_file(self.root / "registry.json", self._import_registry)
            if self.user_root is None:
                return
            try:
                users = sorted(p for p in self.user_root.iterdir() if p.is_dir())
            except OSError:
                return
            for user_dir in users:
                legacy = user_dir / "extensions" / "state.json"
                if not legacy.is_file():
                    continue
                try:
                    uid = check_safe_name(user_dir.name, "用户 id")
                except InvalidName:
                    continue
                migrate_legacy_file(
                    legacy, lambda doc, _uid=uid: self._import_user_state(_uid, doc)
                )

    def _import_registry(self, doc: Dict[str, Any]) -> None:
        if not self._registry.exists("registry"):
            self._registry.put("registry", {"version": _coerce_version(doc.get("version"))})
        extensions = doc.get("extensions")
        if not isinstance(extensions, dict):
            return
        for name, item in extensions.items():
            if not isinstance(item, dict):
                continue
            if not self._records.exists(str(name)):
                self._records.put(str(name), item)

    def _import_user_state(self, uid: str, doc: Dict[str, Any]) -> None:
        if not self._states.exists(uid):
            self._states.put(uid, doc)

    # ---------- 注册表 ----------
    def registry(self) -> Dict[str, Any]:
        meta = self._registry.get("registry") or {}
        return {
            "version": _coerce_version(meta.get("version")),
            "extensions": {name: doc for name, doc in self._records.items()},
        }

    def save_registry(self, data: Dict[str, Any]) -> None:
        """整表覆盖语义（与原 registry.json 的 save_registry 保持一致）。"""
        with self._lock:
            version = _coerce_version(data.get("version"))
            self._registry.put("registry", {"version": version})
            extensions = data.get("extensions")
            if not isinstance(extensions, dict):
                extensions = {}
            for name, item in extensions.items():
                if isinstance(item, dict):
                    self._records.put(str(name), item)
            for name in self._records.keys():
                if name not in extensions:
                    self._records.delete(name)

    def records(self) -> List[InstallRecord]:
        out: List[InstallRecord] = []
        for name, item in self._records.items():
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
        data = self._records.get(name)
        if data is None:
            return None
        return InstallRecord(
            name=str(data.get("name") or name),
            display_name=str(data.get("display_name") or name),
            version=str(data.get("version") or "0.0.0"),
            sha256=str(data.get("sha256") or ""),
            installed_at=str(data.get("installed_at") or ""),
            source=str(data.get("source") or "zip"),
            compat=str(data.get("compat") or "L1"),
            manifest=dict(data.get("manifest") or {}),
        )

    def upsert_record(self, record: InstallRecord) -> None:
        with self._lock:
            self._records.put(record.name, record.to_dict())

    def remove_record(self, name: str) -> None:
        with self._lock:
            self._records.delete(name)

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
    def _state_key(self, uid: str) -> str:
        if self.user_root is None:
            raise RuntimeError("未配置用户状态目录")
        return check_safe_name(uid, "用户 id")

    def user_state(self, uid: str) -> Dict[str, Any]:
        key = self._state_key(uid)
        data = self._states.get(key)
        if not isinstance(data, dict):
            data = {}
        data.setdefault("enabled", {})
        data.setdefault("order", {})
        data.setdefault("settings", {})
        for name in ("enabled", "order", "settings"):
            if not isinstance(data[name], dict):
                data[name] = {}
        return data

    def save_user_state(self, uid: str, data: Dict[str, Any]) -> None:
        key = self._state_key(uid)
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("扩展状态超过 2MB")
        self._states.put(key, data)

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
