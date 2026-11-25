"""用户预设 SQLite 存储。

布局::

    data/{uid}/presets.db     # presets 表（JSON 文档）
    data/{uid}/presets/*.json # 旧布局：首次打开自动导入并改名 .bak-<时间戳>

构造参数保持兼容：传 ``data/{uid}/presets``（旧调用方式）时库落在其父目录
``data/{uid}/presets.db``；传 ``data/{uid}`` 时库落在该目录、旧目录为
``presets/``。这样调用方不用改，数据文件也符合每用户一个库的约定。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..ids import check_safe_name
from ..sqlite_store import JsonTable, SqliteDB, migrate_legacy_dir
from .models import Preset, now_stamp


class PresetStore:
    def __init__(self, base_dir: Path):
        d = Path(base_dir)
        if d.name == "presets":
            self._legacy_dir = d
            db_path = d.parent / "presets.db"
        else:
            self._legacy_dir = d / "presets"
            db_path = d / "presets.db"
        self._db = SqliteDB(db_path)
        self._presets = JsonTable(self._db, "presets")
        # 列表解析缓存：key -> (文档 md5, Preset / None=坏文档)。
        # 只缓存 list()；get() 仍每次解析（toggle_regex 是 get→mutate→save）。
        self._cache_lock = threading.RLock()
        self._cache: Dict[str, Tuple[str, Optional[Preset]]] = {}
        self._migrate_legacy()

    def _migrate_legacy(self) -> None:
        if self._legacy_dir.exists():
            try:
                migrate_legacy_dir(
                    self._legacy_dir, lambda k, d: self._presets.put(k, d)
                )
            except OSError:
                # 旧目录只读、改名失败：已导入的内容仍在库里，重复导入幂等
                pass

    def list(self) -> List[Preset]:
        out: List[Preset] = []
        live = set()
        for row in self._db.query(
            "SELECT k, doc FROM presets"
        ):
            key, doc = row[0], row[1]
            live.add(key)
            digest = _md5(doc)
            with self._cache_lock:
                entry = self._cache.get(key)
            if entry is not None and entry[0] == digest:
                preset = entry[1]
            else:
                try:
                    preset = Preset.from_dict(json.loads(doc))
                except Exception:
                    preset = None
                with self._cache_lock:
                    self._cache[key] = (digest, preset)
            if preset is not None:
                out.append(preset)
        with self._cache_lock:
            for key in [k for k in self._cache if k not in live]:
                del self._cache[key]
        out.sort(key=lambda p: (p.updated_at or "", p.name), reverse=True)
        return out

    def get(self, pid: str) -> Optional[Preset]:
        try:
            check_safe_name(pid)
        except Exception:
            return None
        doc = self._presets.get(pid)
        if doc is None:
            return None
        try:
            return Preset.from_dict(doc)
        except Exception:
            return None

    def save(self, preset: Preset) -> Preset:
        check_safe_name(preset.id)
        preset.updated_at = now_stamp()
        self._presets.put(preset.id, preset.to_dict())
        self._forget(preset.id)
        return preset

    def delete(self, pid: str) -> bool:
        try:
            check_safe_name(pid)
        except Exception:
            return False
        if not self._presets.delete(pid):
            return False
        self._forget(pid)
        return True

    def _forget(self, key: str) -> None:
        with self._cache_lock:
            self._cache.pop(key, None)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()
