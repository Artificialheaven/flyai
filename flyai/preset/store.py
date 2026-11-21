"""用户预设 JSON 存储：data/{uid}/presets/*.json，原子写入。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..ids import safe_child
from .models import Preset, now_stamp


class PresetStore:
    def __init__(self, base_dir: Path):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # 列表解析缓存：路径 -> ((mtime_ns, size), Preset / None=坏文件)。
        # 只缓存 list()；get() 仍每次读盘（toggle_regex 是 get→mutate→save）。
        self._cache_lock = threading.RLock()
        self._cache: Dict[str, Tuple[Tuple[int, int], Optional[Preset]]] = {}

    def list(self) -> List[Preset]:
        out: List[Preset] = []
        live = set()
        for p in self._dir.glob("*.json"):
            key = str(p)
            try:
                st = p.stat()
            except OSError:
                continue
            live.add(key)
            stamp = (st.st_mtime_ns, st.st_size)
            with self._cache_lock:
                entry = self._cache.get(key)
            if entry is not None and entry[0] == stamp:
                preset = entry[1]
            else:
                try:
                    preset = Preset.from_dict(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    preset = None
                with self._cache_lock:
                    self._cache[key] = (stamp, preset)
            if preset is not None:
                out.append(preset)
        with self._cache_lock:
            for key in [k for k in self._cache if k not in live]:
                del self._cache[key]
        out.sort(key=lambda p: (p.updated_at or "", p.name), reverse=True)
        return out

    def get(self, pid: str) -> Optional[Preset]:
        try:
            p = safe_child(self._dir, f"{pid}.json")
        except Exception:
            return None
        if not p.exists():
            return None
        try:
            return Preset.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, preset: Preset) -> Preset:
        preset.updated_at = now_stamp()
        p = safe_child(self._dir, f"{preset.id}.json")
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(preset.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        self._forget(p)
        return preset

    def delete(self, pid: str) -> bool:
        try:
            p = safe_child(self._dir, f"{pid}.json")
        except Exception:
            return False
        if not p.exists():
            return False
        p.unlink()
        self._forget(p)
        return True

    def _forget(self, path: Path) -> None:
        with self._cache_lock:
            self._cache.pop(str(path), None)
