"""配置管理：pydantic 模型 + 加载/保存。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, validator

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
    # 该模型支持图片输入（视觉/多模态）：开启后前端显示图片上传入口
    vision: bool = False
    # 归属与共享：owner 为归属用户 uid；shared 开启后 min_level 等级以上用户可见可用
    owner: str = "admin"
    shared: bool = False
    min_level: int = 0

    @validator("min_level")
    def _clamp_min_level(cls, v):  # noqa: N805
        return max(0, min(int(v), 255))


class ImageModelConfig(BaseModel):
    """生图模型（火山方舟 Ark / 任意 OpenAI 兼容图片接口）。

    与 ProviderConfig 同样支持归属与权限共享。
    """

    id: str
    name: str = ""
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    api_key: str = ""
    model: str = "doubao-seedream-5-0-260128"
    owner: str = "admin"
    shared: bool = False
    min_level: int = 0

    @validator("min_level")
    def _clamp_min_level(cls, v):  # noqa: N805
        return max(0, min(int(v), 255))


class ToolSettings(BaseModel):
    enabled: bool = True
    max_iterations: int = 8
    timeout_seconds: int = 120
    plugin_dirs: List[str] = ["plugins"]
    allowed_read_dirs: List[str] = ["workspace"]
    enabled_plugins: Dict[str, bool] = {"file_reader": True}


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    # 根目录：未匹配任何路由时从此目录取文件；相对路径基于项目根。目录不存在则不挂载
    web_root: str = "webroot"


class AdminSettings(BaseModel):
    """管理员账号（凭据放在 config/config.json，不入库）。"""
    username: str = "admin"
    password: str = ""


class AuthSettings(BaseModel):
    admin: AdminSettings = AdminSettings()


class HistorySettings(BaseModel):
    backend: Literal["memory", "json"] = "json"


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
    # 默认什么属性都没有；需要属性时在角色卡中自定义
    list: List[AttributeDef] = []


class RoleplaySettings(BaseModel):
    """角色扮演对话格式 + 属性跟踪。"""
    format_enabled: bool = True
    format_instruction: str = (
        "你是一个沉浸式角色扮演助手。每轮回复请严格遵守以下格式：\n"
        "1. 动作或场景描述用中文括号（ ）括起，例如（她轻轻推开门走进来）。\n"
        "2. 角色内心活动用一对单个下划线 _ 包裹，例如 _他看起来有些紧张_；"
        "下划线外侧不要紧贴英文字母或数字（避免和变量名混淆），标记不跨行。\n"
        "3. 角色对白使用「」或中文引号“ ”括起，例如「晚上好，旅人。」。\n"
        "4. 同一条消息中，叙述、动作、对白、内心活动等不同类型之间用空行隔开，保持清晰分段。\n"
        "5. 保持简洁自然的语气，直接进入剧情，不要解释你的输出格式。"
    )
    # 内置的工具使用要求默认值（角色卡/世界观可覆盖）
    tool_instruction: str = (
        "需要获取资料、查询设定或改变世界状态时，请主动调用对应工具，"
        "依据工具返回的结果作答，不要臆造工具提供不了的内容。"
    )
    # 已废弃：曾作为 few-shot 对白插到消息尾部，但伪装成真实 assistant 历史会让模型
    # 误以为示例剧情（旧书店）真实发生过。格式要求统一由 format_instruction 经系统提示词注入。
    # 字段保留仅为兼容旧配置文件，内容会被忽略。
    format_example: str = ""
    attributes: AttributeSettings = AttributeSettings()


class EmbeddingsConfig(BaseModel):
    """可选的远程 Embeddings 提供商（OpenAI 兼容）配置，用于 RAG 语义检索。

    配置了 base_url/api_key/model 即视为启用；未配置则 query_chat_memory 回退到关键词检索。
    """
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    top_k: int = 4          # 语义检索返回的命中消息数
    cache_file: str = ""    # 向量持久化缓存路径；留空则仅进程内缓存

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip() and self.api_key.strip())


class ImageGenSettings(BaseModel):
    """AI 生图全局开关与默认尺寸。具体模型在 image_models 列表中，可自建/共享。"""

    enabled: bool = True
    default_size: str = "2K"  # 1K / 2K / 4K


class PixivSettings(BaseModel):
    """P 站插画搜索（lolicon 免登录聚合 API）。

    AI 先 search_pixiv 取候选缩略图交视觉模型筛选，再 pick_pixiv 下载精选原图。
    访问 Pixiv 相关域名需可用的出站代理（本机 Clash / 服务器 Xray）。
    """

    enabled: bool = True
    api_base: str = "https://api.lolicon.app/setu/v2"
    # 出站代理，如 http://127.0.0.1:7897；留空则直连（读环境变量）
    proxy: str = ""
    # R-18 总开关：关闭只返回全龄向（r18=0）；开启为混合（r18=2）
    r18_allowed: bool = False
    # 每次搜索返回的候选数量
    candidate_num: int = 15


class WebSearchSettings(BaseModel):
    """联网搜索：Bing 检索 → 抓取全部结果页正文 → 子 AI 线程总结为 markdown。

    国内默认直连 cn.bing.com；配置 proxy（海外服务器）时改走 bing.com。
    """

    enabled: bool = True
    proxy: str = ""           # 留空直连；如 http://127.0.0.1:7897
    results_num: int = 8      # 搜索结果条数（全部抓正文）
    max_page_chars: int = 6000   # 单页正文截取上限，控 token


# Anything V5（danbooru 系动漫模型）通用负向标签：覆盖常见崩坏
DEFAULT_SD_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, "
    "extra digit, fewer digits, cropped, worst quality, low quality, normal quality, "
    "jpeg artifacts, signature, watermark, username, blurry, bad feet"
)


class StableDiffusionSettings(BaseModel):
    """本地 Stable Diffusion WebUI（Forge/重绘系，经 Gradio API 调用）。

    开启后对话输入区出现「SD」引擎开关；选中后 generate_image 不走云端 Ark，
    而由内置 AI 把画面描述转写为英文 danbooru 标签提示词并确定全部采样参数，
    再经 Gradio /api/predict 调用本机 WebUI——无需以 --api 启动。
    fn_index/槽位以本机这套 Forge WebUI（Gradio 3.41）为准，版本不同可在此调整。
    """

    enabled: bool = False
    base_url: str = "http://127.0.0.1:7860"
    # Gradio 文生图函数编号
    fn_index: int = 99
    # 参数兜底默认（内置 AI 输出缺字段/越界时使用）
    width: int = 512
    height: int = 768
    steps: int = 28
    cfg_scale: float = 7.0
    # 该 WebUI 的采样器与调度器是两个下拉
    sampler: str = "DPM++ 2M"
    schedule: str = "Karras"
    negative_prompt: str = DEFAULT_SD_NEGATIVE

    @validator("width", "height")
    def _round8_size(cls, v):  # noqa: N805
        v = max(256, min(int(v), 1024))
        return (v // 8) * 8

    @validator("steps")
    def _clamp_steps(cls, v):  # noqa: N805
        return max(1, min(int(v), 150))

    @validator("cfg_scale")
    def _clamp_cfg(cls, v):  # noqa: N805
        return max(1.0, min(float(v), 30.0))


class AppConfig(BaseModel):
    server: ServerSettings = ServerSettings()
    active_provider: str = ""
    providers: List[ProviderConfig] = []
    tools: ToolSettings = ToolSettings()
    history: HistorySettings = HistorySettings()
    roleplay: RoleplaySettings = RoleplaySettings()
    auth: AuthSettings = AuthSettings()
    embeddings: EmbeddingsConfig = EmbeddingsConfig()
    image_generation: ImageGenSettings = ImageGenSettings()
    image_models: List[ImageModelConfig] = []
    stable_diffusion: StableDiffusionSettings = StableDiffusionSettings()
    pixiv: PixivSettings = PixivSettings()
    web_search: WebSearchSettings = WebSearchSettings()


def load_config(path: Optional[Path] = None) -> AppConfig:
    """加载配置。文件不存在时尝试从 example 复制，再不存在则返回默认。"""
    cfg_path = path or _env_config_path()
    if not cfg_path.exists():
        _bootstrap_config(cfg_path)
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _migrate_config_data(data)
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


def _migrate_config_data(data: dict) -> None:
    """旧配置就地升级到新结构（parse 前）：

    - 旧 providers 无 owner 字段 → admin 归属、默认共享（min_level 0），
      保持「所有用户可用」的现状；
    - 旧 image_generation 含 base_url/model/api_key，且 image_models 为空
      → 转成一条共享生图模型。
    """
    providers = data.get("providers") or []
    for p in providers:
        if not isinstance(p, dict) or "owner" in p:
            continue
        p["owner"] = "admin"
        p["shared"] = True
        p["min_level"] = 0

    ig = data.get("image_generation") or {}
    image_models = data.get("image_models") or []
    if not image_models and ("base_url" in ig or "model" in ig or "api_key" in ig):
        data["image_models"] = [{
            "id": "ark-default",
            "name": "火山方舟默认",
            "base_url": ig.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": ig.get("api_key") or "",
            "model": ig.get("model") or "doubao-seedream-5-0-260128",
            "owner": "admin",
            "shared": True,
            "min_level": 0,
        }]
    # 生图全局段不再需要旧字段（多余键 pydantic 默认忽略，但顺手清掉）
    for key in ("base_url", "model", "api_key"):
        ig.pop(key, None)


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