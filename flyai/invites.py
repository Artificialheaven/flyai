"""邀请码存储：data/invites.json，原子写入。

每个用户（含 admin）同一时间只保留一个有效邀请码；重新生成或换码时旧码
立即作废。注册时必须提供邀请码，邀请次数用尽后需重新生成。
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROJECT_ROOT

# 生成用字母表：去掉易混淆的 0/O/1/I
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_MIN_USES = 1
_MAX_USES = 10000


class InviteError(Exception):
    """邀请码操作失败（附带前端可直展示的中文原因）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _norm_code(code: str) -> str:
    return (code or "").strip().upper()


def _compact(code: str) -> str:
    return "".join(ch for ch in _norm_code(code) if ch.isalnum())


def _clamp_max_uses(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InviteError("邀请码人数上限必须为 1-10000 的整数")
    if number < _MIN_USES or number > _MAX_USES:
        raise InviteError("邀请码人数上限必须为 1-10000 的整数")
    return number


def _validate_custom_code(code: str) -> str:
    """自定义码归一化为大写；4-64 位，仅 A-Z0-9-，首尾不能是连字符。"""
    normalized = _norm_code(code)
    if len(normalized) < 4 or len(normalized) > 64:
        raise InviteError("邀请码格式不正确")
    if normalized[0] == "-" or normalized[-1] == "-":
        raise InviteError("邀请码格式不正确")
    if len(_compact(normalized)) < 4:
        raise InviteError("邀请码格式不正确")
    for ch in normalized:
        if ch != "-" and not ("A" <= ch <= "Z") and not ("0" <= ch <= "9"):
            raise InviteError("邀请码格式不正确")
    return normalized


class InviteStore:
    def __init__(self, data_dir: Optional[Path] = None):
        self._path = (data_dir or (PROJECT_ROOT / "data")) / "invites.json"
        self._lock = threading.RLock()
        self._codes: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._codes = {}
            return
        if not isinstance(data, dict):
            self._codes = {}
            return
        # 兼容 {"codes": {...}} 包装与顶层以码为 key 两种形态
        raw = data.get("codes")
        if not isinstance(raw, dict):
            raw = data
        for code, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            owner = str(entry.get("owner") or "")
            if not owner:
                continue
            key = _norm_code(str(entry.get("code") or code))
            if len(key) < 4:
                continue
            entry["code"] = key
            entry["owner"] = owner
            try:
                entry["max_uses"] = int(entry.get("max_uses") or 0)
            except (TypeError, ValueError):
                entry["max_uses"] = 0
            if not isinstance(entry.get("uses"), list):
                entry["uses"] = []
            self._codes[key] = entry
        self._dedupe_owners()

    def _dedupe_owners(self) -> None:
        """防御历史脏数据：同一 owner 只保留 created_at 最新的码。"""
        chosen: Dict[str, str] = {}
        for code in list(self._codes):
            entry = self._codes[code]
            owner = str(entry.get("owner") or "")
            previous = chosen.get(owner)
            if previous is None:
                chosen[owner] = code
                continue
            if float(entry.get("created_at") or 0) > float(
                    self._codes[previous].get("created_at") or 0):
                self._codes.pop(previous, None)
                chosen[owner] = code
            else:
                self._codes.pop(code, None)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._codes, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # ---- 工具 ----

    @staticmethod
    def _view(code: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        uses = entry.get("uses") or []
        max_uses = int(entry.get("max_uses") or 0)
        used = len(uses)
        return {
            "code": code,
            "owner": entry.get("owner") or "",
            "max_uses": max_uses,
            "used": used,
            "remaining": max(0, max_uses - used),
            "created_at": entry.get("created_at") or 0,
            "uses": [
                {
                    "uid": str(item.get("uid") or ""),
                    "name": str(item.get("name") or ""),
                    "at": item.get("at") or 0,
                }
                for item in uses
                if isinstance(item, dict)
            ],
            "status": "usable" if used < max_uses else "exhausted",
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
        compacts = {_compact(code) for code in self._codes}
        while True:
            parts = ["".join(secrets.choice(_ALPHABET) for _ in range(4))
                     for _ in range(3)]
            code = "-".join(parts)
            if code not in self._codes and _compact(code) not in compacts:
                return code

    def _remove_owner_locked(self, owner: str) -> bool:
        removed = False
        for code in list(self._codes):
            if self._codes[code].get("owner") == owner:
                del self._codes[code]
                removed = True
        return removed

    # ---- 查询 ----

    def get_for_owner(self, uid: str) -> Optional[Dict[str, Any]]:
        owner = str(uid or "")
        with self._lock:
            for code, entry in self._codes.items():
                if entry.get("owner") == owner:
                    return self._view(code, entry)
        return None

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            views = [self._view(code, entry) for code, entry in self._codes.items()]
        views.sort(key=lambda v: float(v.get("created_at") or 0), reverse=True)
        return views

    def check(self, code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            key = self._find_code(code)
            if not key:
                return None
            return self._view(key, self._codes[key])

    # ---- 生成 / 修改 ----

    def generate(self, uid: str, max_uses: int = 5) -> Dict[str, Any]:
        owner = str(uid or "").strip()
        if not owner:
            raise InviteError("用户不存在")
        limit = _clamp_max_uses(max_uses)
        with self._lock:
            self._remove_owner_locked(owner)
            code = self._new_code()
            entry = {
                "code": code,
                "owner": owner,
                "max_uses": limit,
                "created_at": time.time(),
                "uses": [],
            }
            self._codes[code] = entry
            self._save()
            return self._view(code, entry)

    def set_custom(self, uid: str, code: str, max_uses: int) -> Dict[str, Any]:
        owner = str(uid or "").strip()
        if not owner:
            raise InviteError("用户不存在")
        limit = _clamp_max_uses(max_uses)
        normalized = _validate_custom_code(code)
        with self._lock:
            key = self._find_code(normalized)
            if key:
                entry = self._codes[key]
                if entry.get("owner") != owner:
                    raise InviteError("邀请码已被使用")
                used = len(entry.get("uses") or [])
                if limit < used:
                    raise InviteError("邀请码使用人数不能少于已邀请人数")
                entry["max_uses"] = limit
                self._save()
                return self._view(key, entry)
            # 换码：旧码立即作废，已注册用户不受影响
            self._remove_owner_locked(owner)
            entry = {
                "code": normalized,
                "owner": owner,
                "max_uses": limit,
                "created_at": time.time(),
                "uses": [],
            }
            self._codes[normalized] = entry
            self._save()
            return self._view(normalized, entry)

    # ---- 注册联动 ----

    def mark_used(self, code: str, uid: str, name: str) -> Dict[str, Any]:
        with self._lock:
            key = self._find_code(code)
            if not key:
                raise InviteError("邀请码无效")
            entry = self._codes[key]
            uses = entry.get("uses") or []
            if len(uses) >= int(entry.get("max_uses") or 0):
                raise InviteError("邀请码已用尽")
            record = {"uid": str(uid), "name": str(name or ""), "at": time.time()}
            uses.append(record)
            entry["uses"] = uses
            try:
                self._save()
            except Exception:
                uses.pop()
                raise
            return self._view(key, entry)

    def unmark_used(self, code: str, uid: str) -> None:
        """注册回滚用：移除指定用户的使用记录。"""
        with self._lock:
            key = self._find_code(code)
            if not key:
                return
            entry = self._codes[key]
            uses = entry.get("uses") or []
            kept = [item for item in uses
                    if not isinstance(item, dict) or str(item.get("uid") or "") != str(uid)]
            if len(kept) == len(uses):
                return
            entry["uses"] = kept
            self._save()

    # ---- 删除 ----

    def remove(self, code: str) -> bool:
        with self._lock:
            key = self._find_code(code)
            if not key:
                return False
            del self._codes[key]
            self._save()
            return True

    def remove_owner(self, uid: str) -> bool:
        owner = str(uid or "")
        with self._lock:
            if not self._remove_owner_locked(owner):
                return False
            self._save()
            return True
