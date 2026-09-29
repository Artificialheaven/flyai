"""read_image 工具：uploads 路径/白名单路径读取、越权拒绝、类型校验。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.tools.base import ToolContext
from flyai.tools.read_image_tool import ReadImageTool

# 1x1 PNG（仅需真实文件头供工具校验）
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

import base64 as _b64  # noqa: E402


def _upload_scope(tmp_path, filename="x.png", content=_PNG):
    upload_dir = tmp_path / "uploads" / "admin"
    upload_dir.mkdir(parents=True)
    (upload_dir / filename).write_bytes(content)
    services = {
        "upload_dir": upload_dir,
        "upload_url_base": "/uploads/admin",
    }
    return ToolContext(services=services), upload_dir, f"/uploads/admin/{filename}"


@pytest.mark.asyncio
async def test_read_uploads_path_returns_image_meta(tmp_path):
    ctx, upload_dir, url = _upload_scope(tmp_path)

    r = await ReadImageTool().run({"path": url}, ctx)

    assert r.ok
    img = r.meta["image"]
    assert img["media_type"] == "image/png"
    assert _b64.b64decode(img["data"]) == _PNG
    # 首行图片标记（前端只渲染图片），文本含路径
    assert r.output.startswith(f"[图片] {url}\n")


@pytest.mark.asyncio
async def test_read_uploads_path_traversal_rejected(tmp_path):
    ctx, _, _ = _upload_scope(tmp_path)
    # 另建一个同级"用户"目录，试图用 .. 越界
    secret = tmp_path / "uploads" / "other"
    secret.mkdir(parents=True)
    (secret / "s.png").write_bytes(_PNG)

    r = await ReadImageTool().run(
        {"path": "/uploads/admin/../other/s.png"}, ctx
    )
    assert not r.ok and "安全限制" in r.output


@pytest.mark.asyncio
async def test_read_other_users_uploads_rejected(tmp_path):
    ctx, _, _ = _upload_scope(tmp_path)
    r = await ReadImageTool().run({"path": "/uploads/other/x.png"}, ctx)
    assert not r.ok


@pytest.mark.asyncio
async def test_read_whitelisted_absolute_path(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pic.png").write_bytes(_PNG)
    ctx = ToolContext(allowed_read_dirs=[root.resolve()])

    r = await ReadImageTool().run({"path": str(root / "pic.png")}, ctx)

    assert r.ok and r.meta["image"]["media_type"] == "image/png"
    # 白名单目录无 Web 路径：不出图片标记，文本照常给模型
    assert not r.output.startswith("[图片]")


@pytest.mark.asyncio
async def test_read_relative_path_in_whitelist(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pic.png").write_bytes(_PNG)
    ctx = ToolContext(allowed_read_dirs=[root.resolve()])

    r = await ReadImageTool().run({"path": "pic.png"}, ctx)
    assert r.ok


@pytest.mark.asyncio
async def test_path_outside_all_dirs_rejected(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    ctx = ToolContext(allowed_read_dirs=[root.resolve()])

    r = await ReadImageTool().run({"path": str(tmp_path / "secret.png")}, ctx)
    assert not r.ok and "安全限制" in r.output


@pytest.mark.asyncio
async def test_missing_file(tmp_path):
    ctx, _, _ = _upload_scope(tmp_path)
    r = await ReadImageTool().run({"path": "/uploads/admin/nope.png"}, ctx)
    assert not r.ok and "不存在" in r.output


@pytest.mark.asyncio
async def test_unsupported_extension(tmp_path):
    upload_dir = tmp_path / "uploads" / "admin"
    upload_dir.mkdir(parents=True)
    (upload_dir / "notes.txt").write_text("hello", encoding="utf-8")
    ctx = ToolContext(services={
        "upload_dir": upload_dir, "upload_url_base": "/uploads/admin",
    })

    r = await ReadImageTool().run({"path": "/uploads/admin/notes.txt"}, ctx)
    assert not r.ok and "不支持的图片类型" in r.output


@pytest.mark.asyncio
async def test_fake_image_with_image_extension_rejected(tmp_path):
    upload_dir = tmp_path / "uploads" / "admin"
    upload_dir.mkdir(parents=True)
    (upload_dir / "fake.png").write_text("not a png", encoding="utf-8")
    ctx = ToolContext(services={
        "upload_dir": upload_dir, "upload_url_base": "/uploads/admin",
    })

    r = await ReadImageTool().run({"path": "/uploads/admin/fake.png"}, ctx)
    assert not r.ok and "不是真实图片" in r.output
