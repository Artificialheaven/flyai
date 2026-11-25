"""CDK（兑换码）存储：data/cdk.db（SQLite，JsonTable 文档表）。

每张 CDK 三个核心属性：额度（点数）、有效期（含端点，空=永久）、
全站可兑换总次数（<=0 表示不限）。每张 CDK 同一用户只能兑换一次。
首次打开时自动导入同目录下的旧 cdk.json，随后旧文件改名备份。
"""

from __future__ import annotations

import secrets
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROJECT_ROOT
from .proactor.clock import local_date_str, parse_due_at
from .sqlite_store import JsonTable, SqliteDB, migrate_legacy_file

# 生成用字母表：去掉易混淆的 0/O/1/I
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class CdkError(Exception):
    """兑换失败（附带前端可直展示的中文原因）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _norm_code(code: str) -> str:
    return (code or "").strip().upper()


def _compact(code: str) -> str:
    return "".join(ch for ch in _norm_code(code) if ch.isalnum())


def _fmt_money(v: float) -> float:
    return round(float(v), 2)


class CdkStore:
    def __init__(self, data_dir: Optional[Path] = None, tzname: str = "Asia/Shanghai"):
        self._path = (data_dir or (PROJECT_ROOT / "data")) / "cdk.db"
        self._db = SqliteDB(self._path)
        self._table = JsonTable(self._db, "codes")
        self.tzname = tzname or "Asia/Shanghai"
        self._codes: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        # 首次使用（空库）时导入同目录旧 cdk.json；导入后旧文件已改名，不会重复导入。
        if self._table.count() == 0:
            migrate_legacy_file(
                self._path.with_suffix(".json"), self._import_legacy
            )
        try:
            for code, entry in self._table.items():
                if isinstance(entry, dict):
                    entry.setdefault("redemptions", {})
                    self._codes[_norm_code(code)] = entry
        except Exception:
            self._codes = {}

    def _import_legacy(self, data: Dict[str, Any]) -> None:
        raw = data.get("codes")
        if not isinstance(raw, dict):
            return
        for code, entry in raw.items():
            if isinstance(entry, dict):
                self._table.put(_norm_code(str(code)), entry)

    def _save(self) -> None:
        live = set(self._codes)
        stale = [code for code in self._table.keys() if code not in live]
        conn = self._db.conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for code, entry in self._codes.items():
                self._table.put(code, entry)
            for code in stale:
                self._table.delete(code)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # ---- 工具 ----

    def _today(self, now: Optional[float] = None) -> str:
        return local_date_str(time.time() if now is None else now, self.tzname)

    def _normalize_expiry(self, expires_at: Any) -> str:
        """接受 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM' / epoch；空=永久。"""
        if expires_at is None or expires_at == "":
            return ""
        text = str(expires_at).strip()
        if not text:
            return ""
        # 纯日期直接校验
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            pass
        epoch = parse_due_at(text, time.time(), self.tzname)
        if epoch is None:
            raise CdkError(f"有效期格式无效：{text}")
        return local_date_str(epoch, self.tzname)

    def status(self, entry: Dict[str, Any], now: Optional[float] = None) -> str:
        if entry.get("disabled"):
            return "disabled"
        expires = entry.get("expires_at") or ""
        if expires and self._today(now) > expires:
            return "expired"
        max_uses = int(entry.get("max_uses") or 0)
        used = len(entry.get("redemptions") or {})
        if max_uses > 0 and used >= max_uses:
            return "exhausted"
        return "usable"

    @staticmethod
    def _view(code: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        redemptions = entry.get("redemptions") or {}
        return {
            "code": code,
            "amount": _fmt_money(entry.get("amount") or 0),
            "max_uses": int(entry.get("max_uses") or 0),
            "used": len(redemptions),
            "expires_at": entry.get("expires_at") or "",
            "created_at": entry.get("created_at") or 0,
            "disabled": bool(entry.get("disabled")),
            # 绑定账号（爱发电订单发卡用）；空表示所有用户都可兑换
            "bound_uid": entry.get("bound_uid") or "",
            "status": "",
        }

    def _find_code(self, code: str) -> str:
        """精确匹配优先；兼容用户去掉/替换连字符的输入。"""
        key = _norm_code(code)
        if key in self._codes:
            return key
        compact = _compact(code)
        if not compact:
            return ""
        for stored in self._codes:
            if _compact(stored) == compact:
                return stored
        return ""

    def _new_code(self) -> str:
        while True:
            parts = ["".join(secrets.choice(_ALPHABET) for _ in range(4))
                     for _ in range(3)]
            code = "-".join(parts)
            if code not in self._codes:
                return code

    # ---- 管理操作 ----

    def generate(self, count: int, amount: float, max_uses: int,
                 expires_at: Any = "", bound_uid: str = "") -> List[Dict[str, Any]]:
        count = max(1, min(int(count or 1), 500))
        amount = max(0.0, float(amount or 0))
        max_uses = max(0, int(max_uses or 0))
        expires = self._normalize_expiry(expires_at)
        created = time.time()
        bound = (bound_uid or "").strip()
        out = []
        for _ in range(count):
            code = self._new_code()
            entry = {
                "amount": amount,
                "max_uses": max_uses,
                "expires_at": expires,
                "created_at": created,
                "disabled": False,
                "bound_uid": bound,
                "redemptions": {},
            }
            self._codes[code] = entry
            view = self._view(code, entry)
            view["status"] = self.status(entry)
            out.append(view)
        self._save()
        return out

    def import_codes(self, text: str, amount: float, max_uses: int,
                     expires_at: Any = "") -> Dict[str, Any]:
        """粘贴导入：一行一个（也接受逗号/空格分隔），返回导入结果。"""
        raw = (text or "").replace(",", "\n").replace("，", "\n")
        tokens = [t.strip() for t in raw.split() if t.strip()]
        amount = max(0.0, float(amount or 0))
        max_uses = max(0, int(max_uses or 0))
        expires = self._normalize_expiry(expires_at)
        created = time.time()
        added: List[str] = []
        duplicates: List[str] = []
        invalid: List[str] = []
        for token in tokens:
            code = _norm_code(token)
            if len(code) < 4 or len(code) > 64:
                invalid.append(token)
                continue
            if self._find_code(code):
                duplicates.append(code)
                continue
            entry = {
                "amount": amount,
                "max_uses": max_uses,
                "expires_at": expires,
                "created_at": created,
                "disabled": False,
                "redemptions": {},
            }
            self._codes[code] = entry
            added.append(code)
        if added:
            self._save()
        return {"added": added, "duplicates": duplicates, "invalid": invalid}

    def list(self, status: str = "") -> List[Dict[str, Any]]:
        out = []
        for code, entry in self._codes.items():
            view = self._view(code, entry)
            view["status"] = self.status(entry)
            if status and view["status"] != status:
                continue
            out.append(view)
        out.sort(key=lambda v: float(v.get("created_at") or 0), reverse=True)
        return out

    def extract(self) -> Optional[Dict[str, Any]]:
        """取出一张可用 CDK（按创建顺序，先到先得）；不删除。

        已绑定账号的码只属于该账号的订单，不参与「取出」流程。
        """
        for code, entry in sorted(
                self._codes.items(), key=lambda kv: float(kv[1].get("created_at") or 0)):
            if entry.get("bound_uid"):
                continue
            if self.status(entry) == "usable":
                view = self._view(code, entry)
                view["status"] = "usable"
                return view
        return None

    def disable(self, code: str) -> Dict[str, Any]:
        key = self._find_code(code)
        if not key:
            raise CdkError("CDK 不存在")
        self._codes[key]["disabled"] = True
        self._save()
        view = self._view(key, self._codes[key])
        view["status"] = self.status(self._codes[key])
        return view

    def remove(self, code: str) -> bool:
        key = self._find_code(code)
        if not key:
            return False
        del self._codes[key]
        self._save()
        return True

    def export(self) -> str:
        lines = ["代码,额度,可兑换次数,已兑换,有效期,状态"]
        for view in self.list():
            lines.append(
                f"{view['code']},{view['amount']},{view['max_uses']},"
                f"{view['used']},{view['expires_at'] or '永久'},{view['status']}"
            )
        return "\n".join(lines)

    # ---- 兑换 ----

    def check_redeem(self, uid: str, code: str) -> Dict[str, Any]:
        """校验兑换资格并返回条目；不可兑换抛 CdkError。"""
        key = self._find_code(code)
        if not key:
            raise CdkError("CDK 不存在")
        entry = self._codes[key]
        bound = entry.get("bound_uid") or ""
        if bound and bound != uid:
            raise CdkError("该 CDK 已绑定其他账号")
        if uid in (entry.get("redemptions") or {}):
            raise CdkError("你已经兑换过这张 CDK")
        if entry.get("disabled"):
            raise CdkError("该 CDK 已被停用")
        expires = entry.get("expires_at") or ""
        if expires and self._today() > expires:
            raise CdkError("该 CDK 已过期")
        max_uses = int(entry.get("max_uses") or 0)
        if max_uses > 0 and len(entry.get("redemptions") or {}) >= max_uses:
            raise CdkError("该 CDK 可兑换次数已用完")
        return {"key": key, "entry": entry, "amount": _fmt_money(entry.get("amount") or 0)}

    def mark_redeemed(self, key: str, uid: str) -> None:
        entry = self._codes[key]
        entry.setdefault("redemptions", {})[uid] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save()

    def unmark_redeemed(self, key: str, uid: str) -> None:
        """额度落库失败时回滚兑换记录，避免用户白白消耗次数。"""
        entry = self._codes.get(key)
        if entry is None:
            return
        (entry.get("redemptions") or {}).pop(uid, None)
        self._save()

    def bind(self, code: str, uid: str) -> Dict[str, Any]:
        """把 CDK 绑定到账号（幂等）；已是他人绑定则抛 CdkError。"""
        key = self._find_code(code)
        if not key:
            raise CdkError("CDK 不存在")
        entry = self._codes[key]
        bound = entry.get("bound_uid") or ""
        if bound and bound != uid:
            raise CdkError("该 CDK 已绑定其他账号")
        if not bound:
            entry["bound_uid"] = uid
            self._save()
        view = self._view(key, entry)
        view["status"] = self.status(entry)
        return view

    def is_redeemed(self, code: str, uid: str) -> bool:
        key = self._find_code(code)
        if not key:
            return False
        return uid in (self._codes[key].get("redemptions") or {})