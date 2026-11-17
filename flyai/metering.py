"""按量计费：DeepSeek 搜索/抓取/TTS/ASR 的点数结算与流水。

与 billing.py 的 token 计费分开：这里按次/字符/时长计价。
1 点 = 1 元；管理员不扣费也不写流水；quota_total=-1 仍累计 used。
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Optional

from .billing import append_ledger
from .proactor.clock import local_date_str
from .user import ADMIN_UID


def _is_wide(ch: str) -> bool:
    code = ord(ch)
    return (0x2E80 <= code <= 0x9FFF) or (0xAC00 <= code <= 0xD7AF) or (
        0xF900 <= code <= 0xFAFF
    ) or (0xFF00 <= code <= 0xFFEF) or (0x3000 <= code <= 0x303F)


def billable_chars(text: str) -> int:
    """DeepSeek TTS 计费口径：汉字/全角字符算 2，半角算 1。"""
    return sum(2 if _is_wide(ch) else 1 for ch in (text or ""))


def per_success_cost(price_per_1000: float) -> float:
    """每次成功调用（搜索/抓取）的成本。"""
    try:
        return round(max(0.0, float(price_per_1000)) / 1000.0, 6)
    except (TypeError, ValueError):
        return 0.0


def tts_cost(chars: int, price_per_10000: float) -> float:
    try:
        return round(max(0, int(chars)) * max(0.0, float(price_per_10000))
                     / 10000.0, 6)
    except (TypeError, ValueError):
        return 0.0


def asr_cost(duration_ms: int, price_per_hour: float) -> float:
    """按秒向上取整计费。"""
    try:
        seconds = max(0, int(math.ceil(max(0, int(duration_ms)) / 1000.0)))
        return round(seconds * max(0.0, float(price_per_hour)) / 3600.0, 6)
    except (TypeError, ValueError):
        return 0.0


class UsageMeter:
    """单个用户的一次请求内共享的计量器。"""

    def __init__(self, user_store, uid: str, ledger_path: Optional[Path],
                 tzname: str = "Asia/Shanghai",
                 is_admin: Optional[bool] = None):
        self.user_store = user_store
        self.uid = uid
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.tzname = tzname or "Asia/Shanghai"
        self.is_admin = (uid == ADMIN_UID) if is_admin is None else bool(is_admin)

    def available(self) -> bool:
        """余额是否大于 0；无用户记录/无限额度视为可用。"""
        store = self.user_store
        if store is None:
            return True
        try:
            u = store.get_by_id(self.uid)
        except Exception:
            return True
        if u is None:
            return True
        total = float(getattr(u, "quota_total", 0) or 0)
        if total == -1:
            return True
        used = float(getattr(u, "quota_used", 0) or 0)
        return max(0.0, total - used) > 1e-9

    def charge(self, kind: str, quantity: Any, unit: str, cost: float,
               engine: str = "", session_id: str = "") -> float:
        """成功调用后扣费并写流水；返回实际扣费额。"""
        try:
            cost = max(0.0, float(cost))
        except (TypeError, ValueError):
            cost = 0.0
        if cost <= 0 or self.is_admin:
            return 0.0
        if self.user_store is not None:
            self.user_store.add_used(self.uid, cost)
        if self.ledger_path is not None:
            now = time.time()
            append_ledger(self.ledger_path, {
                "ts": now,
                "date": local_date_str(now, self.tzname),
                "session_id": session_id or "",
                "kind": kind,
                "quantity": quantity,
                "unit": unit,
                "cost": round(cost, 6),
                "engine": engine or "",
                "messages": 0,
            })
        return cost


def make_usage_meter(state, sc) -> UsageMeter:
    """按请求构造计量器；路由层与语音接口共用这一工厂。"""
    user_store = getattr(state, "user_store", None)
    ledger_path = None
    settings_path = getattr(sc, "settings_path", None)
    if settings_path is not None:
        ledger_path = Path(settings_path).parent / "usage.jsonl"
    tzname = "Asia/Shanghai"
    cfg = getattr(state, "config", None)
    scheduler = getattr(cfg, "scheduler", None)
    if scheduler is not None:
        tzname = getattr(scheduler, "timezone", tzname) or tzname
    return UsageMeter(
        user_store, getattr(sc, "uid", ""), ledger_path, tzname,
        is_admin=(getattr(sc, "uid", "") == ADMIN_UID),
    )