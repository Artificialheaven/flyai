"""WebDAV 备份：连接、打包、上传、恢复暂存与启动应用（零新增依赖）。"""

import io
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
import pytest

from flyai.config import WebDavSettings
from flyai.webdav import (
    WebDavClient,
    WebDavError,
    apply_pending_restore,
    backup_name,
    normalize_remote_dir,
    stage_restore,
    upload_backup,
)


def _settings(**overrides):
    values = dict(
        enabled=True,
        url="https://dav.example/root/",
        username="user",
        password="pass",
        remote_dir="FlyAI",
        include_uploads=False,
        keep=2,
        verify_tls=True,
        timeout_seconds=10,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeDav:
    """极简内存 WebDAV：只覆盖代码用到的动词。"""

    def __init__(self):
        self.files = {}
        self.dirs = {"/root"}

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(urlsplit(str(request.url)).path)
        path = path.rstrip("/") or "/"
        method = request.method
        if method == "MKCOL":
            if path in self.dirs:
                return httpx.Response(405, request=request)
            parent = path.rsplit("/", 1)[0] or "/"
            if parent not in self.dirs:
                return httpx.Response(409, request=request)
            self.dirs.add(path)
            return httpx.Response(201, request=request)
        if method == "PROPFIND":
            if path not in self.dirs and path not in self.files:
                return httpx.Response(404, request=request)
            depth = request.headers.get("Depth", "0")
            items = [path]
            if depth == "1" and path in self.dirs:
                prefix = path.rstrip("/") + "/"
                children = set()
                for item in list(self.dirs) + list(self.files):
                    if item.startswith(prefix):
                        rest = item[len(prefix):]
                        if rest and "/" not in rest:
                            children.add(item)
                items.extend(sorted(children))
            parts = [
                '<?xml version="1.0"?>',
                '<d:multistatus xmlns:d="DAV:">',
            ]
            for item in items:
                is_dir = item in self.dirs
                body = (
                    "<d:response><d:href>%s%s</d:href><d:propstat><d:prop>"
                    "<d:resourcetype>%s</d:resourcetype>"
                    "<d:getcontentlength>%d</d:getcontentlength>"
                    "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                ) % (
                    item,
                    "/" if is_dir else "",
                    "<d:collection/>" if is_dir else "",
                    len(self.files.get(item, b"")),
                )
                parts.append(body)
            parts.append("</d:multistatus>")
            return httpx.Response(
                207,
                request=request,
                content="".join(parts).encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
        if method == "PUT":
            if path.rsplit("/", 1)[0] not in self.dirs:
                return httpx.Response(409, request=request)
            self.files[path] = request.content
            return httpx.Response(201, request=request)
        if method == "GET":
            if path not in self.files:
                return httpx.Response(404, request=request)
            return httpx.Response(200, request=request, content=self.files[path])
        if method == "DELETE":
            if path in self.files:
                del self.files[path]
                return httpx.Response(204, request=request)
            return httpx.Response(404, request=request)
        return httpx.Response(405, request=request)


@pytest.fixture()
def dav():
    fake = FakeDav()
    client = httpx.Client(transport=httpx.MockTransport(fake.handle))
    webdav = WebDavClient(_settings(), client=client)
    try:
        yield fake, webdav
    finally:
        client.close()


def _make_project(tmp_path: Path) -> tuple:
    project = tmp_path / "project"
    data = project / "data"
    (project / "config").mkdir(parents=True)
    (data / "uploads").mkdir(parents=True)
    (data / "sessions").mkdir(parents=True)
    (project / "config" / "config.json").write_text('{"server": {}}', encoding="utf-8")
    conn = sqlite3.connect(str(data / "users.db"))
    conn.execute("create table t (x)")
    conn.execute("insert into t values (1)")
    conn.commit()
    conn.close()
    (data / "sessions" / "sessions.db").write_bytes(b"")
    (data / "sessions" / "a.json").write_text("{}", encoding="utf-8")
    (data / "sessions" / "a.json.bak-20260101").write_text("old", encoding="utf-8")
    (data / "users.db-wal").write_bytes(b"wal")
    (data / ".hidden").write_text("x", encoding="utf-8")
    (data / "uploads" / "img.png").write_bytes(b"png")
    return project, data


def test_normalize_remote_dir():
    assert normalize_remote_dir(" /FlyAI/backups/ ") == "FlyAI/backups"
    assert normalize_remote_dir("") == ""
    with pytest.raises(WebDavError):
        normalize_remote_dir("../evil")
    with pytest.raises(WebDavError):
        normalize_remote_dir("a//b")


def test_upload_backup_excludes_uploads_and_unstable_files(tmp_path, dav):
    fake, client = dav
    project, data = _make_project(tmp_path)
    result = upload_backup(
        client, "FlyAI", include_uploads=False, config_path=project / "config" / "config.json",
        data_dir=data, keep=5,
    )
    assert result["name"].startswith("flyai-backup-")
    assert result["entries"] > 0
    assert any(path.endswith(result["name"]) for path in fake.files)

    remote = next(v for k, v in fake.files.items() if k.endswith(result["name"]))
    with zipfile.ZipFile(io.BytesIO(remote)) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "flyai-backup"
    assert "config/config.json" in names
    assert "data/users.db" in names
    assert "data/sessions/a.json" in names
    assert "data/uploads/img.png" not in names
    assert "data/users.db-wal" not in names
    assert "data/sessions/a.json.bak-20260101" not in names
    assert "data/.hidden" not in names
    # SQLite 快照包含已提交数据，而不是空文件
    conn = sqlite3.connect(":memory:")
    with zipfile.ZipFile(io.BytesIO(remote)) as zf:
        with tempfile_path(tmp_path, zf.read("data/users.db")) as db_path:
            src = sqlite3.connect(str(db_path))
            assert src.execute("select x from t").fetchone() == (1,)
            src.close()
    conn.close()


class tempfile_path:
    def __init__(self, root: Path, payload: bytes):
        self.path = Path(root) / ".probe.db"
        self.payload = payload

    def __enter__(self):
        self.path.write_bytes(self.payload)
        return self.path

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def test_upload_includes_uploads_when_requested(tmp_path, dav):
    fake, client = dav
    project, data = _make_project(tmp_path)
    result = upload_backup(
        client, "FlyAI", include_uploads=True, config_path=project / "config" / "config.json",
        data_dir=data, keep=5,
    )
    remote = next(v for k, v in fake.files.items() if k.endswith(result["name"]))
    with zipfile.ZipFile(io.BytesIO(remote)) as zf:
        assert "data/uploads/img.png" in zf.namelist()


def test_prune_keeps_latest(tmp_path, dav):
    fake, client = dav
    project, data = _make_project(tmp_path)
    client.ensure_dir("FlyAI")
    for name in ("flyai-backup-20200101-000000.zip",
                 "flyai-backup-20200102-000000.zip",
                 "flyai-backup-20200103-000000.zip"):
        old = tmp_path / name
        old.write_bytes(b"old")
        fake.files["/root/FlyAI/" + name] = b"old"
    upload_backup(
        client, "FlyAI", include_uploads=False, config_path=project / "config" / "config.json",
        data_dir=data, keep=2,
    )
    names = sorted(k.rsplit("/", 1)[-1] for k in fake.files)
    assert "flyai-backup-20200101-000000.zip" not in names
    assert len(names) == 2


def test_stage_and_apply_restore(tmp_path):
    project, data = _make_project(tmp_path)
    package = tmp_path / "backup.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "flyai-backup", "version": 1}))
        zf.writestr("config/config.json", '{"server": {"host": "restored"}}')
        zf.writestr("data/sessions/a.json", '{"restored": true}')

    pending = stage_restore(package, data, "flyai-backup-x.zip")
    assert pending["files"] == 2
    assert (data / ".restore" / "payload" / "data" / "sessions" / "a.json").is_file()

    applied = apply_pending_restore(project / "config" / "config.json", data)
    assert applied is not None
    assert "restored" in (data / "sessions" / "a.json").read_text(encoding="utf-8")
    assert "restored" in (project / "config" / "config.json").read_text(encoding="utf-8")
    assert not (data / ".restore").exists()


def test_stage_restore_rejects_traversal_and_bad_manifest(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    package = tmp_path / "bad.zip"
    with zipfile.ZipFile(package, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "flyai-backup"}))
        zf.writestr("data/../evil.txt", "x")
    with pytest.raises(WebDavError):
        stage_restore(package, data, "x.zip")
    assert not (data / ".restore").exists()

    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "nope"}))
    with pytest.raises(WebDavError):
        stage_restore(other, data, "x.zip")


def test_client_propfind_parses_namespaces(dav):
    fake, client = dav
    client.ensure_dir("FlyAI")
    fake.files["/root/FlyAI/flyai-backup-20260101-000000.zip"] = b"abc"
    backups = client.list_backups("FlyAI")
    assert backups == [
        {"name": "flyai-backup-20260101-000000.zip", "size": 3, "modified": ""}
    ]


def test_client_test_reaches_root(dav):
    fake, client = dav
    assert client.test() == "连接成功"


def test_backup_name_format():
    assert backup_name().startswith("flyai-backup-")
    assert backup_name().endswith(".zip")
