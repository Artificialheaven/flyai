"""模型可见性：归属直通；共享模型需用户等级 >= min_level。

admin（uid == ADMIN_UID）一律可见全部，便于统一管理。
"""

from __future__ import annotations

from typing import List

from .config import AppConfig, ImageModelConfig, ProviderConfig
from .user import ADMIN_UID


def model_visible(owner: str, shared: bool, min_level: int,
                  uid: str, level: int) -> bool:
    if owner == uid:
        return True
    if not shared:
        return False
    return int(level or 0) >= int(min_level or 0)


def visible_providers(cfg: AppConfig, uid: str, level: int) -> List[ProviderConfig]:
    if uid == ADMIN_UID:
        return list(cfg.providers)
    return [
        p for p in cfg.providers
        if model_visible(p.owner, p.shared, p.min_level, uid, level)
    ]


def visible_image_models(cfg: AppConfig, uid: str, level: int) -> List[ImageModelConfig]:
    if uid == ADMIN_UID:
        return list(cfg.image_models)
    return [
        m for m in cfg.image_models
        if model_visible(m.owner, m.shared, m.min_level, uid, level)
    ]
