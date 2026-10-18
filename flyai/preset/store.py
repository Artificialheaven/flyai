"""用户预设 JSON 存储：data/{uid}/presets/*.json，原子写入。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from ..ids import safe_child
from .models import Preset, now_stamp


class PresetStore:
    def __init__(self, base_dir: Path):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> List[Preset]:
        out: List[Preset] = []
        for p in self._dir.glob("*.json"):
            try:
                out.append(Preset.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
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
        return preset

    def delete(self, pid: str) -> bool:
        try:
            p = safe_child(self._dir, f"{pid}.json")
        except Exception:
            return False
        if not p.exists():
            return False
        p.unlink()
        return True
