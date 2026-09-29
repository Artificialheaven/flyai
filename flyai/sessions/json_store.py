"""JSON 会话持久化存储：每个会话一个 data/sessions/{id}.json，原子写入。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List

from ..config import PROJECT_ROOT
from ..schemas import Message
from .models import RoomMember, Session, Thread
from .store import SessionStore
from .versions import heal_thread


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
        s = Session(
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
            members=[RoomMember.from_dict(m) for m in data.get("members", [])],
            threads=[Thread.from_dict(t) for t in data.get("threads", [])],
            pinned=bool(data.get("pinned", False)),
        )
        # 旧单 AI 会话（beta 前）：迁移为一个用户座位 + 一个 AI 参与者 + 一条私聊线程
        migrated = False
        if not s.members and not s.threads:
            if s.character_id or s.messages:
                s.members = [RoomMember(name="你", is_user=True)]
                ai = RoomMember(name="AI", character_id=s.character_id, override=s.character_override)
                s.members.append(ai)
                t = Thread(kind="dm", title="私聊", member_ids=[ai.id], messages=s.messages)
                s.threads = [t]
                s.messages = []
                migrated = True
        # 自愈：流式中途被硬 kill 留下的孤儿/半成品回复
        for t in s.threads:
            heal_thread(t)
        # 迁移必须落盘：否则每次 get() 都会生成新的随机线程 id，
        # GET 会话返回的线程随后按 id 加载会 404「窗口不存在」
        if migrated:
            self.save(s)
        return s

    def list(self) -> List[Session]:
        # (文件 mtime_ns, session)：updated_at 只有秒级精度，同秒内保存的会话
        # 靠纳秒 mtime 分出真实先后，避免顺序随 glob 结果翻转
        entries = []
        for p in self._dir.glob("*.json"):
            try:
                mtime_ns = p.stat().st_mtime_ns
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                s = Session(
                    id=data.get("id", p.stem),
                    title=data.get("title", "新会话"),
                    provider_id=data.get("provider_id", ""),
                    created_at=data.get("created_at", ""),
                    updated_at=data.get("updated_at", ""),
                    messages=[],
                    pinned=bool(data.get("pinned", False)),
                )
            except Exception:
                continue
            entries.append((mtime_ns, s))
        # 先按落盘时间倒序，再以稳定排序把置顶提至最前
        entries.sort(key=lambda e: e[0], reverse=True)
        out = [s for _, s in entries]
        out.sort(key=lambda s: not s.pinned)
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
            "members": [m.to_dict() for m in session.members],
            "threads": [t.to_dict() for t in session.threads],
            "pinned": session.pinned,
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
            for t in s.threads:
                t.messages = []
            s.title = "新会话"
            self.save(s)