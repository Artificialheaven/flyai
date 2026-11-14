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
from flyai.auth_sessions import AuthSessionStore, LoginRateLimiter
from flyai.user import ADMIN_LEVEL, ADMIN_UID

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
        auth_sessions=AuthSessionStore(),
        login_limiter=LoginRateLimiter(),
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
    assert set(p.keys()) == {"id", "model", "vision", "rate", "min_level"}
    assert p["model"] == "deepseek-chat"
    m = view["image_models"][0]
    assert set(m.keys()) == {"id", "name", "model", "min_level"}
    # 嵌入配置（含掩码 key）不对普通用户下发
    assert "embeddings" not in view
    # 全局功能段仍下发（作为"跟随默认"的默认值）
    assert view["galgame"]["enabled"] is True


def test_user_sees_full_fields_for_own_models(tmp_path):
    """本人自建模型仍可拿到 base_url/掩码 key（编辑自己的模型不受影响）。"""
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    state.config.providers.append(
        ProviderConfig(id="mine", base_url="https://mine.example/v1",
                       api_key="my-secret", model="my-model",
                       owner="u10", shared=False)
    )
    view = get_config(_req(state, "u10"))
    mine = next(p for p in view["providers"] if p["id"] == "mine")
    assert mine["base_url"] == "https://mine.example/v1"
    assert mine["owner"] == "u10"
    assert mine["api_key"] == mask_api_key("my-secret")
    # 管理员的共享模型在同一视图里仍脱敏
    shared = next(p for p in view["providers"] if p["id"] == "deepseek")
    assert "base_url" not in shared and "owner" not in shared


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


@pytest.mark.parametrize("bad_id", [
    '" autofocus="alert', "a/b", "a b", "<x>", ".x",
])
def test_create_provider_bad_id_rejected(tmp_path, bad_id):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        create_provider_model(ProviderPayload(
            id=bad_id, base_url="https://x", api_key="k", model="m",
        ), _req(state, "u10"))
    assert ei.value.status_code == 400


def test_create_image_model_bad_id_rejected(tmp_path):
    state = _mkstate(tmp_path)
    state.user_store.add("u10", level=10)
    with pytest.raises(HTTPException) as ei:
        create_image_model(ImageModelPayload(
            id="a/b", name="x", base_url="https://x", api_key="k", model="m",
        ), _req(state, "u10"))
    assert ei.value.status_code == 400


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
