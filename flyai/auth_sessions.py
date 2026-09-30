"""登录凭证：随机会话令牌 + 登录限速。

取代旧的「X-User-Id 固定字符串即凭证」模型：

- 登录成功后由 AuthSessionStore 签发随机 token（不透明、可吊销、有过期时间），
  之后所有请求带 ``Authorization: Bearer <token>``（也兼容 X-Auth-Token 头）。
- 给 store 绑定文件路径后，令牌持久化到磁盘，重启进程不再掉线（文件含不透明
  令牌，属敏感数据，必须放在 gitignore 的 data/ 下）；不绑定则纯进程内存储。
- ``logout`` 与管理员重置密码仍会真正吊销凭证。
- LoginRateLimiter 按「客户端 IP + 用户名」计数：15 分钟内 5 次失败锁定 5 分钟。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
        if path is not None:
            self.bind_file(Path(path))

    def bind_file(self, path: Path) -> None:
        """绑定持久化文件并载入其中未过期的令牌。"""
        self._path = path
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                now = time.time()
                if isinstance(data, dict):
                    self._tokens = {
                        str(t): rec
                        for t, rec in data.items()
                        if isinstance(rec, dict)
                        and float(rec.get("expires", 0)) >= now
                    }
            except (ValueError, OSError, TypeError):
                # 损坏文件不阻断启动，当作空库重新签发
                self._tokens = {}
        self._save()

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
        """原子写回（tmp + replace），避免写一半损坏导致全部登录态丢失。"""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(
                json.dumps(self._tokens, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(tmp, self._path)
        except OSError:
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
