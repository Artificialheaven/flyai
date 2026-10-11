# -*- coding: utf-8 -*-
"""用 Pillow 从 assets/icon-foreground.png、icon-background.png 生成全套启动图标。

产物（android/app/src/main/res/）：
  mipmap-<d>/ic_launcher_foreground.png  自适应前景 108dp 倍数
  mipmap-<d>/ic_launcher_background.png  自适应背景（模糊图）
  mipmap-<d>/ic_launcher.png             传统方形图标
  mipmap-<d>/ic_launcher_round.png       传统圆形图标（透明角）
  mipmap-anydpi-v26/ic_launcher*.xml     指向上述 mipmap
尺寸依据：https://developer.android.com/studio/write/image-asset-studio
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

MOBILE_DIR = Path(__file__).resolve().parents[1]
ASSETS = MOBILE_DIR / "assets"
RES = MOBILE_DIR / "android" / "app" / "src" / "main" / "res"

# 密度: (传统图标 px, 自适应图层 px)
DENSITIES = {
    "mdpi": (48, 108),
    "hdpi": (72, 162),
    "xhdpi": (96, 216),
    "xxhdpi": (144, 324),
    "xxxhdpi": (192, 432),
}

ANYDPI_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
    '    <background android:drawable="@mipmap/ic_launcher_background"/>\n'
    '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
    '</adaptive-icon>'
)


def round_icon(img: Image.Image, size: int) -> Image.Image:
    """中心圆形裁切，四角透明（传统 ic_launcher_round）。"""
    src = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    return out


def main() -> None:
    fg = Image.open(ASSETS / "icon-foreground.png").convert("RGB")
    bg = Image.open(ASSETS / "icon-background.png").convert("RGB")

    for density, (icon_px, layer_px) in DENSITIES.items():
        ddir = RES / f"mipmap-{density}"
        ddir.mkdir(exist_ok=True)
        fg.resize((layer_px, layer_px), Image.LANCZOS).save(
            ddir / "ic_launcher_foreground.png")
        bg.resize((layer_px, layer_px), Image.LANCZOS).save(
            ddir / "ic_launcher_background.png")
        fg.resize((icon_px, icon_px), Image.LANCZOS).save(
            ddir / "ic_launcher.png")
        round_icon(fg, icon_px).save(ddir / "ic_launcher_round.png")
        print(f"{density}: {icon_px}px 图标 / {layer_px}px 自适应图层")

    anydpi = RES / "mipmap-anydpi-v26"
    anydpi.mkdir(exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (anydpi / name).write_text(ANYDPI_XML, encoding="utf-8")
    print("anydpi-v26 XML 已更新")


if __name__ == "__main__":
    main()
