"""APK 更新：webroot 扫描、公开版本接口与 .apk 下载响应头。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

from flyai.app import create_app
from flyai.config import AppConfig
from flyai.updates import APK_MIME, find_latest_apk, parse_apk_name


@pytest.fixture()
def webroot(tmp_path):
    root = tmp_path / "webroot"
    root.mkdir()
    return root


@pytest.fixture()
def client(webroot):
    cfg = AppConfig()
    cfg.server.web_root = str(webroot)  # 绝对临时目录：绝不触碰仓库 webroot
    app = create_app()
    app.state.flyai = SimpleNamespace(config=cfg)
    return TestClient(app)


def put_apk(webroot, name, content=b"fake-apk"):
    path = webroot / name
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "name,expected",
    [
        ("FlyAI-1.27.6134.apk", ("1.27.6134", 1276134)),
        ("FlyAI-1.9.9999.apk", ("1.9.9999", 1099999)),
        ("flyai-1.28.0.apk", ("1.28.0", 1280000)),
    ],
)
def test_parse_apk_name_valid(name, expected):
    assert parse_apk_name(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "FlyAI-1.27.apk",
        "FlyAI-1.27.6134.apk.bak",
        "Other-1.27.6134.apk",
        "FlyAI-1.27.10000.apk",
        "FlyAI-1.100.1.apk",
        "FlyAI-1.27.6134",
    ],
)
def test_parse_apk_name_invalid(name):
    assert parse_apk_name(name) is None


def test_latest_version_found(client, webroot):
    put_apk(webroot, "FlyAI-1.27.6134.apk", b"apk-bytes")
    r = client.get("/api/app/version")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    data = r.json()
    assert data["version"] == "1.27.6134"
    assert data["version_code"] == 1276134
    assert data["file_name"] == "FlyAI-1.27.6134.apk"
    assert data["url"] == "/FlyAI-1.27.6134.apk"
    assert data["size"] == len(b"apk-bytes")


def test_highest_version_wins(client, webroot):
    put_apk(webroot, "FlyAI-1.9.9999.apk")
    put_apk(webroot, "FlyAI-1.10.0.apk")
    put_apk(webroot, "FlyAI-1.24.6130.apk")
    assert client.get("/api/app/version").json()["version"] == "1.24.6130"

    put_apk(webroot, "FlyAI-1.27.6134.apk")
    data = client.get("/api/app/version").json()
    assert data["version"] == "1.27.6134"
    assert data["version_code"] == 1276134


def test_non_matching_files_ignored(client, webroot):
    put_apk(webroot, "FlyAI-1.27.6134.apk.bak")
    put_apk(webroot, "Other-9.9.9999.apk")
    (webroot / "FlyAI-9.9.9999.apk").mkdir()
    nested = webroot / "sub"
    nested.mkdir()
    put_apk(nested, "FlyAI-9.9.9999.apk")

    data = client.get("/api/app/version").json()
    assert data["version"] is None


def test_empty_webroot_returns_null(client):
    r = client.get("/api/app/version")
    assert r.status_code == 200
    assert r.json() == {
        "version": None,
        "version_code": None,
        "file_name": None,
        "url": None,
        "size": None,
    }


def test_missing_webroot_returns_null(webroot):
    cfg = AppConfig()
    cfg.server.web_root = str(webroot / "does-not-exist")
    app = create_app()
    app.state.flyai = SimpleNamespace(config=cfg)
    r = TestClient(app).get("/api/app/version")
    assert r.status_code == 200
    assert r.json()["version"] is None


def test_zero_byte_apk_ignored(client, webroot):
    put_apk(webroot, "FlyAI-1.27.6134.apk", b"")
    assert client.get("/api/app/version").json()["version"] is None


def test_symlink_outside_root_ignored(webroot, tmp_path):
    outside = tmp_path / "outside.apk"
    outside.write_bytes(b"outside")
    link = webroot / "FlyAI-9.9.9999.apk"
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")
    assert find_latest_apk(webroot) is None


def test_apk_served_with_android_mime(client, webroot):
    put_apk(webroot, "FlyAI-1.27.6134.apk", b"0123456789")
    r = client.get("/FlyAI-1.27.6134.apk")
    assert r.status_code == 200
    assert r.headers["content-type"] == APK_MIME
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.content == b"0123456789"

    partial = client.get(
        "/FlyAI-1.27.6134.apk",
        headers={"Range": "bytes=0-3"},
    )
    assert partial.status_code == 206
    assert partial.content == b"0123"


def test_manifest_url_is_downloadable(client, webroot):
    put_apk(webroot, "FlyAI-1.27.6134.apk", b"apk-content")
    data = client.get("/api/app/version").json()
    download = client.get(data["url"])
    assert download.status_code == 200
    assert download.content == b"apk-content"
    assert download.headers["content-type"] == APK_MIME


def test_version_endpoint_is_public(client, webroot):
    put_apk(webroot, "FlyAI-1.27.6134.apk")
    r = client.get("/api/app/version")  # 无 Authorization
    assert r.status_code == 200
    assert r.json()["version"] == "1.27.6134"
