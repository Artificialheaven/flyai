"""模型可见性：仅共享模型对普通用户可见，且需用户等级 >= min_level。

用户自建模型的能力已下线；此处不再有归属直通，避免私有模型对创建者
仍然可见/可用。admin（uid == ADMIN_UID）一律可见全部，便于统一管理。
"""

from __future__ import annotations

from typing import List

from .config import AppConfig, ImageModelConfig, ProviderConfig
from .user import ADMIN_UID


def model_visible(shared: bool, min_level: int, level: int) -> bool:
    if not shared:
        return False
    return int(level or 0) >= int(min_level or 0)


def visible_providers(cfg: AppConfig, uid: str, level: int) -> List[ProviderConfig]:
    if uid == ADMIN_UID:
        return list(cfg.providers)
    return [
        p for p in cfg.providers
        if model_visible(p.shared, p.min_level, level)
    ]


def visible_image_models(cfg: AppConfig, uid: str, level: int) -> List[ImageModelConfig]:
    if uid == ADMIN_UID:
        return list(cfg.image_models)
    return [
        m for m in cfg.image_models
        if model_visible(m.shared, m.min_level, level)
    ]