"""配置管理：pydantic 模型 + 加载/保存。"""

from __future__ import annotations

import json
import math
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


class PricePeriod(BaseModel):
    """分时段价格覆盖：按列表顺序匹配，先命中先生效。

    价格单位均为「点数 / 每百万 token」（1 点 = 1 元）。
    时段条件可组合：daily_start/daily_end 为每日重复的挂钟时间窗（支持跨午夜），
    date_start/date_end 为显式日期区间（含端点）；留空表示该维度不限。
    """

    name: str = ""
    cache_hit_input: float = 0.0
    input: float = 0.0
    output: float = 0.0
    daily_start: str = ""
    daily_end: str = ""
    date_start: str = ""
    date_end: str = ""

    @validator("cache_hit_input", "input", "output")
    def _clamp_price(cls, v):  # noqa: N805
        return max(0.0, float(v))

    @validator("daily_start", "daily_end")
    def _valid_hhmm(cls, v):  # noqa: N805
        text = (v or "").strip()
        if not text:
            return ""
        from .proactor.clock import parse_hhmm

        if parse_hhmm(text) is None:
            raise ValueError(f"时间段格式应为 HH:MM：{text}")
        return text

    @validator("date_start", "date_end")
    def _valid_date(cls, v):  # noqa: N805
        text = (v or "").strip()
        if not text:
            return ""
        from datetime import date

        try:
            date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"日期格式应为 YYYY-MM-DD：{text}")
        return text

    def matches(self, local: "datetime") -> bool:  # noqa: F821
        """local 为指定时区的本地挂钟 naive datetime。"""
        from .proactor.clock import parse_hhmm

        if self.date_start and local.date().isoformat() < self.date_start:
            return False
        if self.date_end and local.date().isoformat() > self.date_end:
            return False
        if self.daily_start and self.daily_end:
            start = parse_hhmm(self.daily_start)
            end = parse_hhmm(self.daily_end)
            if start is not None and end is not None:
                cur = local.hour * 60 + local.minute
                s_min, e_min = start[0] * 60 + start[1], end[0] * 60 + end[1]
                if s_min == e_min:
                    return False
                if s_min < e_min:
                    if not (s_min <= cur < e_min):
                        return False
                # 跨午夜：起点之后或终点之前
                elif not (cur >= s_min or cur < e_min):
                    return False
        return True


class TokenPricing(BaseModel):
    """按 token 计费：顶层为基准价，periods 按顺序覆盖。"""

    cache_hit_input: float = 0.0
    input: float = 0.0
    output: float = 0.0
    periods: List[PricePeriod] = []

    @validator("cache_hit_input", "input", "output")
    def _clamp_price(cls, v):  # noqa: N805
        return max(0.0, float(v))

    def prices_at(self, local: "datetime") -> Dict[str, float]:  # noqa: F821
        for p in self.periods or []:
            if p.matches(local):
                return {
                    "cache_hit_input": p.cache_hit_input,
                    "input": p.input,
                    "output": p.output,
                    "period": p.name,
                }
        return {
            "cache_hit_input": self.cache_hit_input,
            "input": self.input,
            "output": self.output,
            "period": "",
        }


class ProviderConfig(BaseModel):
    id: str
    type: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: str = ""
    model: str
    params: Dict[str, Any] = {}
    # 「深度思考」开关开启时才合并进请求体的参数（各厂商协议不同，
    # 如 {"reasoning_effort":"high"} / {"enable_thinking":true} /
    # {"thinking":{"type":"enabled"}}）；空则不额外下发
    thinking_params: Dict[str, Any] = {}
    # 开关关闭时合并进请求体的参数：部分模型（如火山/DeepSeek 新版）默认思考，
    # 需显式 {"thinking":{"type":"disabled"}} 才关得掉；空则不额外下发
    thinking_off_params: Dict[str, Any] = {}
    # 该模型支持图片输入（视觉/多模态）：开启后前端显示图片上传入口
    vision: bool = False
    # 归属与共享：owner 为归属用户 uid；shared 开启后 min_level 等级以上用户可见可用
    owner: str = "admin"
    shared: bool = False
    min_level: int = 0
    # 计费倍率：普通用户每发起一轮扣 1×rate；允许小数，默认 1.0（管理员不计费）
    # 配置了 pricing 时改按实际 token 计费，rate 不再生效
    rate: float = 1.0
    # 按 token 计费（每百万 token 的点数）；None=沿用每轮 rate 计费
    pricing: Optional[TokenPricing] = None

    @validator("min_level")
    def _clamp_min_level(cls, v):  # noqa: N805
        return max(0, min(int(v), 255))

    @validator("rate")
    def _clamp_rate(cls, v):  # noqa: N805
        return max(0.0, float(v))


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


class RegistrationSettings(BaseModel):
    """邀请码注册设置（仅 admin 可见/可改）。"""
    # 新注册用户的初始额度（点数）；-1 表示不限，0 表示需管理员充值
    default_quota: float = 0
    # 注册是否必须提供邀请码；关闭后任何人都可直接注册
    require_invite: bool = True

    @validator("default_quota", pre=True)
    def _check_default_quota(cls, v):  # noqa: N805
        try:
            number = float(v)
        except (TypeError, ValueError):
            return 0
        if math.isnan(number) or math.isinf(number):
            return 0
        if number < 0 and number != -1:
            return 0
        return number


class AnnouncementSettings(BaseModel):
    """启动公告（每次打开应用弹出；仅 admin 可改，公开接口下发展示内容）。"""
    enabled: bool = False
    title: str = "公告"
    content: str = ""

    @validator("title", pre=True)
    def _trim_title(cls, v):  # noqa: N805
        return str(v or "").strip()[:120]

    @validator("content", pre=True)
    def _trim_content(cls, v):  # noqa: N805
        return str(v or "").strip()[:20000]


class EmailSettings(BaseModel):
    """SMTP 邮件服务（注册邮箱验证与找回密码；仅 admin 可见/可改）。"""
    # 关闭时注册行为保持原样（只校验邀请码），不要求邮箱
    enabled: bool = False
    host: str = "smtp.qq.com"
    port: int = 465
    # ssl（465，推荐）/ starttls（587）/ none（明文，不推荐）
    encryption: Literal["ssl", "starttls", "none"] = "ssl"
    # QQ 邮箱账号；同时作为默认发件地址
    username: str = ""
    # SMTP 授权码（不是 QQ 登录密码）
    password: str = ""
    from_name: str = "FlyAI"
    # 留空则使用 username
    from_address: str = ""
    # 验证码有效期 / 重发冷却 / 单邮箱每小时发码上限
    code_ttl_seconds: int = 600
    resend_interval_seconds: int = 60
    hourly_limit: int = 10

    @validator("code_ttl_seconds", pre=True)
    def _clamp_ttl(cls, v):  # noqa: N805
        return _clamp_int(v, 60, 3600, 600)

    @validator("resend_interval_seconds", pre=True)
    def _clamp_interval(cls, v):  # noqa: N805
        return _clamp_int(v, 10, 600, 60)

    @validator("hourly_limit", pre=True)
    def _clamp_hourly(cls, v):  # noqa: N805
        return _clamp_int(v, 1, 100, 10)

    @validator("port", pre=True)
    def _clamp_port(cls, v):  # noqa: N805
        return _clamp_int(v, 1, 65535, 465)


def _clamp_int(v: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(v)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


class AfdianPlanSettings(BaseModel):
    """爱发电方案映射：基数（0 = 按订单实付金额）× 倍率 = 发放点数。"""
    plan_id: str = ""
    name: str = ""
    amount: float = 0
    ratio: float = 1

    @validator("plan_id", "name", pre=True)
    def _trim_plan_text(cls, v):  # noqa: N805
        return str(v or "").strip()[:120]

    @validator("amount", pre=True)
    def _check_plan_amount(cls, v):  # noqa: N805
        try:
            number = float(v)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(number) or math.isinf(number) or number < 0:
            return 0.0
        return number

    @validator("ratio", pre=True)
    def _check_plan_ratio(cls, v):  # noqa: N805
        try:
            number = float(v)
        except (TypeError, ValueError):
            return 1.0
        if math.isnan(number) or math.isinf(number) or number < 0:
            return 1.0
        return number


class AfdianSettings(BaseModel):
    """爱发电自动发卡（webhook 触发 + query-order 权威校验）。"""
    enabled: bool = False
    # 爱发电开发者页的 user_id 与 API Token（秘密）
    user_id: str = ""
    token: str = ""
    api_base: str = "https://afdian.com"
    # webhook 地址里的校验令牌（秘密）；留空保存时由后台自动生成
    webhook_token: str = ""
    # 展示 webhook 地址用的公网前缀；空则前端用 location.origin
    public_base_url: str = ""
    # 累计销售额上限（元）；-1 不限。超限订单记待补发
    max_total_amount: float = -1
    # 未匹配到方案时的兜底：基数（0 = 按实付金额）× 倍率
    default_amount: float = 0
    default_ratio: float = 1
    plans: List[AfdianPlanSettings] = []
    # 前台「去爱发电购买」入口
    purchase_url: str = ""
    purchase_label: str = "去爱发电支持"

    @validator("user_id", "token", "public_base_url", "purchase_url", pre=True)
    def _trim_afdian_text(cls, v):  # noqa: N805
        return str(v or "").strip()[:500]

    @validator("api_base", pre=True)
    def _trim_api_base(cls, v):  # noqa: N805
        text = str(v or "").strip().rstrip("/")
        return text or "https://afdian.com"

    @validator("webhook_token", pre=True)
    def _trim_webhook_token(cls, v):  # noqa: N805
        return str(v or "").strip()[:200]

    @validator("purchase_label", pre=True)
    def _trim_purchase_label(cls, v):  # noqa: N805
        return str(v or "").strip()[:40] or "去爱发电支持"

    @validator("max_total_amount", pre=True)
    def _check_max_total(cls, v):  # noqa: N805
        try:
            number = float(v)
        except (TypeError, ValueError):
            return -1.0
        if math.isnan(number) or math.isinf(number):
            return -1.0
        return number if number >= 0 else -1.0

    @validator("default_amount", pre=True)
    def _check_default_amount(cls, v):  # noqa: N805
        try:
            number = float(v)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(number) or math.isinf(number) or number < 0:
            return 0.0
        return number

    @validator("default_ratio", pre=True)
    def _check_default_ratio(cls, v):  # noqa: N805
        try:
            number = float(v)
        except (TypeError, ValueError):
            return 1.0
        if math.isnan(number) or math.isinf(number) or number < 0:
            return 1.0
        return number


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
    # 提示词注入字符预算（0=不限，默认全部关闭；手动填值后超长部分按段落截断）
    intro_chars: int = 0          # 角色简介
    background_chars: int = 0     # 角色经历
    world_chars: int = 0          # 世界观内容
    mes_example_chars: int = 0    # 对话范例（整块丢弃后轮）
    pinned_chars: int = 0         # 常驻条目（角色/世界各自独立计）

    @validator("intro_chars", "background_chars", "world_chars", "mes_example_chars", "pinned_chars")
    def _non_negative_budget(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 0
        return v if v >= 0 else 0


class EmbeddingsConfig(BaseModel):
    """可选的远程 Embeddings 提供商（OpenAI 兼容）配置，用于 RAG 语义检索。

    配置了 base_url/api_key/model 即视为启用；未配置则 query_chat_memory 回退到关键词检索。
    """
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    top_k: int = 4              # 语义检索返回的命中消息数
    cache_file: str = ""        # 向量持久化缓存路径；留空则仅进程内缓存
    score_threshold: float = 0.55   # 语义命中余弦阈值；0.0=不筛选
    keyword_limit: int = 8          # 关键词检索返回上限

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip() and self.api_key.strip())

    @validator("score_threshold")
    def _clamp_threshold(cls, v: float) -> float:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.55
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v

    @validator("keyword_limit")
    def _clamp_keyword_limit(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 8
        if v < 1:
            return 1
        if v > 50:
            return 50
        return v


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
    # 搜索执行方式：chromium=Playwright 真实浏览器（默认，反爬更稳，
    # 但每次搜索慢 2~5 秒、占更多内存，需安装 Playwright Chromium）；
    # http=httpx 直连（快，不依赖 Playwright，个别环境易被限流）
    engine: str = "chromium"
    # 搜索引擎：bing（默认）/ baidu。百度仅在 chromium 方式下可用，
    # 遇到验证码会自动回退 Bing
    search_engine: str = "bing"
    # web_search/web_fetch 专属工具轮数，与 tools.max_iterations 不共享
    # （搜索→抓 1~3 页→分页翻读很容易超过通用的 8 轮，故联网单独放宽）
    max_iterations: int = 15

    @validator("engine")
    def _check_engine(cls, v: str) -> str:
        return v if v in ("http", "chromium") else "chromium"

    @validator("search_engine")
    def _check_search_engine(cls, v: str) -> str:
        return v if v in ("bing", "baidu") else "bing"

    @validator("max_iterations")
    def _clamp_web_max_iterations(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 15
        return v if 1 <= v <= 50 else 15


class BrowserSettings(BaseModel):
    """AI 浏览器：基于 Playwright 的有状态网页操控。

    AI 通过 browser_open / browser_snapshot / browser_click / browser_type
    像人一样逐级进入网页（打开 → 看可交互元素编号 → 点击 → 再看再点）。
    Playwright 是可选依赖：未安装时浏览器工具不注册；
    安装见 requirements-browser.txt（pip install playwright && playwright install chromium）。
    """

    enabled: bool = False
    headless: bool = True
    executable_path: str = ""       # Chromium 路径；留空自动发现
    proxy: str = ""                 # 如 http://127.0.0.1:7897；留空直连
    nav_timeout_ms: int = 20000     # 单次导航/动作超时
    idle_seconds: int = 180         # 会话空闲自动关闭（标签页回收）
    max_steps: int = 15             # 单个浏览器会话最多动作数（open/click/type/back）
    max_snapshot_chars: int = 6000  # snapshot / get_text 文本上限，控 token

    @validator("nav_timeout_ms")
    def _clamp_nav_timeout(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 20000
        return v if 5000 <= v <= 120000 else 20000

    @validator("idle_seconds")
    def _clamp_idle(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 180
        return v if 30 <= v <= 3600 else 180

    @validator("max_steps")
    def _clamp_max_steps(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 15
        return v if 1 <= v <= 50 else 15

    @validator("max_snapshot_chars")
    def _clamp_snapshot_chars(cls, v: int) -> int:
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 6000
        return v if 500 <= v <= 30000 else 6000


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


class VoiceConfig(BaseModel):
    """音色清单条目：显示名 + speaker ID（供角色卡下拉选择）。"""

    name: str = ""
    id: str = ""

    @validator("name", "id")
    def _strip(cls, v):  # noqa: N805
        return (v or "").strip()


class TtsSettings(BaseModel):
    """语音合成（豆包语音大模型 V3，WebSocket 单向流式）。

    AI 在用户要求朗读/语音时调用 text_to_speech 工具，音频存入用户上传目录
    并在消息内以独立语音条展示。需在火山控制台开通对应服务并填写 API Key。
    voices 为管理员维护的音色清单（角色卡绑定用）；min_level 为使用语音合成
    的最低权限等级。保存配置后立即生效（工具热重建）。
    """

    enabled: bool = True
    # 合成引擎：deepseek（默认，走独立 DeepSeek Key）/ doubao（火山豆包）
    engine: str = "deepseek"
    api_key: str = ""
    # 资源版本（决定模型效果与计费）
    resource_id: str = "seed-tts-2.0"
    # 音色清单：显示名 + speaker ID（下拉数据源，不强制校验绑定值）
    voices: List[VoiceConfig] = []
    # 默认音色 ID；角色卡未绑定时使用（允许是清单外 ID）
    default_speaker: str = "S_BcqE5org2"
    # 使用语音合成的最低权限等级（0=不限制，0-255；admin=256 恒过）
    min_level: int = 0
    format: str = "mp3"               # mp3 / ogg_opus / pcm
    sample_rate: int = 24000
    speech_rate: int = 0              # -50(0.5倍速) ~ 100(2.0倍速)
    loudness_rate: int = 0            # -50(0.5倍音量) ~ 100(2.0倍音量)
    proxy: str = ""                   # HTTP 代理；留空直连（不读环境变量）
    timeout_seconds: int = 30

    @validator("resource_id")
    def _check_resource_id(cls, v):  # noqa: N805
        return v if v in (
            "seed-tts-2.0",
            "seed-tts-1.0",
            "seed-tts-1.0-concurr",
            "seed-icl-2.0",
        ) else "seed-tts-2.0"

    @validator("default_speaker", "proxy")
    def _strip(cls, v):  # noqa: N805
        return (v or "").strip()

    @validator("engine")
    def _check_tts_engine(cls, v):  # noqa: N805
        return v if v in ("deepseek", "doubao") else "deepseek"

    @validator("format")
    def _check_format(cls, v):  # noqa: N805
        return v if v in ("mp3", "ogg_opus", "pcm") else "mp3"

    @validator("sample_rate")
    def _check_sample_rate(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 24000
        return v if v in (8000, 16000, 22050, 24000, 32000, 44100, 48000) else 24000

    @validator("speech_rate", "loudness_rate")
    def _clamp_rate(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 0
        return v if -50 <= v <= 100 else 0

    @validator("timeout_seconds")
    def _clamp_timeout(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 30
        return v if 5 <= v <= 120 else 30

    @validator("min_level")
    def _clamp_min_level(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(v, 255))


class GalgameSettings(BaseModel):
    """Galgame 模式（全屏视觉小说，单角色）。

    max_loops 为一轮用户交互内 agent 循环的最大工具迭代数：每轮可能包含
    绘图、场景切换、选项等多次工具调用，通用 8 轮预算不够用。
    """

    enabled: bool = True
    max_loops: int = 40

    @validator("max_loops")
    def _clamp_max_loops(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 40
        return max(1, min(v, 200))


class LoreSettings(BaseModel):
    """内容库导入限制。

    角色卡 PNG 卡会把头像像素与卡片 JSON 一起上传，文件通常比纯 JSON 大；
    默认 50MB，可在管理面板调整（1–200MB）。
    """

    max_card_mb: int = 50

    @validator("max_card_mb", pre=True)
    def _clamp_max_card_mb(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 50
        return max(1, min(v, 200))


class PromptTemplateSettings(BaseModel):
    """酒馆 EJS 提示词模板（方案 B：客户端执行 + 服务端校验生效）。

    总开关关闭时后端不标记/不提供模板接口，行为与旧版本逐字节一致。
    输出侧执行模型生成的代码，需 allow_output_side 单独授权。
    """

    enabled: bool = False
    min_level: int = 0
    allow_output_side: bool = False

    @validator("min_level")
    def _clamp_min_level(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 0
        return max(0, min(v, 255))


class SandboxSettings(BaseModel):
    """AI 文件沙箱（P0）：双根目录、单文件大小与配额。

    会话根 data/{uid}/workspace/sessions/{sid}/ 用会话配额，
    公共根 data/{uid}/workspace/public/ 用独立的 public 配额。
    enabled=False 时 fs_* 工具不注册，行为与旧版本完全一致。
    """

    enabled: bool = False
    max_file_kb: int = 512
    quota_mb: int = 10
    public_max_file_kb: int = 4096
    public_quota_mb: int = 50

    @validator("max_file_kb", "quota_mb", "public_max_file_kb", "public_quota_mb")
    def _clamp_positive(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 512
        return max(1, v)


class SchedulerSettings(BaseModel):
    """后台调度器（P0 骨架，P3 起承载主动行为/定时任务/提醒）。

    多进程部署时靠 data/.scheduler.lock 领导锁保证只有一个实例在 tick。
    """

    enabled: bool = False
    timezone: str = "Asia/Shanghai"
    tick_seconds: int = 30
    reminder_check_seconds: int = 60

    @validator("timezone")
    def _strip_tz(cls, v):  # noqa: N805
        return (v or "").strip() or "Asia/Shanghai"

    @validator("tick_seconds", "reminder_check_seconds")
    def _clamp_tick(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 30
        return max(5, v)


class CustomToolSettings(BaseModel):
    """AI 自定义工具（P1）：隔离子进程执行 AI 编写的 Python 工具。

    信任边界：管理动作（发布/删除）仅管理员；代码在 python -I 短生子进程里
    exec，环境变量中的密钥/令牌一律剥离，超时强杀整棵进程树。注意这不是
    容器/沙箱级隔离——子进程仍有本机普通用户权限与网络访问，因此默认关闭。
    """

    enabled: bool = False
    code_max_chars: int = 8000
    max_per_scope: int = 20
    validate_timeout: float = 10.0
    call_timeout: float = 20.0

    @validator("code_max_chars", "max_per_scope")
    def _clamp_int(cls, v):  # noqa: N805
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 20
        return max(1, v)

    @validator("validate_timeout", "call_timeout")
    def _clamp_timeout(cls, v):  # noqa: N805
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 10.0
        return max(1.0, min(v, 300.0))


class LoopSettings(BaseModel):
    """长跑模式（P2）：无轮限长任务 + 安全点打断 + 滚动压缩。

    默认关闭。开启后 AI 可申请长跑（loop_request，risk 工具，按
    request_min_level 授权，0=仅管理员），审批通过后在既有 ChatRun 通道里
    以 LoopContext 运行：跳过双预算封顶，靠四道保护机制约束失控风险。
    """

    enabled: bool = False
    # 每 N 个工具轮强制一次无工具口语汇报
    every_rounds: int = 5
    # 连续 N 轮零工具操作即判定任务结束
    idle_turn_limit: int = 3
    # 历史字符数达到该阈值触发滚动压缩
    compact_char_threshold: int = 40000
    # 估算 token 达到（上下文窗口-输出余量）的该比例时触发压缩
    compact_token_ratio: float = 0.8
    # 压缩时保留最近多少个完整工具轮
    keep_rounds: int = 4
    # 模型上下文窗口 token 数；0=未知，仅靠字符阈值与超长报错学习值
    context_window: int = 0
    # loop_request 的最低用户等级；0=仅管理员可申请
    request_min_level: int = 0

    @validator("every_rounds", "idle_turn_limit", "keep_rounds")
    def _clamp_rounds(cls, v):  # noqa: N805
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 5

    @validator("compact_char_threshold")
    def _clamp_chars(cls, v):  # noqa: N805
        try:
            return max(1000, int(v))
        except (TypeError, ValueError):
            return 40000

    @validator("compact_token_ratio")
    def _clamp_ratio(cls, v):  # noqa: N805
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0.8
        return min(1.0, max(0.1, v))

    @validator("context_window", "request_min_level")
    def _clamp_nonneg(cls, v):  # noqa: N805
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0


class MoodSettings(BaseModel):
    """心情系统（P3）：半衰期心情值 + 七绪，便宜模型判定、本地二次限幅。

    默认关闭。判定结果只通过 LIVE_CTX 尾部块注入；显著波动每天最多宣布
    daily_cap 次，且「先 pending、ChatRun 成功后再 consumed」避免丢失宣布。
    """

    enabled: bool = False
    baseline: int = 70              # 心情基线/回归值
    delta_threshold: int = 5        # |delta| 达到该值才允许宣布
    min_confidence: int = 60        # 判定置信度下限（0-100）
    daily_cap: int = 6              # 每天最多注入宣布次数
    timeline_keep: int = 200        # 每会话时间线保留条数
    judge_provider_id: str = ""     # 判定模型路由；空=当前激活 provider
    judge_model: str = ""

    @validator("baseline", "delta_threshold", "min_confidence",
               "daily_cap", "timeline_keep")
    def _clamp_ints(cls, v):  # noqa: N805
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0


class SelfMemorySettings(BaseModel):
    """第一人称自我记忆（P3）：全局「我」视角，复用 BM25 召回。

    默认启用但只在对应工具暴露时生效；每日写配额只对新建计费，更新不计费。
    """

    enabled: bool = True
    daily_quota: int = 8
    recall_topk: int = 5
    recall_budget: int = 300        # 召回文本注入的字符预算

    @validator("daily_quota", "recall_topk")
    def _clamp_pos(cls, v):  # noqa: N805
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1

    @validator("recall_budget")
    def _clamp_budget(cls, v):  # noqa: N805
        try:
            return max(50, int(v))
        except (TypeError, ValueError):
            return 300


class ProactorSettings(BaseModel):
    """主动行为（P3）：总闸默认关闭，按会话 opt-in。

    30 分钟一个 tick：基础唤醒概率 + 连续未中保底；主动搭话按工作日峰谷、
    冷场乘子、自冷却与静默时段约束。触发只产生后台 ChatRun，模型可用
    [静默] 选择不发言。
    """

    enabled: bool = False
    wake_check_minutes: int = 30
    wake_prob_percent: int = 10
    wake_every_n: int = 20
    active_hours: float = 12.0
    speakup_peak_percent: int = 20
    speakup_valley_percent: int = 60
    self_cooldown_minutes: int = 20
    quiet_hours: str = "01:00-08:00"
    polls_max_per_session: int = 10

    @validator("wake_check_minutes", "wake_every_n",
               "self_cooldown_minutes", "polls_max_per_session")
    def _clamp_pos_int(cls, v):  # noqa: N805
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1

    @validator("wake_prob_percent", "speakup_peak_percent",
               "speakup_valley_percent")
    def _clamp_pct(cls, v):  # noqa: N805
        try:
            return min(100, max(0, int(v)))
        except (TypeError, ValueError):
            return 0

    @validator("active_hours")
    def _clamp_hours(cls, v):  # noqa: N805
        try:
            return min(72.0, max(0.5, float(v)))
        except (TypeError, ValueError):
            return 12.0


class McpSettings(BaseModel):
    """MCP stdio 生态接入（P4）：自研精简 JSON-RPC 客户端，不依赖官方 SDK。

    默认关闭。所有 mcp__ 工具 risk=True：call_min_level=0 时仅管理员可调用，
    >0 时等级达标或管理员可调用。server 配置与工具 schema 缓存在 admin 的
    agent.db，停机也能查看缓存。
    """

    enabled: bool = False
    timeout: float = 30.0
    call_min_level: int = 0

    @validator("timeout")
    def _clamp_timeout(cls, v):  # noqa: N805
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 30.0
        return min(300.0, max(1.0, v))

    @validator("call_min_level")
    def _clamp_level(cls, v):  # noqa: N805
        try:
            return min(255, max(0, int(v)))
        except (TypeError, ValueError):
            return 0


class ExtTriggerSettings(BaseModel):
    """外部事件触发（P4）：独立 Bearer 令牌的 /api/ext/trigger 入口。

    token 为空或 enabled=False 时端点直接禁用；令牌独立于用户登录令牌，
    不下发给普通用户配置视图。
    """

    enabled: bool = False
    token: str = ""
    cooldown_seconds: int = 30
    # watcher 回调地址前缀；空则按 server.host/port 拼本机回环
    base_url: str = ""

    @validator("cooldown_seconds")
    def _clamp_cooldown(cls, v):  # noqa: N805
        try:
            return min(3600, max(1, int(v)))
        except (TypeError, ValueError):
            return 30

    @validator("token", "base_url")
    def _strip(cls, v):  # noqa: N805
        return (v or "").strip()


class WatcherSettings(BaseModel):
    """监测进程（P4）：AI 写脚本、detached 子进程跑、经触发端点回调。"""

    max_per_session: int = 5

    @validator("max_per_session")
    def _clamp_cap(cls, v):  # noqa: N805
        try:
            return min(20, max(1, int(v)))
        except (TypeError, ValueError):
            return 5


class AccessSettings(BaseModel):
    """功能权限门槛：低于阈值的普通用户不能使用对应功能。

    0 = 不限制；范围 0-255（admin 按 UID 判定，恒不受限）。
    agent_min_level 覆盖除 base 外的全部工具类别（联网/生图/TTS/沙箱/
    自定义工具/长跑/自我记忆/主动行为/MCP）；browser/pixiv/galgame
    使用各自专属阈值，不再被 agent 阈值二次拦截。
    """

    browser_min_level: int = 0
    pixiv_min_level: int = 0
    galgame_min_level: int = 0
    agent_min_level: int = 0

    @validator("browser_min_level", "pixiv_min_level",
               "galgame_min_level", "agent_min_level", pre=True)
    def _clamp_level(cls, v):  # noqa: N805
        try:
            return max(0, min(int(v), 255))
        except (TypeError, ValueError):
            return 0


class DeepSeekSettings(BaseModel):
    """独立 DeepSeek Key：搜索/抓取/TTS/ASR 默认走 DeepSeek 官方接口。

    仅管理员可配置；普通用户不接触该 Key。价格单位为「点数」，
    1 点 = 1 元：search/fetch 为元/1000 次，tts 为元/10000 计费字符，
    asr 为元/小时。对应 *_enabled 关闭或调用失败时回退现有实现，
    回退成功同样计费。
    """

    enabled: bool = True
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    proxy: str = ""
    timeout_seconds: int = 60
    search_enabled: bool = True
    fetch_enabled: bool = True
    tts_enabled: bool = True
    asr_enabled: bool = True
    search_price: float = 5.0
    fetch_price: float = 5.0
    tts_price: float = 3.0
    asr_price: float = 1.0
    tts_voice: str = "stella"

    @validator("api_key", "base_url", "proxy")
    def _strip(cls, v):  # noqa: N805
        return (v or "").strip()

    @validator("base_url")
    def _check_base_url(cls, v):  # noqa: N805
        a = (v or "").strip().rstrip("/")
        return a if a.startswith(("http://", "https://")) else "https://api.deepseek.com"

    @validator("timeout_seconds", pre=True)
    def _clamp_timeout(cls, v):  # noqa: N805
        try:
            return max(5, min(int(v), 300))
        except (TypeError, ValueError):
            return 60

    @validator("search_price", "fetch_price", "tts_price", "asr_price",
               pre=True)
    def _clamp_price(cls, v):  # noqa: N805
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return 0.0

    @validator("tts_voice")
    def _check_voice(cls, v):  # noqa: N805
        return v if v in ("stella", "tide") else "stella"


class StImageSettings(BaseModel):
    """酒馆卡外链插图：服务端代理（方案 4）的兜底开关。

    前端默认走方案 1（直连 + 每用户开关）；代理仅当 enabled 为真且
    用户权限等级 ≥ min_level 时可用。allowed_domains 为空表示不限制
    （但仍受 SSRF/内网地址拦截）。admin 恒不受 min_level 约束。
    """

    enabled: bool = False
    min_level: int = 256
    allowed_domains: List[str] = []

    @validator("min_level", pre=True)
    def _clamp_level(cls, v):  # noqa: N805
        # 256 = 仅 admin（普通用户等级上限 255，admin 按 uid 判定恒过）
        try:
            return max(0, min(int(v), 256))
        except (TypeError, ValueError):
            return 256

    @validator("allowed_domains", pre=True)
    def _clean_domains(cls, v):  # noqa: N805
        if not isinstance(v, (list, tuple)):
            return []
        out = []
        for item in v:
            host = str(item or "").strip().lower().lstrip(".")
            if host and "/" not in host and len(host) <= 253:
                out.append(host)
        return out


class StNetSettings(BaseModel):
    """酒馆卡受控网络代理（L4-C）：卡内 fetch 由服务端代发。

    默认关闭。开启后域名策略为并集：allow_any_public_https 为真时放行任意
    公网 HTTPS；allowed_domains 非空时额外放行其中的域名（含 http）。
    所有请求仍受 flyai/netguard.py 的 SSRF/内网地址拦截。
    """

    enabled: bool = False
    allow_any_public_https: bool = True
    allowed_domains: List[str] = []
    min_level: int = 0
    max_bytes: int = 5 * 1024 * 1024
    timeout: float = 15.0
    # POST 等动态请求的每分钟上限；GET 静态资源单独用更宽松的上限，
    # 否则卡启动时批量加载 JS/图片会瞬间撞 429。
    per_minute: int = 60
    per_minute_get: int = 600

    @validator("min_level", pre=True)
    def _clamp_level(cls, v):  # noqa: N805
        try:
            return max(0, min(int(v), 256))
        except (TypeError, ValueError):
            return 0

    @validator("allowed_domains", pre=True)
    def _clean_domains(cls, v):  # noqa: N805
        if not isinstance(v, (list, tuple)):
            return []
        out = []
        for item in v:
            host = str(item or "").strip().lower().lstrip(".")
            if host and "/" not in host and len(host) <= 253:
                out.append(host)
        return out

    @validator("max_bytes", pre=True)
    def _clamp_bytes(cls, v):  # noqa: N805
        try:
            return max(64 * 1024, min(int(v), 32 * 1024 * 1024))
        except (TypeError, ValueError):
            return 5 * 1024 * 1024

    @validator("timeout", pre=True)
    def _clamp_timeout(cls, v):  # noqa: N805
        try:
            return max(1.0, min(float(v), 60.0))
        except (TypeError, ValueError):
            return 15.0

    @validator("per_minute", pre=True)
    def _clamp_rate(cls, v):  # noqa: N805
        try:
            return max(1, min(int(v), 600))
        except (TypeError, ValueError):
            return 60

    @validator("per_minute_get", pre=True)
    def _clamp_rate_get(cls, v):  # noqa: N805
        try:
            return max(1, min(int(v), 100000))
        except (TypeError, ValueError):
            return 600


class MvuSettings(BaseModel):
    """原生 MVU 变量系统总开关（关闭后不解析、不注入）。"""

    enabled: bool = True


class AppConfig(BaseModel):
    server: ServerSettings = ServerSettings()
    active_provider: str = ""
    providers: List[ProviderConfig] = []
    tools: ToolSettings = ToolSettings()
    history: HistorySettings = HistorySettings()
    roleplay: RoleplaySettings = RoleplaySettings()
    auth: AuthSettings = AuthSettings()
    registration: RegistrationSettings = RegistrationSettings()
    announcement: AnnouncementSettings = AnnouncementSettings()
    email: EmailSettings = EmailSettings()
    afdian: AfdianSettings = AfdianSettings()
    embeddings: EmbeddingsConfig = EmbeddingsConfig()
    image_generation: ImageGenSettings = ImageGenSettings()
    image_models: List[ImageModelConfig] = []
    stable_diffusion: StableDiffusionSettings = StableDiffusionSettings()
    pixiv: PixivSettings = PixivSettings()
    web_search: WebSearchSettings = WebSearchSettings()
    browser: BrowserSettings = BrowserSettings()
    tts: TtsSettings = TtsSettings()
    galgame: GalgameSettings = GalgameSettings()
    lore: LoreSettings = LoreSettings()
    sandbox: SandboxSettings = SandboxSettings()
    prompt_template: PromptTemplateSettings = PromptTemplateSettings()
    scheduler: SchedulerSettings = SchedulerSettings()
    custom_tools: CustomToolSettings = CustomToolSettings()
    loop: LoopSettings = LoopSettings()
    mood: MoodSettings = MoodSettings()
    self_memory: SelfMemorySettings = SelfMemorySettings()
    proactor: ProactorSettings = ProactorSettings()
    mcp: McpSettings = McpSettings()
    ext_trigger: ExtTriggerSettings = ExtTriggerSettings()
    watchers: WatcherSettings = WatcherSettings()
    access: AccessSettings = AccessSettings()
    deepseek: DeepSeekSettings = DeepSeekSettings()
    st_image: StImageSettings = StImageSettings()
    st_net: StNetSettings = StNetSettings()
    mvu: MvuSettings = MvuSettings()


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