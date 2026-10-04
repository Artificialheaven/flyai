"""角色/世界观 JSON 存储，原子写入。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ..config import PROJECT_ROOT
from ..ids import safe_child
from .models import Character, World


class LoreStore:
    def __init__(self, base_dir: Path = None, shared_base: Path = None):
        self._base = base_dir or (PROJECT_ROOT / "data")
        self._char_dir = self._base / "characters"
        self._world_dir = self._base / "worlds"
        self._char_dir.mkdir(parents=True, exist_ok=True)
        self._world_dir.mkdir(parents=True, exist_ok=True)
        # 全体用户共享角色卡目录（admin 发布）；本用户只可读
        self._shared_char_dir = (shared_base / "characters") if shared_base is not None else None
        if self._shared_char_dir is not None:
            self._shared_char_dir.mkdir(parents=True, exist_ok=True)

    # ---- 角色 ----

    @staticmethod
    def _load_characters(directory: Path) -> List[Character]:
        out = []
        for p in directory.glob("*.json"):
            try:
                out.append(Character.from_dict(json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return out

    def list_characters(self) -> List[Character]:
        """合并自身目录与共享目录，按 id 去重：自身优先（共享卡被遮蔽）。"""
        own = self._load_characters(self._char_dir)
        seen = {c.id for c in own}
        if self._shared_char_dir is not None:
            for c in self._load_characters(self._shared_char_dir):
                if c.id not in seen:
                    own.append(c)
                    seen.add(c.id)
        own.sort(key=lambda c: c.name)
        return own

    def get_character(self, cid: str) -> Optional[Character]:
        """先自身后共享。"""
        c = self._read_character(self._char_dir, cid)
        if c is not None:
            return c
        if self._shared_char_dir is not None:
            return self._read_character(self._shared_char_dir, cid)
        return None

    @staticmethod
    def _read_character(directory: Path, cid: str) -> Optional[Character]:
        try:
            p = safe_child(directory, f"{cid}.json")
        except Exception:
            return None
        if not p.exists():
            return None
        try:
            return Character.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def character_source(self, cid: str) -> Optional[str]:
        """角色卡所在位置：own（可改可删）/ shared（只读）/ None（不存在/非法名）。"""
        fname = f"{cid}.json"
        try:
            if safe_child(self._char_dir, fname).exists():
                return "own"
            if self._shared_char_dir is not None and safe_child(self._shared_char_dir, fname).exists():
                return "shared"
        except Exception:
            return None
        return None

    def save_character(self, c: Character) -> None:
        self._write(self._char_dir, c.id, c.to_dict())

    def delete_character(self, cid: str) -> None:
        safe_child(self._char_dir, f"{cid}.json").unlink(missing_ok=True)

    def delete_character_cascade(self, cid: str) -> None:
        """删除根卡时一并删除其全部副本（仅自身目录）。"""
        variants = [
            c for c in self._load_characters(self._char_dir) if c.parent_id == cid
        ]
        for v in variants:
            (self._char_dir / f"{v.id}.json").unlink(missing_ok=True)
        (self._char_dir / f"{cid}.json").unlink(missing_ok=True)

    def publish_character(self, c: Character) -> None:
        """发布到共享目录（保持原 id/author），同步自身副本 public 标记。"""
        if self._shared_char_dir is None:
            raise RuntimeError("未配置共享目录")
        c.public = True
        self._write(self._shared_char_dir, c.id, c.to_dict())
        if (self._char_dir / f"{c.id}.json").exists():
            self._write(self._char_dir, c.id, c.to_dict())

    def unpublish_character(self, cid: str) -> None:
        """从共享目录移除，并把自身副本 public 标记复位。"""
        if self._shared_char_dir is not None:
            (self._shared_char_dir / f"{cid}.json").unlink(missing_ok=True)
        c = self._read_character(self._char_dir, cid)
        if c is not None and c.public:
            c.public = False
            self._write(self._char_dir, c.id, c.to_dict())

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
        try:
            p = safe_child(self._world_dir, f"{wid}.json")
        except Exception:
            return None
        if not p.exists():
            return None
        return World.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def save_world(self, w: World) -> None:
        self._write(self._world_dir, w.id, w.to_dict())

    def delete_world(self, wid: str) -> None:
        # 裸 id 直接拼文件名删除：非法名（穿越/分隔符）在此收口
        safe_child(self._world_dir, f"{wid}.json").unlink(missing_ok=True)

    @staticmethod
    def _write(directory: Path, cid: str, data: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        p = safe_child(directory, f"{cid}.json")
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)