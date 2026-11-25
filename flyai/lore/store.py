"""角色/世界观 SQLite 存储。

布局::

    data/{uid}/lore.db        # characters、worlds 两张 JSON 文档表
    data/shared/lore.db       # characters：admin 发布的共享卡（只读来源）

首次打开时自动导入旧 JSON 目录（characters/*.json、worlds/*.json），
导入成功后旧文件改名为 ``.bak-<时间戳>``，重复启动不会重复导入。
共享卡只读：保存/删除只作用于当前用户自己的 characters 表，
发布/下架（publish_character / unpublish_character）才会写共享表。

列表里的 md5 仍表示整卡内容哈希（自身优先，其次共享），用于前端缓存判断。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..config import PROJECT_ROOT
from ..ids import check_safe_name
from ..sqlite_store import JsonTable, SqliteDB, migrate_legacy_dir
from .models import Character, World

# 解析失败哨兵：坏文档也缓存，避免每次列表都反复解析同一份坏 JSON
_INVALID = object()


class LoreStore:
    def __init__(self, base_dir: Path = None, shared_base: Path = None):
        self._base = Path(base_dir) if base_dir is not None else (PROJECT_ROOT / "data")
        # 旧 JSON 布局（迁移来源；保留属性名，迁移后目录可能为空）
        self._char_dir = self._base / "characters"
        self._world_dir = self._base / "worlds"
        self._db = SqliteDB(self._base / "lore.db")
        self._chars = JsonTable(self._db, "characters")
        self._worlds = JsonTable(self._db, "worlds")
        # 解析结果缓存：(db 路径, 表, key) -> (文档 md5, 对象 / _INVALID)。
        # 内容库列表与 _st_payload 的逐成员 get 都靠它把重复解析压到一次哈希。
        # 缓存对象视为只读；写路径落库后按内容哈希自然失效。
        self._cache_lock = threading.RLock()
        self._cache: Dict[Tuple[str, str, str], Tuple[str, object]] = {}
        self._migrate_legacy()
        # 全体用户共享的角色卡库（admin 发布）；本用户只可读
        self._shared_char_dir: Optional[Path] = None
        self._shared_db: Optional[SqliteDB] = None
        self._shared_chars: Optional[JsonTable] = None
        if shared_base is not None:
            self._shared_char_dir = Path(shared_base) / "characters"
            self._shared_db = SqliteDB(Path(shared_base) / "lore.db")
            self._shared_chars = JsonTable(self._shared_db, "characters")
            self._migrate_shared()

    # ---- 旧 JSON 导入 ----

    def _migrate_legacy(self) -> None:
        """旧目录 -> 当前用户库；坏 JSON 跳过（migrate_legacy_dir 不改名）。

        改名为 .bak 失败（目录只读/权限不足）时不影响启动：已入库的文档
        重复导入是幂等的（同一 key 覆盖），下次打开再试。
        """
        try:
            if self._char_dir.exists():
                migrate_legacy_dir(
                    self._char_dir, lambda k, d: self._chars.put(k, d)
                )
            if self._world_dir.exists():
                migrate_legacy_dir(
                    self._world_dir, lambda k, d: self._worlds.put(k, d)
                )
        except OSError:
            pass

    def _migrate_shared(self) -> None:
        """共享目录旧 JSON -> 共享库；重复启动时文件已改名，不会重复导入。

        共享目录可能对当前进程只读，改名失败只影响清理，不影响读取。
        """
        if self._shared_chars is not None and self._shared_char_dir is not None:
            if self._shared_char_dir.exists():
                try:
                    migrate_legacy_dir(
                        self._shared_char_dir,
                        lambda k, d: self._shared_chars.put(k, d),
                    )
                except OSError:
                    pass

    # ---- 缓存 ----

    @staticmethod
    def _md5(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _cache_key(self, table: JsonTable, key: str) -> Tuple[str, str, str]:
        return (str(table.db.path), table.table, key)

    def _parse_row(self, table: JsonTable, key: str, doc: str,
                   from_dict: Callable):
        digest = self._md5(doc)
        ck = self._cache_key(table, key)
        with self._cache_lock:
            entry = self._cache.get(ck)
        if entry is not None and entry[0] == digest:
            return entry[1]
        try:
            data = json.loads(doc)
            obj = from_dict(data) if isinstance(data, dict) else _INVALID
        except Exception:
            obj = _INVALID
        with self._cache_lock:
            self._cache[ck] = (digest, obj)
        return obj

    def _load_table(self, table: JsonTable, from_dict: Callable) -> List:
        rows = table.db.query(
            f"SELECT k, doc FROM {table.table}"
        )
        out = []
        live = set()
        for row in rows:
            key = row[0]
            live.add(key)
            obj = self._parse_row(table, key, row[1], from_dict)
            if obj is not _INVALID:
                out.append(obj)
        # 只清理本表已消失的文档（共享表与本表缓存互不影响）
        prefix = (str(table.db.path), table.table)
        with self._cache_lock:
            for ck in [
                k for k in self._cache
                if k[:2] == prefix and k[2] not in live
            ]:
                del self._cache[ck]
        return out

    def _read_table(self, table: JsonTable, key: str, from_dict: Callable):
        try:
            check_safe_name(key)
        except Exception:
            return None
        row = table.db.one(
            f"SELECT doc FROM {table.table} WHERE k=?", (key,)
        )
        if row is None:
            return None
        obj = self._parse_row(table, key, row[0], from_dict)
        return None if obj is _INVALID else obj

    def _forget(self, table: JsonTable, key: str) -> None:
        with self._cache_lock:
            self._cache.pop(self._cache_key(table, key), None)

    def _digest(self, table: Optional[JsonTable], key: str) -> str:
        if table is None:
            return ""
        try:
            check_safe_name(key)
        except Exception:
            return ""
        row = table.db.one(
            f"SELECT doc FROM {table.table} WHERE k=?", (key,)
        )
        if row is None:
            return ""
        return self._md5(row[0])

    def character_digest(self, cid: str) -> str:
        """内容库列表用：整卡内容 md5（自身优先，其次共享库）。"""
        digest = self._digest(self._chars, cid)
        return digest or self._digest(self._shared_chars, cid)

    def world_digest(self, wid: str) -> str:
        return self._digest(self._worlds, wid)

    # ---- 角色 ----

    def _load_characters(self, table: JsonTable) -> List[Character]:
        return self._load_table(table, Character.from_dict)

    def list_characters(self) -> List[Character]:
        """合并自身表与共享表，按 id 去重：自身优先（共享卡被遮蔽）。"""
        own = self._load_characters(self._chars)
        seen = {c.id for c in own}
        if self._shared_chars is not None:
            for c in self._load_characters(self._shared_chars):
                if c.id not in seen:
                    own.append(c)
                    seen.add(c.id)
        own.sort(key=lambda c: c.name)
        return own

    def get_character(self, cid: str) -> Optional[Character]:
        """先自身后共享。"""
        c = self._read_table(self._chars, cid, Character.from_dict)
        if c is not None:
            return c
        if self._shared_chars is not None:
            return self._read_table(self._shared_chars, cid, Character.from_dict)
        return None

    def character_source(self, cid: str) -> Optional[str]:
        """角色卡所在位置：own（可改可删）/ shared（只读）/ None（不存在/非法名）。"""
        try:
            check_safe_name(cid)
        except Exception:
            return None
        if self._chars.exists(cid):
            return "own"
        if self._shared_chars is not None and self._shared_chars.exists(cid):
            return "shared"
        return None

    def save_character(self, c: Character) -> None:
        check_safe_name(c.id)
        self._chars.put(c.id, c.to_dict())
        self._forget(self._chars, c.id)

    def delete_character(self, cid: str) -> None:
        check_safe_name(cid)
        self._chars.delete(cid)
        self._forget(self._chars, cid)

    def delete_character_cascade(self, cid: str) -> None:
        """删除根卡时一并删除其全部副本（仅自身表）。"""
        check_safe_name(cid)
        variants = [
            c for c in self._load_characters(self._chars) if c.parent_id == cid
        ]
        for v in variants:
            self._chars.delete(v.id)
            self._forget(self._chars, v.id)
        self._chars.delete(cid)
        self._forget(self._chars, cid)

    def publish_character(self, c: Character) -> None:
        """发布到共享库（保持原 id/author），同步自身副本 public 标记。"""
        if self._shared_chars is None:
            raise RuntimeError("未配置共享目录")
        check_safe_name(c.id)
        c.public = True
        self._shared_chars.put(c.id, c.to_dict())
        self._forget(self._shared_chars, c.id)
        if self._chars.exists(c.id):
            self._chars.put(c.id, c.to_dict())
            self._forget(self._chars, c.id)

    def unpublish_character(self, cid: str) -> None:
        """从共享库移除，并把自身副本 public 标记复位。"""
        check_safe_name(cid)
        if self._shared_chars is not None:
            self._shared_chars.delete(cid)
            self._forget(self._shared_chars, cid)
        c = self._read_table(self._chars, cid, Character.from_dict)
        if c is not None and c.public:
            c.public = False
            self._chars.put(c.id, c.to_dict())
            self._forget(self._chars, c.id)

    # ---- 世界 ----

    def list_worlds(self) -> List[World]:
        out = self._load_table(self._worlds, World.from_dict)
        out.sort(key=lambda w: w.name)
        return out

    def get_world(self, wid: str) -> World:
        return self._read_table(self._worlds, wid, World.from_dict)

    def save_world(self, w: World) -> None:
        check_safe_name(w.id)
        self._worlds.put(w.id, w.to_dict())
        self._forget(self._worlds, w.id)

    def delete_world(self, wid: str) -> None:
        # 非法名（穿越/分隔符）在此收口
        check_safe_name(wid)
        self._worlds.delete(wid)
        self._forget(self._worlds, wid)
