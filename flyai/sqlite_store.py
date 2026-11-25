"""SQLite 通用存储层。

所有业务数据（用户、鉴权、角色/世界书、会话、市场、扩展注册表、模板变量、
提示词归档、Galgame 存档点等）落到 SQLite；只有 config.json、上传的二进制
资产和扩展包安装目录里的代码文件仍留在文件系统。

设计要点：
- 每个 store 一个 .db 文件（目录与旧 JSON 布局一致），互不干扰、便于备份；
- 连接按线程缓存（FastAPI 线程池里也能安全使用），WAL + busy_timeout；
- 单表 JSON 文档模式（k/doc/updated_at）覆盖绝大多数 set/get/list 场景；
- 旧 JSON 数据在首次打开时自动导入，导入后原文件改名 .json.bak-<时间戳>，
  可随时回滚（把备份改名回来即可）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


def _utc_now() -> float:
    return time.time()


class SqliteDB:
    """一个 .db 文件的门面：线程本地连接 + 常用查询辅助。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    # ---- 连接 ----
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=10000")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            finally:
                self._local.conn = None

    # ---- 查询辅助 ----
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn().execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        return self.conn().executemany(sql, rows)

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        return self.conn().execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self.conn().execute(sql, tuple(params)).fetchone()

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.one(sql, params)
        return row[0] if row is not None else None

    def ensure(self, *ddl: str) -> None:
        c = self.conn()
        for stmt in ddl:
            c.execute(stmt)


class JsonTable:
    """单表 JSON 文档表：k 主键 + doc 文本 + updated_at。"""

    def __init__(self, db: SqliteDB, table: str = "docs"):
        self.db = db
        self.table = table
        db.ensure(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            " k TEXT PRIMARY KEY,"
            " doc TEXT NOT NULL,"
            " updated_at REAL NOT NULL"
            ")"
        )

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        row = self.db.one(f"SELECT doc FROM {self.table} WHERE k=?", (key,))
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, doc: Dict[str, Any]) -> None:
        self.db.execute(
            f"INSERT INTO {self.table}(k, doc, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(k) DO UPDATE SET doc=excluded.doc, updated_at=excluded.updated_at",
            (key, json.dumps(doc, ensure_ascii=False, separators=(",", ":")), _utc_now()),
        )

    def delete(self, key: str) -> bool:
        cur = self.db.execute(f"DELETE FROM {self.table} WHERE k=?", (key,))
        return cur.rowcount > 0

    def exists(self, key: str) -> bool:
        return self.db.one(f"SELECT 1 FROM {self.table} WHERE k=?", (key,)) is not None

    def keys(self) -> List[str]:
        return [r[0] for r in self.db.query(f"SELECT k FROM {self.table}")]

    def items(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        for row in self.db.query(f"SELECT k, doc FROM {self.table}"):
            try:
                data = json.loads(row[1])
            except Exception:
                continue
            if isinstance(data, dict):
                yield row[0], data

    def values(self) -> Iterator[Dict[str, Any]]:
        for _, doc in self.items():
            yield doc

    def count(self) -> int:
        return int(self.db.scalar(f"SELECT COUNT(*) FROM {self.table}") or 0)

    def clear(self) -> None:
        self.db.execute(f"DELETE FROM {self.table}")


# ---- 旧 JSON 迁移 ----------------------------------------------------------

def backup_path(path: Path) -> Path:
    """迁移后旧文件的备份名：<name>.bak-<时间戳>（同目录、不覆盖）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = path.with_name(path.name + f".bak-{stamp}")
    n = 1
    out = base
    while out.exists():
        out = base.with_name(base.name + f"-{n}")
        n += 1
    return out


def read_legacy_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def migrate_legacy_file(path: Path, on_doc) -> int:
    """导入单文件 JSON 后改名备份；返回导入条数（0/1）。"""
    if not path.exists():
        return 0
    data = read_legacy_json(path)
    if data is None:
        return 0
    on_doc(data)
    try:
        path.replace(backup_path(path))
    except OSError:
        pass
    return 1


def migrate_legacy_dir(directory: Path, on_doc) -> int:
    """导入目录下所有 *.json 后改名备份；返回导入条数。"""
    if not directory.exists():
        return 0
    n = 0
    for p in sorted(directory.glob("*.json")):
        if not p.is_file():
            continue
        data = read_legacy_json(p)
        if data is None:
            continue
        on_doc(p.stem, data)
        n += 1
        try:
            p.replace(backup_path(p))
        except OSError:
            pass
    return n
