"""角色/世界观 JSON 存储，原子写入。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..config import PROJECT_ROOT
from ..ids import safe_child
from .models import Character, World

# 解析失败哨兵：坏文件也缓存，避免每次列表都反复解析同一个坏 JSON
_INVALID = object()


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
        # 解析结果缓存：路径 -> ((mtime_ns, size), 对象 / _INVALID)。
        # 内容库列表与 _st_payload 的逐成员 get 都靠它把重复解析压到 stat 级。
        # 缓存对象视为只读；写路径落盘后必须 _forget 对应路径。
        self._cache_lock = threading.RLock()
        self._cache: Dict[str, Tuple[Tuple[int, int], object]] = {}

    # ---- 缓存 ----

    def _load_dir(self, directory: Path, from_dict: Callable) -> List:
        out = []
        live = set()
        for p in directory.glob("*.json"):
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
                obj = entry[1]
            else:
                try:
                    obj = from_dict(json.loads(p.read_text(encoding="utf-8")))
                except Exception:
                    obj = _INVALID
                with self._cache_lock:
                    self._cache[key] = (stamp, obj)
            if obj is not _INVALID:
                out.append(obj)
        # 只清理本目录下已消失的文件（其它目录的缓存互不影响）
        dir_key = str(directory)
        with self._cache_lock:
            for key in [
                k for k in self._cache if os.path.dirname(k) == dir_key and k not in live
            ]:
                del self._cache[key]
        return out

    def _read_file(self, directory: Path, cid: str, from_dict: Callable):
        try:
            p = safe_child(directory, f"{cid}.json")
        except Exception:
            return None
        try:
            st = p.stat()
        except OSError:
            return None
        key = str(p)
        stamp = (st.st_mtime_ns, st.st_size)
        with self._cache_lock:
            entry = self._cache.get(key)
        if entry is not None and entry[0] == stamp:
            obj = entry[1]
        else:
            try:
                obj = from_dict(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                obj = _INVALID
            with self._cache_lock:
                self._cache[key] = (stamp, obj)
        return None if obj is _INVALID else obj

    def _forget(self, path: Path) -> None:
        with self._cache_lock:
            self._cache.pop(str(path), None)

    # ---- 角色 ----

    def _load_characters(self, directory: Path) -> List[Character]:
        return self._load_dir(directory, Character.from_dict)

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

    def _read_character(self, directory: Path, cid: str) -> Optional[Character]:
        return self._read_file(directory, cid, Character.from_dict)

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
        p = safe_child(self._char_dir, f"{cid}.json")
        p.unlink(missing_ok=True)
        self._forget(p)

    def delete_character_cascade(self, cid: str) -> None:
        """删除根卡时一并删除其全部副本（仅自身目录）。"""
        variants = [
            c for c in self._load_characters(self._char_dir) if c.parent_id == cid
        ]
        for v in variants:
            p = self._char_dir / f"{v.id}.json"
            p.unlink(missing_ok=True)
            self._forget(p)
        p = self._char_dir / f"{cid}.json"
        p.unlink(missing_ok=True)
        self._forget(p)

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
            p = self._shared_char_dir / f"{cid}.json"
            p.unlink(missing_ok=True)
            self._forget(p)
        c = self._read_character(self._char_dir, cid)
        if c is not None and c.public:
            c.public = False
            self._write(self._char_dir, c.id, c.to_dict())

    # ---- 世界 ----

    def list_worlds(self) -> List[World]:
        out = self._load_dir(self._world_dir, World.from_dict)
        out.sort(key=lambda w: w.name)
        return out

    def get_world(self, wid: str) -> World:
        return self._read_file(self._world_dir, wid, World.from_dict)

    def save_world(self, w: World) -> None:
        self._write(self._world_dir, w.id, w.to_dict())

    def delete_world(self, wid: str) -> None:
        # 裸 id 直接拼文件名删除：非法名（穿越/分隔符）在此收口
        p = safe_child(self._world_dir, f"{wid}.json")
        p.unlink(missing_ok=True)
        self._forget(p)

    def _write(self, directory: Path, cid: str, data: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        p = safe_child(directory, f"{cid}.json")
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        self._forget(p)