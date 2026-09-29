"""配置接口：key 遮蔽显示、掩码串兑现、按权限过滤、自建模型 CRUD。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi import HTTPException

from flyai.api.routes_config import (
    ConfigPayload,
    ImageModelPayload,
    ProviderPayload,
    SharePayload,
    activate_provider,
    create_image_model,
    create_provider_model,
    delete_image_model,
    delete_provider_model,
    get_config,
    put_config,
    share_image_model,
    share_provider_model,
    update_image_model,
    update_provider_model,
)
from flyai.config import (
    AppConfig,
    EmbeddingsConfig,
    ImageModelConfig,
    ProviderConfig,
    load_config,
    mask_api_key,
)
from flyai.user import ADMIN_UID

EMB_KEY = "sk-emb-secret-key-123"
CHAT_KEY = "sk-chat-secret-key-456"
IMG_KEY = "ark-image-secret-key-789"


class FakeUserStore:
    def __init__(self):
        self.users = {}

    def add(self, uid, name=None, level=0):
        u = SimpleNamespace(id=uid, name=name or uid, level=level)
        self.users[uid] = u
        return u

    def get_by_id(self, uid):
        return self.users.get(uid)


def _mkstate(tmp_path):
    cfg = AppConfig()
    cfg.providers = [
        ProviderConfig(id="deepseek", base_url="https://api.deepseek.com/v1",
                       api_key=CHAT_KEY, model="deepseek-chat",
                       shared=True),
        ProviderConfig(id="gated", base_url="https://api.gated.com/v1",
                       api_key="gated-secret", model="gated-model",
                       shared=True, min_level=50),
    ]
    cfg.image_models = [
        ImageModelConfig(id="img-open", name="开放生图", api_key=IMG_KEY,
                         model="seedream", shared=True, min_level=0),
        ImageModelConfig(id="img-gated", name="等级生图", api_key="img-gated-key",
                         model="seedream-pro", shared=True, min_level=80),
    ]
    cfg.embeddings = EmbeddingsConfig(
        base_url="https://emb.example/v1", api_key=EMB_KEY,
        model="text-embedding-3-small", top_k=4,
    )
    return SimpleNamespace(
        config=cfg,
        config_path=tmp_path / "config.json",
        user_store=FakeUserStore(),
        _scopes={},
        reload_providers=lambda: None,
    )


def _req(state, uid=ADMIN_UID):
    return SimpleNamespace(
        headers={"x-user-id": uid},
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )


# ---- 管理员视图与全局保存 ----

def test_masked_config_masks_embeddings_key(tmp_path):
    state = _mkstate(tmp_path)
    view = get_config(_req(state))
    emb = view["embeddings"]
    assert emb["api_key"] == mask_api_key(EMB_KEY)
    assert emb["api_key"] != EMB_KEY
    assert emb["base_url"] == "https://emb.example/v1"
    assert emb["model"] == "text-embedding-3-small"
    assert emb["top_k"] == 4
    # 聊天提供商的 key 也被屏蔽
    assert view["providers"][0]["api_key"] == mask_api_key(CHAT_KEY)


def test_admin_sees_every_model(tmp_path):
    state = _mkstate(tmp_path)
    view = get_config(_req(state))
    assert {p["id"] for p in view["providers"]} == {"deepseek", "gated"}
    assert {m["id"] for m in view["image_models"]} == {"img-open", "img-gated"}


def test_put_config_credits_masked_embeddings_key(tmp_path):
    state = _mkstate(tmp_path)
    payload = ConfigPayload(embeddings={
        "base_url": "https://new-emb.example/v1",
        "api_key": mask_api_key(EMB_KEY),  # 用户没改 key，仍发回掩码串
        "model": "embed-v2",
        "top_k": 6,
    })
    put_config(payload, _req(state))
    assert state.config.embeddings.base_url == "https://new-emb.example/v1"
    assert state.config.embeddings.model == "embed-v2"
    assert state.config.embeddings.top_k == 6
    assert state.config.embeddings.api_key == EMB_KEY  # 掩码串被兑现回真实 key
    # 已写入磁盘
    on_disk = load_config(tmp_path / "config.json")
    assert on_disk.embeddings.api_key == EMB_KEY


def test_put_config_new_key_overwrites(tmp_path):
    state = _mkstate(tmp_path)
    payload = ConfigPayload(embeddings={
        "base_url": "https://emb.example/v1",
        "api_key": "sk-brand-new-token",
        "model": "text-embedding-3-small",
        "top_k": 4,
    })
    put_config(payload, _req(state))
    assert state.config.embeddings.api_key == "sk-brand-new-token"


def test_put_image_models_credits_masked_key(tmp_path):
    state = _mkstate(tmp_path)
    payload = ConfigPayload(image_models=[{
        "id": "img-open",
        "name": "开放生图",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": mask_api_key(IMG_KEY),  # 用户没改 key
        "model": "seedream-2",
        "owner": "admin",
        "shared": True,
        "min_level": 0,
    }])
    put_config(payload, _req(state))
    models = state.config.image_models
    assert len(models) == 1
    assert models[0].api_key == IMG_KEY  # 掩码串兑现回真实 key
    assert models[0].model == "seedream-2"
    assert load_config(tmp_path / "config.json").image_models[0].api_key == IMG_KEY


def test_pixiv_section_roundtrip(tmp_path):
    state = _mkstate(tmp_path)
    payload = ConfigPayload(pixiv={
        "enabled": True,
        "proxy": "http://127.0.0.1:7897",
        "r18_allowed": True,
        "candidate_num": 20,
    })
    put_config(payload, _req(state))
    px = state.config.pixiv
    assert px.proxy == "http://127.0.0.1:7897"
    assert px.r18_allowed is True
    assert px.candidate_num == 20
    # api_base 未传时保留旧值，不被默认值覆盖
    assert px.api_base == "https://api.lolicon.app/setu/v2"
    on_disk = load_config(tmp_path / "config.json")
    assert on_disk.pixiv.r18_allowed is True


def test_masked_config_includes_pixiv(tmp_path):
    state = _mkstate(tmp_path)
    view = get_config(_req(state))
    assert view["pixiv"]["r18_allowed"] is False
    assert view["pixiv"]["candidate_num"] == 15


# ---- 普通用户：过滤 / 禁改全局 ----

def test_user_low_level_get_filtered(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    view = get_config(_req(state, "u10"))
    pids = {p["id"] for p in view["providers"]}
    assert pids == {"deepseek"}  # gated 完全消失
    mids = {m["id"] for m in view["image_models"]}
    assert mids == {"img-open"}  # img-gated 完全消失
    # 可见条目 key 仍是掩码
    assert view["providers"][0]["api_key"] == mask_api_key(CHAT_KEY)


def test_user_boundary_level_sees_gated(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u50", level=50)
    view = get_config(_req(state, "u50"))
    assert {p["id"] for p in view["providers"]} == {"deepseek", "gated"}
    # 生图模型门槛 80，仍不可见
    assert {m["id"] for m in view["image_models"]} == {"img-open"}

    state.user_store.add("u80", level=80)
    view80 = get_config(_req(state, "u80"))
    assert {m["id"] for m in view80["image_models"]} == {"img-open", "img-gated"}


def test_put_config_forbidden_for_user(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        put_config(ConfigPayload(), _req(state, "u10"))
    assert ei.value.status_code == 403


def test_unknown_uid_rejected(tmp_path):
    state = _mkstate(tmp_path)
    with pytest.raises(HTTPException) as ei:
        get_config(_req(state, "ghost"))
    assert ei.value.status_code == 401


# ---- 普通用户：自建 LLM CRUD ----

def test_user_provider_crud(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    created = create_provider_model(ProviderPayload(
        base_url="https://mine.example/v1", api_key="my-key",
        model="my-model", vision=True,
    ), _req(state, "u10"))
    assert created["owner"] == "u10"
    assert created["shared"] is False
    assert created["api_key"] == mask_api_key("my-key")
    item = next(p for p in state.config.providers if p.id == created["id"])
    assert item.api_key == "my-key" and item.vision is True

    # 更新：掩码 key 兑现
    updated = update_provider_model(item.id, ProviderPayload(
        base_url="https://mine.example/v1", api_key=mask_api_key("my-key"),
        model="my-model-2",
    ), _req(state, "u10"))
    assert updated["model"] == "my-model-2"
    assert next(p for p in state.config.providers if p.id == item.id).api_key == "my-key"

    # 删除
    assert delete_provider_model(item.id, _req(state, "u10"))["ok"]
    assert all(p.id != item.id for p in state.config.providers)


def test_user_cannot_touch_other_users_model(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    state.user_store.add("u20", level=20)
    created = create_provider_model(ProviderPayload(
        base_url="https://mine.example/v1", api_key="k", model="m",
    ), _req(state, "u10"))
    with pytest.raises(HTTPException) as ei:
        update_provider_model(created["id"], ProviderPayload(
            base_url="https://mine.example/v1", api_key="k", model="m2",
        ), _req(state, "u20"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        delete_provider_model(created["id"], _req(state, "u20"))
    assert ei.value.status_code == 403


def test_share_only_admin(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        share_provider_model("deepseek", SharePayload(shared=True, min_level=10),
                             _req(state, "u10"))
    assert ei.value.status_code == 403

    out = share_provider_model("deepseek",
                               SharePayload(shared=True, min_level=30),
                               _req(state))
    assert out["shared"] is True and out["min_level"] == 30
    item = next(p for p in state.config.providers if p.id == "deepseek")
    assert item.shared is True and item.min_level == 30


def test_share_clamps_level(tmp_path):
    state = _mkstate(tmp_path)
    out = share_image_model("img-open", SharePayload(shared=True, min_level=999),
                            _req(state))
    assert out["min_level"] == 255


# ---- 普通用户：自建行生图模型 CRUD ----

def test_user_image_model_crud(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    created = create_image_model(ImageModelPayload(
        name="我的生图", base_url="https://mine.ark/v3",
        api_key="my-img-key", model="seedream-x",
    ), _req(state, "u10"))
    assert created["owner"] == "u10" and created["shared"] is False
    mid = created["id"]

    updated = update_image_model(mid, ImageModelPayload(
        name="我的生图2", base_url="https://mine.ark/v3",
        api_key=mask_api_key("my-img-key"), model="seedream-y",
    ), _req(state, "u10"))
    assert updated["name"] == "我的生图2" and updated["model"] == "seedream-y"
    item = next(m for m in state.config.image_models if m.id == mid)
    assert item.api_key == "my-img-key"

    assert delete_image_model(mid, _req(state, "u10"))["ok"]
    assert all(m.id != mid for m in state.config.image_models)


def test_user_share_image_forbidden(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        share_image_model("img-open", SharePayload(shared=True, min_level=1),
                          _req(state, "u10"))
    assert ei.value.status_code == 403


# ---- 普通用户激活（仅校验可见性）----

def test_activate_visibility_check(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    assert activate_provider("deepseek", _req(state, "u10"))["ok"]
    with pytest.raises(HTTPException) as ei:
        activate_provider("gated", _req(state, "u10"))
    assert ei.value.status_code == 404
