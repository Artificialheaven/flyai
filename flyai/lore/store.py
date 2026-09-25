"""角色/世界观 JSON 存储，原子写入。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from ..config import PROJECT_ROOT
from .models import Character, World


class LoreStore:
    def __init__(self, base_dir: Path = None):
        self._base = base_dir or (PROJECT_ROOT / "data")
        self._char_dir = self._base / "characters"
        self._world_dir = self._base / "worlds"
        self._char_dir.mkdir(parents=True, exist_ok=True)
        self._world_dir.mkdir(parents=True, exist_ok=True)

    # ---- 角色 ----

    def list_characters(self) -> List[Character]:
        out = []
        for p in self._char_dir.glob("*.json"):
            try:
                out.append(Character.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        out.sort(key=lambda c: c.name)
        return out

    def get_character(self, cid: str) -> Character:
        p = self._char_dir / f"{cid}.json"
        if not p.exists():
            return None
        return Character.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save_character(self, c: Character) -> None:
        self._write(self._char_dir, c.id, c.to_dict())

    def delete_character(self, cid: str) -> None:
        (self._char_dir / f"{cid}.json").unlink(missing_ok=True)

    # ---- 世界 ----

    def list_worlds(self) -> List[World]:
        out = []
        for p in self._world_dir.glob("*.json"):
            try:
                out.append(World.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        out.sort(key=lambda w: w.name)
        return out

    def get_world(self, wid: str) -> World:
        p = self._world_dir / f"{wid}.json"
        if not p.exists():
            return None
        return World.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save_world(self, w: World) -> None:
        self._write(self._world_dir, w.id, w.to_dict())

    def delete_world(self, wid: str) -> None:
        (self._world_dir / f"{wid}.json").unlink(missing_ok=True)

    @staticmethod
    def _write(directory: Path, cid: str, data: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{cid}.json"
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)