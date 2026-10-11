# -*- coding: utf-8 -*-
"""生成 App 图标：调用 config 中的云端生图模型（seedream），
再处理成 @capacitor/assets 需要的 icon-foreground / icon-background（1024x1024）。

产物（mobile/assets/）：
  icon-source.<ext>   云端生成的原图（留档）
  icon-background.png 铺满裁切 + 高斯模糊压暗的背景
  icon-foreground.png 铺满裁切的前景（头像居中，系统圆形/圆角只裁到边角光斑）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

MOBILE_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = MOBILE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from PIL import Image, ImageFilter, ImageEnhance  # noqa: E402

from flyai.config import load_config  # noqa: E402
from flyai.tools.image_tool import ark_generate  # noqa: E402

ASSETS_DIR = MOBILE_DIR / "assets"

# 图标专用提示词：头像居中、肩部以上、正方形构图、小尺寸可辨识
PROMPT = (
    "二次元赛博朋克猫娘头像特写，粉色短发配黑色机械猫耳，耳尖发出青色霓虹光，"
    "异色瞳（左眼青色、右眼粉紫），戴着半透明AR眼镜，俏皮自信的微笑；"
    "背景是霓虹紫与青蓝色的未来都市夜景，虚化的霓虹光斑bokeh；"
    "赛博朋克氛围，霓虹边缘光勾勒脸颊，精致日系插画风，干净锐利的线条，"
    "高对比度高饱和度；肩部以上头像，严格居中的正方形构图，"
    "脸部位于画面正中央。画面必须完全充满整个画布直到四条边缘，"
    "背景铺满画面，禁止出现任何边框、外框、圆角矩形轮廓、留白边距、"
    "卡片或徽章形状，就是一张完整的正方形插画。"
)


def cover(img: Image.Image, size=(1024, 1024)) -> Image.Image:
    """等比放大铺满后居中裁切。"""
    w, h = img.size
    tw, th = size
    s = max(tw / w, th / h)
    nw, nh = max(tw, round(w * s)), max(th, round(h * s))
    img = img.resize((nw, nh), Image.LANCZOS)
    return img.crop(((nw - tw) // 2, (nh - th) // 2, (nw + tw) // 2, (nh + th) // 2))


def process(raw_path: Path) -> None:
    src = Image.open(raw_path).convert("RGB")
    print("原图尺寸:", src.size)

    base = cover(src)

    # 背景：大半径高斯模糊 + 压暗（自适应图标背景层）
    bg = base.filter(ImageFilter.GaussianBlur(45))
    bg = ImageEnhance.Brightness(bg).enhance(0.72)
    bg.save(ASSETS_DIR / "icon-background.png")

    # 前景：铺满裁切（脸部已要求居中，圆形遮罩只切掉边角的光斑）
    base.save(ASSETS_DIR / "icon-foreground.png")
    print("已生成 assets/icon-background.png、assets/icon-foreground.png")


async def main() -> None:
    cfg = load_config()
    if not cfg.image_models:
        raise SystemExit("config 中没有可用的生图模型")
    model = cfg.image_models[0]
    print("使用生图模型:", model.model, "| 2K")
    # 图标素材不带「AI生成」水印（水印位置在系统遮罩边缘可能露出）
    content, ext, _media = await ark_generate(model, PROMPT, "2K", watermark=False)
    ASSETS_DIR.mkdir(exist_ok=True)
    raw_path = ASSETS_DIR / ("icon-source" + ext)
    raw_path.write_bytes(content)
    print("云端原图已保存:", raw_path)
    process(raw_path)


if __name__ == "__main__":
    asyncio.run(main())
