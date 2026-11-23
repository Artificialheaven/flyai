"""JSON 会话持久化存储：每个会话一个 data/sessions/{id}.json，原子写入。"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import PROJECT_ROOT
from ..ids import safe_child
from ..schemas import Message
from .models import RoomMember, Session, Thread, bootstrap_room
from .resolve import strip_heavy_extensions
from .store import SessionStore
from .versions import heal_thread


def _norm_name(s: str) -> str:
    return (s or "").strip().casefold()


def _count_messages(data: Dict) -> int:
    """列表摘要用：房间形态数各窗口消息；旧形态退回顶层 messages。"""
    threads = data.get("threads") or []
    if threads:
        return sum(len(t.get("messages") or []) for t in threads)
    return len(data.get("messages") or [])


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
        context_warned={
            str(k): [int(x) for x in (v or [])]
            for k, v in (data.get("context_warned") or {}).items()
        },
        mode=data.get("mode", "normal") or "normal",
        opening_entry=data.get("opening_entry", "") or "",
        greeting_index=int(data.get("greeting_index", 0) or 0),
        galgame=data.get("galgame", {}) or {},
        galgame_tts=data.get("galgame_tts", {}) or {},
        max_tokens=int(data.get("max_tokens", 0) or 0),
        disabled_tools=list(data.get("disabled_tools", []) or []),
        mvu=data.get("mvu", {}) or {},
        template_vars=data.get("template_vars", {}) or {},
    )


def _member_dict(m: RoomMember) -> Dict:
    data = m.to_dict()
    if isinstance(data.get("override"), dict):
        # 酒馆助手脚本只存库主卡一份；会话副本落盘时剥离（读取时补回）
        data["override"] = strip_heavy_extensions(data["override"])
    return data


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
        "character_override": strip_heavy_extensions(session.character_override),
        "world_override": session.world_override,
        "members": [_member_dict(m) for m in session.members],
        "threads": [t.to_dict() for t in session.threads],
        "pinned": session.pinned,
        "token_usage": session.token_usage or {},
        "context_warned": session.context_warned or {},
        "mode": session.mode,
        "opening_entry": session.opening_entry or "",
        "greeting_index": int(getattr(session, "greeting_index", 0) or 0),
        "galgame": session.galgame or {},
        "galgame_tts": session.galgame_tts or {},
        "max_tokens": int(session.max_tokens or 0),
        "disabled_tools": list(session.disabled_tools or []),
        "mvu": session.mvu or {},
        "template_vars": session.template_vars or {},
    }


class JsonSessionStore(SessionStore):
    def __init__(self, base_dir: Path = None):
        self._dir = base_dir or (PROJECT_ROOT / "data" / "sessions")
        self._dir.mkdir(parents=True, exist_ok=True)
        # 列表摘要缓存：路径 -> ((mtime_ns, size), 摘要 dict / None=解析失败)。
        # 只缓存列表用的轻量摘要，正文与图片每次仍从磁盘读（get 必须是最新）。
        self._summary_lock = threading.RLock()
        self._summary_cache: Dict[str, Tuple[Tuple[int, int], Optional[Dict]]] = {}

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
        live_paths = set()
        for p in self._dir.glob("*.json"):
            key = str(p)
            try:
                st = p.stat()
            except OSError:
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            live_paths.add(key)
            with self._summary_lock:
                cached = self._summary_cache.get(key)
            if cached is not None and cached[0] == stamp:
                summary = cached[1]
            else:
                summary = self._read_summary(p)
                with self._summary_lock:
                    self._summary_cache[key] = (stamp, summary)
            if summary is None:
                continue
            entries.append((st.st_mtime_ns, dict(summary)))
        # 删除磁盘上已不存在的会话缓存，避免改名/删除后无限增长
        with self._summary_lock:
            for key in [k for k in self._summary_cache if k not in live_paths]:
                del self._summary_cache[key]
        # 先按落盘时间倒序，再以稳定排序把置顶提至最前
        entries.sort(key=lambda e: e[0], reverse=True)
        out = []
        for _, summary in entries:
            summary["messages"] = []
            out.append(Session(**summary))
        out.sort(key=lambda s: not s.pinned)
        return out

    @staticmethod
    def _read_summary(p: Path) -> Optional[Dict]:
        """读取列表摘要；解析失败返回 None（也会被缓存，坏文件不再反复解析）。"""
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "id": data.get("id", p.stem),
                "title": data.get("title", "新会话"),
                "provider_id": data.get("provider_id", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "pinned": bool(data.get("pinned", False)),
                "mode": data.get("mode", "normal") or "normal",
                "token_usage": dict(data.get("token_usage") or {}),
                "message_count": _count_messages(data),
            }
        except Exception:
            return None


    def save(self, session: Session) -> None:
        session.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        data = session_to_dict(session)
        p = self._path(session.id)
        # 并发保存（卡片连续写角色/世界书 + 宿主刷新）不能共用同一个 .tmp：
        # Windows 上 os.replace 会因另一个请求仍在写/读而报 WinError 32。
        tmp = p.with_name("%s.%s.tmp" % (p.name, uuid.uuid4().hex))
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            for attempt in range(6):
                try:
                    os.replace(tmp, p)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    # 杀毒/索引/并发读取短暂占用，退避重试
                    time.sleep(0.05 * (attempt + 1))
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        self._forget(p)

    def delete(self, sid: str) -> None:
        p = self._path(sid)
        p.unlink(missing_ok=True)
        self._forget(p)

    def _forget(self, path: Path) -> None:
        with self._summary_lock:
            self._summary_cache.pop(str(path), None)

    def clear(self, sid: str) -> None:
        s = self.get(sid)
        if s:
            s.messages = []
            for t in s.threads:
                t.messages = []
            s.title = "新会话"
            self.save(s)