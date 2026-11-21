"""Pixiv 搜图工具单测：检索、候选缩略图、精选原图、清理与失败路径。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest

from flyai.config import PixivSettings
from flyai.schemas import ImageAttachment, Message
from flyai.tools import pixiv_tool as mod
from flyai.tools.base import ToolContext
from flyai.tools.pixiv_tool import PickPixivTool, SearchPixivTool

# 最小合法 JPEG（能过魔数检查）
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
FULL_JPEG = b"\xff\xd8\xff\xe0" + b"\x11" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _settings(**kw):
    return PixivSettings(**kw)


def _ctx(tmp_path, history=None, vision=True, extra=None):
    services = {
        "upload_dir": str(tmp_path),
        "upload_url_base": "/uploads/u",
        "history": history or [],
        "vision": vision,
    }
    if extra:
        services.update(extra)
    return ToolContext(services=services)


def _entry(pid):
    return {
        "pid": pid,
        "uid": 1,
        "title": "title %d" % pid,
        "author": "artist %d" % pid,
        "width": 600,
        "height": 800,
        "r18": False,
        "tags": ["原神", "甘雨", "Ganyu"],
        "urls": {
            "regular": (
                "https://i.pixiv.re/img-master/img/2024/01/01/00/00/00/"
                "%d_p0_master1200.jpg" % pid
            ),
            "original": (
                "https://i.pixiv.re/img-original/img/2024/01/01/00/00/00/"
                "%d_p0.jpg" % pid
            ),
        },
    }


def _api_json(pids):
    return {"data": [_entry(p) for p in pids], "error": ""}


def _patch_client(monkeypatch, handler):
    def fake_make_client(settings):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(mod, "_make_client", fake_make_client)


def _make_handler(captured, api_json, regular_resp=(200, JPEG),
                  full_resp=(200, FULL_JPEG)):
    def handler(request):
        path = request.url.path
        if path == "/setu/v2":
            captured["r18"] = request.url.params.get("r18")
            captured["tag"] = request.url.params.get("tag")
            captured["num"] = request.url.params.get("num")
            return httpx.Response(200, json=api_json)
        if "img-master" in path:
            code, content = regular_resp
            return httpx.Response(code, content=content,
                                  headers={"content-type": "image/jpeg"})
        if "img-original" in path:
            code, content = full_resp
            return httpx.Response(code, content=content,
                                  headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    return handler


# ---- search_pixiv ----

def test_search_success(tmp_path, monkeypatch):
    captured = {}
    _patch_client(
        monkeypatch,
        _make_handler(captured, _api_json([1001, 1002])),
    )
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "甘雨"}, _ctx(tmp_path)
    ))
    assert result.ok
    assert captured["tag"] == "甘雨"
    assert captured["r18"] == "0"  # 默认全龄向
    # 候选文件落盘 + meta 多图
    assert len(result.meta["images"]) == 2
    assert len(list(tmp_path.iterdir())) == 2
    lines = result.output.splitlines()
    assert lines[0] == "[P站候选]"
    assert lines[1].startswith("[候] 0 1001 /uploads/u/pcand_")
    assert "视觉" not in result.output  # vision=True 走筛选指引


def test_search_r18_mapping(tmp_path, monkeypatch):
    captured = {}
    _patch_client(
        monkeypatch,
        _make_handler(captured, _api_json([1001])),
    )
    asyncio.run(SearchPixivTool(_settings(r18_allowed=True)).run(
        {"keyword": "霊夢"}, _ctx(tmp_path)
    ))
    assert captured["r18"] == "2"  # 允许 R-18 → 混合


def test_search_empty_result(tmp_path, monkeypatch):
    captured = {}
    _patch_client(monkeypatch, _make_handler(captured, _api_json([])))
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "不存在的角色xyz"}, _ctx(tmp_path)
    ))
    assert not result.ok and "没有搜索到" in result.output


def test_search_skips_failed_download(tmp_path, monkeypatch):
    captured = {}
    # regular 全部 404 → 无可用候选
    _patch_client(
        monkeypatch,
        _make_handler(captured, _api_json([1001, 1002]),
                      regular_resp=(404, b"")),
    )
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "甘雨"}, _ctx(tmp_path)
    ))
    assert not result.ok and "下载失败" in result.output


def test_search_skips_bad_magic(tmp_path, monkeypatch):
    captured = {}
    _patch_client(
        monkeypatch,
        _make_handler(captured, _api_json([1001]),
                      regular_resp=(200, b"not an image")),
    )
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "甘雨"}, _ctx(tmp_path)
    ))
    assert not result.ok


def test_search_num_clamped(tmp_path, monkeypatch):
    captured = {}
    _patch_client(
        monkeypatch,
        _make_handler(captured, _api_json([1001])),
    )
    asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "甘雨", "num": 999}, _ctx(tmp_path)
    ))
    assert captured["num"] == "30"  # 上限 30


def test_search_non_vision_hint(tmp_path, monkeypatch):
    captured = {}
    _patch_client(monkeypatch, _make_handler(captured, _api_json([1001])))
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "甘雨"}, _ctx(tmp_path, vision=False)
    ))
    assert result.ok and "不支持图片识别" in result.output


def test_search_missing_keyword(tmp_path):
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "  "}, _ctx(tmp_path)
    ))
    assert not result.ok and "参数错误" in result.output


def test_search_missing_upload_context(tmp_path):
    result = asyncio.run(SearchPixivTool(_settings()).run(
        {"keyword": "甘雨"}, ToolContext(services={"vision": True})
    ))
    assert not result.ok and "保存目录" in result.output


# ---- pick_pixiv ----

def _holder_item(i, pid, tmp_path):
    f = tmp_path / ("pcand_%d.jpg" % i)
    f.write_bytes(JPEG)
    return {
        "index": i,
        "pid": pid,
        "cand_file": f,
        "media_type": "image/jpeg",
        "original_url": (
            "https://i.pixiv.re/img-original/img/2024/01/01/00/00/00/"
            "%d_p0.jpg" % pid
        ),
    }


def test_pick_success(tmp_path, monkeypatch):
    captured = {}
    _patch_client(monkeypatch, _make_handler(captured, _api_json([])))
    items = [_holder_item(0, 1001, tmp_path), _holder_item(1, 1002, tmp_path)]
    # 模拟搜索阶段的工具消息（带候选 base64）
    search_msg = Message(
        role="tool", name="search_pixiv", content="[P站候选]\nx",
        images=[ImageAttachment(media_type="image/jpeg", data="AAAA")],
    )
    ctx = _ctx(tmp_path, history=[search_msg], extra={
        "pixiv_candidates": {"items": items}
    })
    result = asyncio.run(PickPixivTool(_settings()).run(
        {"indices": [0]}, ctx
    ))
    assert result.ok
    lines = result.output.splitlines()
    assert lines[0] == "[P站精选]"
    assert lines[1].startswith("[选] 0 1001 /uploads/u/pixiv_")
    # 精选原图落盘、候选文件全部删除
    saved = [p for p in tmp_path.iterdir() if p.name.startswith("pixiv_")]
    assert len(saved) == 1
    assert not any(p.name.startswith("pcand_") for p in tmp_path.iterdir())
    # 搜索工具消息中的 base64 被剪掉
    assert search_msg.images is None


def test_pick_bad_index(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _make_handler({}, _api_json([])))
    items = [_holder_item(0, 1001, tmp_path)]
    ctx = _ctx(tmp_path, extra={"pixiv_candidates": {"items": items}})
    result = asyncio.run(PickPixivTool(_settings()).run(
        {"indices": [5]}, ctx
    ))
    assert not result.ok and "超出范围" in result.output


def test_pick_non_integer(tmp_path):
    items = [_holder_item(0, 1001, tmp_path)]
    ctx = _ctx(tmp_path, extra={"pixiv_candidates": {"items": items}})
    result = asyncio.run(PickPixivTool(_settings()).run(
        {"indices": ["x"]}, ctx
    ))
    assert not result.ok and "整数" in result.output


def test_pick_without_search(tmp_path):
    result = asyncio.run(PickPixivTool(_settings()).run(
        {"indices": [0]}, _ctx(tmp_path)
    ))
    assert not result.ok and "重新调用" in result.output


def test_pick_empty_indices(tmp_path):
    items = [_holder_item(0, 1001, tmp_path)]
    ctx = _ctx(tmp_path, extra={"pixiv_candidates": {"items": items}})
    result = asyncio.run(PickPixivTool(_settings()).run(
        {"indices": []}, ctx
    ))
    assert not result.ok and "候选编号" in result.output


def test_pick_dedup_indices(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _make_handler({}, _api_json([])))
    items = [_holder_item(0, 1001, tmp_path)]
    ctx = _ctx(tmp_path, extra={"pixiv_candidates": {"items": items}})
    result = asyncio.run(PickPixivTool(_settings()).run(
        {"indices": [0, 0, 0]}, ctx
    ))
    assert result.ok
    assert result.output.splitlines()[1].startswith("[选] 0 1001")


# ---- 工具函数 ----

def test_sniff_image():
    assert mod._sniff_image(JPEG) == (".jpg", "image/jpeg")
    assert mod._sniff_image(PNG) == (".png", "image/png")
    assert mod._sniff_image(b"GIF87a xx") == (".gif", "image/gif")
    assert mod._sniff_image(b"GIF89a xx") == (".gif", "image/gif")
    webp = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8
    assert mod._sniff_image(webp) == (".webp", "image/webp")
    assert mod._sniff_image(b"unknown") == (None, None)


def test_make_client_proxy():
    c = mod._make_client(_settings(proxy="http://127.0.0.1:7897"))
    try:
        # httpx 0.28：proxy 挂载在 _mounts
        assert c._mounts
    finally:
        asyncio.run(c.aclose())


def test_make_client_no_proxy():
    c = mod._make_client(_settings(proxy=""))
    try:
        # httpx 0.28 无代理时也会挂 http:// / https:// 直连 mount，
        # 代理则是 all:// 通配 mount，按 pattern 判断更稳
        patterns = [getattr(k, "pattern", None) for k in c._mounts]
        assert "all://" not in patterns
    finally:
        asyncio.run(c.aclose())
