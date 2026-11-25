"""SQLite 会话持久化：sessions / threads / messages 三张表 + FTS5 全文索引。

与 JSON store 的差异：
- 消息按行存储，保存时只写新增/修改的行（按内容哈希比对），不再整份重写；
- 会话列表只读元数据列，不解析消息正文；
- 全文搜索走 FTS5(trigram)，中文短词（<3 字符）回退 LIKE；
- 旧 data/{uid}/sessions/*.json 首次打开时自动导入并改名备份。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import PROJECT_ROOT
from ..ids import safe_child
from ..schemas import Message
from .json_store import migrate_actor_ids, session_from_dict, session_to_dict
from .models import Session, bootstrap_room
from .store import SessionStore
from .versions import heal_thread
from ..sqlite_store import SqliteDB, backup_path, read_legacy_json


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        provider_id TEXT NOT NULL DEFAULT '',
        character_id TEXT NOT NULL DEFAULT '',
        world_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        updated_ts REAL NOT NULL DEFAULT 0,
        created_ts REAL NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0,
        mode TEXT NOT NULL DEFAULT 'normal',
        message_count INTEGER NOT NULL DEFAULT 0,
        token_usage TEXT NOT NULL DEFAULT '{}',
        doc TEXT NOT NULL,
        doc_size INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL DEFAULT 0,
        doc TEXT NOT NULL,
        FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL DEFAULT '',
        search_text TEXT NOT NULL DEFAULT '',
        hash TEXT NOT NULL DEFAULT '',
        doc TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_threads_session ON threads(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_id ON messages(thread_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(updated_ts)",
)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _search_text(msg: Dict) -> str:
    """正文 + 墓碑 + 发言者名：与 JSON store 的可搜索文本一致。"""
    parts = [str(msg.get("content") or "")]
    tomb = msg.get("tombstone")
    if isinstance(tomb, dict) and tomb.get("content"):
        parts.append(str(tomb["content"]))
    if msg.get("name"):
        parts.append(str(msg["name"]))
    return "\n".join(p for p in parts if p)


def _snippet(text: str, needle: str, width: int = 40) -> str:
    low = text.casefold()
    pos = low.find(needle.casefold())
    if pos < 0:
        return ""
    start = max(0, pos - width)
    end = min(len(text), pos + len(needle) + width)
    return text[start:end].replace("\n", " ").strip()


class SqliteSessionStore(SessionStore):
    def __init__(self, base_dir: Path = None):
        self._dir = Path(base_dir or (PROJECT_ROOT / "data" / "sessions"))
        self._dir.mkdir(parents=True, exist_ok=True)
        self.db = SqliteDB(self._dir / "sessions.db")
        self.db.ensure(*_SCHEMA)
        self._fts = True
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
                "content, tokenize='trigram')"
            )
        except Exception:
            # 老 SQLite 没有 trigram：退化为 LIKE，不漏功能
            self._fts = False
        self._import_legacy()

    # ---- 事务 ----
    @contextmanager
    def _tx(self):
        c = self.db.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except Exception:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")

    # ---- 旧 JSON 导入 ----
    def _import_legacy(self) -> None:
        files = sorted(self._dir.glob("*.json"))
        if not files:
            return
        for p in files:
            if not p.is_file():
                continue
            data = read_legacy_json(p)
            if data is None:
                # 坏文件不阻塞启动，改名留档
                try:
                    p.replace(backup_path(p))
                except OSError:
                    pass
                continue
            sid = str(data.get("id") or p.stem)
            exists = self.db.one("SELECT 1 FROM sessions WHERE id=?", (sid,))
            if not exists:
                session = session_from_dict(data, sid)
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = time.time()
                if not session.created_at:
                    session.created_at = session.updated_at or time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime(mtime))
                self.save(session, touch=False, ts=mtime)
            try:
                p.replace(backup_path(p))
            except OSError:
                pass

    # ---- 内部写 ----
    def _sync_messages(self, c, sid: str, thread_id: str, messages) -> None:
        cur = c.execute(
            "SELECT id, rowid, hash FROM messages WHERE thread_id=?", (thread_id,)
        )
        existing = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        seen = set()
        for seq, m in enumerate(messages):
            data = m.to_dict() if hasattr(m, "to_dict") else dict(m)
            mid = str(data.get("id") or "")
            if not mid:
                mid = uuid.uuid4().hex
                data["id"] = mid
            doc = _dump(data)
            h = _sha1(doc)
            old = existing.get(mid)
            if old is not None and old[1] == h:
                seen.add(mid)
                continue
            role = str(data.get("role") or "")
            name = str(data.get("name") or "")
            content = str(data.get("content") or "")
            stext = _search_text(data)
            if old is None:
                cur = c.execute(
                    "INSERT INTO messages(id, thread_id, session_id, seq, role, name,"
                    " content, search_text, hash, doc) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (mid, thread_id, sid, seq, role, name, content, stext, h, doc),
                )
                rowid = cur.lastrowid
            else:
                rowid = old[0]
                c.execute(
                    "UPDATE messages SET seq=?, role=?, name=?, content=?,"
                    " search_text=?, hash=?, doc=? WHERE rowid=?",
                    (seq, role, name, content, stext, h, doc, rowid),
                )
                if self._fts:
                    c.execute("DELETE FROM messages_fts WHERE rowid=?", (rowid,))
            if self._fts:
                c.execute(
                    "INSERT INTO messages_fts(rowid, content) VALUES(?,?)", (rowid, stext)
                )
            seen.add(mid)
        # 删除本线程已不存在的消息
        for mid, (rowid, _h) in existing.items():
            if mid in seen:
                continue
            if self._fts:
                c.execute("DELETE FROM messages_fts WHERE rowid=?", (rowid,))
            c.execute("DELETE FROM messages WHERE rowid=?", (rowid,))

    def save(self, session: Session, touch: bool = True, ts: float = 0.0) -> None:
        if touch:
            session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        now = ts or time.time()
        data = session_to_dict(session)
        threads = data.pop("threads", []) or []
        top_messages = data.get("messages") or []
        doc = _dump(data)
        count = len(top_messages) + sum(len(t.get("messages") or []) for t in threads)
        with self._tx() as c:
            c.execute(
                "INSERT INTO sessions(id,title,provider_id,character_id,world_id,"
                "created_at,updated_at,updated_ts,created_ts,pinned,mode,message_count,"
                "token_usage,doc,doc_size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                "provider_id=excluded.provider_id, character_id=excluded.character_id,"
                "world_id=excluded.world_id, created_at=excluded.created_at,"
                "updated_at=excluded.updated_at, updated_ts=excluded.updated_ts,"
                "pinned=excluded.pinned, mode=excluded.mode,"
                "message_count=excluded.message_count, token_usage=excluded.token_usage,"
                "doc=excluded.doc, doc_size=excluded.doc_size",
                (
                    session.id, session.title or "", session.provider_id or "",
                    session.character_id or "", session.world_id or "",
                    session.created_at or "", session.updated_at or "", now, now,
                    1 if session.pinned else 0, getattr(session, "mode", "normal") or "normal",
                    count, _dump(session.token_usage or {}), doc, len(doc),
                ),
            )
            # 顶层兼容消息（beta 前会话）：只参与搜索
            self._sync_messages(c, session.id, "", top_messages)
            keep = set()
            for seq, t in enumerate(threads):
                tdoc = {k: v for k, v in t.items() if k != "messages"}
                c.execute(
                    "INSERT INTO threads(id, session_id, seq, doc) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,"
                    " seq=excluded.seq, doc=excluded.doc",
                    (str(t.get("id") or uuid.uuid4().hex), session.id, seq, _dump(tdoc)),
                )
                keep.add(str(t.get("id") or ""))
                self._sync_messages(c, session.id, str(t.get("id") or ""),
                                    t.get("messages") or [])
            # 删除不再存在的线程及其消息
            cur = c.execute("SELECT id FROM threads WHERE session_id=?", (session.id,))
            for (tid,) in cur.fetchall():
                if tid in keep:
                    continue
                if self._fts:
                    c.execute(
                        "DELETE FROM messages_fts WHERE rowid IN "
                        "(SELECT rowid FROM messages WHERE thread_id=?)", (tid,)
                    )
                c.execute("DELETE FROM messages WHERE thread_id=?", (tid,))
                c.execute("DELETE FROM threads WHERE id=?", (tid,))

    def create(self, title: str = "", provider_id: str = "",
               character_id: str = "", world_id: str = "") -> Session:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        s = Session(
            id=uuid.uuid4().hex,
            title=title or "新会话",
            provider_id=provider_id or "",
            character_id=character_id or "",
            world_id=world_id or "",
        )
        s.created_at = s.updated_at = now
        self.save(s)
        return s

    def _path(self, sid: str) -> Path:
        return safe_child(self._dir, f"{sid}.json")

    def get(self, sid: str) -> Optional[Session]:
        row = self.db.one("SELECT doc FROM sessions WHERE id=?", (str(sid),))
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except Exception:
            return None
        data["threads"] = []
        for t in self.db.query(
            "SELECT id, doc FROM threads WHERE session_id=? ORDER BY seq", (str(sid),)
        ):
            tdata = json.loads(t[1])
            tdata["id"] = tdata.get("id") or t[0]
            tdata["messages"] = [
                json.loads(m[0])
                for m in self.db.query(
                    "SELECT doc FROM messages WHERE thread_id=? ORDER BY seq", (t[0],)
                )
            ]
            data["threads"].append(tdata)
        s = session_from_dict(data, str(sid))
        migrated = False
        if not s.members and not s.threads:
            if s.character_id or s.messages:
                bootstrap_room(s)
                migrated = True
        for t in s.threads:
            heal_thread(t)
        if migrate_actor_ids(s):
            migrated = True
        if migrated:
            self.save(s)
        return s

    def list(self) -> List[Session]:
        rows = self.db.query(
            "SELECT id, doc, message_count, updated_ts, token_usage, pinned FROM sessions"
        )
        entries = []
        for r in rows:
            try:
                data = json.loads(r[1])
            except Exception:
                data = {}
            summary = {
                "id": data.get("id", r[0]),
                "title": data.get("title", "新会话"),
                "provider_id": data.get("provider_id", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "pinned": bool(data.get("pinned", False)),
                "mode": data.get("mode", "normal") or "normal",
                "token_usage": json.loads(r[4]) if r[4] else {},
                "message_count": int(r[2] or 0),
                "messages": [],
            }
            entries.append((float(r[3] or 0), summary))
        entries.sort(key=lambda e: e[0], reverse=True)
        out = [Session(**summary) for _, summary in entries]
        out.sort(key=lambda s: not s.pinned)
        return out

    def delete(self, sid: str) -> None:
        sid = str(sid)
        with self._tx() as c:
            if self._fts:
                c.execute(
                    "DELETE FROM messages_fts WHERE rowid IN "
                    "(SELECT rowid FROM messages WHERE session_id=?)", (sid,)
                )
            c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
            c.execute("DELETE FROM threads WHERE session_id=?", (sid,))
            c.execute("DELETE FROM sessions WHERE id=?", (sid,))

    def clear(self, sid: str) -> None:
        s = self.get(sid)
        if s:
            s.messages = []
            for t in s.threads:
                t.messages = []
            s.title = "新会话"
            self.save(s)

    def stat(self, sid: str) -> Optional[Dict[str, int]]:
        row = self.db.one(
            "SELECT updated_ts, doc_size FROM sessions WHERE id=?", (str(sid),)
        )
        if row is None:
            return None
        size = int(row[1] or 0)
        # 线程/消息行数也计入指纹，编辑消息后 ETag 必须变化
        size += int(self.db.scalar(
            "SELECT COALESCE(SUM(LENGTH(doc)),0) FROM messages WHERE session_id=?",
            (str(sid),),
        ) or 0)
        return {"mtime_ns": int(float(row[0] or 0) * 1_000_000_000), "size": size}

    # ---- 搜索 ----
    @staticmethod
    def _like(needle: str) -> str:
        esc = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%" + esc + "%"

    def _fts_match(self, needle: str) -> Optional[List[str]]:
        if not self._fts or len(needle) < 3:
            return None
        quoted = '"' + needle.replace('"', '""') + '"'
        try:
            rows = self.db.query(
                "SELECT m.session_id FROM messages_fts f "
                "JOIN messages m ON m.rowid = f.rowid "
                "WHERE messages_fts MATCH ? GROUP BY m.session_id",
                (quoted,),
            )
        except Exception:
            return None
        return [r[0] for r in rows]

    def _count_and_snippet(self, sid: str, needle: str) -> Tuple[int, str]:
        like = self._like(needle)
        row = self.db.one(
            "SELECT COALESCE(SUM("
            "(LENGTH(search_text) - LENGTH(REPLACE(LOWER(search_text), LOWER(?), '')))"
            " / MAX(LENGTH(?), 1)), 0), MIN(search_text) FROM messages "
            "WHERE session_id=? AND search_text LIKE ? ESCAPE '\\'",
            (needle, needle, sid, like),
        )
        cnt = int((row[0] if row else 0) or 0)
        sample = str(row[1] or "") if row else ""
        return cnt, _snippet(sample, needle)

    def message_search(self, query: str, limit: int = 200) -> Dict[str, Tuple[int, str]]:
        q = (query or "").strip()
        out: Dict[str, Tuple[int, str]] = {}
        if not q:
            return out
        fts_ids = self._fts_match(q)
        if fts_ids is not None:
            for sid in fts_ids:
                cnt, snip = self._count_and_snippet(sid, q)
                if cnt <= 0:
                    continue
                out[sid] = (cnt, snip)
                if len(out) >= limit:
                    break
            return out
        like = self._like(q)
        rows = self.db.query(
            "SELECT session_id FROM messages WHERE search_text LIKE ? ESCAPE '\\' "
            "GROUP BY session_id LIMIT ?",
            (like, int(limit)),
        )
        for (sid,) in rows:
            cnt, snip = self._count_and_snippet(sid, q)
            out[sid] = (max(cnt, 1), snip)
        return out
        fts_ids = self._fts_match(q)
        if fts_ids is not None:
            for sid in fts_ids:
                r = self.db.one(
                    "SELECT COUNT(*), MIN(content) FROM messages WHERE session_id=? "
                    "AND content LIKE ? ESCAPE '\\'",
                    (sid, "%" + q.replace("\\", "\\\\").replace("%", "\\%")
                     .replace("_", "\\_") + "%"),
                )
                if r is None or not r[0]:
                    continue
                out[sid] = (int(r[0]), _snippet(str(r[1] or ""), q))
                if len(out) >= limit:
                    break
            return out
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.db.query(
            "SELECT session_id, COUNT(*), MIN(search_text) FROM messages "
            "WHERE search_text LIKE ? ESCAPE '\\' GROUP BY session_id LIMIT ?",
            (like, int(limit)),
        )
        for r in rows:
            # 计数按 JSON store 的口径只统计正文/名字命中的次数
            cnt = int(self.db.scalar(
                "SELECT COALESCE(SUM("
                " (LENGTH(content) - LENGTH(REPLACE(LOWER(content), LOWER(?), '')))"
                " / MAX(LENGTH(?), 1)), 0) FROM messages WHERE session_id=?"
                " AND search_text LIKE ? ESCAPE '\\'",
                (q, q, r[0], like),
            ) or 0)
            out[r[0]] = (max(cnt, 1), _snippet(str(r[2] or ""), q))
        return out
