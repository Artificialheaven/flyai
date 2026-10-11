"""Galgame 专用工具：背景/立绘生成、清单、场景切换、阻塞选项。"""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.galgame.assets import GalgameAssetStore
from flyai.galgame.choices import GalgameChoiceManager
from flyai.lore.models import Character
from flyai.tools import galgame_tools
from flyai.tools.base import ToolContext
from flyai.tools.galgame_tools import (
    GalgameChoiceTool,
    GalgameComposeMusicTool,
    GalgameDrawBackgroundTool,
    GalgameDrawSpriteTool,
    GalgameListAssetsTool,
    GalgameSetSceneTool,
    _select_sprite_refs,
)

_PNG = b"\x89PNG\r\n\x1a\nFAKE-PNG"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _ArkStub:
    def __init__(self):
        self.calls = []

    async def __call__(self, model_cfg, prompt, size, ref_images=None):
        self.calls.append({"prompt": prompt, "size": size, "ref_images": ref_images})
        return _PNG, ".png", "image/png"


def _ctx(tmp_path, state=None, **extra):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    services = {
        "upload_dir": upload_dir,
        "upload_url_base": "/uploads/u1",
        "image_models": [SimpleNamespace(id="m1", api_key="k",
                                         model="seedream", base_url="http://x")],
        "image_model_id": "",
        "galgame_assets": GalgameAssetStore(upload_dir, "/uploads/u1"),
        "galgame_char_id": "cid1",
        "galgame_sid": "sid1",
        "galgame_state": state if state is not None else {},
        "galgame_choice_manager": GalgameChoiceManager(),
        "uid": "u1",
    }
    services.update(extra)
    return ToolContext(services=services, run_id="run1"), services


@pytest.fixture
def ark_stub(monkeypatch):
    stub = _ArkStub()
    monkeypatch.setattr(galgame_tools, "ark_generate", stub)
    return stub


# ---- 背景 ----

def test_draw_background_first_auto_sets_scene(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    out = _run(GalgameDrawBackgroundTool().run(
        {"description": "黄昏的教室"}, ctx))
    assert out.ok
    assert "[背景已生成]" in out.output
    state = services["galgame_state"]
    assert state["background"]["url"].startswith("/uploads/u1/galgame/backgrounds/sid1/")
    call = ark_stub.calls[0]
    assert call["ref_images"] is None
    assert "横版" in call["prompt"] and "无人物" in call["prompt"]


def test_draw_background_second_not_auto(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path, state={
        "background": {"id": "old", "url": "/old.png"}})
    out = _run(GalgameDrawBackgroundTool().run(
        {"description": "夜晚的天台"}, ctx))
    assert out.ok
    state = services["galgame_state"]
    assert state["background"]["id"] == "old"  # 未被替换


def test_draw_background_empty_desc(tmp_path, ark_stub):
    ctx, _ = _ctx(tmp_path)
    out = _run(GalgameDrawBackgroundTool().run({"description": "  "}, ctx))
    assert not out.ok and "参数错误" in out.output


# ---- 立绘 ----

def test_draw_sprite_uses_avatar_reference(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    avatar = services["upload_dir"] / "avatar.png"
    avatar.write_bytes(_PNG)
    char = Character(id="cid1", name="凛", avatar="/uploads/u1/avatar.png")
    services["galgame_character"] = char

    out = _run(GalgameDrawSpriteTool().run(
        {"description": "校服、微笑"}, ctx))
    assert out.ok
    assert "[立绘已生成]" in out.output
    call = ark_stub.calls[0]
    assert call["ref_images"] is not None
    media, b64 = call["ref_images"][0]
    assert media == "image/png" and b64
    assert "#00FF00" in call["prompt"] and "全身" in call["prompt"]
    state = services["galgame_state"]
    assert state["sprite"]["url"].startswith("/uploads/u1/galgame/sprites/cid1/")


def test_draw_sprite_without_avatar_is_txt2img(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    services["galgame_character"] = Character(id="cid1", name="凛")
    out = _run(GalgameDrawSpriteTool().run(
        {"description": "夏装", "use_avatar": True}, ctx))
    assert out.ok
    assert ark_stub.calls[0]["ref_images"] is None
    assert "文生图" in out.output


def test_draw_sprite_use_avatar_false(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    avatar = services["upload_dir"] / "avatar.png"
    avatar.write_bytes(_PNG)
    services["galgame_character"] = Character(
        id="cid1", name="凛", avatar="/uploads/u1/avatar.png")
    _run(GalgameDrawSpriteTool().run(
        {"description": "制服", "use_avatar": False}, ctx))
    assert ark_stub.calls[0]["ref_images"] is None


# ---- 历史立绘参考（第二张起强制）----

def test_select_sprite_refs_first_drawing(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    assets = services["galgame_assets"]
    recs, auto = _select_sprite_refs({}, assets, "cid1", {})
    assert recs == [] and auto is False


def test_select_sprite_refs_by_id(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    assets = services["galgame_assets"]
    _run(GalgameDrawSpriteTool().run({"description": "sp1"}, ctx))
    _run(GalgameDrawSpriteTool().run({"description": "sp2"}, ctx))
    sprites = assets.list_sprites("cid1")

    recs, auto = _select_sprite_refs(
        {"reference_sprite_ids": [sprites[0].id]}, assets, "cid1", {})
    assert auto is False
    assert [r.id for r in recs] == [sprites[0].id]


def test_select_sprite_refs_auto_picks_latest(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    assets = services["galgame_assets"]
    _run(GalgameDrawSpriteTool().run({"description": "sp1"}, ctx))
    _run(GalgameDrawSpriteTool().run({"description": "sp2"}, ctx))
    sprites = assets.list_sprites("cid1")

    recs, auto = _select_sprite_refs({}, assets, "cid1", {})
    assert auto is True
    assert [r.id for r in recs] == [sprites[-1].id]


def test_select_sprite_refs_prefers_current_scene_sprite(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path, state={})
    assets = services["galgame_assets"]
    _run(GalgameDrawSpriteTool().run({"description": "sp1"}, ctx))  # 自动设当前
    _run(GalgameDrawSpriteTool().run({"description": "sp2"}, ctx))  # 不覆盖
    sprites = assets.list_sprites("cid1")
    state = services["galgame_state"]

    recs, auto = _select_sprite_refs({}, assets, "cid1", state)
    assert auto is True
    assert [r.id for r in recs] == [sprites[0].id]  # 取画面上的 sp1


def test_select_sprite_refs_bad_id_falls_back(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    assets = services["galgame_assets"]
    _run(GalgameDrawSpriteTool().run({"description": "sp1"}, ctx))

    recs, auto = _select_sprite_refs(
        {"reference_sprite_ids": ["nope-id"]}, assets, "cid1", {})
    assert auto is True and len(recs) == 1


def test_draw_sprite_second_auto_includes_previous(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    # 首张：无历史，文生图
    _run(GalgameDrawSpriteTool().run({"description": "常服"}, ctx))
    # 第二张：没给 reference_sprite_ids，自动补选第一张
    out = _run(GalgameDrawSpriteTool().run({"description": "制服"}, ctx))
    assert out.ok and "自动补选" in out.output
    call = ark_stub.calls[-1]
    assert call["ref_images"] is not None and len(call["ref_images"]) == 1
    import base64
    media, b64 = call["ref_images"][0]
    assert media == "image/png"
    assert base64.b64decode(b64) == _PNG  # 第一张立绘的字节


def test_draw_sprite_second_explicit_ref_before_avatar(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    avatar_bytes = b"AVATAR-BYTES"
    avatar = services["upload_dir"] / "avatar.png"
    avatar.write_bytes(avatar_bytes)
    services["galgame_character"] = Character(
        id="cid1", name="凛", avatar="/uploads/u1/avatar.png")

    _run(GalgameDrawSpriteTool().run({"description": "常服"}, ctx))
    sp1 = services["galgame_assets"].list_sprites("cid1")[0]
    out = _run(GalgameDrawSpriteTool().run(
        {"description": "冬装", "reference_sprite_ids": [sp1.id]}, ctx))

    assert out.ok and "自动补选" not in out.output
    refs = ark_stub.calls[-1]["ref_images"]
    assert len(refs) == 2  # 历史立绘在前，头像在后
    import base64
    assert base64.b64decode(refs[0][1]) == _PNG
    assert base64.b64decode(refs[1][1]) == avatar_bytes


# ---- 清单 ----

def test_list_assets_empty(tmp_path):
    ctx, _ = _ctx(tmp_path)
    out = _run(GalgameListAssetsTool().run({"kind": "sprites"}, ctx))
    assert "清单为空" in out.output


def test_list_assets_sprites_and_backgrounds(tmp_path, ark_stub):
    ctx, _ = _ctx(tmp_path)
    _run(GalgameDrawBackgroundTool().run({"description": "教室"}, ctx))
    _run(GalgameDrawSpriteTool().run({"description": "常服"}, ctx))
    out = _run(GalgameListAssetsTool().run({"kind": "backgrounds"}, ctx))
    assert "教室" in out.output and "共 1 张" in out.output
    out = _run(GalgameListAssetsTool().run({"kind": "sprites"}, ctx))
    assert "常服" in out.output


def test_list_assets_bad_kind(tmp_path):
    ctx, _ = _ctx(tmp_path)
    out = _run(GalgameListAssetsTool().run({"kind": "nope"}, ctx))
    assert not out.ok


# ---- 场景切换 ----

def test_set_scene_switches_background_and_sprite(tmp_path, ark_stub):
    ctx, services = _ctx(tmp_path)
    bg = _run(GalgameDrawBackgroundTool().run({"description": "bg1"}, ctx))
    sp = _run(GalgameDrawSpriteTool().run({"description": "sp1"}, ctx))
    # 首轮已自动占位；再造第二张，然后手动切过去
    bg2 = _run(GalgameDrawBackgroundTool().run({"description": "bg2"}, ctx))
    bg2_rec = services["galgame_assets"].list_backgrounds("sid1")[-1]
    state = services["galgame_state"]
    old_bg = state["background"]["id"]

    out = _run(GalgameSetSceneTool().run(
        {"background_id": bg2_rec.id}, ctx))
    assert out.ok and "[场景切换]" in out.output
    assert state["background"]["id"] == bg2_rec.id != old_bg
    assert state["sprite"]["id"]  # 立绘不动


def test_set_scene_bad_ids(tmp_path):
    ctx, _ = _ctx(tmp_path)
    out = _run(GalgameSetSceneTool().run(
        {"background_id": "nope"}, ctx))
    assert not out.ok and "不存在" in out.output
    out = _run(GalgameSetSceneTool().run({}, ctx))
    assert not out.ok


# ---- 作曲 ----

_MUSIC_SPEC = {
    "description": "日常·温馨·厨房",
    "bpm": 76,
    "bars": 2,
    "tracks": [
        {"program": 0, "notes": [
            {"t": 0, "d": 1, "n": 60}, {"t": 1, "d": 1, "n": 64}]},
        {"drums": True, "notes": [{"t": 0, "d": 0.5, "n": 36}]},
    ],
}


def test_compose_first_auto_sets_music(tmp_path):
    ctx, services = _ctx(tmp_path)
    out = _run(GalgameComposeMusicTool().run(dict(_MUSIC_SPEC), ctx))
    assert out.ok and "[音乐已生成]" in out.output
    music = services["galgame_state"]["music"]
    assert music["url"].startswith("/uploads/u1/galgame/music/sid1/")
    assert music["url"].endswith(".mid")


def test_compose_second_not_auto(tmp_path):
    ctx, services = _ctx(tmp_path, state={
        "music": {"id": "old", "url": "/old.mid"}})
    out = _run(GalgameComposeMusicTool().run(dict(_MUSIC_SPEC), ctx))
    assert out.ok
    assert services["galgame_state"]["music"]["id"] == "old"


def test_compose_bad_structure_returns_param_error(tmp_path):
    ctx, _ = _ctx(tmp_path)
    bad = dict(_MUSIC_SPEC)
    bad["tracks"] = [{"program": 0,
                      "notes": [{"t": 0, "d": 1, "n": 60} for _ in range(601)]}]
    out = _run(GalgameComposeMusicTool().run(bad, ctx))
    assert not out.ok and "参数错误" in out.output


def test_compose_missing_description(tmp_path):
    ctx, _ = _ctx(tmp_path)
    bad = dict(_MUSIC_SPEC)
    bad["description"] = "  "
    out = _run(GalgameComposeMusicTool().run(bad, ctx))
    assert not out.ok and "参数错误" in out.output


def test_list_assets_music(tmp_path):
    ctx, _ = _ctx(tmp_path)
    _run(GalgameComposeMusicTool().run(dict(_MUSIC_SPEC), ctx))
    out = _run(GalgameListAssetsTool().run({"kind": "music"}, ctx))
    assert "日常·温馨·厨房" in out.output and "共 1 条" in out.output


def test_set_scene_switches_music(tmp_path):
    ctx, services = _ctx(tmp_path, state={
        "music": {"id": "old", "url": "/old.mid"}})
    _run(GalgameComposeMusicTool().run(dict(_MUSIC_SPEC), ctx))
    rec = services["galgame_assets"].list_music("sid1")[-1]
    out = _run(GalgameSetSceneTool().run({"music_id": rec.id}, ctx))
    assert out.ok and "[场景切换]" in out.output
    assert services["galgame_state"]["music"]["id"] == rec.id


def test_set_scene_bad_music_id(tmp_path):
    ctx, _ = _ctx(tmp_path)
    out = _run(GalgameSetSceneTool().run({"music_id": "nope"}, ctx))
    assert not out.ok and "不存在" in out.output


# ---- 选项 ----

def test_choice_blocks_until_resolved(tmp_path):
    ctx, services = _ctx(tmp_path)
    manager = services["galgame_choice_manager"]

    async def scenario():
        task = asyncio.ensure_future(GalgameChoiceTool().run(
            {"options": ["进门", "离开", "再想想"]}, ctx))
        await asyncio.sleep(0)  # 让工具注册 future 并挂起
        assert manager.resolve("run1", "u1", "进门")
        return await task

    out = _run(scenario())
    assert out.ok and "进门" in out.output


def test_choice_too_few_options(tmp_path):
    ctx, _ = _ctx(tmp_path)
    out = _run(GalgameChoiceTool().run({"options": ["仅一个"]}, ctx))
    assert not out.ok


def test_choice_persists_session_before_blocking(tmp_path):
    # 回归：阻塞等选项前必须先落盘，否则刷新后 AI 回复全部丢失
    class _FakeStore:
        def __init__(self):
            self.saved = []

        def save(self, sess):
            self.saved.append(sess)

    fake_store = _FakeStore()
    sess = SimpleNamespace(id="sid1")
    ctx, services = _ctx(tmp_path,
                         galgame_store=fake_store, galgame_session=sess)
    manager = services["galgame_choice_manager"]

    async def scenario():
        task = asyncio.ensure_future(GalgameChoiceTool().run(
            {"options": ["进门", "离开"]}, ctx))
        await asyncio.sleep(0)  # 注册 future 并挂起
        assert fake_store.saved == [sess]  # 挂起前已保存
        manager.resolve("run1", "u1", "进门")
        return await task

    out = _run(scenario())
    assert out.ok


# ---- 上下文缺失 ----

def test_tools_without_galgame_context(tmp_path, ark_stub):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    ctx = ToolContext(services={
        "upload_dir": upload_dir, "upload_url_base": "/uploads/u1",
        "image_models": [SimpleNamespace(id="m1", api_key="k")]})
    out = _run(GalgameDrawBackgroundTool().run(
        {"description": "x"}, ctx))
    assert not out.ok and "galgame" in out.output


def test_draw_background_no_model(tmp_path, ark_stub):
    ctx, _ = _ctx(tmp_path, image_models=[])
    out = _run(GalgameDrawBackgroundTool().run(
        {"description": "x"}, ctx))
    assert not out.ok and "生图模型" in out.output
