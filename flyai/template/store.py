"""EJS 模板 global 作用域存储：每用户一个 JSON 文件，原子写入。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

MAX_FILE_BYTES = 1024 * 1024


def _empty() -> Dict[str, Any]:
    return {"version": 1, "vars": {}, "updated_at": ""}


def _valid_vars(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if value.get("version") != 1:
        return {}
    vars_ = value.get("vars")
    return vars_ if isinstance(vars_, dict) else {}


def user_store(state: Any, sc: Any) -> TemplateVarStore:
    """按用户定位 global 变量文件：优先 data_dir，其次用户设置目录。"""
    data_dir = getattr(state, "data_dir", None)
    if data_dir is None:
        settings_path = getattr(sc, "settings_path", None)
        if settings_path is None:
            raise ValueError("无法定位用户数据目录")
        path = Path(settings_path).parent / "template_vars.json"
    else:
        uid = str(getattr(sc, "uid", "") or "")
        path = Path(data_dir) / "template_vars" / (uid + ".json")
    return TemplateVarStore(path)


class TemplateVarStore:
    """按文件路径缓存数据；同一文件的读写用 RLock 串行化。"""

    _locks: Dict[str, threading.RLock] = {}
    _guard = threading.Lock()

    def __init__(self, path: Path):
        self.path = Path(path)
        key = str(self.path)
        with TemplateVarStore._guard:
            lock = TemplateVarStore._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                TemplateVarStore._locks[key] = lock
        self._lock = lock

    def load(self) -> Dict[str, Any]:
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError:
                return {}
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                return {}
            return _valid_vars(data)

    def load_document(self) -> Dict[str, Any]:
        """完整文档（含 version/updated_at）；损坏时返回空结构。"""
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError:
                return _empty()
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                return _empty()
            if not isinstance(data, dict) or data.get("version") != 1:
                return _empty()
            vars_ = data.get("vars")
            if not isinstance(vars_, dict):
                return _empty()
            return {
                "version": 1,
                "vars": vars_,
                "updated_at": str(data.get("updated_at") or ""),
            }

    def save(self, vars_: Dict[str, Any], updated_at: str = "") -> None:
        payload = {
            "version": 1,
            "vars": dict(vars_ or {}),
            "updated_at": updated_at,
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("模板变量超过 1MB 上限")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
