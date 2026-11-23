"""内容库头像缩略图：列表不能直接下发原图。"""

from pathlib import Path

from PIL import Image

from flyai.lore.avatar_image import ensure_avatar_thumb


def _write_png(path: Path, size=(1200, 1600)) -> None:
    Image.new("RGB", size, (200, 30, 30)).save(path, "PNG")


def test_thumb_generated_and_reused(tmp_path):
    uid = "admin"
    up = tmp_path / uid
    up.mkdir()
    src = up / "abc.png"
    _write_png(src)
    url = ensure_avatar_thumb(up, f"/uploads/{uid}/abc.png")
    assert url == f"/uploads/{uid}/abc.thumb.webp"
    thumb = up / "abc.thumb.webp"
    assert thumb.is_file()
    with Image.open(thumb) as im:
        assert max(im.size) <= 256
    # 缩略图应远小于原图
    assert thumb.stat().st_size < src.stat().st_size

    # 再次调用复用已有文件（mtime 不早于原图）
    before = thumb.stat().st_mtime_ns
    assert ensure_avatar_thumb(up, f"/uploads/{uid}/abc.png") == url
    assert thumb.stat().st_mtime_ns == before


def test_thumb_rejects_foreign_or_missing(tmp_path):
    up = tmp_path / "admin"
    up.mkdir()
    assert ensure_avatar_thumb(up, "/uploads/other/abc.png") == ""
    assert ensure_avatar_thumb(up, "/uploads/admin/missing.png") == ""
    assert ensure_avatar_thumb(None, "/uploads/admin/abc.png") == ""
