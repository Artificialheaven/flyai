"""邮箱验证码：生成、发送限速、校验。

设计取舍：
- 进程内存储（dict + RLock），不落盘：验证码 TTL 很短，服务重启后未使用的码
  作废可接受；落盘反而会把可离线爆破的材料留在磁盘上。
- 单进程部署假设（uvicorn 单 worker）。多 worker 时各进程的码不互通，需改为
  共享存储（如 Redis），当前部署形态下不引入。
- 发送冷却与小时限额独立于验证码有效期：即使旧码过期，仍然计入一小时内的
  发送次数，防止「等码过期再重发」绕过限速。
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

CODE_LENGTH = 6
DEFAULT_TTL = 600
RESEND_INTERVAL = 60
MAX_ATTEMPTS = 5
HOURLY_LIMIT = 10
IP_HOURLY_LIMIT = 20
HOUR = 3600

PURPOSES = ("register", "reset")


class EmailCodeError(Exception):
    """发码被限速/拒绝；message 面向用户，retry_after 秒。"""

    def __init__(self, message: str, retry_after: int = 0, status: int = 400):
        super().__init__(message)
        self.message = message
        self.retry_after = int(retry_after or 0)
        self.status = int(status)


def _random_code() -> str:
    """6 位数字验证码；测试可 monkeypatch 本函数固定返回值。"""
    return "%0*d" % (CODE_LENGTH, secrets.randbelow(10 ** CODE_LENGTH))


def _recent(times: Optional[List[float]], now: float) -> List[float]:
    return [t for t in (times or []) if t >= now - HOUR]


class EmailCodeStore:
    """(purpose, email) → 验证码记录；另按 IP 统计发码次数。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (purpose, email) -> {code, expires_at, attempts, last_sent_at}
        self._records: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # (purpose, email) -> 最近一小时发码时间戳；独立于记录有效期
        self._sends: Dict[Tuple[str, str], List[float]] = {}
        # ip -> 最近一小时发码时间戳
        self._ip_sends: Dict[str, List[float]] = {}

    def issue(
        self,
        purpose: str,
        email: str,
        ip: str = "",
        ttl: int = DEFAULT_TTL,
        resend_interval: int = RESEND_INTERVAL,
        hourly_limit: int = HOURLY_LIMIT,
        ip_hourly_limit: int = IP_HOURLY_LIMIT,
    ) -> str:
        """生成并保存验证码；冷却/超限抛 EmailCodeError（status=429）。"""
        if purpose not in PURPOSES:
            raise EmailCodeError("验证码用途不正确", status=400)
        email = (email or "").strip().lower()
        now = time.time()
        key = (purpose, email)
        with self._lock:
            self._purge_locked(now)

            rec = self._records.get(key)
            if rec is not None:
                wait = int(rec.get("last_sent_at", 0) + resend_interval - now) + 1
                if wait > 0:
                    raise EmailCodeError(
                        "发送过于频繁，请 %d 秒后再试" % wait,
                        retry_after=wait,
                        status=429,
                    )

            sends = _recent(self._sends.get(key), now)
            if len(sends) >= hourly_limit:
                wait = max(1, int(sends[0] + HOUR - now) + 1)
                raise EmailCodeError(
                    "该邮箱发码次数过多，请 %d 秒后再试" % wait,
                    retry_after=wait,
                    status=429,
                )

            ip_key = (ip or "").strip()
            if ip_key:
                ip_sends = _recent(self._ip_sends.get(ip_key), now)
                if len(ip_sends) >= ip_hourly_limit:
                    wait = max(1, int(ip_sends[0] + HOUR - now) + 1)
                    raise EmailCodeError(
                        "请求过于频繁，请稍后再试",
                        retry_after=wait,
                        status=429,
                    )
                ip_sends.append(now)
                self._ip_sends[ip_key] = ip_sends

            code = _random_code()
            sends.append(now)
            self._sends[key] = sends
            self._records[key] = {
                "code": code,
                "expires_at": now + int(ttl or DEFAULT_TTL),
                "attempts": 0,
                "last_sent_at": now,
            }
            return code

    def verify(self, purpose: str, email: str, code: str) -> bool:
        """校验验证码；成功后保留记录（由调用方在业务成功后 discard）。

        校验失败累计尝试次数，达到 MAX_ATTEMPTS 即作废。
        """
        code = (code or "").strip()
        if purpose not in PURPOSES or not code:
            return False
        key = (purpose, (email or "").strip().lower())
        now = time.time()
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                return False
            if float(rec.get("expires_at", 0)) < now:
                self._records.pop(key, None)
                return False
            if hmac.compare_digest(str(rec.get("code", "")), code):
                return True
            rec["attempts"] = int(rec.get("attempts", 0)) + 1
            if rec["attempts"] >= MAX_ATTEMPTS:
                self._records.pop(key, None)
            return False

    def discard(self, purpose: str, email: str) -> None:
        """业务成功后消费验证码。"""
        key = (purpose, (email or "").strip().lower())
        with self._lock:
            self._records.pop(key, None)

    def purge_expired(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._purge_locked(now)

    def _purge_locked(self, now: float) -> None:
        dead = [
            key
            for key, rec in self._records.items()
            if float(rec.get("expires_at", 0)) < now
        ]
        for key in dead:
            self._records.pop(key, None)
        for store in (self._sends, self._ip_sends):
            empty = []
            for key, times in store.items():
                kept = _recent(times, now)
                if kept:
                    store[key] = kept
                else:
                    empty.append(key)
            for key in empty:
                store.pop(key, None)
