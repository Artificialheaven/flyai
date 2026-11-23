"""SessionStore 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from .models import Session


class SessionStore(ABC):
    # 由 PATCH 类接口（改名/置顶/改绑角色/世界观/工具）维护、聊天 drive 从不写的字段。
    # drive 持有的是发消息时加载的旧副本，流式生成期间用户可能已并发修改并落盘，
    # save_merged() 回填这些字段，避免整份旧副本保存时静默回滚用户操作。
    MERGED_FIELDS = (
        "title", "pinned", "max_tokens", "disabled_tools",
        "character_id", "world_id", "character_override", "world_override",
        "opening_entry",
    )

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

    def message_search(self, query: str, limit: int = 200) -> Dict[str, Tuple[int, str]]:
        """按消息正文/墓碑/发言者名搜索；返回 {sid: (命中数, 片段)}。

        基类默认不支持（只按标题搜）；JsonSessionStore 覆盖实现。
        """
        return {}

    def save_merged(self, session: Session) -> None:
        """并发安全落盘：长跑执行期间用户的改名/置顶/改绑等元数据可能已被
        另一请求写入磁盘，这里先从磁盘回填这些字段再整体保存，避免旧副本覆盖。"""
        disk = self.get(session.id) if session.id else None
        if disk is not None and disk is not session:
            for name in self.MERGED_FIELDS:
                setattr(session, name, getattr(disk, name))
        self.save(session)

    @abstractmethod
    def delete(self, sid: str) -> None:
        raise NotImplementedError

    def stat(self, sid: str) -> Optional[Dict[str, int]]:
        """会话文件指纹（mtime_ns/size），用于条件请求 ETag。

        基类默认不支持（返回 None）；JsonSessionStore 覆盖实现。
        """
        return None

    @abstractmethod
    def clear(self, sid: str) -> None:
        raise NotImplementedError