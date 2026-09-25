"""JSON 会话持久化存储：每个会话一个 data/sessions/{id}.json，原子写入。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List

from ..config import PROJECT_ROOT
from ..schemas import Message
from .models import Session
from .store import SessionStore


class JsonSessionStore(SessionStore):
    def __init__(self, base_dir: Path = None):
        self._dir = base_dir or (PROJECT_ROOT / "data" / "sessions")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path:
        return self._dir / f"{sid}.json"

    def create(self, title: str = "", provider_id: str = "", character_id: str = "", world_id: str = "") -> Session:
        s = Session(
            title=title or "新会话",
            provider_id=provider_id,
            character_id=character_id,
            world_id=world_id,
        )
        s.created_at = s.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save(s)
        return s

    def get(self, sid: str) -> Session:
        p = self._path(sid)
        if not p.exists():
            return None
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        msg_data = data.get("messages", [])
        return Session(
            id=data.get("id", sid),
            title=data.get("title", "新会话"),
            provider_id=data.get("provider_id", ""),
            character_id=data.get("character_id", ""),
            world_id=data.get("world_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            messages=[Message.from_dict(m) for m in msg_data],
            attributes=data.get("attributes", {}),
            character_override=data.get("character_override"),
            world_override=data.get("world_override"),
        )

    def list(self) -> List[Session]:
        out = []
        for p in self._dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                out.append(Session(
                    id=data.get("id", p.stem),
                    title=data.get("title", "新会话"),
                    provider_id=data.get("provider_id", ""),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    messages=[],
                ))
            except Exception:
                continue
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    def save(self, session: Session) -> None:
        session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        data = {
            "id": session.id,
            "title": session.title,
            "provider_id": session.provider_id,
            "character_id": session.character_id,
            "world_id": session.world_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": [m.to_dict() for m in session.messages],
            "attributes": session.attributes,
            "character_override": session.character_override,
            "world_override": session.world_override,
        }
        p = self._path(session.id)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def delete(self, sid: str) -> None:
        self._path(sid).unlink(missing_ok=True)

    def clear(self, sid: str) -> None:
        s = self.get(sid)
        if s:
            s.messages = []
            s.title = "新会话"
            self.save(s)