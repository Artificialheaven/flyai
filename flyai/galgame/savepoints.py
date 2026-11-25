"""Galgame 存档点：会话完整快照（消息/场景/语音映射），按「用户 + 会话」存放。

存储改成 ``data/<uid>/savepoints.db``：``savepoints`` 表存清单行（id/name/
created_at/messages/file/thumb），``savepoint_snapshots`` 表存完整会话快照。
旧布局 ``data/<uid>/savepoints/<sid>/index.json + sp_*.json`` 在首次打开时
自动导入并改名 ``.bak-<时间戳>``。

资产（背景/立绘/音乐）文件不复制：它们永不删除、快照只引用 URL。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..ids import InvalidName, check_safe_name, safe_child
from ..sqlite_store import JsonTable, SqliteDB, backup_path

# 每个会话最多保留的存档点数；超出后最旧的自动淘汰（连同快照）
MAX_SAVEPOINTS = 30

_RECORD_FIELDS = ("id", "name", "created_at", "messages", "file", "thumb")

# 同一个用户的 savepoints.db 只建一套表（连接是线程本地的，缓存跨线程安全）。
_STORES: Dict[str, Tuple[JsonTable, JsonTable, threading.RLock]] = {}
_GUARD = threading.Lock()
_MIGRATED: set = set()


@dataclass
class SavePointRecord:
    id: str
    name: str
    created_at: str
    messages: int      # 快照时主房间第一条线程的消息数（展示用）
    file: str          # 兼容字段：快照的逻辑文件名（现在只是标签）
    thumb: str = ""    # 快照时场景背景图 url（清单缩略图，可空）

    def to_dict(self) -> Dict:
        return asdict(self)


def _extract_thumb(snapshot: Dict) -> str:
    """取快照场景背景图 URL；结构异常时静默返回空串。"""
    scene = snapshot.get("galgame")
    if not isinstance(scene, dict):
        return ""
    bg = scene.get("background")
    if not isinstance(bg, dict):
        return ""
    url = bg.get("url")
    return url if isinstance(url, str) else ""


def _row_key(sid: str, savepoint_id: str) -> str:
    # 长度前缀保证不同会话的 key 不会互相混淆。
    return "%d:%s:%s" % (len(sid), sid, savepoint_id)


def _rows(db: SqliteDB, table: str, sid: str) -> List[Tuple[str, Dict]]:
    prefix = "%d:%s:" % (len(sid), sid)
    rows = db.query(
        "SELECT k, doc FROM %s WHERE substr(k, 1, ?) = ? ORDER BY rowid" % table,
        (len(prefix), prefix),
    )
    out: List[Tuple[str, Dict]] = []
    for key, raw in rows:
        try:
            doc = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(doc, dict):
            out.append((str(key), doc))
    return out


def _safe_legacy_file(directory: Path, name: str) -> Optional[Path]:
    try:
        return safe_child(directory, name)
    except InvalidName:
        return None


def _migrate_legacy(index: JsonTable, snapshots: JsonTable, legacy_root: Path) -> None:
    """导入旧 index.json + sp_*.json；坏 JSON 跳过，导入后改名 .bak。"""
    if not legacy_root.is_dir():
        return
    try:
        sessions = sorted(p for p in legacy_root.iterdir() if p.is_dir())
    except OSError:
        return
    for sid_dir in sessions:
        index_path = sid_dir / "index.json"
        if not index_path.is_file():
            continue
        try:
            items = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(items, list):
            continue
        sid = sid_dir.name
        try:
            check_safe_name(sid, "会话 ID")
        except InvalidName:
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("id") or not item.get("file"):
                continue
            try:
                savepoint_id = check_safe_name(str(item["id"]), "存档点 ID")
            except InvalidName:
                continue
            row = _row_key(sid, savepoint_id)
            doc = {field: item[field] for field in _RECORD_FIELDS if field in item}
            if not index.exists(row):
                index.put(row, doc)
            if snapshots.exists(row):
                continue
            path = _safe_legacy_file(sid_dir, str(item["file"]))
            if path is None or not path.is_file():
                continue
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(snapshot, dict):
                snapshots.put(row, snapshot)
        # 导入完成（或已尽力）后统一改名，防止重复导入。
        for path in sorted(sid_dir.glob("*.json")):
            try:
                path.replace(backup_path(path))
            except OSError:
                pass
        try:
            sid_dir.rmdir()
        except OSError:
            pass


def _open(db_path: Path, legacy_root: Path) -> Tuple[JsonTable, JsonTable, threading.RLock]:
    key = str(db_path)
    with _GUARD:
        entry = _STORES.get(key)
        if entry is None:
            db = SqliteDB(db_path)
            entry = (
                JsonTable(db, "savepoints"),
                JsonTable(db, "savepoint_snapshots"),
                threading.RLock(),
            )
            _STORES[key] = entry
        index, snapshots, lock = entry
        if key not in _MIGRATED:
            try:
                _migrate_legacy(index, snapshots, legacy_root)
            finally:
                _MIGRATED.add(key)
    return index, snapshots, lock


class SavePointStore:
    """存档点行 + 快照行存储（SQLite，原子提交）。"""

    def __init__(self, data_dir: Path, uid: str):
        base = Path(data_dir) / uid
        self.root = base / "savepoints"          # 旧布局目录，仅用于迁移
        self.db_path = base / "savepoints.db"
        self._index, self._snapshots, self._lock = _open(self.db_path, self.root)
        self._db = self._index.db

    # ---------- 查询 ----------
    def list(self, sid: str) -> List[SavePointRecord]:
        check_safe_name(sid, "会话 ID")
        out: List[SavePointRecord] = []
        for _key, doc in _rows(self._db, self._index.table, sid):
            try:
                out.append(SavePointRecord(
                    **{field: doc[field] for field in _RECORD_FIELDS if field in doc}
                ))
            except TypeError:
                continue
        return out

    def load_snapshot(self, sid: str, savepoint_id: str) -> Optional[Dict]:
        check_safe_name(sid, "会话 ID")
        check_safe_name(savepoint_id, "存档点 ID")
        row = _row_key(sid, savepoint_id)
        if not self._index.exists(row):
            return None
        return self._snapshots.get(row)

    # ---------- 写入 ----------
    def create(self, sid: str, snapshot: Dict, name: str = "") -> SavePointRecord:
        check_safe_name(sid, "会话 ID")
        stamp = time.strftime("%Y%m%d%H%M%S")
        fname = "sp_%s_%s.json" % (stamp, secrets.token_hex(4))

        threads = snapshot.get("threads") or []
        msg_count = len(threads[0].get("messages") or []) if threads else 0
        default_name = "存档点 · " + time.strftime("%m-%d %H:%M")
        rec = SavePointRecord(
            id=secrets.token_hex(8),
            name=(name or "").strip()[:80] or default_name,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            messages=msg_count,
            file=fname,
            thumb=_extract_thumb(snapshot),
        )
        row = _row_key(sid, rec.id)
        with self._lock:
            self._snapshots.put(row, snapshot)
            self._index.put(row, rec.to_dict())
            self._evict(sid)
        return rec

    def delete(self, sid: str, savepoint_id: str) -> bool:
        check_safe_name(sid, "会话 ID")
        check_safe_name(savepoint_id, "存档点 ID")
        row = _row_key(sid, savepoint_id)
        with self._lock:
            if not self._index.exists(row):
                return False
            self._index.delete(row)
            self._snapshots.delete(row)
        return True

    # ---------- 内部 ----------
    def _evict(self, sid: str) -> None:
        """超过上限时最旧的存档点连带快照一起淘汰（调用方需持锁）。"""
        rows = _rows(self._db, self._index.table, sid)
        overflow = len(rows) - MAX_SAVEPOINTS
        if overflow <= 0:
            return
        for key, _doc in rows[:overflow]:
            self._index.delete(key)
            self._snapshots.delete(key)
