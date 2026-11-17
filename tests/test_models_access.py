"""模型可见性纯逻辑：非共享隐藏、等级 >= 边界、admin 全见。"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import AppConfig, ImageModelConfig, ProviderConfig
from flyai.models_access import (
    model_visible,
    visible_image_models,
    visible_providers,
)
from flyai.user import ADMIN_UID


def test_unshared_hidden_even_high_level():
    assert not model_visible(False, 0, 255)


def test_level_boundary_inclusive():
    assert not model_visible(True, 50, 49)
    assert model_visible(True, 50, 50)
    assert model_visible(True, 50, 51)


def test_visible_providers_filtering():
    cfg = AppConfig()
    cfg.providers = [
        ProviderConfig(id="open", base_url="x", model="m",
                       owner="admin", shared=True, min_level=0),
        ProviderConfig(id="gated", base_url="x", model="m",
                       owner="admin", shared=True, min_level=50),
        ProviderConfig(id="private", base_url="x", model="m",
                       owner="u9", shared=False),
    ]
    ids10 = {p.id for p in visible_providers(cfg, "u1", 10)}
    assert ids10 == {"open"}
    ids50 = {p.id for p in visible_providers(cfg, "u1", 50)}
    assert ids50 == {"open", "gated"}
    # 用户自建能力已下线：私有模型连本人也不可见
    assert "private" not in {p.id for p in visible_providers(cfg, "u9", 0)}


def test_admin_sees_all():
    cfg = AppConfig()
    cfg.providers = [ProviderConfig(id="p", base_url="x", model="m",
                                    owner="u9", shared=False)]
    cfg.image_models = [ImageModelConfig(id="i", owner="u9", shared=False)]
    assert [p.id for p in visible_providers(cfg, ADMIN_UID, 256)] == ["p"]
    assert [m.id for m in visible_image_models(cfg, ADMIN_UID, 256)] == ["i"]


def test_visible_image_models_filtering():
    cfg = AppConfig()
    cfg.image_models = [
        ImageModelConfig(id="a", shared=True, min_level=80),
    ]
    assert visible_image_models(cfg, "u1", 79) == []
    assert [m.id for m in visible_image_models(cfg, "u1", 80)] == ["a"]
