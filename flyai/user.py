"""多用户：账户存储 + 每个用户的数据作用域。

数据按用户隔离：「所有角色卡、世界观、会话、上传头像」都落在各自用户的目录下；
提供商与全局配置仍共享（本地工具）。密码用 sha256 加盐存储。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import AppConfig, PROJECT_ROOT
from .lore.store import LoreStore
from .sessions import JsonSessionStore, MemorySessionStore, SessionStore


# 管理员标识：管理员不在 user 库中，凭据存于 config/auth（config.auth.admin）
ADMIN_UID = "admin"
# 管理员权限等级：普通用户 0-255，管理员固定 256
ADMIN_LEVEL = 256


@dataclass
class User:
    """普通用户（由管理员创建，一律非管理员）。"""

    id: str = ""
    name: str = ""
    # 注册邮箱（小写；空串=老用户或管理员手建未填），用于找回密码与唯一性
    email: str = ""
    password_hash: str = ""
    created_at: str = ""
    # 最近一次登录（含注册后自动登录）时间；空串=从未登录过
    last_login_at: str = ""
    # 权限等级 0-255，决定可使用的共享模型；越大越高
    level: int = 0
    # 计费：对话次数总额（-1=无限）；累计已扣。每轮扣 1×当前模型倍率
    quota_total: float = 0
    quota_used: float = 0

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
            "email": (self.email or "").strip().lower(),
            "password_hash": self.password_hash,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "level": int(self.level or 0),
            "quota_total": float(self.quota_total or 0),
            "quota_used": float(self.quota_used or 0),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "User":
        def _num(key: str) -> float:
            try:
                return float(d.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            email=str(d.get("email") or "").strip().lower(),
            password_hash=d.get("password_hash", ""),
            created_at=d.get("created_at", ""),
            last_login_at=d.get("last_login_at", "") or "",
            level=int(d.get("level", 0) or 0),
            quota_total=_num("quota_total"),
            quota_used=max(0.0, _num("quota_used")),
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

    def get_by_email(self, email: str) -> Optional[User]:
        """按邮箱查找（大小写不敏感）；空串不匹配任何人。"""
        target = (email or "").strip().lower()
        if not target:
            return None
        for u in self._users.values():
            if (u.email or "").strip().lower() == target:
                return u
        return None

    def create(
        self, name: str, password: str, level: int = 0, email: str = ""
    ) -> User:
        if self.get_by_name(name):
            raise ValueError(f"用户名已存在: {name}")
        email = (email or "").strip().lower()
        if email and self.get_by_email(email):
            raise ValueError("该邮箱已被使用")
        u = User(name=name, email=email, level=int(level or 0))
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

    def touch_login(self, uid: str) -> None:
        """记录一次成功登录（注册后自动登录同样计入）。"""
        u = self._users.get(uid)
        if u is None:
            return
        u.last_login_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save()

    # ---- 计费额度 ----

    def set_quota_total(self, uid: str, total: float) -> float:
        """管理员调整对话次数总额（-1=无限）。返回归一后的总额。"""
        u = self._users.get(uid)
        if u is None:
            raise ValueError("用户不存在")
        total = float(total)
        if total != -1 and total < 0:
            raise ValueError("额度不能为负数（-1 表示无限）")
        u.quota_total = total
        # 缩额后已扣可能超过新总额：不回改 used，剩余按 0 处理
        self._save()
        return total

    def consume(self, uid: str, cost: float) -> bool:
        """检查并扣减一次对话费用；无限额度直接通过，余额不足返回 False（不扣）。"""
        u = self._users.get(uid)
        if u is None:
            return False
        cost = max(0.0, float(cost))
        if u.quota_total == -1:
            u.quota_used += cost
            self._save()
            return True
        remaining = u.quota_total - u.quota_used
        if remaining + 1e-9 < cost:
            return False
        u.quota_used += cost
        self._save()
        return True

    def refund(self, uid: str, cost: float) -> None:
        """退回未实际发生的费用。"""
        u = self._users.get(uid)
        if u is None:
            return
        u.quota_used = max(0.0, u.quota_used - max(0.0, float(cost)))
        self._save()

    def add_quota(self, uid: str, amount: float) -> float:
        """增加额度（CDK 兑换）：无限额度保持 -1；返回归一后的总额。"""
        u = self._users.get(uid)
        if u is None:
            raise ValueError("用户不存在")
        amount = max(0.0, float(amount))
        if u.quota_total != -1:
            u.quota_total += amount
            self._save()
        return u.quota_total

    def add_used(self, uid: str, cost: float) -> None:
        """按 token 实际用量的结算：直接累加 used（调用方已做余额预检）。"""
        u = self._users.get(uid)
        if u is None:
            return
        cost = max(0.0, float(cost))
        if cost <= 0:
            return
        u.quota_used += cost
        self._save()


# ---- 应用背景（每个设备类型各一套）----

BG_MODES = ("theme", "solid", "image")
_BG_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# 只接受本站 upload 目录的路径形态；「是否属于当前用户」由 API 层校验
_BG_IMAGE_RE = re.compile(
    r"^/uploads/[A-Za-z0-9_-]{1,64}/[A-Za-z0-9_-]{1,96}\.(?:png|jpe?g|gif|webp)$",
    re.IGNORECASE,
)


def _normalize_opacity(value: Any) -> int:
    """背景图层不透明度 0-100；非法值按 100（完全不透明）。"""
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 100
    return max(0, min(100, number))


def _normalize_background(
    mode: Any, color: Any, image: Any, opacity: Any = 100
) -> Tuple[str, str, str, int]:
    """归一化一组背景配置：非法值回退默认，solid/image 缺少素材时回落 theme。"""
    mode = mode.strip().lower() if isinstance(mode, str) else ""
    if mode not in BG_MODES:
        mode = "theme"
    color = color if isinstance(color, str) else ""
    color = color.strip().lower()
    if color and not _BG_COLOR_RE.match(color):
        color = ""
    image = image if isinstance(image, str) else ""
    image = image.strip()
    if image and not _BG_IMAGE_RE.match(image):
        image = ""
    if mode == "solid" and not color:
        mode = "theme"
    if mode == "image" and not image:
        mode = "theme"
    return mode, color, image, _normalize_opacity(opacity)


@dataclass
class UserSettings:
    """单个用户的个人设置（data/{uid}/settings.json，跨设备跟随用户）。"""

    # 发给模型的最近对话轮数；0 = 全部发送。历史仍完整保存在会话文件中
    keep_turns: int = 0
    # 发消息前自动从角色记忆簿/历史中召回相关事实（keep_turns 的配套）。
    # 新用户默认开启；已落盘 settings.json（含显式 false）的老用户保持原值
    auto_recall: bool = True
    # 自动召回注入的事实条数上限
    auto_recall_max: int = 5
    # 每 N 轮做一次结构化批量抽取；0 = 关闭
    extract_every_turns: int = 10
    # 内容表触发词自动注入：用户消息模糊命中世界书/角色卡条目的 keys/标题时，
    # 系统直接把条目注入系统提示词（不依赖 AI 调 query_lore）
    lore_auto_inject: bool = True
    # 单次回复最大 token（None/0=跟随提供商配置）
    max_tokens: Optional[int] = None
    # 当前启用的模型预设 id（data/{uid}/presets/{id}.json）；空=不使用预设
    active_preset_id: str = ""

    # ---- 应用背景：电脑与手机各一套（theme=跟随主题 / solid=纯色 / image=图片）----
    bg_desktop_mode: str = "theme"
    bg_desktop_color: str = ""
    bg_desktop_image: str = ""
    # 背景图层不透明度 0-100（100=完全不透明）
    bg_desktop_opacity: int = 100
    bg_mobile_mode: str = "theme"
    bg_mobile_color: str = ""
    bg_mobile_image: str = ""
    bg_mobile_opacity: int = 100

    # ---- 功能开关覆盖（None=跟随管理员全局默认；见 flyai/feature_prefs.py）----
    # 工具与迭代
    tools_enabled: Optional[bool] = None
    tool_max_iterations: Optional[int] = None
    tool_timeout_seconds: Optional[int] = None
    # 角色扮演（格式/属性开关 + 五个字符预算）
    roleplay_format_enabled: Optional[bool] = None
    roleplay_attributes_enabled: Optional[bool] = None
    intro_chars: Optional[int] = None
    background_chars: Optional[int] = None
    world_chars: Optional[int] = None
    mes_example_chars: Optional[int] = None
    pinned_chars: Optional[int] = None
    # 各功能总开关
    web_enabled: Optional[bool] = None
    browser_enabled: Optional[bool] = None
    pixiv_enabled: Optional[bool] = None
    image_enabled: Optional[bool] = None
    galgame_enabled: Optional[bool] = None
    # 第一人称自我记忆（P3）：个人开关，默认关闭；管理员全局开启后才可启用
    self_memory_enabled: bool = False
    # Galgame 单轮工具循环上限
    galgame_max_loops: Optional[int] = None
    # 酒馆卡扩展（None=跟随默认；默认值见 st 模块与 AppConfig）
    st_regex_prompt_enabled: Optional[bool] = None
    st_regex_display_enabled: Optional[bool] = None
    st_html_enabled: Optional[bool] = None
    st_external_images_enabled: Optional[bool] = None
    # 外链插图经服务端代理（需管理员开启 st_image 且权限等级达标）
    st_image_proxy_enabled: Optional[bool] = None
    # 卡内整页 HTML+JS 应用放进沙箱 iframe（默认仅管理员开启）
    st_js_sandbox_enabled: Optional[bool] = None
    # 卡内授权动作（发送/重生成/继续）：逐次弹窗确认，仅单角色 DM
    st_card_actions_enabled: Optional[bool] = None
    # 卡内写回会话副本（变量/世界书/角色）：仅写会话副本，永不写根卡
    st_card_write_enabled: Optional[bool] = None
    # 卡内网络请求经服务端受控代理（需管理员开启 st_net 且等级达标）
    st_net_proxy_enabled: Optional[bool] = None
    mvu_enabled: Optional[bool] = None

    # 可空覆盖字段名 → 类型（b=布尔/i=整数）；to/from_dict 与 API 共用
    OVERRIDE_FIELDS = {
        "tools_enabled": "b",
        "tool_max_iterations": "i",
        "tool_timeout_seconds": "i",
        "roleplay_format_enabled": "b",
        "roleplay_attributes_enabled": "b",
        "intro_chars": "i",
        "background_chars": "i",
        "world_chars": "i",
        "mes_example_chars": "i",
        "pinned_chars": "i",
        "web_enabled": "b",
        "browser_enabled": "b",
        "pixiv_enabled": "b",
        "image_enabled": "b",
        "galgame_enabled": "b",
        "self_memory_enabled": "b",
        "galgame_max_loops": "i",
        "st_regex_prompt_enabled": "b",
        "st_regex_display_enabled": "b",
        "st_html_enabled": "b",
        "st_external_images_enabled": "b",
        "st_image_proxy_enabled": "b",
        "st_js_sandbox_enabled": "b",
        "st_card_actions_enabled": "b",
        "st_card_write_enabled": "b",
        "st_net_proxy_enabled": "b",
        "mvu_enabled": "b",
    }

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "keep_turns": int(self.keep_turns or 0),
            "auto_recall": bool(self.auto_recall),
            "auto_recall_max": int(self.auto_recall_max or 5),
            "extract_every_turns": int(self.extract_every_turns or 0),
            "lore_auto_inject": bool(self.lore_auto_inject),
            "max_tokens": self.max_tokens if self.max_tokens else None,
            "active_preset_id": self.active_preset_id or "",
            "bg_desktop_mode": self.bg_desktop_mode,
            "bg_desktop_color": self.bg_desktop_color,
            "bg_desktop_image": self.bg_desktop_image,
            "bg_desktop_opacity": int(self.bg_desktop_opacity),
            "bg_mobile_mode": self.bg_mobile_mode,
            "bg_mobile_color": self.bg_mobile_color,
            "bg_mobile_image": self.bg_mobile_image,
            "bg_mobile_opacity": int(self.bg_mobile_opacity),
        }
        for key in self.OVERRIDE_FIELDS:
            out[key] = getattr(self, key)  # None 原样落盘 = 跟随默认
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "UserSettings":
        def _int(key: str, default: int) -> int:
            try:
                return int(d.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        keep = max(0, _int("keep_turns", 0))
        recall_max = max(1, min(20, _int("auto_recall_max", 5)))
        every = max(0, _int("extract_every_turns", 10))
        kwargs = dict(
            keep_turns=keep,
            auto_recall=bool(d.get("auto_recall", True)),
            auto_recall_max=recall_max,
            extract_every_turns=every,
            lore_auto_inject=bool(d.get("lore_auto_inject", True)),
        )
        try:
            mt = d.get("max_tokens", None)
            kwargs["max_tokens"] = int(mt) if mt else None
        except (TypeError, ValueError):
            kwargs["max_tokens"] = None
        pid = d.get("active_preset_id", "")
        kwargs["active_preset_id"] = str(pid).strip() if pid else ""
        # 背景：逐设备归一化（只做形状校验，图片归属由 API 层校验）
        for device in ("desktop", "mobile"):
            mode, color, image, opacity = _normalize_background(
                d.get("bg_%s_mode" % device),
                d.get("bg_%s_color" % device),
                d.get("bg_%s_image" % device),
                d.get("bg_%s_opacity" % device),
            )
            kwargs["bg_%s_mode" % device] = mode
            kwargs["bg_%s_color" % device] = color
            kwargs["bg_%s_image" % device] = image
            kwargs["bg_%s_opacity" % device] = opacity
        # 可空覆盖：仅接受 bool/int；None 与缺键都表示跟随默认（0/False 是合法覆盖值）
        for key, kind in cls.OVERRIDE_FIELDS.items():
            if key not in d or d[key] is None:
                continue
            try:
                kwargs[key] = bool(d[key]) if kind == "b" else int(d[key])
            except (TypeError, ValueError):
                continue
        return cls(**kwargs)


def load_user_settings(path: Path) -> UserSettings:
    """读用户设置；文件不存在或内容损坏时回退默认实例。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return UserSettings.from_dict(data)
    except Exception:
        pass
    return UserSettings()


def save_user_settings(path: Path, settings: UserSettings) -> None:
    """原子写入用户设置（.tmp + os.replace）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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
    """单个用户的数据作用域：权限等级、会话存储、角色/世界观存储、上传目录。"""

    uid: str
    store: SessionStore
    lore: LoreStore
    upload_dir: Path
    char_memory: Any = None      # CharacterMemoryStore：角色级跨会话记忆簿
    presets: Any = None          # PresetStore：该用户的模型预设库
    level: int = 0
    user_settings: Any = None    # UserSettings：该用户的个人设置（keep_turns 等）
    settings_path: Any = None    # Path：data/{uid}/settings.json
    # LoreQuerier 复用缓存：key=角色/世界内容签名，容量 32 FIFO
    _querier_cache: Dict[str, Any] = field(default_factory=dict, compare=False)

    _QUERIER_CACHE_MAX = 32

    def get_querier(self, character, world):
        """按角色/世界内容签名复用 LoreQuerier；内容变化即不命中，无需显式失效。"""
        from .lore.diff import content_signature
        from .lore.query import LoreQuerier

        csig = content_signature(character.to_dict()) if character is not None else ""
        wsig = content_signature(world.to_dict()) if world is not None else ""
        key = csig + ":" + wsig
        q = self._querier_cache.get(key)
        if q is None:
            q = LoreQuerier(character, world)
            self._querier_cache[key] = q
            if len(self._querier_cache) > self._QUERIER_CACHE_MAX:
                self._querier_cache.pop(next(iter(self._querier_cache)))
        return q

    def save_user_settings(self) -> None:
        """把当前 user_settings 原子写回 settings.json。"""
        save_user_settings(self.settings_path, self.user_settings)

    @classmethod
    def build(cls, uid: str, config: AppConfig, data_dir: Path,
              user_store: Optional["UserStore"] = None) -> "UserScope":
        user_dir = data_dir / uid
        if config.history.backend == "json":
            store = JsonSessionStore(user_dir / "sessions")
        else:
            store = MemorySessionStore()
        upload_dir = (data_dir / "uploads" / uid)
        upload_dir.mkdir(parents=True, exist_ok=True)
        lore = LoreStore(user_dir, shared_base=data_dir / "shared")
        from .lore.character_memory import CharacterMemoryStore
        from .preset.store import PresetStore

        char_memory = CharacterMemoryStore(user_dir)
        presets = PresetStore(user_dir / "presets")
        settings_path = user_dir / "settings.json"
        user_settings = load_user_settings(settings_path)
        if uid == ADMIN_UID:
            level = ADMIN_LEVEL
        elif user_store is not None:
            u = user_store.get_by_id(uid)
            level = int(u.level or 0) if u else 0
        else:
            level = 0
        return cls(uid=uid, store=store, lore=lore, upload_dir=upload_dir,
                   char_memory=char_memory, presets=presets, level=level,
                   user_settings=user_settings, settings_path=settings_path)