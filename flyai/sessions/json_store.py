"""JSON 会话持久化存储：每个会话一个 data/sessions/{id}.json，原子写入。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List

from ..config import PROJECT_ROOT
from ..ids import safe_child
from ..schemas import Message
from .models import RoomMember, Session, Thread, bootstrap_room
from .store import SessionStore
from .versions import heal_thread


def _norm_name(s: str) -> str:
    return (s or "").strip().casefold()


def migrate_actor_ids(session: Session) -> bool:
    """旧群聊消息没有 actor_id（2026-10 前的数据）：按 assistant 消息持久化的
    name 精确匹配当前 AI 座位名补归属；tool 结果按 tool_call_id 跟随发起调用的
    assistant，匹配不上再退而跟随紧邻的上一条 assistant。

    补归属后，老对话也能享受 _actor_view 的座位隔离（否则旧工具回合只能整条
    透传，旧正文靠 _actor_view 内的 name 兜底临时加前缀但不落盘）。
    返回是否改动过（调用方据此落盘一次）。
    """
    ai_members = [m for m in session.members if not getattr(m, "is_user", False)]
    if not ai_members:
        return False
    name_to_id: Dict[str, str] = {}
    for m in ai_members:
        key = _norm_name(m.name)
        if key and key not in name_to_id:
            name_to_id[key] = m.id
    changed = False
    for t in session.threads:
        # 第一遍：assistant 按名字补归属，同时建 tool_call_id -> actor 索引
        call_owner: Dict[str, str] = {}
        for m in t.messages:
            if m.role != "assistant":
                continue
            owner = m.actor_id
            if not owner:
                owner = name_to_id.get(_norm_name(m.name))
                if owner:
                    m.actor_id = owner
                    changed = True
            if owner:
                for tc in (m.tool_calls or []):
                    cid = getattr(tc, "id", None)
                    if cid:
                        call_owner[cid] = owner
        # 第二遍：无主 tool 结果跟随它所属的工具调用，再退而跟随上一条 assistant
        last_owner = ""
        for m in t.messages:
            if m.role == "assistant":
                last_owner = m.actor_id or last_owner
            elif m.role == "tool" and not m.actor_id:
                owner = call_owner.get(m.tool_call_id or "") or last_owner
                if owner:
                    m.actor_id = owner
                    changed = True
    return changed


def session_from_dict(data: Dict, sid: str = "") -> Session:
    """从完整快照还原会话（存档点恢复同样使用）。"""
    return Session(
        id=data.get("id", sid),
        title=data.get("title", "新会话"),
        provider_id=data.get("provider_id", ""),
        character_id=data.get("character_id", ""),
        world_id=data.get("world_id", ""),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
        messages=[Message.from_dict(m) for m in data.get("messages", [])],
        attributes=data.get("attributes", {}),
        character_override=data.get("character_override"),
        world_override=data.get("world_override"),
        members=[RoomMember.from_dict(m) for m in data.get("members", [])],
        threads=[Thread.from_dict(t) for t in data.get("threads", [])],
        pinned=bool(data.get("pinned", False)),
        token_usage=data.get("token_usage", {}) or {},
        mode=data.get("mode", "normal") or "normal",
        galgame=data.get("galgame", {}) or {},
        galgame_tts=data.get("galgame_tts", {}) or {},
        max_tokens=int(data.get("max_tokens", 0) or 0),
        disabled_tools=list(data.get("disabled_tools", []) or []),
    )


def session_to_dict(session: Session) -> Dict:
    """会话完整序列化（存档点快照同样使用此结构）。"""
    return {
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
        "token_usage": session.token_usage or {},
        "mode": session.mode,
        "galgame": session.galgame or {},
        "galgame_tts": session.galgame_tts or {},
        "max_tokens": int(session.max_tokens or 0),
        "disabled_tools": list(session.disabled_tools or []),
    }


class JsonSessionStore(SessionStore):
    def __init__(self, base_dir: Path = None):
        self._dir = base_dir or (PROJECT_ROOT / "data" / "sessions")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sid: str) -> Path:
        # 外部 id 直接拼文件名：穿越/分隔符/.. 在这里收口（路由层另有 400 预检）
        return safe_child(self._dir, f"{sid}.json")

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
        s = session_from_dict(data, sid)
        # 旧单 AI 会话（beta 前）：迁移为一个用户座位 + 一个 AI 参与者 + 一条私聊线程
        migrated = False
        if not s.members and not s.threads:
            if s.character_id or s.messages:
                bootstrap_room(s)
                migrated = True
        # 自愈：流式中途被硬 kill 留下的孤儿/半成品回复
        for t in s.threads:
            heal_thread(t)
        # 旧群聊消息补座位归属（一次性，补完落盘）
        if migrate_actor_ids(s):
            migrated = True
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
                    mode=data.get("mode", "normal") or "normal",
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
        data = session_to_dict(session)
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