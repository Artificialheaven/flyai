"""内置 AI：把画面描述转写为 SD（Anything V5）的 danbooru 标签提示词与全部参数。

子 AI 走当前对话的 LLM 提供商（providers.complete 一次性补全），只回一个 JSON；
返回值经 _normalize_params 兜底——缺字段用 StableDiffusionSettings 默认，
越界值夹回范围，保证交给 Gradio 的参数始终可用。
"""

from __future__ import annotations

from typing import Any, Dict

from ..config import StableDiffusionSettings
from ..lore.gen import _extract_json
from ..providers.complete import complete
from ..schemas import Message

# 内置给子 AI 的系统提示词：danbooru 标签写法 + 参数规则 + 仅输出 JSON
SD_PROMPT_SYSTEM = """你是一名 Stable Diffusion 提示词工程师，专门为动漫模型 Anything V5（danbooru 训练体系）编写提示词。
任务：把用户给出的自然语言画面描述，转写为 SD 可用的英文 danbooru 标签，并确定全部采样参数。

【prompt 规则】
1. 全部使用英文 danbooru 标签：名词或形容词短语，不要完整句子；标签按重要程度从高到低排列，用英文逗号加空格分隔。
2. 开头必须是画质标签：masterpiece, best quality, ultra-detailed；随后依次写：
   主体数量与身份（如 1girl, solo）、发型发色、五官与表情、服饰与配件（从上到下逐层写清）、
   动作姿势、场景与道具、镜头构图（如 cowboy shot, close-up, from above, dutch angle）、
   光线（如 soft lighting, golden hour, dramatic shadow）、氛围与画风标签。
3. 用户没有指定的细节由你合理补全，让画面完整美观；用户明确否定或与描述矛盾的元素绝不能出现。
4. 可使用权重语法：重要标签 (tag:1.2)，需要弱化 (tag:0.8)；整张 prompt 标签不超过 60 个。

【negative_prompt 规则】
用英文标签覆盖常见崩坏与不想要的元素：lowres, bad anatomy, bad hands, text, error,
missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality,
jpeg artifacts, signature, watermark, username, blurry, bad feet；再按画面追加针对性负向标签
（例如画面是夜晚室内就不要写 sky, outdoors）。

【参数规则】
- width/height：竖构图人像 512×768，横构图 768×512，方形 512×512；必须是 8 的倍数，范围 256~1024。
- steps 取 20~35；cfg_scale 取 5.0~9.0。
- sampler 从 ["DPM++ 2M", "DPM++ SDE", "DPM++ 2M SDE", "Euler", "Euler a"] 中选择；
  schedule 从 ["Automatic", "Karras", "Exponential"] 中选择；动漫出图推荐 DPM++ 2M + Karras。
- seed 填 -1（随机）。

【输出格式】
只输出一个 JSON 对象，不要 Markdown、解释或注释：
{"prompt": "...", "negative_prompt": "...", "width": 512, "height": 768,
"steps": 28, "cfg_scale": 7.0, "sampler": "DPM++ 2M", "schedule": "Karras", "seed": -1}"""


async def generate_sd_params(
    provider, scene: str, settings: StableDiffusionSettings
) -> Dict[str, Any]:
    """让内置 AI 依据画面描述生成全套 SD 参数，返回规范化后的字典。"""
    user = (
        "画面描述：\n" + scene.strip() + "\n\n"
        "请据此生成英文 danbooru 标签提示词与全部采样参数，仅输出 JSON 对象。"
    )
    text = await complete(
        provider, [Message(role="user", content=user)], system_prompt=SD_PROMPT_SYSTEM
    )
    try:
        data = _extract_json(text)
        if not isinstance(data, dict):
            raise ValueError("AI 输出不是 JSON 对象")
    except ValueError:
        # 解析不出来也要能出图：全部走兜底默认，prompt 直接用原始画面描述
        data = {}
    return _normalize_params(data, scene, settings)


def _clamp_num(v: Any, low: float, high: float, default: Any, cast=float):
    try:
        x = cast(v)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, x))


def _round8(v: Any):
    x = _clamp_num(v, 256, 1024, None, cast=int)
    if x is None:
        return None
    return int((x // 8) * 8)


def _normalize_params(
    data: Dict[str, Any], scene: str, settings: StableDiffusionSettings
) -> Dict[str, Any]:
    """缺字段 → 兜底默认；越界 → 夹回范围。"""
    prompt = str(data.get("prompt") or "").strip() or scene.strip() or "masterpiece, best quality"
    negative = str(data.get("negative_prompt") or "").strip() or settings.negative_prompt

    width = _round8(data.get("width")) or settings.width
    height = _round8(data.get("height")) or settings.height
    steps = int(_clamp_num(data.get("steps"), 1, 150, settings.steps, cast=int))
    cfg_scale = _clamp_num(data.get("cfg_scale"), 1.0, 30.0, settings.cfg_scale, cast=float)

    sampler = str(data.get("sampler") or "").strip() or settings.sampler
    schedule = str(data.get("schedule") or "").strip() or settings.schedule

    seed = -1
    try:
        seed = int(data.get("seed"))
    except (TypeError, ValueError):
        pass

    return {
        "prompt": prompt,
        "negative_prompt": negative,
        "width": int(width),
        "height": int(height),
        "steps": steps,
        "cfg_scale": float(cfg_scale),
        "sampler": sampler,
        "schedule": schedule,
        "seed": seed,
    }
