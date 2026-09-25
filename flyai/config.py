"""配置管理：pydantic 模型 + 加载/保存。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from .errors import ConfigError

# 项目根目录上级（运行目录）。配置文件默认放在项目根。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "config.example.json"


class ProviderConfig(BaseModel):
    id: str
    type: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: str = ""
    model: str
    params: Dict[str, Any] = {}


class ToolSettings(BaseModel):
    enabled: bool = True
    max_iterations: int = 8
    timeout_seconds: int = 60
    plugin_dirs: List[str] = ["plugins"]
    allowed_read_dirs: List[str] = ["workspace"]
    enabled_plugins: Dict[str, bool] = {"file_reader": True}


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class AdminSettings(BaseModel):
    """管理员账号（凭据放在 config/config.json，不入库）。"""
    username: str = "admin"
    password: str = ""


class AuthSettings(BaseModel):
    admin: AdminSettings = AdminSettings()


class HistorySettings(BaseModel):
    backend: Literal["memory", "json"] = "memory"


class AttributeDef(BaseModel):
    """一条可选属性定义（如好感度、金钱）。"""
    key: str
    label: str = ""
    initial: float = 0
    min: Optional[float] = None
    max: Optional[float] = None


class AttributeSettings(BaseModel):
    enabled: bool = True
    # True：AI 每回合自行调用工具提交属性增减；False：仅展示，不自动生成说明提示。
    ai_provided: bool = True
    list: List[AttributeDef] = [
        AttributeDef(key="affection", label="好感度", initial=0, min=-100, max=100),
        AttributeDef(key="money", label="金钱", initial=100, min=0, max=999999),
    ]


class RoleplaySettings(BaseModel):
    """角色扮演对话格式 + 属性跟踪。"""
    format_enabled: bool = True
    format_instruction: str = (
        "你是一个沉浸式角色扮演助手。每轮回复请严格遵守以下格式：\n"
        "1. 动作或场景描述用中文括号（ ）括起，例如（她轻轻推开门走进来）。\n"
        "2. 角色内心活动用成对的 ____ 标记，例如 ____他看起来有些紧张____。\n"
        "3. 角色对白使用「」或中文引号“ ”括起，例如「晚上好，旅人。」。\n"
        "4. 同一条消息中，叙述、动作、对白、内心活动等不同类型之间用空行隔开，保持清晰分段。\n"
        "5. 保持简洁自然的语气，直接进入剧情，不要解释你的输出格式。"
    )
    # 内置的工具使用要求默认值（角色卡/世界观可覆盖）
    tool_instruction: str = (
        "需要获取资料、查询设定或改变世界状态时，请主动调用对应工具，"
        "依据工具返回的结果作答，不要臆造工具提供不了的内容。"
    )
    # 请求时注入到消息尾部、紧邻当前回合前的「格式示范」few-shot。
    # 留空则不注入。为一串符合 format_instruction 的示例对白，靠近当前回合更醒目，
    # 且插在尾部不破坏 DeepSeek 等提供商的前缀缓存（前缀 system+历史保持不变）。
    format_example: str = (
        "（她推开旧书店的木门，檐下的风铃轻轻响了一声。）\n\n"
        "____他……真的不会再回来了吗？____\n\n"
        "「欢迎光临。想喝点什么？」"
    )
    attributes: AttributeSettings = AttributeSettings()


class AppConfig(BaseModel):
    server: ServerSettings = ServerSettings()
    active_provider: str = ""
    providers: List[ProviderConfig] = []
    tools: ToolSettings = ToolSettings()
    history: HistorySettings = HistorySettings()
    roleplay: RoleplaySettings = RoleplaySettings()
    auth: AuthSettings = AuthSettings()


def load_config(path: Optional[Path] = None) -> AppConfig:
    """加载配置。文件不存在时尝试从 example 复制，再不存在则返回默认。"""
    cfg_path = path or _env_config_path()
    if not cfg_path.exists():
        _bootstrap_config(cfg_path)
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig.parse_obj(data)
    except Exception as exc:  # JSON 或校验失败给出友好错误
        raise ConfigError(f"配置加载失败: {cfg_path}: {exc}") from exc


def save_config(cfg: AppConfig, path: Optional[Path] = None) -> Path:
    cfg_path = path or _env_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg.dict(), f, ensure_ascii=False, indent=2)
    # 原子替换，避免写一半损坏
    os.replace(tmp, cfg_path)
    return cfg_path


def get_active_provider(cfg: AppConfig) -> Optional[ProviderConfig]:
    if not cfg.active_provider:
        return None
    for p in cfg.providers:
        if p.id == cfg.active_provider:
            return p
    return None


def _env_config_path() -> Path:
    override = os.environ.get("FLAI_CONFIG")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _bootstrap_config(cfg_path: Path) -> None:
    """首次启动：把 example 复制为真实配置；无 example 则写默认。"""
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if EXAMPLE_CONFIG_PATH.exists():
        shutil.copyfile(EXAMPLE_CONFIG_PATH, cfg_path)
    else:
        save_config(AppConfig(), cfg_path)


def mask_api_key(key: str) -> str:
    """掩码 api_key，仅展示首尾。"""
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return key[:4] + "****" + key[-4:]