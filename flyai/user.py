"""多用户：账户存储 + 每个用户的数据作用域。

数据按用户隔离：「所有角色卡、世界观、会话、上传头像」都落在各自用户的目录下；
提供商与全局配置仍共享（本地工具）。密码用 sha256 加盐存储。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .config import AppConfig, PROJECT_ROOT
from .lore.store import LoreStore
from .sessions import JsonSessionStore, MemorySessionStore, SessionStore


# 管理员标识：管理员不在 user 库中，凭据存于 config/auth（config.auth.admin）
ADMIN_UID = "admin"


@dataclass
class User:
    """普通用户（由管理员创建，一律非管理员）。"""

    id: str = ""
    name: str = ""
    password_hash: str = ""
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex
        if not self.created_at:
            self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    def set_password(self, password: str) -> None:
        self.password_hash = _hash_password(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return True  # 无密码用户，任何输入均可进入
        return _hash_password(password) == self.password_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "password_hash": self.password_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "User":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            password_hash=d.get("password_hash", ""),
            created_at=d.get("created_at", ""),
        )


def _hash_password(password: str, salt: str = "") -> str:
    salt = salt or "flyai"
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


class UserStore:
    """data/users.json 普通用户清单。管理员不在库里（凭据在 config）。"""

    def __init__(self, data_dir: Path = None):
        self._path = (data_dir or (PROJECT_ROOT / "data")) / "users.json"
        self._users: Dict[str, User] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for ud in data.get("users", []):
                u = User.from_dict(ud)
                if u.id == "default":
                    continue  # 清理旧版自动创建的管理员账号（新模型管理员在 config）
                self._users[u.id] = u
        except Exception:
            self._users = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"users": [u.to_dict() for u in self._users.values()]}
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def list(self) -> List[User]:
        return sorted(self._users.values(), key=lambda u: u.created_at)

    def get_by_id(self, uid: str) -> Optional[User]:
        return self._users.get(uid)

    def get_by_name(self, name: str) -> Optional[User]:
        for u in self._users.values():
            if u.name == name:
                return u
        return None

    def create(self, name: str, password: str) -> User:
        if self.get_by_name(name):
            raise ValueError(f"用户名已存在: {name}")
        u = User(name=name)
        if password:
            u.set_password(password)  # 空密码保留空 hash，check_password 视为任意输入可进入
        self._users[u.id] = u
        self._save()
        return u

    def remove(self, uid: str) -> bool:
        """删除普通用户。返回删除是否发生。"""
        if uid not in self._users:
            return False
        del self._users[uid]
        self._save()
        return True

    def valid(self, uid: str) -> bool:
        return uid in self._users


def migrate_legacy(data_dir: Path) -> None:
    """旧数据并入管理员目录：单用户时代的 data/characters|worlds|sessions，
    以及旧版默认用户目录 data/default/*。

    仅在管理员目录尚未创建角色/世界观时执行，避免覆盖已有内容。
    """
    admin = data_dir / ADMIN_UID
    if (admin / "characters").exists():
        return
    # 旧版单用户数据
    for sub in ("characters", "worlds", "sessions"):
        src = data_dir / sub
        if src.exists() and any(src.iterdir()):
            dst = admin / sub
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
    # 旧版默认用户目录里的数据（合并，跳过已并入的）
    legacy_default = data_dir / "default"
    if legacy_default.exists():
        for sub in ("characters", "worlds", "sessions", "uploads"):
            src = legacy_default / sub
            if src.exists() and any(src.iterdir()):
                dst = admin / sub
                if dst.exists() and any(dst.iterdir()):
                    continue  # 已有内容则不覆盖
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))


@dataclass
class UserScope:
    """单个用户的数据作用域：会话存储、角色/世界观存储、上传目录。"""

    uid: str
    store: SessionStore
    lore: LoreStore
    upload_dir: Path

    @classmethod
    def build(cls, uid: str, config: AppConfig, data_dir: Path) -> "UserScope":
        user_dir = data_dir / uid
        if config.history.backend == "json":
            store = JsonSessionStore(user_dir / "sessions")
        else:
            store = MemorySessionStore()
        upload_dir = (data_dir / "uploads" / uid)
        upload_dir.mkdir(parents=True, exist_ok=True)
        return cls(uid=uid, store=store, lore=LoreStore(user_dir), upload_dir=upload_dir)