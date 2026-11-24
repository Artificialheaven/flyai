"""酒馆扩展管理器：manifest、扫描、安装安全、启停与快照。"""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from flyai.extensions.installer import InstallError, InstallLimits, install_zip
from flyai.extensions.manifest import parse_manifest
from flyai.extensions.models import ManifestError
from flyai.extensions.scanner import HOST_MODULES, scan_extension
from flyai.extensions.service import ExtensionService
from flyai.extensions.store import ExtensionStore


MANIFEST = {
    "display_name": "Logic Fixture",
    "version": "1.2.3",
    "js": "index.js",
    "css": "style.css",
    "loading_order": 50,
    "hooks": {"activate": "init"},
    "flyai": {
        "compat": "logic",
        "capabilities": ["chat.read", "variables.read", "settings"],
    },
}


def make_zip(files, manifest=None, top="fixture-ext"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = f"{top}/" if top else ""
        zf.writestr(prefix + "manifest.json", json.dumps(manifest or MANIFEST))
        for name, content in files.items():
            zf.writestr(prefix + name, content)
    return buf.getvalue()


def make_service(tmp_path):
    cfg = SimpleNamespace(max_total_bytes=20 * 1024 * 1024, max_files=500)
    return ExtensionService(tmp_path, cfg, user_root=tmp_path)


def test_manifest_validation():
    m = parse_manifest(MANIFEST, fallback_name="dir-name")
    assert m.name == "dir-name"
    assert m.compat == "logic"
    assert m.capabilities == ["chat.read", "variables.read", "settings"]
    assert m.hooks["activate"] == "init"

    with pytest.raises(ManifestError):
        parse_manifest({"js": "index.js"}, fallback_name="x")
    with pytest.raises(ManifestError):
        parse_manifest({**MANIFEST, "js": "../evil.js"}, fallback_name="x")
    with pytest.raises(ManifestError):
        parse_manifest({**MANIFEST, "version": "not-a-version"}, fallback_name="x")
    with pytest.raises(ManifestError):
        parse_manifest({**MANIFEST, "hooks": {"activate": "1bad()"}}, fallback_name="x")


def test_scanner_reports_missing_symbols(tmp_path):
    m = parse_manifest(MANIFEST, fallback_name="fixture-ext")
    files = tmp_path / "files"
    files.mkdir()
    (files / "index.js").write_text(
        "import { eventSource, event_types } from '../../../script.js';\n"
        "import { clamp, notARealFunction } from '../../../scripts/utils.js';\n"
        "import { SlashCommandParser } from '../../../scripts/slash-commands/SlashCommandParser.js';\n",
        encoding="utf-8",
    )
    report = scan_extension(files, m)
    missing = {(item["module"], item["symbol"]) for item in report.missing_symbols}
    assert ("scripts/utils.js", "notARealFunction") in missing
    assert ("script.js", "eventSource") not in missing
    assert "script.js" in {item["path"] for item in report.modules}


def test_install_list_enable_settings_and_snapshot(tmp_path):
    svc = make_service(tmp_path)
    data = make_zip({
        "index.js": "import { eventSource } from '../../../script.js'; console.log(eventSource);",
        "style.css": ".x{}",
    })
    result = svc.install(data, fallback_name="fixture-ext")
    assert result.manifest.name == "fixture-ext"
    assert result.report.level in ("L1", "L2")
    assert svc.store.files_dir("fixture-ext").joinpath("index.js").is_file()

    items = svc.list_for("admin")
    assert len(items) == 1 and items[0]["display_name"] == "Logic Fixture"
    assert items[0]["enabled"] is False

    svc.set_enabled("admin", "fixture-ext", True)
    snap = svc.host_state("admin", "fixture-ext")
    assert snap["baseUrl"] == "/scripts/extensions/third-party/fixture-ext/"
    assert snap["js"] == "index.js"
    assert "settings" in snap["capabilities"]

    svc.put_settings("admin", "fixture-ext", {"token": "x"})
    assert svc.get_settings("admin", "fixture-ext") == {"token": "x"}
    enabled = svc.enabled_for("admin")
    assert [e["name"] for e in enabled] == ["fixture-ext"]


def test_install_rejects_zip_slip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST))
        zf.writestr("../evil.js", "x")
    with pytest.raises(InstallError):
        install_zip(ExtensionStore(tmp_path / "ext"), buf.getvalue(), "x", InstallLimits())


def test_install_rejects_symlink(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(MANIFEST))
        info = zipfile.ZipInfo("link.js")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "target")
    with pytest.raises(InstallError):
        install_zip(ExtensionStore(tmp_path / "ext"), buf.getvalue(), "x", InstallLimits())


def test_update_keeps_user_settings(tmp_path):
    svc = make_service(tmp_path)
    svc.install(make_zip({"index.js": "export const v = 1;"}), "fixture-ext")
    svc.set_enabled("admin", "fixture-ext", True)
    svc.put_settings("admin", "fixture-ext", {"keep": True})

    updated = dict(MANIFEST, version="1.3.0")
    svc.install(make_zip({"index.js": "export const v = 2;"}, manifest=updated), "fixture-ext", update=True)
    assert svc.detail("admin", "fixture-ext")["manifest"]["version"] == "1.3.0"
    assert svc.get_settings("admin", "fixture-ext") == {"keep": True}
    assert svc.store.files_dir("fixture-ext").joinpath("index.js").read_text() == "export const v = 2;"


def test_duplicate_install_rejected(tmp_path):
    svc = make_service(tmp_path)
    data = make_zip({"index.js": "export const v = 1;"})
    svc.install(data, "fixture-ext")
    with pytest.raises(InstallError):
        svc.install(data, "fixture-ext")


def test_static_path_validation():
    from flyai.api.routes_extensions import _safe_asset_path
    from fastapi import HTTPException

    assert _safe_asset_path("templates/a.html") == ["templates", "a.html"]
    with pytest.raises(HTTPException):
        _safe_asset_path("../secret")
    with pytest.raises(HTTPException):
        _safe_asset_path("a/../../secret")


def test_host_modules_contract():
    # 与前端模块注册表保持同源：关键符号必须存在
    assert "eventSource" in HOST_MODULES["script.js"]
    assert "event_types" in HOST_MODULES["script.js"]
    assert "setExtensionPrompt" in HOST_MODULES["script.js"]
    assert "SlashCommandParser" in HOST_MODULES["scripts/slash-commands/SlashCommandParser.js"]
