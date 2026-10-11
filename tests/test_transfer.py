"""自包含迁移包 transfer：URL 收集、打包 / 解析、URL 改写、落盘与安全限制。"""

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai import transfer


def _write_file(upload_root, rel, data=b"PNGDATA"):
    p = Path(upload_root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _url(uid, rel):
    return "/uploads/%s/%s" % (uid, Path(rel).as_posix())


def test_build_and_open_roundtrip(tmp_path):
    up = tmp_path / "up"
    rel = "galgame/backgrounds/sid1/ga_1.png"
    _write_file(up, rel)
    url = _url("u1", rel)
    payload = {"data": {"messages": [{"content": "看 %s 。" % url}]}}

    raw = transfer.build_export_zip(payload, "u1", up)
    text, entries = transfer.open_export_zip(raw)

    envelope = json.loads(text)
    assert envelope["data"]["messages"][0]["content"] == "看 %s 。" % url
    assert len(entries) == 1
    assert entries[0]["rel"] == rel
    assert entries[0]["url"] == url
    assert entries[0]["data"] == b"PNGDATA"

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "conversation.json" in names
        assert "files/" + rel in names


def test_rewrite_urls_and_materialize(tmp_path):
    up = tmp_path / "up1"
    rel = "galgame/sprites/c1/ga_2.png"
    _write_file(up, rel)
    raw = transfer.build_export_zip(
        {"text": "![%s]" % _url("u1", rel)}, "u1", up)

    text, entries = transfer.open_export_zip(raw)
    text = transfer.rewrite_urls(text, entries, "u2")
    assert _url("u2", rel) in text
    assert "/uploads/u1/" not in text

    new_root = tmp_path / "up2"
    transfer.materialize_files(entries, new_root)
    assert (new_root / rel).read_bytes() == b"PNGDATA"


def test_missing_file_skipped_but_url_kept(tmp_path):
    up = tmp_path / "up"
    rel = "galgame/music/sid9/ga_x.mid"
    _write_file(up, rel, b"MIDI")
    url = _url("u1", rel)
    (up / rel).unlink()  # 磁盘上已不存在

    raw = transfer.build_export_zip({"m": url}, "u1", up)
    text, entries = transfer.open_export_zip(raw)
    assert entries == []
    assert url in text


def test_collect_urls_dedupes_nested_and_strips_tail(tmp_path):
    up = tmp_path / "up"
    _write_file(up, "galgame/backgrounds/s/a.png")
    _write_file(up, "galgame/music/s/b.mid", b"M")
    a_url = _url("u1", "galgame/backgrounds/s/a.png")
    b_url = _url("u1", "galgame/music/s/b.mid")

    payload = {
        "messages": [
            "图 %s，" % a_url,
            "重复 %s。" % a_url,
            ["乐 %s）" % b_url, {"x": "%s！" % a_url}],
        ]
    }
    urls = transfer._collect_urls(payload, "u1")
    assert urls == sorted([a_url, b_url])


def test_open_rejects_non_zip_bytes():
    with pytest.raises(transfer.TransferError):
        transfer.open_export_zip(b"not a zip file")


def test_open_rejects_missing_conversation():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
    with pytest.raises(transfer.TransferError):
        transfer.open_export_zip(buf.getvalue())


def test_open_rejects_entry_outside_files_prefix(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("conversation.json", "{}")
        zf.writestr(
            "manifest.json",
            json.dumps({"files": [{"url": "/uploads/u1/a.png", "zip": "a.png"}]}))
    with pytest.raises(transfer.TransferError):
        transfer.open_export_zip(buf.getvalue())


def test_open_rejects_duplicate_rel():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("conversation.json", "{}")
        zf.writestr(
            "files/a.png", b"x")
        zf.writestr(
            "manifest.json",
            json.dumps({"files": [
                {"url": "/uploads/u1/a.png", "zip": "files/a.png"},
                {"url": "/uploads/u1/a.png", "zip": "files/a.png"},
            ]}))
    with pytest.raises(transfer.TransferError):
        transfer.open_export_zip(buf.getvalue())


def test_open_enforces_file_size_limit(tmp_path):
    up = tmp_path / "up"
    _write_file(up, "a.png", b"X" * 10)
    raw = transfer.build_export_zip({"m": _url("u1", "a.png")}, "u1", up)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transfer, "MAX_FILE_BYTES", 8)
        with pytest.raises(transfer.TransferError):
            transfer.open_export_zip(raw)


def test_open_enforces_total_and_entry_limits(tmp_path):
    up = tmp_path / "up"
    _write_file(up, "a.png", b"X")
    raw = transfer.build_export_zip({"m": _url("u1", "a.png")}, "u1", up)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transfer, "MAX_TOTAL_BYTES", 1)
        with pytest.raises(transfer.TransferError):
            transfer.open_export_zip(raw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(transfer, "MAX_ENTRIES", 1)  # 含 conversation/manifest
        with pytest.raises(transfer.TransferError):
            transfer.open_export_zip(raw)


def test_safe_rel_rejects_illegal_paths():
    bad = ["../x", "a/../../b", "/abs", "a\\b", "a//b", "a/./b", "x:y", ""]
    for rel in bad:
        with pytest.raises(transfer.TransferError):
            transfer._safe_rel(rel)


def test_build_rejects_url_path_traversal(tmp_path):
    payload = {"m": "/uploads/u1/../secret.txt"}
    with pytest.raises(transfer.TransferError):
        transfer.build_export_zip(payload, "u1", tmp_path)


def test_materialize_blocks_zip_slip(tmp_path):
    root = tmp_path / "up"
    entries = [{"rel": "../evil.bin", "url": "/uploads/u1/../evil.bin", "data": b"x"}]
    with pytest.raises(transfer.TransferError):
        transfer.materialize_files(entries, root)
    assert not (tmp_path / "evil.bin").exists()
