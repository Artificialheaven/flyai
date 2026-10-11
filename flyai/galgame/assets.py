"""Galgame 资产库：立绘（绑定角色卡，跨会话复用）、背景与音乐（按会话存放）。

目录结构（位于用户上传目录下，复用 /uploads 静态挂载）::

    galgame/
      sprites/<cid>/index.json + ga_*.png
      backgrounds/<sid>/index.json + ga_*.png
      music/<sid>/index.json + ga_*.mid

index.json 为资产清单（原子写入）；资产文件名为 ga_<时间戳>_<hex8><ext>。
cid / sid 等外部 key 一律经 ids.check_safe_name 收口防穿越。
"""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..ids import InvalidName, check_safe_name

_SPRITES = "sprites"
_BACKGROUNDS = "backgrounds"
_MUSIC = "music"
_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".mid", ".midi")
_DEFAULT_EXT = {_SPRITES: ".png", _BACKGROUNDS: ".png", _MUSIC: ".mid"}


@dataclass
class AssetRecord:
    """一条 Galgame 资产（立绘或背景）。"""

    id: str
    name: str           # AI 给的描述（清单检索用）
    url: str            # 可访问 URL
    file: str           # 目录内文件名
    prompt: str = ""    # 生成时使用的提示词
    created_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


class GalgameAssetStore:
    """立绘 / 背景的文件 + index.json 结构化存储。"""

    def __init__(self, root: Path, url_base: str):
        self.root = Path(root) / "galgame"
        self.url_base = (url_base or "").rstrip("/")

    # ---- 内部 ----

    def _key_dir(self, kind: str, key: str) -> Path:
        what = "角色 ID" if kind == _SPRITES else "会话 ID"
        check_safe_name(key, what)
        # 防御：kind 由内部代码传入
        if kind not in (_SPRITES, _BACKGROUNDS, _MUSIC):
            raise InvalidName("资产类型非法")
        d = self.root / kind / key
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _index_path(self, d: Path) -> Path:
        return d / "index.json"

    def _load_index(self, d: Path) -> List[Dict]:
        p = self._index_path(d)
        if not p.exists():
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def _write_index(self, d: Path, items: List[Dict]) -> None:
        p = self._index_path(d)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def _add(self, kind: str, key: str, content: bytes, ext: str,
             name: str, prompt: str) -> AssetRecord:
        ext = (ext or "").lower()
        if ext not in _EXTS:
            ext = _DEFAULT_EXT.get(kind, ".png")
        d = self._key_dir(kind, key)
        stamp = time.strftime("%Y%m%d%H%M%S")
        fname = "ga_%s_%s%s" % (stamp, secrets.token_hex(4), ext)
        with open(d / fname, "wb") as f:
            f.write(content)
        url = "%s/galgame/%s/%s/%s" % (self.url_base, kind, key, fname)
        rec = AssetRecord(
            id=uuid.uuid4().hex,
            name=(name or "").strip()[:200],
            url=url,
            file=fname,
            prompt=(prompt or "")[:2000],
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        items = self._load_index(d)
        items.append(rec.to_dict())
        self._write_index(d, items)
        return rec

    def _list(self, kind: str, key: str) -> List[AssetRecord]:
        d = self._key_dir(kind, key)
        return [AssetRecord(**item) for item in self._load_index(d)
                if isinstance(item, dict) and item.get("id") and item.get("file")]

    def _get(self, kind: str, key: str, aid: str) -> Optional[AssetRecord]:
        check_safe_name(aid, "资产 ID")
        for rec in self._list(kind, key):
            if rec.id == aid:
                return rec
        return None

    # ---- 立绘（按角色卡） ----

    def list_sprites(self, character_id: str) -> List[AssetRecord]:
        return self._list(_SPRITES, character_id)

    def add_sprite(self, character_id: str, content: bytes, ext: str,
                   name: str = "", prompt: str = "") -> AssetRecord:
        return self._add(_SPRITES, character_id, content, ext, name, prompt)

    def get_sprite(self, character_id: str, asset_id: str) -> Optional[AssetRecord]:
        return self._get(_SPRITES, character_id, asset_id)

    # ---- 背景（按会话） ----

    def list_backgrounds(self, session_id: str) -> List[AssetRecord]:
        return self._list(_BACKGROUNDS, session_id)

    def add_background(self, session_id: str, content: bytes, ext: str,
                       name: str = "", prompt: str = "") -> AssetRecord:
        return self._add(_BACKGROUNDS, session_id, content, ext, name, prompt)

    def get_background(self, session_id: str, asset_id: str) -> Optional[AssetRecord]:
        return self._get(_BACKGROUNDS, session_id, asset_id)

    # ---- 音乐（按会话，AI 作曲的 MIDI） ----

    def list_music(self, session_id: str) -> List[AssetRecord]:
        return self._list(_MUSIC, session_id)

    def add_music(self, session_id: str, content: bytes, ext: str = ".mid",
                  name: str = "", prompt: str = "") -> AssetRecord:
        return self._add(_MUSIC, session_id, content, ext, name, prompt)

    def get_music(self, session_id: str, asset_id: str) -> Optional[AssetRecord]:
        return self._get(_MUSIC, session_id, asset_id)
