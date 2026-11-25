"""配置接口：key 遮蔽显示、掩码串兑现、按权限过滤、管理员模型 CRUD。"""

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
    DeepSeekSettings,
    EmbeddingsConfig,
    ImageModelConfig,
    ProviderConfig,
    TtsSettings,
    load_config,
    mask_api_key,
)
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.user import ADMIN_LEVEL, ADMIN_UID

EMB_KEY = "sk-emb-secret-key-123"
CHAT_KEY = "sk-chat-secret-key-456"
IMG_KEY = "ark-image-secret-key-789"


class FakeMailer:
    def __init__(self):
        self.sent = []
        self.error = None

    def send_test(self, cfg, to):
        if self.error is not None:
            raise self.error
        self.sent.append(to)


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
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
        mailer=FakeMailer(),
        reload_providers=lambda: None,
        reload_tools=lambda: None,
    )


def _req(state, uid=ADMIN_UID):
    is_admin = uid == ADMIN_UID
    token = state.auth_sessions.issue(
        uid, uid, "admin" if is_admin else "user", ADMIN_LEVEL if is_admin else 0
    )
    return SimpleNamespace(
        headers={"authorization": "Bearer " + token},
        client=SimpleNamespace(host="127.0.0.1"),
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


def test_browser_config_roundtrip(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(browser={
        "enabled": True,
        "headless": True,
        "proxy": "http://127.0.0.1:7897",
        "max_steps": 20,
        "idle_seconds": 300,
    }), _req(state))
    view = get_config(_req(state))
    br = view["browser"]
    assert br["enabled"] is True
    assert br["proxy"] == "http://127.0.0.1:7897"
    assert br["max_steps"] == 20
    assert br["idle_seconds"] == 300
    # UI 未暴露的字段回落默认值，仍在响应中
    assert br["executable_path"] == ""
    assert br["nav_timeout_ms"] == 20000


def test_admin_sees_every_model(tmp_path):
    state = _mkstate(tmp_path)
    view = get_config(_req(state))
    assert {p["id"] for p in view["providers"]} == {"deepseek", "gated"}


def test_custom_tools_roundtrip_visible_to_all_users(tmp_path):
    state = _mkstate(tmp_path)
    # 默认段随配置下发（不含密钥，普通用户也可见）
    default_view = get_config(_req(state))
    assert default_view["custom_tools"]["enabled"] is False
    put_config(ConfigPayload(custom_tools={
        "enabled": True,
        "code_max_chars": 4000,
        "max_per_scope": 5,
        "validate_timeout": 8,
        "call_timeout": 15,
    }), _req(state))
    view = get_config(_req(state))
    assert view["custom_tools"] == {
        "enabled": True,
        "code_max_chars": 4000,
        "max_per_scope": 5,
        "validate_timeout": 8.0,
        "call_timeout": 15.0,
    }
    state.user_store.add("u1", level=1)
    user_view = get_config(_req(state, "u1"))
    assert user_view["custom_tools"]["enabled"] is True
    assert {m["id"] for m in view["image_models"]} == {"img-open", "img-gated"}


def test_lore_settings_roundtrip_and_clamp(tmp_path):
    state = _mkstate(tmp_path)
    # 默认段随配置下发（不含密钥）
    assert get_config(_req(state))["lore"]["max_card_mb"] == 50
    assert get_config(_req(state))["lore"]["max_card_write_mb"] == 2
    assert get_config(_req(state))["lore"]["max_author_script_mb"] == 5
    put_config(
        ConfigPayload(lore={
            "max_card_mb": 120,
            "max_card_write_mb": 4,
            "max_author_script_mb": 5,
        }),
        _req(state),
    )
    assert state.config.lore.max_card_mb == 120
    assert state.config.lore.max_card_write_mb == 4
    assert state.config.lore.max_author_script_mb == 5
    assert get_config(_req(state))["lore"]["max_card_write_mb"] == 4
    # 已写入磁盘
    assert load_config(tmp_path / "config.json").lore.max_card_mb == 120
    assert load_config(tmp_path / "config.json").lore.max_card_write_mb == 4
    assert load_config(tmp_path / "config.json").lore.max_author_script_mb == 5
    # 越界值按 1–200 / 1–5 收口
    put_config(
        ConfigPayload(lore={
            "max_card_mb": 9999,
            "max_card_write_mb": 999,
            "max_author_script_mb": 999,
        }),
        _req(state),
    )
    assert state.config.lore.max_card_mb == 200
    assert state.config.lore.max_card_write_mb == 5
    assert state.config.lore.max_author_script_mb == 5


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
    # 他人共享模型：只露必要字段，不下发 base_url/归属/api_key/参数
    p = view["providers"][0]
    assert set(p.keys()) == {"id", "model", "group", "vision", "rate", "pricing",
                             "min_level"}
    assert p["model"] == "deepseek-chat"
    m = view["image_models"][0]
    assert set(m.keys()) == {"id", "name", "model", "min_level"}
    # 嵌入配置（含掩码 key）不对普通用户下发
    assert "embeddings" not in view
    # 全局功能段仍下发（作为"跟随默认"的默认值）
    assert view["galgame"]["enabled"] is True




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
    # 无令牌（伪造/过期令牌同理）→ 401
    req = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(flyai=state)),
    )
    with pytest.raises(HTTPException) as ei:
        get_config(req)
    assert ei.value.status_code == 401


# ---- 模型管理：仅管理员可 CRUD ----

def test_user_provider_crud_forbidden(tmp_path):
    """用户自建模型能力已下线：普通用户无法新建/修改/删除任何模型。"""
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        create_provider_model(ProviderPayload(
            base_url="https://mine.example/v1", api_key="my-key", model="my-model",
        ), _req(state, "u10"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        update_provider_model("deepseek", ProviderPayload(model="x"),
                              _req(state, "u10"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        delete_provider_model("deepseek", _req(state, "u10"))
    assert ei.value.status_code == 403


def test_admin_provider_crud(tmp_path):
    state = _mkstate(tmp_path)
    created = create_provider_model(ProviderPayload(
        base_url="https://mine.example/v1", api_key="my-key",
        model="my-model", vision=True,
    ), _req(state))
    assert created["owner"] == ADMIN_UID
    assert created["shared"] is False
    assert created["api_key"] == mask_api_key("my-key")
    item = next(p for p in state.config.providers if p.id == created["id"])
    assert item.api_key == "my-key" and item.vision is True

    # 更新：掩码 key 兑现
    updated = update_provider_model(item.id, ProviderPayload(
        base_url="https://mine.example/v1", api_key=mask_api_key("my-key"),
        model="my-model-2",
    ), _req(state))
    assert updated["model"] == "my-model-2"
    assert next(p for p in state.config.providers if p.id == item.id).api_key == "my-key"

    # 删除
    assert delete_provider_model(item.id, _req(state))["ok"]
    assert all(p.id != item.id for p in state.config.providers)


def test_update_provider_blank_key_keeps_existing(tmp_path):
    """未填 key（空串）不能抹掉已保存的 key——编辑其它字段也走同一路径。"""
    state = _mkstate(tmp_path)
    created = create_provider_model(ProviderPayload(
        base_url="https://mine.example/v1", api_key="my-key", model="my-model",
    ), _req(state))
    update_provider_model(created["id"], ProviderPayload(
        base_url="https://mine.example/v1", api_key="", model="my-model-2",
    ), _req(state))
    item = next(p for p in state.config.providers if p.id == created["id"])
    assert item.api_key == "my-key"
    assert item.model == "my-model-2"


@pytest.mark.parametrize("bad_id", [
    '" autofocus="alert', "a/b", "a b", "<x>", ".x",
])
def test_create_provider_bad_id_rejected(tmp_path, bad_id):
    state = _mkstate(tmp_path)
    with pytest.raises(HTTPException) as ei:
        create_provider_model(ProviderPayload(
            id=bad_id, base_url="https://x", api_key="k", model="m",
        ), _req(state))
    assert ei.value.status_code == 400


def test_create_image_model_bad_id_rejected(tmp_path):
    state = _mkstate(tmp_path)
    with pytest.raises(HTTPException) as ei:
        create_image_model(ImageModelPayload(
            id="a/b", name="x", base_url="https://x", api_key="k", model="m",
        ), _req(state))
    assert ei.value.status_code == 400


def test_user_cannot_touch_other_users_model(tmp_path):
    """旧归属模型（历史上由普通用户创建）现仅管理员能改。"""
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    state.user_store.add("u20", level=20)
    state.config.providers.append(ProviderConfig(
        id="mine", base_url="https://mine.example/v1", api_key="k",
        model="m", owner="u10", shared=False,
    ))
    with pytest.raises(HTTPException) as ei:
        update_provider_model("mine", ProviderPayload(
            base_url="https://mine.example/v1", api_key="k", model="m2",
        ), _req(state, "u20"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        delete_provider_model("mine", _req(state, "u20"))
    assert ei.value.status_code == 403
    # 管理员仍可管理既有条目
    assert update_provider_model("mine", ProviderPayload(model="m3"),
                                 _req(state))["model"] == "m3"
    assert delete_provider_model("mine", _req(state))["ok"]


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


# ---- 生图模型：仅管理员可 CRUD ----

def test_user_image_model_crud_forbidden(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        create_image_model(ImageModelPayload(
            name="我的生图", base_url="https://mine.ark/v3",
            api_key="my-img-key", model="seedream-x",
        ), _req(state, "u10"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        update_image_model("img-open", ImageModelPayload(name="x"),
                           _req(state, "u10"))
    assert ei.value.status_code == 403
    with pytest.raises(HTTPException) as ei:
        delete_image_model("img-open", _req(state, "u10"))
    assert ei.value.status_code == 403


def test_admin_image_model_crud(tmp_path):
    state = _mkstate(tmp_path)
    created = create_image_model(ImageModelPayload(
        name="我的生图", base_url="https://mine.ark/v3",
        api_key="my-img-key", model="seedream-x",
    ), _req(state))
    assert created["owner"] == ADMIN_UID and created["shared"] is False
    mid = created["id"]

    updated = update_image_model(mid, ImageModelPayload(
        name="我的生图2", base_url="https://mine.ark/v3",
        api_key=mask_api_key("my-img-key"), model="seedream-y",
    ), _req(state))
    assert updated["name"] == "我的生图2" and updated["model"] == "seedream-y"
    item = next(m for m in state.config.image_models if m.id == mid)
    assert item.api_key == "my-img-key"

    assert delete_image_model(mid, _req(state))["ok"]
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


def test_user_seat_provider_must_be_visible(tmp_path):
    """群聊座位不能指向对本人不可见的模型（含等级门槛与未共享）。"""
    from flyai.api.routes_room import _apply_member_seat_settings

    state = _mkstate(tmp_path)
    sc = SimpleNamespace(uid="u10", level=10)
    seat = SimpleNamespace(provider_id=None)
    with pytest.raises(HTTPException) as ei:
        _apply_member_seat_settings(state, sc, seat, "gated", None, None)
    assert ei.value.status_code == 403
    _apply_member_seat_settings(state, sc, seat, "deepseek", None, None)
    assert seat.provider_id == "deepseek"
    # 未共享模型即使归属本人也不可用（自建能力已下线）
    state.config.providers.append(ProviderConfig(
        id="mine", base_url="x", model="m", owner="u10", shared=False,
    ))
    with pytest.raises(HTTPException) as ei:
        _apply_member_seat_settings(state, sc, seat, "mine", None, None)
    assert ei.value.status_code == 403


# ---- 保存配置后工具热重建（无需重启）----

def _real_state(tmp_path):
    from flyai.app import AppState

    cfg = AppConfig()
    # 测试不扫描真实插件目录
    cfg.tools.plugin_dirs = []
    return AppState(config=cfg, config_path=tmp_path / "config.json")


def test_tts_key_hot_reload(tmp_path):
    state = _real_state(tmp_path)
    state.reload_tools()
    tool = state.registry.get("text_to_speech")
    assert tool is not None
    assert tool.settings.api_key == ""

    put_config(ConfigPayload(tts={
        "enabled": True,
        "api_key": "speech-real-key",
        "resource_id": "seed-tts-2.0",
        "default_speaker": "S_BcqE5org2",
        "format": "mp3",
        "sample_rate": 24000,
    }), _req(state))

    new_tool = state.registry.get("text_to_speech")
    assert new_tool is not None
    assert new_tool is not tool
    assert new_tool.settings.api_key == "speech-real-key"


def test_tts_disable_hot_unregisters(tmp_path):
    state = _real_state(tmp_path)
    state.reload_tools()
    assert state.registry.get("text_to_speech") is not None

    put_config(ConfigPayload(tts={
        "enabled": False,
        "api_key": "speech-real-key",
        "resource_id": "seed-tts-2.0",
        "default_speaker": "S_BcqE5org2",
        "format": "mp3",
        "sample_rate": 24000,
    }), _req(state))

    assert state.registry.get("text_to_speech") is None


def test_browser_enable_hot_and_lazy_manager(tmp_path):
    """启用浏览器后工具立即可用；管理器在首个请求 get_browser() 时才创建。"""
    state = _real_state(tmp_path)
    state.reload_tools()
    assert state.browser is None

    put_config(ConfigPayload(browser={
        "enabled": True,
        "proxy": "http://127.0.0.1:7897",
    }), _req(state))

    # 保存配置不再立即创建管理器（懒创建）
    assert state.browser is None
    assert state.registry.get("browser_open") is not None
    manager = state.get_browser()
    assert manager is not None
    assert state.get_browser() is manager  # 单例


def test_tts_voices_and_min_level_roundtrip(tmp_path):
    state = _real_state(tmp_path)
    state.reload_tools()

    put_config(ConfigPayload(tts={
        "enabled": True,
        "api_key": "speech-real-key",
        "resource_id": "seed-tts-2.0",
        "voices": [
            {"name": "鲸鱼娘", "id": "S_NkpD5org2"},
            {"name": "  ", "id": " S_OTHER "},
            {"name": "空ID原样保留（仅前端收集时过滤）", "id": ""},
        ],
        "default_speaker": "S_BcqE5org2",
        "min_level": 30,
        "format": "mp3",
        "sample_rate": 24000,
    }), _req(state))

    tts = state.config.tts
    assert len(tts.voices) == 3
    assert tts.voices[0].name == "鲸鱼娘"
    assert tts.voices[0].id == "S_NkpD5org2"
    # strip 生效
    assert tts.voices[1].name == ""
    assert tts.voices[1].id == "S_OTHER"
    assert tts.voices[2].id == ""
    assert tts.min_level == 30

    # 热重建后的工具拿到新设置
    tool = state.registry.get("text_to_speech")
    assert tool.settings.min_level == 30
    assert tool.settings.voices[0].id == "S_NkpD5org2"


def test_tts_min_level_clamped(tmp_path):
    state = _real_state(tmp_path)
    state.reload_tools()

    put_config(ConfigPayload(tts={
        "enabled": True,
        "api_key": "k",
        "min_level": 999,
    }), _req(state))
    assert state.config.tts.min_level == 255

    put_config(ConfigPayload(tts={
        "enabled": True,
        "api_key": "k",
        "min_level": -5,
    }), _req(state))
    assert state.config.tts.min_level == 0


# ---- P4 外部触发令牌：只允许 token_set 标志位下发，明文绝不离开服务端 ----

def test_ext_trigger_token_never_leaked(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(ext_trigger={
        "enabled": True,
        "token": "top-secret-token",
        "cooldown_seconds": 45,
        "base_url": "https://h.example",
    }), _req(state))
    view = get_config(_req(state))
    ext = view["ext_trigger"]
    assert ext["token"] == ""
    assert ext["token_set"] is True
    assert ext["cooldown_seconds"] == 45
    assert "top-secret-token" not in str(view)

    # 空令牌保存 = 不改原令牌（前端只有 token_set 标志位）
    put_config(ConfigPayload(ext_trigger={
        "enabled": True,
        "token": "",
        "cooldown_seconds": 60,
        "base_url": "https://h.example",
    }), _req(state))
    assert state.config.ext_trigger.token == "top-secret-token"

    # 新令牌正常覆盖
    put_config(ConfigPayload(ext_trigger={
        "enabled": True,
        "token": "rotated-token",
        "cooldown_seconds": 60,
        "base_url": "https://h.example",
    }), _req(state))
    assert state.config.ext_trigger.token == "rotated-token"


def test_ext_trigger_section_hidden_from_non_admin(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    put_config(ConfigPayload(ext_trigger={
        "enabled": True,
        "token": "top-secret-token",
        "cooldown_seconds": 30,
        "base_url": "https://h.example",
    }), _req(state))
    view = get_config(_req(state, "u10"))
    # 普通用户视图完全没有 ext_trigger 段，令牌与 token_set 都不可见
    assert "ext_trigger" not in view
    assert "top-secret-token" not in str(view)
    # 无密钥的 mcp/watchers 段仍对所有登录用户下发
    assert view["mcp"]["enabled"] is False
    assert view["watchers"]["max_per_session"] == 5


# ---- 权限门槛：不含密钥，对所有用户下发；等级 clamp ----

def test_access_section_roundtrip_visible_to_all(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(access={
        "browser_min_level": 50,
        "pixiv_min_level": 30,
        "galgame_min_level": 20,
        "agent_min_level": 60,
    }), _req(state))
    assert state.config.access.browser_min_level == 50
    assert state.config.access.agent_min_level == 60
    on_disk = load_config(tmp_path / "config.json")
    assert on_disk.access.pixiv_min_level == 30

    # 普通用户也能读到门槛（前端据此禁用入口）
    state.user_store.add("u10", level=10)
    view = get_config(_req(state, "u10"))
    assert view["access"]["galgame_min_level"] == 20
    assert view["access"]["browser_min_level"] == 50


def test_access_levels_clamped(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(access={
        "browser_min_level": 999,
        "pixiv_min_level": -5,
        "galgame_min_level": "x",
        "agent_min_level": 255,
    }), _req(state))
    acc = state.config.access
    assert acc.browser_min_level == 255
    assert acc.pixiv_min_level == 0
    assert acc.galgame_min_level == 0
    assert acc.agent_min_level == 255


# ---- DeepSeek Key：仅 admin 可见，掩码下发，空 Key 保留 ----

def test_deepseek_admin_only_masked(tmp_path):
    state = _mkstate(tmp_path)
    state.config.deepseek = DeepSeekSettings(api_key="sk-ds-secret",
                                             base_url="https://ds.example")
    view = get_config(_req(state))
    ds = view["deepseek"]
    assert ds["api_key"] == mask_api_key("sk-ds-secret")
    assert ds["api_key"] != "sk-ds-secret"
    assert ds["key_set"] is True
    assert ds["base_url"] == "https://ds.example"
    assert "sk-ds-secret" not in str(view)

    # 未配置 Key 时 key_set=False、api_key 为空串
    state2 = _mkstate(tmp_path / "s2")
    view2 = get_config(_req(state2))
    assert view2["deepseek"]["key_set"] is False
    assert view2["deepseek"]["api_key"] == ""


def test_deepseek_section_hidden_from_non_admin(tmp_path):
    state = _mkstate(tmp_path)
    state.config.deepseek = DeepSeekSettings(api_key="sk-ds-secret",
                                             search_price=8.0)
    state.user_store.add("u10", level=10)
    view = get_config(_req(state, "u10"))
    assert "deepseek" not in view
    assert "sk-ds-secret" not in str(view)


def test_deepseek_put_roundtrip_and_key_credit(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(deepseek={
        "enabled": True,
        "api_key": "sk-ds-secret",
        "base_url": "https://api.deepseek.com/",
        "proxy": " http://127.0.0.1:7897 ",
        "timeout_seconds": 500,
        "search_enabled": False,
        "search_price": 8.0,
        "tts_price": 4.5,
        "tts_voice": "tide",
    }), _req(state))
    ds = state.config.deepseek
    assert ds.api_key == "sk-ds-secret"
    assert ds.base_url == "https://api.deepseek.com"  # 尾斜杠被去掉
    assert ds.proxy == "http://127.0.0.1:7897"  # strip
    assert ds.timeout_seconds == 300  # clamp 上限
    assert ds.search_enabled is False
    assert ds.search_price == 8.0
    assert ds.tts_price == 4.5
    assert ds.tts_voice == "tide"
    assert load_config(tmp_path / "config.json").deepseek.api_key == "sk-ds-secret"

    # 再次保存时前端下发掩码串 → 兑现回真实 Key
    put_config(ConfigPayload(deepseek={
        "enabled": True,
        "api_key": mask_api_key("sk-ds-secret"),
        "search_price": 6.0,
    }), _req(state))
    assert state.config.deepseek.api_key == "sk-ds-secret"
    assert state.config.deepseek.search_price == 6.0

    # 空 Key 保存同样保留原 Key（编辑其它字段）
    put_config(ConfigPayload(deepseek={"enabled": True, "api_key": "",
                                       "search_price": 7.0}), _req(state))
    assert state.config.deepseek.api_key == "sk-ds-secret"
    assert state.config.deepseek.search_price == 7.0


# ---- DeepSeek Key 探测（设置 → DeepSeek 服务的「测试」按钮）----

def test_deepseek_test_admin_only(tmp_path):
    import asyncio

    from flyai.api.routes_config import test_deepseek_key

    state = _mkstate(tmp_path)
    state.config.deepseek = DeepSeekSettings(api_key="sk-ds-secret")
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(test_deepseek_key(_req(state, "u10")))
    assert ei.value.status_code == 403


def test_deepseek_test_without_key(tmp_path):
    import asyncio

    from flyai.api.routes_config import test_deepseek_key

    state = _mkstate(tmp_path)
    out = asyncio.run(test_deepseek_key(_req(state)))
    assert out["ok"] is False and "未配置" in out["message"]


def test_deepseek_test_ok_and_failure(tmp_path, monkeypatch):
    import asyncio

    import flyai.api.routes_config as rc

    state = _mkstate(tmp_path)
    state.config.deepseek = DeepSeekSettings(api_key="sk-ds-secret")
    calls = []

    class _FakeClient:
        def __init__(self, cfg):
            self.cfg = cfg

        async def tts(self, text, voice, fmt):
            calls.append((text, voice, fmt))
            return b"\x00\x01" * 10

    monkeypatch.setattr(rc, "DeepSeekToolsClient", _FakeClient)
    out = asyncio.run(rc.test_deepseek_key(_req(state)))
    assert out["ok"] is True and "20" in out["message"]
    assert calls == [("好", "stella", "pcm")]

    class _BadClient(_FakeClient):
        async def tts(self, text, voice, fmt):
            raise rc.DeepSeekError("Authentication Fails, invalid key",
                                   status_code=401)

    monkeypatch.setattr(rc, "DeepSeekToolsClient", _BadClient)
    out = asyncio.run(rc.test_deepseek_key(_req(state)))
    assert out["ok"] is False
    assert "Key 不可用" in out["message"] and "Authentication" in out["message"]
    assert "DeepSeek 服务" in out["message"]


def test_deepseek_bad_url_and_voice_sanitized(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(deepseek={
        "api_key": "k", "base_url": "ftp://bad", "tts_voice": "ghost",
        "search_price": -3,
    }), _req(state))
    ds = state.config.deepseek
    assert ds.base_url == "https://api.deepseek.com"
    assert ds.tts_voice == "stella"
    assert ds.search_price == 0.0


# ---- TTS 引擎选择持久化 ----

def test_tts_engine_persisted(tmp_path):
    state = _mkstate(tmp_path)
    assert state.config.tts.engine == "deepseek"  # 默认 DeepSeek
    put_config(ConfigPayload(tts={
        "enabled": True, "api_key": "k", "engine": "doubao",
    }), _req(state))
    assert state.config.tts.engine == "doubao"
    assert load_config(tmp_path / "config.json").tts.engine == "doubao"
    view = get_config(_req(state))
    assert view["tts"]["engine"] == "doubao"

    # 非法值回落到 deepseek
    put_config(ConfigPayload(tts={"engine": "ghost"}), _req(state))
    assert state.config.tts.engine == "deepseek"


# ---- 邀请码注册默认额度 ----

def test_registration_default_quota_admin_only(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(registration={"default_quota": 12}), _req(state))
    assert state.config.registration.default_quota == 12
    # 未提交的 require_invite 回落默认值 true
    assert state.config.registration.require_invite is True
    view = get_config(_req(state))
    assert view["registration"] == {"default_quota": 12, "require_invite": True}
    # 落盘后重新加载仍保留
    assert load_config(tmp_path / "config.json").registration.default_quota == 12

    state.user_store.add("u10", level=0)
    assert "registration" not in get_config(_req(state, "u10"))


def test_registration_require_invite_roundtrip(tmp_path):
    state = _mkstate(tmp_path)
    put_config(
        ConfigPayload(registration={"default_quota": 5, "require_invite": False}),
        _req(state),
    )
    assert state.config.registration.require_invite is False
    view = get_config(_req(state))
    assert view["registration"] == {"default_quota": 5, "require_invite": False}
    assert load_config(tmp_path / "config.json").registration.require_invite is False


def test_registration_default_quota_normalized(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(registration={"default_quota": -5}), _req(state))
    assert state.config.registration.default_quota == 0
    put_config(ConfigPayload(registration={"default_quota": -1}), _req(state))
    assert state.config.registration.default_quota == -1
    put_config(ConfigPayload(registration={"default_quota": "oops"}), _req(state))
    assert state.config.registration.default_quota == 0


# ---- SMTP 邮件服务：仅 admin 可见，授权码掩码 + 正文兑现，测试发信 ----

def test_email_section_admin_only_masked(tmp_path):
    state = _mkstate(tmp_path)
    view = get_config(_req(state))
    em = view["email"]
    assert em["enabled"] is False
    assert em["password"] == "" and em["password_set"] is False

    state.config.email.username = "bot@qq.com"
    state.config.email.password = "qq-auth-code"
    view = get_config(_req(state))
    em = view["email"]
    assert em["password"] == mask_api_key("qq-auth-code")
    assert em["password"] != "qq-auth-code"
    assert em["password_set"] is True
    assert "qq-auth-code" not in str(view)

    # 普通用户：整段消失
    state.user_store.add("u10", level=10)
    assert "email" not in get_config(_req(state, "u10"))


def test_email_put_roundtrip_and_credits_password(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(email={
        "enabled": True,
        "host": "smtp.qq.com",
        "port": 465,
        "encryption": "ssl",
        "username": "bot@qq.com",
        "password": "qq-auth-code",
        "from_name": "FlyAI",
        "from_address": "noreply@example.com",
        "code_ttl_seconds": 900,
        "resend_interval_seconds": 90,
        "hourly_limit": 5,
    }), _req(state))
    em = state.config.email
    assert em.enabled is True and em.password == "qq-auth-code"
    assert em.code_ttl_seconds == 900
    assert em.resend_interval_seconds == 90
    assert em.hourly_limit == 5
    assert load_config(tmp_path / "config.json").email.password == "qq-auth-code"

    # 掩码串回传 → 兑现旧授权码；其它字段照常更新
    put_config(ConfigPayload(email={
        "enabled": True,
        "username": "bot@qq.com",
        "password": mask_api_key("qq-auth-code"),
        "hourly_limit": 6,
    }), _req(state))
    assert state.config.email.password == "qq-auth-code"
    assert state.config.email.hourly_limit == 6

    # 空授权码 → 同样保留
    put_config(ConfigPayload(email={
        "enabled": True,
        "username": "bot@qq.com",
        "password": "",
        "hourly_limit": 7,
    }), _req(state))
    assert state.config.email.password == "qq-auth-code"
    assert state.config.email.hourly_limit == 7


def test_email_values_clamped(tmp_path):
    state = _mkstate(tmp_path)
    put_config(ConfigPayload(email={
        "enabled": True,
        "password": "k",
        "code_ttl_seconds": 5,
        "resend_interval_seconds": 9999,
        "hourly_limit": 0,
    }), _req(state))
    em = state.config.email
    assert em.code_ttl_seconds == 60
    assert em.resend_interval_seconds == 600
    assert em.hourly_limit == 1


def test_email_test_send_ok(tmp_path):
    import asyncio

    from flyai.api.routes_config import TestEmailPayload, test_email

    state = _mkstate(tmp_path)
    state.config.email.username = "bot@qq.com"
    state.config.email.password = "qq-auth-code"

    out = asyncio.run(test_email(TestEmailPayload(), _req(state)))
    assert out["ok"] is True and "bot@qq.com" in out["message"]
    assert state.mailer.sent == ["bot@qq.com"]

    out = asyncio.run(test_email(TestEmailPayload(to=" to@example.com "),
                                 _req(state)))
    assert out["ok"] is True and state.mailer.sent[-1] == "to@example.com"


def test_email_test_no_recipient(tmp_path):
    import asyncio

    from flyai.api.routes_config import TestEmailPayload, test_email

    state = _mkstate(tmp_path)
    out = asyncio.run(test_email(TestEmailPayload(), _req(state)))
    assert out["ok"] is False and "收件邮箱" in out["message"]

    state.config.email.from_address = "noreply@example.com"
    out = asyncio.run(test_email(TestEmailPayload(), _req(state)))
    assert out["ok"] is True and state.mailer.sent == ["noreply@example.com"]


def test_email_test_failure_and_permission(tmp_path):
    import asyncio

    from flyai.api.routes_config import TestEmailPayload, test_email
    from flyai.mailer import MailError

    state = _mkstate(tmp_path)
    state.config.email.username = "bot@qq.com"
    state.mailer.error = MailError("SMTP 认证失败（QQ 邮箱请使用授权码）")
    out = asyncio.run(test_email(TestEmailPayload(), _req(state)))
    assert out["ok"] is False and "授权码" in out["message"]

    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(test_email(TestEmailPayload(), _req(state, "u10")))
    assert ei.value.status_code == 403
