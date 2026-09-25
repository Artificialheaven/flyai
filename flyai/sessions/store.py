"""SessionStore 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .models import Session


class SessionStore(ABC):
    @abstractmethod
    def create(self, title: str = "", provider_id: str = "") -> Session:
        raise NotImplementedError

    @abstractmethod
    def get(self, sid: str) -> Session:
        raise NotImplementedError

    @abstractmethod
    def list(self) -> List[Session]:
        raise NotImplementedError

    @abstractmethod
    def save(self, session: Session) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, sid: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear(self, sid: str) -> None:
        raise NotImplementedError