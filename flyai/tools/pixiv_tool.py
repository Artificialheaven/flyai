"""Pixiv 插画搜索工具（lolicon 免登录聚合 API）。

两个工具配合完成一次「搜索 → 视觉筛选 → 取原图」：

- search_pixiv：按角色名/标签检索候选，下载 master1200 缩略图：
  * 经 meta.images 把全部候选图一次回喂给视觉模型判断；
  * 候选缩略图文件落本用户上传目录，前端以毛玻璃遮盖展示。
- pick_pixiv：AI 给出选中的候选编号后，下载原始大图落盘；
  * 前端去掉对应位置模糊、展示原图；
  * 删除全部候选缩略图文件、清空搜索工具消息里的 base64，避免会话膨胀。

需要可用的出站代理（本机 Clash / 服务器 Xray），地址在 config.pixiv.proxy。
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import PixivSettings
from .base import Tool, ToolContext, ToolResult

# 候选缩略图下载并发 / 精选原图下载并发
_CAND_CONCURRENCY = 6
_PICK_CONCURRENCY = 3

# 候选缩略图与原图的体积上限
_MAX_CAND_BYTES = 8 * 1024 * 1024
_MAX_FULL_BYTES = 30 * 1024 * 1024


class SearchPixivTool(Tool):
    name = "search_pixiv"
    description = (
        "当用户想找某个角色/作品的 Pixiv 精美插画、二次元角色图时调用。"
        "传入角色名（可附作品名等限定词，空格分隔），工具返回一批候选缩略图供你查看。"
        "你必须逐一查看候选图片，按 ①角色是否相符 ②画面是否精美（构图、完成度、"
        "面部正常、无草稿/崩坏/粗制滥造）筛选，然后调用 pick_pixiv 给出选中编号。"
        "不要凭空描述没有调用工具的图片。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "角色名/标签，可带作品名限定（空格分隔多词为同时满足）",
            },
            "num": {
                "type": "integer",
                "description": "候选数量，默认取配置值（15）",
            },
        },
        "required": ["keyword"],
        "additionalProperties": False,
    }

    def __init__(self, settings: PixivSettings):
        self.settings = settings

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        keyword = str(arguments.get("keyword") or "").strip()
        if not keyword:
            return ToolResult(ok=False, output="[参数错误] 请提供要搜索的角色名或标签（keyword）")
        services = ctx.services or {}
        if services.get("upload_dir") is None or not (services.get("upload_url_base") or ""):
            return ToolResult(
                ok=False, output="[工具错误] 当前对话没有可用的图片保存目录上下文"
            )

        num = int(arguments.get("num") or self.settings.candidate_num or 15)
        num = max(5, min(num, 30))
        r18 = 2 if self.settings.r18_allowed else 0

        client = _make_client(self.settings)
        try:
            items = await _query_candidates(client, self.settings, keyword, num, r18)
            if not items:
                return ToolResult(
                    ok=False,
                    output=f"[工具错误] 没有搜索到与「{keyword}」相关的插画，可换个角色名再试",
                )
            stamp = time.strftime("%Y%m%d_%H%M%S")
            await _download_candidates(client, items, stamp, services)
            usable = [it for it in items if it.get("cand_url")]
            if not usable:
                return ToolResult(ok=False, output="[工具错误] 候选缩略图全部下载失败，请稍后重试")
        finally:
            await client.aclose()

        # 供同一 agent run 内的 pick_pixiv 使用（原图 URL、候选文件路径等）
        services["pixiv_candidates"] = {"items": usable}

        return ToolResult(
            ok=True,
            output=_render_search_output(usable, keyword, bool(services.get("vision"))),
            meta={
                "images": [
                    {
                        "media_type": it["media_type"],
                        "data": base64.b64encode(it["cand_bytes"]).decode("ascii"),
                    }
                    for it in usable
                ]
            },
        )


class PickPixivTool(Tool):
    name = "pick_pixiv"
    description = (
        "在 search_pixiv 返回候选后调用：传入你筛选出的候选编号（3-6 张最佳的）。"
        "工具下载原始大图并展示。不要在没有先调用 search_pixiv 的情况下使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "选中的候选编号，如 [0, 3, 5]",
            },
        },
        "required": ["indices"],
        "additionalProperties": False,
    }

    def __init__(self, settings: PixivSettings):
        self.settings = settings

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        services = ctx.services or {}
        raw_indices = arguments.get("indices") or []
        if not isinstance(raw_indices, list) or not raw_indices:
            return ToolResult(ok=False, output="[参数错误] 请提供选中的候选编号（indices）")
        holder = services.get("pixiv_candidates")
        if not holder or not holder.get("items"):
            return ToolResult(
                ok=False,
                output="[工具错误] 找不到本次搜索的候选信息，请重新调用 search_pixiv",
            )
        items = holder["items"]
        try:
            indices = sorted({int(i) for i in raw_indices})
        except (TypeError, ValueError):
            return ToolResult(ok=False, output="[参数错误] 候选编号必须是整数")
        if any(i < 0 or i >= len(items) for i in indices):
            return ToolResult(
                ok=False, output=f"[参数错误] 编号超出范围（共 {len(items)} 个候选）"
            )

        client = _make_client(self.settings)
        try:
            picked = await _download_fulls(client, items, indices)
        finally:
            await client.aclose()
        if not picked:
            return ToolResult(ok=False, output="[工具错误] 精选原图全部下载失败，请稍后重试")

        _save_fulls(picked, services)
        _cleanup_candidates(items, services)

        lines = ["[P站精选]"]
        for p in picked:
            lines.append(f"[选] {p['index']} {p['pid']} {p['url']}")
        lines.append("")
        lines.append(
            "已为用户精选以上插画。请用简短自然的话告知用户并可简单描述画面，"
            "不要输出链接或文件路径。"
        )
        return ToolResult(ok=True, output="\n".join(lines))


# ---- HTTP ----

def _make_client(settings: PixivSettings) -> httpx.AsyncClient:
    """统一出口客户端：出站代理 + 重定向跟随（lolicon 会 301）。"""
    proxy = (settings.proxy or "").strip() or None
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        proxy=proxy,
        follow_redirects=True,
    )


async def _query_candidates(
    client: httpx.AsyncClient,
    settings: PixivSettings,
    keyword: str,
    num: int,
    r18: int,
) -> List[Dict[str, Any]]:
    """检索候选并整理成内部 item 结构（此时未下载缩略图）。"""
    params = {
        "tag": keyword,
        "num": num,
        "r18": r18,
        # regular 缩略图用于视觉与展示，original 仅取 URL 待 pick 下载
        "size": ["regular", "original"],
    }
    resp = await client.get(settings.api_base, params=params)
    if resp.status_code >= 400:
        raise RuntimeError(f"搜索接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"搜索接口报错: {data['error']}")

    items: List[Dict[str, Any]] = []
    for i, entry in enumerate(data.get("data") or []):
        urls = entry.get("urls") or {}
        if not urls.get("regular") or not urls.get("original"):
            continue
        tags = entry.get("tags") or []
        items.append(
            {
                "index": i,
                "pid": entry.get("pid"),
                "title": entry.get("title") or "",
                "author": entry.get("author") or "",
                "width": entry.get("width"),
                "height": entry.get("height"),
                "r18": bool(entry.get("r18")),
                "tags": tags if isinstance(tags, list) else [str(tags)],
                "regular_url": urls["regular"],
                "original_url": urls["original"],
            }
        )
    return items


async def _download_candidates(
    client: httpx.AsyncClient,
    items: List[Dict[str, Any]],
    stamp: str,
    services: Dict[str, Any],
) -> None:
    """并发下载 master1200 缩略图，校验后落候选文件；失败的 item 标 cand_url=None。"""
    sem = asyncio.Semaphore(_CAND_CONCURRENCY)
    upload_dir = Path(services["upload_dir"])
    url_base = str(services["upload_url_base"]).rstrip("/")
    upload_dir.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:8]

    async def one(item: Dict[str, Any]) -> None:
        try:
            async with sem:
                resp = await client.get(item["regular_url"])
            if resp.status_code >= 400:
                return
            content = resp.content
            ext, media_type = _sniff_image(content)
            if ext is None or len(content) > _MAX_CAND_BYTES:
                return
            filename = f"pcand_{stamp}_{tag}_{item['index']}{ext}"
            (upload_dir / filename).write_bytes(content)
            item["cand_bytes"] = content
            item["media_type"] = media_type
            item["cand_file"] = upload_dir / filename
            item["cand_url"] = f"{url_base}/{filename}"
        except Exception:
            return

    await asyncio.gather(*(one(it) for it in items))


async def _download_fulls(
    client: httpx.AsyncClient,
    items: List[Dict[str, Any]],
    indices: List[int],
) -> List[Dict[str, Any]]:
    """并发下载选中项的原始大图并校验。"""
    sem = asyncio.Semaphore(_PICK_CONCURRENCY)

    async def one(index: int) -> Optional[Dict[str, Any]]:
        item = items[index]
        try:
            async with sem:
                resp = await client.get(item["original_url"])
            if resp.status_code >= 400:
                return None
            content = resp.content
            ext, media_type = _sniff_image(content)
            if ext is None or len(content) > _MAX_FULL_BYTES:
                return None
            return {
                "index": index,
                "pid": item["pid"],
                "content": content,
                "ext": ext,
                "media_type": media_type,
            }
        except Exception:
            return None

    results = await asyncio.gather(*(one(i) for i in indices))
    return [r for r in results if r is not None]


# ---- 落盘与清理 ----

def _save_fulls(picked: List[Dict[str, Any]], services: Dict[str, Any]) -> None:
    upload_dir = Path(services["upload_dir"])
    url_base = str(services["upload_url_base"]).rstrip("/")
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for p in picked:
        filename = f"pixiv_{stamp}_{uuid.uuid4().hex[:8]}_{p['pid']}{p['ext']}"
        (upload_dir / filename).write_bytes(p["content"])
        p["url"] = f"{url_base}/{filename}"


def _cleanup_candidates(items: List[Dict[str, Any]], services: Dict[str, Any]) -> None:
    """精选完成后：删除全部候选缩略图文件，并清空搜索工具消息里的 base64。"""
    for it in items:
        f = it.get("cand_file")
        if f:
            try:
                Path(f).unlink()
            except OSError:
                pass
    # 搜索工具消息中的候选图 base64 已完成视觉使命，剪掉防止会话 JSON 膨胀
    history = services.get("history") or []
    for msg in reversed(history):
        if getattr(msg, "role", "") == "tool" and getattr(msg, "name", "") == "search_pixiv":
            msg.images = None
            break


# ---- 输出文本 ----

def _render_search_output(items: List[Dict[str, Any]], keyword: str, vision: bool) -> str:
    lines = ["[P站候选]"]
    for it in items:
        lines.append(f"[候] {it['index']} {it['pid']} {it['cand_url']}")
    lines.append("")
    lines.append(f"以下为按「{keyword}」检索到的候选（编号与上方图片顺序一致）：")
    for it in items:
        size = ""
        if it.get("width") and it.get("height"):
            size = f" {it['width']}x{it['height']} |"
        r18 = " R-18 |" if it["r18"] else ""
        tags = " ".join(str(t) for t in it["tags"][:8])
        lines.append(
            f"{it['index']}. 《{it['title']}》 画师:{it['author']} |{size}{r18}"
            f" pid:{it['pid']} | tags: {tags}"
        )
    lines.append("")
    if vision:
        lines.append(
            "请逐一查看上方候选图片，只保留 ①角色与搜索词相符 ②画面精美的"
            "（构图好、完成度高、面部正常；草稿、低质、画面崩坏、粗制滥造的一律剔除），"
            "选出 3-6 张最佳的调用 pick_pixiv，indices 传入选中编号。"
        )
    else:
        lines.append(
            "注意：当前模型不支持图片识别，无法进行视觉筛选。"
            "可仅依据上方标签与标题信息挑选若干张调用 pick_pixiv，"
            "或提示用户切换为视觉模型再做质量筛选。"
        )
    return "\n".join(lines)


# ---- 图片校验 ----

def _sniff_image(data: bytes) -> Tuple[Optional[str], Optional[str]]:
    """按文件头魔数识别类型，防伪装/截断；返回 (扩展名, media_type)。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif", "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None, None
