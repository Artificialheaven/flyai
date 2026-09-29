"""旧配置结构迁移：旧 providers 升级共享；旧 image_generation 转生图模型。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import AppConfig, _migrate_config_data


def test_legacy_providers_become_admin_shared():
    data = {"providers": [
        {"id": "p1", "base_url": "x", "api_key": "k", "model": "m"},
        {"id": "p2", "base_url": "x", "model": "m2", "owner": "u9"},
    ]}
    _migrate_config_data(data)
    p0 = data["providers"][0]
    assert p0["owner"] == "admin"
    assert p0["shared"] is True
    assert p0["min_level"] == 0
    # 已有 owner 的条目不被动
    assert data["providers"][1]["owner"] == "u9"
    # 迁移后可直接 parse
    assert AppConfig.parse_obj(data).providers[0].shared is True


def test_legacy_image_generation_becomes_shared_model():
    data = {"image_generation": {
        "enabled": True,
        "base_url": "https://ark/v3",
        "api_key": "ark-key",
        "model": "seedream-x",
        "default_size": "4K",
    }}
    _migrate_config_data(data)
    models = data["image_models"]
    assert len(models) == 1
    m = models[0]
    assert m["id"] == "ark-default"
    assert m["api_key"] == "ark-key"
    assert m["model"] == "seedream-x"
    assert m["owner"] == "admin" and m["shared"] is True
    # 旧字段被清掉
    assert "api_key" not in data["image_generation"]
    assert "model" not in data["image_generation"]
    cfg = AppConfig.parse_obj(data)
    assert cfg.image_generation.default_size == "4K"
    assert cfg.image_models[0].api_key == "ark-key"


def test_existing_image_models_untouched():
    data = {
        "image_generation": {"api_key": "old"},
        "image_models": [{"id": "custom", "owner": "u1"}],
    }
    _migrate_config_data(data)
    assert len(data["image_models"]) == 1
    assert data["image_models"][0]["id"] == "custom"
    # 旧单例字段仍会被清理
    assert "api_key" not in data["image_generation"]


def test_migration_without_legacy_fields_noop():
    data = {"providers": [], "image_generation": {"enabled": False}}
    _migrate_config_data(data)
    assert data.get("image_models") is None
