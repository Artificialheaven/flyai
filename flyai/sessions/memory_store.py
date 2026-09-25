"""进程内会话存储（阶段1可用，重启清空）。"""

from __future__ import annotations

from typing import Dict, List

from .models import Session
from .store import SessionStore


class MemorySessionStore(SessionStore):
    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def create(self, title: str = "", provider_id: str = "", character_id: str = "", world_id: str = "") -> Session:
        s = Session(
            title=title or "新会话",
            provider_id=provider_id,
            character_id=character_id,
            world_id=world_id,
        )
        self._sessions[s.id] = s
        return s

    def get(self, sid: str) -> Session:
        return self._sessions.get(sid)

    def list(self) -> List[Session]:
        return list(self._sessions.values())

    def save(self, session: Session) -> None:
        self._sessions[session.id] = session

    def delete(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def clear(self, sid: str) -> None:
        s = self._sessions.get(sid)
        if s:
            s.messages = []