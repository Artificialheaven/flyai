"""EJS 模板 global 作用域存储：每用户一行，落在同目录 ``template_vars.db``。

构造参数仍是旧的 ``template_vars.json`` 路径（路由层不用改）：数据库固定为
该文件同目录的 ``template_vars.db``，行 key 取旧文件名（如 ``u1``）。
首次打开自动导入旧 JSON 并改名 ``.bak-<时间戳>``。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Tuple

from ..sqlite_store import JsonTable, SqliteDB, migrate_legacy_file

MAX_FILE_BYTES = 1024 * 1024

# 同一个 db 只建一个 JsonTable（连接是线程本地的，缓存跨线程安全）。
_TABLES: Dict[str, JsonTable] = {}
_MIGRATED: set = set()
_GUARD = threading.Lock()


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
    """按路径缓存数据；同一文件的读写用 RLock 串行化。"""

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

    # ---------- 存储定位 / 迁移 ----------
    def _table(self) -> Tuple[JsonTable, str]:
        if self.path.suffix == ".db":
            db_path = self.path
            row_key = "global"
        else:
            db_path = self.path.parent / "template_vars.db"
            row_key = self.path.stem
        with _GUARD:
            table = _TABLES.get(str(db_path))
            if table is None:
                table = JsonTable(SqliteDB(db_path), "vars")
                _TABLES[str(db_path)] = table
            if self.path.suffix != ".db" and str(self.path) not in _MIGRATED:
                migrate_legacy_file(self.path, lambda doc: self._import(table, row_key, doc))
                _MIGRATED.add(str(self.path))
        return table, row_key

    @staticmethod
    def _import(table: JsonTable, row_key: str, doc: Any) -> None:
        if not isinstance(doc, dict) or not isinstance(doc.get("vars"), dict):
            return
        if not table.exists(row_key):
            table.put(row_key, doc)

    # ---------- 读写 ----------
    def load(self) -> Dict[str, Any]:
        with self._lock:
            table, row_key = self._table()
            return _valid_vars(table.get(row_key))

    def load_document(self) -> Dict[str, Any]:
        """完整文档（含 version/updated_at）；损坏时返回空结构。"""
        with self._lock:
            table, row_key = self._table()
            doc = table.get(row_key)
            if not isinstance(doc, dict) or doc.get("version") != 1:
                return _empty()
            vars_ = doc.get("vars")
            if not isinstance(vars_, dict):
                return _empty()
            return {
                "version": 1,
                "vars": vars_,
                "updated_at": str(doc.get("updated_at") or ""),
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
            table, row_key = self._table()
            table.put(row_key, payload)
