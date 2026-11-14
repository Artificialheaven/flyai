"""P0 文件沙箱：双根路径校验、symlink 防护、配额、public 只读、删除保护。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from flyai.config import SandboxSettings
from flyai.sandbox import Sandbox, SandboxError, build_user_sandbox


def _sb(tmp_path, **kw):
    return Sandbox(
        session_root=tmp_path / "sessions" / "s1",
        public_root=tmp_path / "public",
        **kw,
    )


def test_write_read_list_roundtrip(tmp_path):
    sb = _sb(tmp_path)
    assert sb.write_file("notes/a.txt", "hello") == len("hello".encode("utf-8"))
    assert sb.read_file("notes/a.txt") == "hello"
    names = [it["name"] for it in sb.list_dir(".")]
    assert "notes/" in names
    sub = sb.list_dir("notes")
    assert sub[0]["name"] == "a.txt" and sub[0]["type"] == "file"


def test_backslash_and_dot_segments(tmp_path):
    sb = _sb(tmp_path)
    sb.write_file("a\\b\\c.txt", "x")
    assert sb.read_file("a/b/c.txt") == "x"
    assert sb.list_dir("./a/b/")[0]["name"] == "c.txt"


def test_reject_absolute_and_drive(tmp_path):
    sb = _sb(tmp_path)
    with pytest.raises(SandboxError):
        sb.resolve("/etc/passwd")
    with pytest.raises(SandboxError):
        sb.resolve("C:/Windows/x")
    with pytest.raises(SandboxError):
        sb.write_file("../escape.txt", "x")


def test_public_routing_and_readonly(tmp_path):
    sb = _sb(tmp_path)  # public_writable 默认 False
    sb.write_file("local.txt", "a")
    with pytest.raises(SandboxError):
        sb.write_file("public/shared.txt", "b")
    # 显式开放写后可写，且配额独立
    sb2 = _sb(tmp_path, public_writable=True)
    sb2.write_file("public/shared.txt", "b")
    assert sb2.read_file("public/shared.txt") == "b"
    # public 文件不在会话根里
    assert not (tmp_path / "sessions" / "s1" / "public").exists()


def test_per_file_cap(tmp_path):
    sb = _sb(tmp_path, max_file_kb=1)
    with pytest.raises(SandboxError, match="单文件"):
        sb.write_file("big.txt", "x" * 2048)


def test_total_quota(tmp_path):
    sb = _sb(tmp_path, max_file_kb=1024, quota_mb=1)
    sb.write_file("a.bin", "x" * (700 * 1024))
    # 两笔都未传单文件上限，但合计超过 1 MiB 总配额
    with pytest.raises(SandboxError, match="配额"):
        sb.write_file("b.bin", "x" * (400 * 1024))
    # 覆盖写按「减旧文件」记账，不会误报配额
    sb.write_file("a.bin", "y" * 1024)


def test_public_own_quota(tmp_path):
    sb = _sb(tmp_path, public_writable=True, public_quota_mb=1,
             public_max_file_kb=512)
    sb.write_file("public/p.bin", "x" * (512 * 1024))
    with pytest.raises(SandboxError):
        sb.write_file("public/q.bin", "x" * (512 * 1024 + 1))


def test_symlink_escape_blocked(tmp_path):
    sb = _sb(tmp_path)
    sb.write_file("ok.txt", "in")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = sb.session_root / "evil"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("当前环境无创建 symlink 权限（Windows 需开发者模式/管理员）")
    with pytest.raises(SandboxError):
        sb.read_file("evil")
    with pytest.raises(SandboxError):
        sb.resolve("evil/child")


def test_delete_rules(tmp_path):
    sb = _sb(tmp_path)
    assert sb.delete("missing.txt") is False
    sb.write_file("d/a.txt", "x")
    assert sb.delete("d/a.txt")
    sb.write_file("d/b.txt", "y")
    assert sb.delete("d")  # 目录递归删除
    with pytest.raises(SandboxError):
        sb.delete(".")
    sb2 = _sb(tmp_path, public_writable=True)
    with pytest.raises(SandboxError):
        sb2.delete("public")


def test_binary_read_preview(tmp_path):
    sb = _sb(tmp_path)
    target = sb.session_root / "bin.dat"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(range(256)))
    text = sb.read_file("bin.dat")
    assert "二进制文件" in text


def test_build_user_sandbox_paths_and_settings(tmp_path):
    settings = SandboxSettings(
        max_file_kb=256, quota_mb=5,
        public_max_file_kb=1024, public_quota_mb=20,
    )
    sb = build_user_sandbox(tmp_path, "u1", "room-1", settings)
    assert sb.session_root == (tmp_path / "u1" / "workspace" / "sessions" / "room-1").resolve()
    assert sb.public_root == (tmp_path / "u1" / "workspace" / "public").resolve()
    assert sb.max_file_bytes == 256 * 1024
    assert sb.quota_bytes == 5 * 1024 * 1024
    assert sb.public_quota_bytes == 20 * 1024 * 1024
    assert sb.public_writable is False
