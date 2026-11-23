"""角色卡 JSON 导出/导入：头像内嵌 + 保存保真字段。"""

import base64
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.api.routes_lore import (
    CharacterPayload,
    LoreImportPayload,
    export_character,
    import_character,
    update_character,
)
from flyai.lore.models import Character
from flyai.lore.store import LoreStore

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


def _env(tmp_path, with_upload=True):
    base = tmp_path / "data"
    lore = LoreStore(base / "u_admin" / "lore")
    upload_dir = base / "uploads" / "admin"
    if with_upload:
        upload_dir.mkdir(parents=True, exist_ok=True)
    sc = SimpleNamespace(uid="admin", level=256, lore=lore)
    if with_upload:
        sc.upload_dir = upload_dir
    state = SimpleNamespace()
    state.user = lambda request: sc
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)))
    return sc, request, upload_dir


def _saved_character(sc, avatar=""):
    c = Character(id="c1", name="初", author="admin", avatar=avatar)
    sc.lore.save_character(c)
    return c


# ---- 导出 ----

def test_export_embeds_avatar_base64(tmp_path):
    sc, request, upload_dir = _env(tmp_path)
    (upload_dir / "avatar.png").write_bytes(PNG)
    _saved_character(sc, "/uploads/admin/avatar.png")

    resp = export_character("c1", request)
    body = json.loads(resp.body)
    image = body["data"]["avatar_image"]
    assert image["media_type"] == "image/png"
    assert base64.b64decode(image["data"]) == PNG
    # 原有字段不变
    assert body["type"] == "flyai-character" and body["version"] == 1
    assert body["data"]["avatar"] == "/uploads/admin/avatar.png"


def test_export_skips_embed_without_readable_avatar(tmp_path):
    sc, request, upload_dir = _env(tmp_path)
    c = _saved_character(sc, "/uploads/admin/missing.png")

    def reload():
        return sc.lore.get_character("c1")  # 每次从磁盘读新对象

    # 1) 文件不存在
    assert "avatar_image" not in json.loads(export_character("c1", request).body)["data"]

    # 2) 穿越型 URL
    c.avatar = "/uploads/../avatar.png"
    sc.lore.save_character(c)
    (upload_dir.parent / "avatar.png").write_bytes(PNG)
    assert "avatar_image" not in json.loads(export_character("c1", request).body)["data"]

    # 3) 非上传目录 URL
    c = reload()
    c.avatar = "https://example.com/a.png"
    sc.lore.save_character(c)
    assert "avatar_image" not in json.loads(export_character("c1", request).body)["data"]


def test_export_without_upload_dir_has_no_avatar_image(tmp_path):
    sc, request, _ = _env(tmp_path, with_upload=False)
    _saved_character(sc, "/uploads/admin/avatar.png")
    body = json.loads(export_character("c1", request).body)
    assert "avatar_image" not in body["data"]


# ---- 导入 ----

def _import_payload(data):
    return LoreImportPayload(type="flyai-character", data=data)


def test_import_materializes_embedded_avatar(tmp_path):
    sc, request, upload_dir = _env(tmp_path)
    out = import_character(_import_payload({
        "name": "新卡",
        "avatar": "/uploads/someone/old.png",
        "author": "别人",
        "public": True,
        "avatar_image": {
            "media_type": "image/png",
            "data": base64.b64encode(PNG).decode("ascii"),
        },
    }), request)

    assert out["name"] == "新卡"
    assert out["author"] == "admin"  # 服务端强制
    # 导入响应带整卡 md5：前端上传后可直接写本地缓存
    assert len(out["md5"]) == 32
    assert out["md5"] == sc.lore.character_digest(out["id"])
    assert out["public"] is False
    assert "avatar_image" not in out
    avatar = out["avatar"]
    assert avatar.startswith("/uploads/admin/")
    assert avatar.endswith(".png")
    fname = avatar.rsplit("/", 1)[1]
    assert (upload_dir / fname).read_bytes() == PNG


def test_import_bad_avatar_blob_still_imports(tmp_path):
    sc, request, _ = _env(tmp_path)
    out = import_character(_import_payload({
        "name": "坏图",
        "avatar": "/uploads/someone/old.png",
        "avatar_image": {"media_type": "image/png", "data": "!!!not-base64!!!"},
    }), request)
    assert out["name"] == "坏图"
    # 不能留下指向导出者目录的坏 URL
    assert out["avatar"] == ""


def test_import_without_avatar_image_keeps_legacy_avatar(tmp_path):
    sc, request, _ = _env(tmp_path)
    out = import_character(_import_payload({
        "name": "老卡",
        "avatar": "/uploads/admin/legacy.png",
    }), request)
    assert out["avatar"] == "/uploads/admin/legacy.png"


def test_import_rejects_missing_name(tmp_path):
    sc, request, _ = _env(tmp_path)
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as ei:
        import_character(_import_payload({"name": "  "}), request)
    assert ei.value.status_code == 400


# ---- PUT 保真 ----

def _payload(**kw):
    base = dict(id="c1", name="初", avatar="", intro="", first_mes="")
    base.update(kw)
    return CharacterPayload(**base)


def test_update_character_preserves_fidelity_fields_when_omitted(tmp_path):
    sc, request, _ = _env(tmp_path)
    c = _saved_character(sc)
    c.regex_scripts = [{"findRegex": "a", "replaceString": "b"}]
    c.extensions = {"depth_prompt": {"prompt": "深度"}}
    c.alternate_greetings = ["开场 A"]
    c.assets = [{"type": "icon", "uri": "ccdefault:"}]
    sc.lore.save_character(c)

    out = update_character("c1", _payload(name="改名"), request)
    assert out["name"] == "改名"
    assert out["regex_scripts"] == [{"findRegex": "a", "replaceString": "b"}]
    assert out["extensions"] == {"depth_prompt": {"prompt": "深度"}}
    assert out["alternate_greetings"] == ["开场 A"]
    assert out["assets"] == [{"type": "icon", "uri": "ccdefault:"}]


def test_update_character_clears_fidelity_fields_when_empty(tmp_path):
    sc, request, _ = _env(tmp_path)
    c = _saved_character(sc)
    c.regex_scripts = [{"findRegex": "a", "replaceString": "b"}]
    c.extensions = {"world": "和睦小镇"}
    c.alternate_greetings = ["开场 A"]
    c.assets = [{"type": "icon", "uri": "ccdefault:"}]
    sc.lore.save_character(c)

    out = update_character(
        "c1",
        _payload(regex_scripts=[], extensions={}, alternate_greetings=[], assets=[]),
        request,
    )
    assert out["regex_scripts"] == []
    assert out["extensions"] == {}
    assert out["alternate_greetings"] == []
    assert out["assets"] == []
