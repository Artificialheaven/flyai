"""九宫格宣传图批量生成脚本（大肥鱼主角版）。

以 promo/img.png（DeepSeek 大肥鱼 Q 版少女形象）为参考图走图生图：
- 8 张图各对应一个功能，大肥鱼以不同动作/表情出镜
- 每张图中心附近有一条琉璃横幅，标注功能名（短中文，3~4 字）
- 统一深夜星空 + 青紫辉光画风，输出 1:1 方图到 promo/grid/

用法：
    python promo/generate_promo.py --dry-run      # 只打印 8 段提示词，不生图
    python promo/generate_promo.py 1              # 只生成第 1 张（试效果）
    python promo/generate_promo.py                # 生成全部 8 张
    python promo/generate_promo.py 1 3 5          # 生成指定几张
    python promo/generate_promo.py --size 1K 1    # 指定尺寸
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "promo" / "grid"
REF_IMAGE = ROOT / "promo" / "img.png"

# ---- 角色固定描述（与参考图互相印证，保证 8 张图是同一只大肥鱼）----

CHARACTER = (
    "画面主角是参考图中的Q版大肥鱼拟人少女，必须严格保持参考图的外貌特征："
    "Q版二头身，头大身子小，软萌可爱；深蓝色长波浪卷发，发梢渐变为浅蓝色，"
    "头顶有一根弯翘的呆毛，戴白色蕾丝发箍；两侧是蓝白相间的鲸鱼鳍状发饰，"
    "各别一只蓝色小蝴蝶结；身后有一条蓝色鲸鱼尾巴；"
    "穿深蓝色哥特长袖女仆连衣裙，白色蕾丝围裙的胸口有一只可爱的小蓝鲸图案，"
    "裙装点缀金色细线与金色小蝴蝶结，脚穿深蓝色小皮鞋。"
    "保持角色的发型、发色、服饰与配色与参考图完全一致，只改变她的动作、表情和所处场景"
)

# ---- 统一画风 ----

STYLE_SUFFIX = (
    "整体为精致Q版二次元插画，半扁平厚涂融合柔和3D光影，线条干净、色彩通透；"
    "深夜星空背景，深靛蓝到墨蓝渐变，青色与紫罗兰色双色辉光，"
    "点缀少量品红色光点与一只极小的发光蝴蝶；电影感布光，角色边缘有细腻轮廓光，"
    "正方形构图，画面饱满。除指定横幅上的标题外，画面中不要出现任何其它文字、"
    "字母、数字、水印、logo或边框"
)

# ---- 每张图：(序号, 功能标签, 动作表情, 场景描述) ----

FEATURES: List[Tuple[int, str, str, str]] = [
    (
        1,
        "角色卡",
        "兴奋地双手把一张发光的角色卡牌高举过头顶，眼睛变成闪亮的星星眼，"
        "脸颊泛红，张嘴开心大笑，鲸鱼尾巴俏皮翘起",
        "周围夜空中呈扇形悬浮着数张塔罗牌大小的发光角色卡，"
        "卡面是不同奇幻角色的唯美半身像与剪影，卡牌之间有细微的魔法丝线相连，"
        "星光粒子飘散",
    ),
    (
        2,
        "群聊房间",
        "坐在圆桌主位上，一只手热情挥手，另一只手端着茶杯，"
        "眯着眼开怀大笑，神情活跃，像正在主持一场热闹的茶会",
        "一张悬浮在星空中的发光圆桌，数个造型各异的奇幻角色剪影围坐交谈，"
        "每个角色头顶飘出不同颜色的半透明对话气泡（气泡内只有柔光没有文字），"
        "圆桌中央有一小簇青紫色火焰",
    ),
    (
        3,
        "世界书",
        "从一本比她还大的摊开古书后面探出半个身子，双手扒着书沿，"
        "好奇地睁大眼睛、微微张嘴，眼睛里映着光芒，满脸探索欲",
        "巨大的发光魔法古书悬浮在夜空中，书页上方升起全息投影般的微缩世界："
        "小山丘、城堡、树木、小人物、齿轮与星轨环绕旋转，"
        "光带如数据流般在书与投影之间流动",
    ),
    (
        4,
        "智能生图",
        "双手握着一支比她还高的发光大画笔，俏皮地单眼眨眼（wink），"
        "嘴角上扬，正挥洒画笔作画，身姿轻盈跃起，尾尖甩出彩色光点",
        "笔触所过之处彩色光粒汇聚成一幅美轮美奂的角色肖像画，"
        "精致画框悬浮在空中，周围飘散着颜料般的星云粒子与几张草稿线稿的虚影",
    ),
    (
        5,
        "插画搜索",
        "双手举着一面巨大的放大镜挡在脸前，一只圆睁的眼睛透过镜片放大，"
        "神情专注又惊喜，眉毛上扬，像发现了宝藏",
        "一面悬浮在夜空中的画廊墙，整齐排列着许多动漫风格画作的缩略图，"
        "大部分缩略图覆盖毛玻璃磨砂模糊效果并微微发光，"
        "唯独一张画作清晰明亮、被青紫色光框高亮选中，有光流从模糊图汇聚向清晰图",
    ),
    (
        6,
        "联网搜索",
        "一只手叉腰，另一只手伸出食指点向身前的发光地球，"
        "胸有成竹地自信微笑，眼神明亮，发丝与尾巴随数据气流轻轻飘动",
        "一颗由蓝色光线与节点构成的发光地球在星空中缓慢旋转，"
        "周围环绕着数个半透明浏览器窗口的虚影（窗口内只有柔光与图像轮廓），"
        "发光的数据流如流星般在地球与窗口之间穿梭，科技感强",
    ),
    (
        7,
        "导出归档",
        "双手把一颗晶莹剔透的水晶存档球紧紧抱在脸颊旁，闭眼幸福微笑，"
        "脸颊微红，尾巴安心地卷起来，一副如获至宝的模样",
        "一串串发光的对话气泡如溪流般螺旋汇聚、封存入水晶球中，"
        "球内隐约可见微光的对话剪影，球体周围有金色与青紫色的封印光纹缓缓旋转",
    ),
    (
        8,
        "模型共享",
        "双手把一颗发光的多面体核心高高托起，开朗灿烂地大笑，"
        "身体微微跃起，尾巴舒展，像在向伙伴们分发礼物",
        "数个半透明的人形剪影站成一圈，围绕着她手中的发光核心，"
        "核心向每个人射出不同亮度的光线，部分人影身旁悬浮着钥匙与小盾牌光纹，"
        "人影脚下有明暗不同的等级光环",
    ),
]


def build_prompt(label: str, pose: str, scene: str) -> str:
    """组装单张图的完整提示词。"""
    text_clause = (
        f"画面正中偏上的位置（图片中心附近）悬浮一条半透明深紫琉璃质感的圆角横幅，"
        f"横幅两端有微光卷云装饰；横幅中央用圆润可爱的粗体中文写着醒目的"
        f"“{label}”标题：字体为清晰的发光描边艺术字，青蓝色渐变填充、紫罗兰色描边，"
        f"字形端正饱满；标题必须且只能是“{label}”这几个字，"
        f"一字不多、一字不少、绝无错别字或乱码"
    )
    return f"{CHARACTER}。{pose}。{scene}。{text_clause}。{STYLE_SUFFIX}"


def load_reference() -> Tuple[str, str]:
    """读取参考图，返回 (base64, media_type)。"""
    if not REF_IMAGE.exists():
        raise FileNotFoundError(f"找不到参考图: {REF_IMAGE}")
    raw = REF_IMAGE.read_bytes()
    ext = REF_IMAGE.suffix.lower().lstrip(".")
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp"}.get(ext, "image/png")
    return base64.b64encode(raw).decode("ascii"), media


async def generate_one(client: httpx.AsyncClient, model_cfg: Dict[str, Any],
                       size: str, prompt: str, ref_b64: str, ref_media: str,
                       idx: int, label: str) -> Path:
    url = model_cfg["base_url"].rstrip("/") + "/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {model_cfg['api_key']}",
    }

    def payload(image_value: Any, px_size: Optional[str] = None) -> Dict[str, Any]:
        return {
            "model": model_cfg["model"],
            "prompt": prompt,
            "response_format": "url",
            "size": px_size or size,
            "stream": False,
            "watermark": False,
            # 参考图：保持角色形象；禁用连续生成，只要一张结果
            "image": image_value,
            "sequential_image_generation": "disabled",
        }

    data_uri = f"data:{ref_media};base64,{ref_b64}"
    resp = await client.post(url, headers=headers, json=payload(data_uri))
    # 某些形态只接受裸 base64
    if resp.status_code == 400 and _mentions(resp.text, ("image", "base64", "图片")):
        resp = await client.post(url, headers=headers, json=payload(ref_b64))
    # 某些形态只接受像素尺寸
    if resp.status_code == 400 and "size" in resp.text.lower():
        px = "2048x2048" if size == "2K" else "1024x1024"
        resp = await client.post(url, headers=headers, json=payload(data_uri, px))
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")

    data = resp.json()
    images = data.get("data") or []
    if not images or not images[0].get("url"):
        raise RuntimeError(f"返回中没有图片地址: {data}")

    img_resp = await client.get(images[0]["url"])
    if img_resp.status_code >= 400:
        raise RuntimeError(f"图片下载失败 HTTP {img_resp.status_code}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{idx}_{label}.png"
    out.write_bytes(img_resp.content)
    return out


def _mentions(text: str, words: Tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(w in low for w in words)


async def main(which: List[int], size: str, dry_run: bool) -> None:
    if dry_run:
        for idx, label, pose, scene in FEATURES:
            if idx in which:
                print(f"===== 图{idx}《{label}》 =====")
                print(build_prompt(label, pose, scene))
                print()
        return

    cfg = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))
    models = cfg.get("image_models") or []
    if not models or not (models[0].get("api_key") or "").strip():
        print("错误：config/config.json 的 image_models[0].api_key 为空，"
              "请先填入火山方舟 API Key")
        sys.exit(1)
    model_cfg = models[0]
    ref_b64, ref_media = load_reference()
    print(f"使用模型: {model_cfg.get('name')} ({model_cfg['model']})，尺寸 {size}")
    print(f"参考图: {REF_IMAGE.relative_to(ROOT)}")

    picked = [f for f in FEATURES if f[0] in which]
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
        for idx, label, pose, scene in picked:
            print(f"生成图{idx}《{label}》...", flush=True)
            try:
                prompt = build_prompt(label, pose, scene)
                out = await generate_one(client, model_cfg, size, prompt,
                                         ref_b64, ref_media, idx, label)
                print(f"  -> {out.relative_to(ROOT)}")
            except Exception as exc:
                print(f"  !! {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    args = sys.argv[1:]
    size = "2K"
    dry_run = False
    if "--size" in args:
        i = args.index("--size")
        size = args[i + 1]
        del args[i:i + 2]
    if "--dry-run" in args:
        dry_run = True
        args.remove("--dry-run")
    if args:
        which = [int(a) for a in args]
    else:
        which = [f[0] for f in FEATURES]
    asyncio.run(main(which, size, dry_run))
