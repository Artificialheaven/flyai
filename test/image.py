"""豆包 Seedream 生图 demo（火山方舟 Ark 图片生成接口）。

运行：
    python test/image.py
    python test/image.py --prompt "一只趴在键盘上的橘猫" --size 2K

API Key 从 config/config.json 的提供商中读取（优先 base_url 含 volces.com 的提供商，
其次当前激活提供商）。生成的图片保存到 test/temp/。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

API_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
# MODEL = "doubao-seedream-5-0-flash-260915"
MODEL = "doubao-seedream-5-0-260128"

DEFAULT_PROMPT = (
    "日系二次元动漫插画，杰作，最高画质，极致细节，精致的五官与清澈的大眼睛，柔和通透的光影，清爽配色。"
    "一位高中女生站在放学后的教室里，身穿JK制服：白色短袖衬衫，深蓝色格纹百褶裙，红色蝴蝶结领结。"
    "异色裤袜：左腿穿着纯白色过膝丝袜，右腿穿着纯黑色过膝丝袜，双腿对比鲜明。"
    "她双手抬起举过头顶，手指在脑后把一头柔顺的长发收拢、正在给自己系高马尾；"
    "嘴里轻轻叼着一根黑色发绳备用，微微低头，眼神专注向上看，表情略带一丝认真与俏皮。"
    "发丝随动作轻盈飘动，夕阳从窗户斜射进来，金色逆光勾边，空气中有细小的尘埃光点，景深虚化，氛围清新治愈。"
    "全身构图，人物居中，动态自然，高质量动画赛璐璐上色风格，唯美，细腻，高清8K"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(__file__).resolve().parent / "temp"


def load_api_key() -> str:
    """从 config/config.json 读取火山方舟 API Key。"""
    cfg_path = PROJECT_ROOT / "config" / "config.json"
    if not cfg_path.exists():
        sys.exit(f"找不到配置文件: {cfg_path}")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    providers = cfg.get("providers", [])

    # 优先火山方舟的提供商
    for p in providers:
        if "volces.com" in (p.get("base_url") or "") and p.get("api_key"):
            return p["api_key"]
    # 回退：当前激活提供商
    active_id = cfg.get("active_provider")
    for p in providers:
        if p.get("id") == active_id and p.get("api_key"):
            return p["api_key"]
    sys.exit("config 中没有可用的 API Key")


def generate(prompt: str, size: str, api_key: str) -> str:
    """调用生图接口，返回图片 URL。"""
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "response_format": "url",
        "size": size,
        "stream": False,
        "watermark": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    with httpx.Client(timeout=httpx.Timeout(120.0)) as client:
        resp = client.post(API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            sys.exit(f"生图失败 HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        images = data.get("data") or []
        if not images or not images[0].get("url"):
            sys.exit(f"返回中没有图片地址: {data}")
        img_url = images[0]["url"]

        # 下载图片；按 Content-Type 决定扩展名
        img_resp = client.get(img_url)
        if img_resp.status_code >= 400:
            sys.exit(f"图片下载失败 HTTP {img_resp.status_code}")
    content_type = img_resp.headers.get("content-type", "").split(";")[0].strip()
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".png")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"seedream_{stamp}{ext}"
    out_path.write_bytes(img_resp.content)
    print("图片地址:", img_url)
    print("已保存到:", out_path)
    return str(out_path)


def main():
    parser = argparse.ArgumentParser(description="豆包 Seedream 生图 demo")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="生图提示词")
    parser.add_argument("--size", default="2K", help="图片尺寸，如 1K / 2K / 4K")
    args = parser.parse_args()

    api_key = load_api_key()
    generate(args.prompt, args.size, api_key)


if __name__ == "__main__":
    main()
