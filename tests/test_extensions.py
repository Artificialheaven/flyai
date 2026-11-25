"""酒馆扩展管理器：manifest、扫描、安装安全、启停与快照。"""

import io
import json
import zipfile
from pathlib import Path
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


def _zip_fixture(name: str) -> bytes:
    root = Path(__file__).parent / "fixtures" / "extensions" / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, f"{name}/{path.relative_to(root).as_posix()}")
    return buf.getvalue()


@pytest.mark.parametrize("fixture,command", [
    ("logic-events", None),
    ("logic-slash", "hello"),
    ("logic-settings", None),
    ("logic-panel", None),
])
def test_fixture_extensions_are_l1_loadable(tmp_path, fixture, command):
    svc = make_service(tmp_path)
    result = svc.install(_zip_fixture(fixture), fallback_name=fixture)
    assert result.report.level == "L1"
    assert result.report.missing_modules == []
    assert result.report.missing_symbols == []
    svc.set_enabled("admin", result.manifest.name, True)
    enabled = svc.enabled_for("admin")
    assert [item["name"] for item in enabled] == [result.manifest.name]
    if command:
        source = svc.store.files_dir(result.manifest.name).joinpath("index.js").read_text()
        assert f"name: '{command}'" in source
    if fixture == "logic-panel":
        panel = svc.panel_state(result.manifest.name)
        assert panel and panel["type"] == "schema"
        assert panel["schema"]["properties"]["greeting"]["type"] == "string"


def test_dom_extension_is_l3_and_not_loadable(tmp_path):
    svc = make_service(tmp_path)
    manifest = {
        "display_name": "DOM Fixture",
        "version": "1.0.0",
        "js": "index.js",
        "flyai": {"compat": "logic"},
    }
    data = make_zip({
        "index.js": "import { dragElement } from '../../../../scripts/RossAscends-mods.js';\ndragElement();",
    }, manifest=manifest, top="dom-fixture")
    result = svc.install(data, "dom-fixture")
    assert result.report.level == "L3"
    with pytest.raises(InstallError):
        svc.set_enabled("admin", "dom-fixture", True)


def test_popup_extension_is_l2_but_loadable(tmp_path):
    svc = make_service(tmp_path)
    manifest = {
        "display_name": "Popup Fixture",
        "version": "1.0.0",
        "js": "index.js",
        "flyai": {"compat": "logic", "capabilities": ["ui"]},
    }
    data = make_zip({
        "index.js": "import { callGenericPopup, POPUP_TYPE } from '../../../../scripts/popup.js';\n",
    }, manifest=manifest, top="popup-fixture")
    result = svc.install(data, "popup-fixture")
    assert result.report.level == "L2"
    svc.set_enabled("admin", "popup-fixture", True)
    assert svc.enabled_for("admin")[0]["name"] == "popup-fixture"


def test_schema_panel_and_missing(tmp_path):
    svc = make_service(tmp_path)
    manifest = {
        "display_name": "Panel Fixture",
        "version": "1.0.0",
        "js": "index.js",
        "flyai": {
            "compat": "logic",
            "capabilities": ["settings"],
            "panel": {"type": "schema", "schema": "panel.schema.json"},
        },
    }
    schema = {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "title": "Enabled", "default": True},
            "limit": {"type": "integer", "title": "Limit", "minimum": 1, "maximum": 100},
            "mode": {"type": "string", "enum": ["a", "b"], "title": "Mode"},
        },
    }
    data = make_zip({
        "index.js": "export {};",
        "panel.schema.json": json.dumps(schema),
    }, manifest=manifest, top="panel-fixture")
    svc.install(data, "panel-fixture")
    panel = svc.panel_state("panel-fixture")
    assert panel["type"] == "schema"
    assert panel["schema"]["properties"]["limit"]["type"] == "integer"
    assert panel["url"].endswith("/panel.schema.json")


def test_panel_rejects_traversal(tmp_path):
    svc = make_service(tmp_path)
    manifest = {
        "display_name": "Bad Panel",
        "version": "1.0.0",
        "js": "index.js",
        "flyai": {"compat": "logic", "panel": {"type": "iframe", "html": "../secret.html"}},
    }
    data = make_zip({"index.js": "export {};"}, manifest=manifest, top="bad-panel")
    svc.install(data, "bad-panel")
    # manifest 解析阶段把非法路径清空，panel_state 因此不可用
    assert svc.panel_state("bad-panel") is None


def test_store_migrates_legacy_json(tmp_path):
    root = tmp_path / "extensions"
    root.mkdir()
    (root / "registry.json").write_text(json.dumps({
        "version": 1,
        "extensions": {"demo": {
            "name": "demo", "display_name": "Demo", "version": "1.0.0",
            "sha256": "x", "installed_at": "2024-01-01T00:00:00",
            "source": "zip", "compat": "L1", "manifest": {},
        }},
    }), "utf-8")
    state = tmp_path / "admin" / "extensions" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps(
        {"enabled": {"demo": True}, "order": {}, "settings": {}}), "utf-8")

    store = ExtensionStore(root, user_root=tmp_path)
    assert [r.name for r in store.records()] == ["demo"]
    assert store.user_state("admin")["enabled"] == {"demo": True}
    assert (root / "extensions.db").is_file()
    # 旧 JSON 改名备份，不再留在原路径
    assert not (root / "registry.json").exists()
    assert list(root.glob("registry.json.bak-*"))
    assert not state.exists()
    assert list(state.parent.glob("state.json.bak-*"))
    # 重复打开不重复导入
    again = ExtensionStore(root, user_root=tmp_path)
    assert [r.name for r in again.records()] == ["demo"]
    # 新写入只落 DB
    again.set_enabled("admin", "demo", False)
    assert again.user_state("admin")["enabled"]["demo"] is False
    assert not (root / "registry.json").exists()


def test_store_tolerates_bad_legacy_json(tmp_path):
    root = tmp_path / "extensions"
    root.mkdir()
    (root / "registry.json").write_text("{not json", "utf-8")
    store = ExtensionStore(root, user_root=tmp_path)
    # 坏 JSON 跳过、不炸、不删原文件
    assert store.records() == []
    assert (root / "registry.json").exists()
    assert (root / "extensions.db").is_file()
