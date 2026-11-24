import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mobile" / "poc"))

import build_overlay  # noqa: E402


def test_code_rev_is_deterministic():
    first = build_overlay.compute_code_rev(ROOT)
    second = build_overlay.compute_code_rev(ROOT)
    assert first == second
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)


def test_canonical_payload_order():
    manifest = {
        "version_code": 6,
        "overlay_version": 202611241530,
        "code_rev": "a" * 16,
        "requirements_sha256": "b" * 64,
        "min_app_version_code": 6,
        "overlay": {"sha256": "c" * 64, "size": 123},
        "full": {"sha256": "d" * 64, "size": 456, "version_code": 7},
        "signing_key_id": "poc-ota-2026",
    }
    assert build_overlay.canonical_payload(manifest).decode() == (
        "flyai-poc-overlay-v1\n"
        "6\n"
        "202611241530\n"
        "aaaaaaaaaaaaaaaa\n"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n"
        "6\n"
        "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\n"
        "123\n"
        "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd\n"
        "456\n"
        "7\n"
        "poc-ota-2026\n"
    )


def test_payload_only_contains_runtime_code(tmp_path):
    target = tmp_path / "overlay.tar.gz"
    build_overlay.write_payload(ROOT, target)
    with tarfile.open(str(target), "r:gz") as archive:
        names = archive.getnames()
    assert "./opt/flyai/main.py" in names
    assert "./opt/flyai/start.sh" in names
    assert any(name.startswith("./opt/flyai/flyai/") for name in names)
    for name in names:
        assert name.startswith("./opt/flyai/"), name
        assert "/data/" not in name
        assert "/.venv/" not in name
        assert not name.endswith("/config/config.json")


def test_read_tar_member_accepts_docker_style_paths(tmp_path):
    import rootfs_overlay

    data = b"fastapi==0.115.6\n"
    tar_path = tmp_path / "rootfs.tar.gz"
    with tarfile.open(str(tar_path), "w:gz") as archive:
        info = tarfile.TarInfo("opt/flyai/requirements.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    # GNU tar 的 "./opt/..." 与 docker export 的 "opt/..." 都要能命中
    assert rootfs_overlay.read_tar_member(tar_path, "./opt/flyai/requirements.txt") == data
    assert rootfs_overlay.read_tar_member(tar_path, "opt/flyai/requirements.txt") == data
    assert rootfs_overlay.read_tar_member(tar_path, "./opt/flyai/missing.txt") is None


def test_read_base_requirements_missing_returns_none(tmp_path):
    tar_path = tmp_path / "rootfs.tar.gz"
    with tarfile.open(str(tar_path), "w:gz") as archive:
        info = tarfile.TarInfo("opt/flyai/main.py")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))

    # 缺 requirements.txt 不能当成空文件，否则会误判依赖变化而禁用 overlay
    assert build_overlay.read_base_requirements(tar_path) is None


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl unavailable")
def test_manifest_signature_roundtrip(tmp_path):
    key = tmp_path / "ota.key"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    manifest = {
        "version_code": 6,
        "overlay_version": 1,
        "code_rev": "a" * 16,
        "requirements_sha256": "b" * 64,
        "min_app_version_code": 6,
        "overlay": {"sha256": "c" * 64, "size": 1},
        "full": None,
        "signing_key_id": "test",
    }
    signature = build_overlay.sign_manifest(manifest, key)
    assert len(signature) > 100

    public = tmp_path / "ota.pem"
    subprocess.run(
        ["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    payload = tmp_path / "manifest.txt"
    payload.write_bytes(build_overlay.canonical_payload(manifest))
    sig = tmp_path / "manifest.sig"
    import base64

    sig.write_bytes(base64.b64decode(signature))
    result = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(public),
            "-signature",
            str(sig),
            str(payload),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
