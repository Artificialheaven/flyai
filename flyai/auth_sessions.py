"""登录凭证：随机会话令牌 + 登录限速。

取代旧的「X-User-Id 固定字符串即凭证」模型：

- 登录成功后由 AuthSessionStore 签发随机 token（不透明、可吊销、有过期时间），
  之后所有请求带 ``Authorization: Bearer <token>``（也兼容 X-Auth-Token 头）。
- 给 store 绑定文件路径后，令牌持久化到 SQLite（``auth_sessions.db``，内容为
  不透明令牌，属敏感数据，必须放在 gitignore 的 data/ 下）；不绑定则纯进程内
  存储。首次打开时自动导入同目录下的旧 auth_sessions.json 并改名备份。
- ``logout`` 与管理员重置密码仍会真正吊销凭证。
- LoginRateLimiter 按「客户端 IP + 用户名」计数：15 分钟内 5 次失败锁定 5 分钟。
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sqlite_store import JsonTable, SqliteDB, migrate_legacy_file

# 令牌默认有效期 7 天
DEFAULT_TOKEN_TTL = 7 * 24 * 3600
# 限速：窗口 15 分钟 / 允许失败 5 次 / 锁定 5 分钟
LIMIT_WINDOW = 15 * 60
LIMIT_MAX_FAILS = 5
LIMIT_LOCK_SECONDS = 5 * 60


class AuthSessionStore:
    """token -> 会话记录（uid/name/role/level/过期时间）。

    绑定 path 后从磁盘加载并在每次变更时原子写回，重启不丢登录态。
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TOKEN_TTL,
        path: Optional[Path] = None,
    ):
        self._ttl = int(ttl_seconds)
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._path: Optional[Path] = None
        self._db: Optional[SqliteDB] = None
        self._table: Optional[JsonTable] = None
        if path is not None:
            self.bind_file(Path(path))

    def bind_file(self, path: Path) -> None:
        """绑定持久化数据库（.db）并载入其中未过期的令牌。"""
        path = Path(path)
        if path.suffix.lower() != ".db":
            path = path.with_suffix(".db")
        if self._db is not None:
            self._db.close()
        self._path = path
        self._db = SqliteDB(path)
        self._table = JsonTable(self._db, "sessions")
        self._tokens = {}
        # 首次使用（空库）时导入同目录旧 auth_sessions.json；导入后旧文件已改名，
        # 不会重复导入。
        if self._table.count() == 0:
            migrate_legacy_file(path.with_suffix(".json"), self._import_legacy)
        now = time.time()
        tokens: Dict[str, Dict[str, Any]] = {}
        try:
            for token, rec in self._table.items():
                if not isinstance(rec, dict):
                    continue
                if float(rec.get("expires", 0)) >= now:
                    tokens[str(token)] = rec
        except (TypeError, ValueError):
            # 损坏数据不阻断启动，当作空库重新签发
            tokens = {}
        self._tokens = tokens
        self._save()

    def _import_legacy(self, data: Dict[str, Any]) -> None:
        for token, rec in data.items():
            if isinstance(rec, dict):
                self._table.put(str(token), rec)

    def issue(self, uid: str, name: str, role: str, level: int) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            "uid": uid,
            "name": name,
            "role": role,
            "level": int(level or 0),
            "expires": time.time() + self._ttl,
        }
        self._save()
        return token

    def resolve(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """返回该 token 的记录；过期/不存在返回 None（过期顺手清除并落盘）。"""
        if not token:
            return None
        rec = self._tokens.get(token)
        if rec is None:
            return None
        if rec.get("expires", 0) < time.time():
            self._tokens.pop(token, None)
            self._save()
            return None
        return rec

    def revoke(self, token: Optional[str]) -> bool:
        removed = self._tokens.pop(token, None) is not None
        if removed:
            self._save()
        return removed

    def revoke_for_uid(self, uid: str) -> int:
        """吊销某用户的全部令牌（如管理员重置密码后），返回吊销数量。"""
        dead = [t for t, rec in self._tokens.items() if rec.get("uid") == uid]
        for t in dead:
            self._tokens.pop(t, None)
        if dead:
            self._save()
        return len(dead)

    def gc(self) -> None:
        now = time.time()
        dead = [t for t, rec in self._tokens.items() if rec.get("expires", 0) < now]
        for t in dead:
            self._tokens.pop(t, None)
        if dead:
            self._save()

    def _save(self) -> None:
        """把内存令牌集合事务性同步到 SQLite，避免写一半损坏导致登录态丢失。"""
        if self._table is None:
            return
        conn = self._db.conn()
        try:
            live = set(self._tokens)
            stale = [token for token in self._table.keys() if token not in live]
            conn.execute("BEGIN IMMEDIATE")
            for token, rec in self._tokens.items():
                self._table.put(token, rec)
            for token in stale:
                self._table.delete(token)
            conn.execute("COMMIT")
        except (OSError, sqlite3.Error):
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            # 磁盘不可写时降级为进程内语义，不阻断登录
            pass


class LoginRateLimiter:
    """简单计数限速：窗口内失败达上限 → 锁定一段时间。

    内存结构：key -> {"fails": [时间戳...], "locked_until": float}。
    """

    def __init__(
        self,
        max_fails: int = LIMIT_MAX_FAILS,
        window_seconds: int = LIMIT_WINDOW,
        lock_seconds: int = LIMIT_LOCK_SECONDS,
    ):
        self._max_fails = int(max_fails)
        self._window = int(window_seconds)
        self._lock = int(lock_seconds)
        self._attempts: Dict[str, Dict[str, Any]] = {}

    def locked_seconds(self, key: str, now: Optional[float] = None) -> int:
        """当前是否处于锁定；返回剩余锁定秒数（0 表示未锁定）。"""
        now = time.time() if now is None else now
        rec = self._attempts.get(key)
        if rec is None:
            return 0
        locked_until = rec.get("locked_until", 0)
        if locked_until > now:
            return int(locked_until - now) + 1
        return 0

    def record_failure(
        self, key: str, now: Optional[float] = None
    ) -> Tuple[bool, int]:
        """记一次失败。返回 (是否新触发锁定, 锁定剩余秒数)。

        若失败前已锁定，直接返回当前锁定状态，不重置锁定时间。
        """
        now = time.time() if now is None else now
        already = self.locked_seconds(key, now)
        if already:
            return False, already

        rec = self._attempts.setdefault(key, {"fails": [], "locked_until": 0})
        fails = [t for t in rec["fails"] if t >= now - self._window]
        fails.append(now)
        rec["fails"] = fails
        if len(fails) >= self._max_fails:
            rec["locked_until"] = now + self._lock
            rec["fails"] = []
            return True, self._lock
        return False, 0

    def record_success(self, key: str) -> None:
        self._attempts.pop(key, None)

    def gc(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        dead = []
        for key, rec in self._attempts.items():
            if rec.get("locked_until", 0) <= now and not any(
                t >= now - self._window for t in rec.get("fails", [])
            ):
                dead.append(key)
        for key in dead:
            self._attempts.pop(key, None)


# ---- 请求侧解析 ----

def extract_token(request: Any) -> Optional[str]:
    """从请求中取令牌：Authorization: Bearer <token>，其次 X-Auth-Token。"""
    auth = (getattr(request, "headers", {}).get("authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        token = auth[7:].strip()
        if token:
            return token
    alt = (request.headers.get("x-auth-token") or "").strip()
    return alt or None


def resolve_uid(state: Any, request: Any) -> Optional[str]:
    """经会话令牌解析出 uid；不校验用户是否仍存在（由调用方决定）。"""
    token = extract_token(request)
    rec = state.auth_sessions.resolve(token)
    if rec is None:
        return None
    return rec.get("uid")
