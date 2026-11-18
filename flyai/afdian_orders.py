"""爱发电订单流水：data/afdian.json，进程内锁 + 原子写入。

状态流转：

    received ──► verify_failed ──► issued
        │              │            ▲
        └──► invalid   └──► pending_cap ──► issued（管理员补发）

同一订单号只登记一次（webhook 重试幂等）；「校验 → 算额度 → 上限判断 →
发卡 → 落库」整体在 store.lock 内完成，防止并发回调重复发卡。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROJECT_ROOT

# 计入累计销售额的状态（真正会发卡的订单）
_PAID_STATES = ("issued", "pending_cap")


def _amount(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


class AfdianOrderStore:
    def __init__(self, data_dir: Optional[Path] = None):
        self._path = (data_dir or (PROJECT_ROOT / "data")) / "afdian.json"
        self._orders: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.RLock()
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for key, entry in (data.get("orders") or {}).items():
                if isinstance(entry, dict):
                    entry.setdefault("state", "received")
                    self._orders[str(key)] = entry
        except Exception:
            self._orders = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"orders": self._orders}
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # ---- 查询 ----

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self._orders.get(str(key or ""))

    def list(self) -> List[Dict[str, Any]]:
        with self.lock:
            out = [dict(v) for v in self._orders.values()]
        out.sort(key=lambda v: float(v.get("created_at") or 0), reverse=True)
        return out

    def cumulative_paid(self, exclude_key: str = "") -> float:
        """所有已发卡/待补发订单的实付金额之和（销售额上限计量口径）。"""
        with self.lock:
            total = 0.0
            for key, entry in self._orders.items():
                if key == exclude_key:
                    continue
                if entry.get("state") in _PAID_STATES:
                    total += _amount(entry.get("total_amount"))
            return round(total, 2)

    def summary(self, cap: float) -> Dict[str, Any]:
        with self.lock:
            states = [v.get("state") for v in self._orders.values()]
        return {
            "cumulative": self.cumulative_paid(),
            "cap": cap,
            "pending": states.count("pending_cap"),
            "issued": states.count("issued"),
            "total": len(states),
        }

    # ---- 写入 ----

    def begin(self, key: str, raw: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """登记 webhook 收到的订单；已存在返回 None（幂等）。"""
        key = str(key or "").strip()
        if not key:
            return None
        with self.lock:
            if key in self._orders:
                return None
            record = {
                "out_trade_no": key,
                "afdian_user_id": "",
                "plan_id": "",
                "plan_title": "",
                "total_amount": 0.0,
                "remark": "",
                "order_status": "",
                "state": "received",
                "credits": 0.0,
                "cdk_code": "",
                "bound_uid": "",
                "email_sent": False,
                "email_error": "",
                "reason": "",
                "created_at": time.time(),
                "verified_at": 0,
                "issued_at": 0,
                "raw": raw or {},
            }
            self._orders[key] = record
            self._save()
            return dict(record)

    def mark(self, key: str, state: Optional[str] = None, **fields: Any) -> Optional[Dict[str, Any]]:
        with self.lock:
            entry = self._orders.get(str(key or ""))
            if entry is None:
                return None
            if state:
                entry["state"] = state
            entry.update(fields)
            self._save()
            return dict(entry)
