"""会话数据结构。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..schemas import Message


@dataclass
class RoomMember:
    """房间参与者：一个 AI 角色（或用户座位）。每个 AI 在自己每个窗口都有记忆。"""

    id: str = ""
    name: str = ""
    character_id: str = ""
    is_user: bool = False           # 用户座位无角色
    override: Optional[Dict[str, Any]] = None  # R7 会话专属角色副本（to_dict）
    memory: str = ""                # 该 AI 在本房间的记忆簿（各窗口共享）
    attributes: Dict[str, Any] = field(default_factory=dict)
    provider_id: str = ""           # 座位专属 LLM 提供商；空=用请求级（房间默认）
    # 角色级全局记忆簿挂载：""=不挂载；"rw"=可读写；"ro"=只读
    memory_mode: str = ""
    # 挂载的记忆簿归属角色 id；空=自己的角色（继承别的卡时填那张卡 id，限同一用户）
    memory_char_id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "character_id": self.character_id,
            "is_user": self.is_user,
            "override": self.override,
            "memory": self.memory,
            "attributes": self.attributes,
            "provider_id": self.provider_id,
            "memory_mode": self.memory_mode,
            "memory_char_id": self.memory_char_id,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RoomMember":
        d = d or {}
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            character_id=d.get("character_id", ""),
            is_user=bool(d.get("is_user", False)),
            override=d.get("override"),
            memory=d.get("memory", "") or "",
            attributes=d.get("attributes", {}) or {},
            provider_id=d.get("provider_id", "") or "",
            memory_mode=d.get("memory_mode", "") or "",
            memory_char_id=d.get("memory_char_id", "") or "",
        )


@dataclass
class Thread:
    """房间内的一个窗口：单 AI 私聊（dm）或群聊（group）。窗口内独立历史=该窗口记忆。"""

    id: str = ""
    kind: str = "dm"                 # dm | group
    title: str = ""
    member_ids: List[str] = field(default_factory=list)
    messages: List[Message] = field(default_factory=list)
    # 历史编辑/重生成版本：key=锚点用户消息 id，见 sessions/versions.py
    edits: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "member_ids": list(self.member_ids),
            "messages": [m.to_dict() for m in self.messages],
            "edits": self.edits,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Thread":
        d = d or {}
        return cls(
            id=d.get("id", ""),
            kind=d.get("kind", "dm"),
            title=d.get("title", ""),
            member_ids=list(d.get("member_ids", []) or []),
            messages=[Message.from_dict(m) for m in d.get("messages", [])],
            edits=d.get("edits", {}) or {},
        )


@dataclass
class Session:
    """会话 = 房间。beta：内含多个参与者（members）与多个窗口（threads）。"""

    id: str = ""
    title: str = "新会话"
    provider_id: str = ""
    character_id: str = ""           # 兼容：单 AI 快捷引用
    world_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    messages: List[Message] = field(default_factory=list)  # 兼容字段
    attributes: Dict[str, Any] = field(default_factory=dict)
    # 会话专属副本（R7）：序列化的 Character/World dict，优先于 character_id/world_id 引用
    character_override: Optional[Dict[str, Any]] = None
    world_override: Optional[Dict[str, Any]] = None
    # beta：多 AI 群聊
    members: List[RoomMember] = field(default_factory=list)
    threads: List[Thread] = field(default_factory=list)
    pinned: bool = False              # 置顶（侧栏排序优先）
    # 整个会话自创建以来的累计 token（跨刷新持久），各轮 done.usage 累加
    token_usage: Dict[str, int] = field(default_factory=dict)
    # 已提示过的上下文阈值：{窗口 id: [200000, 400000, ...]}；旧会话缺省为空
    context_warned: Dict[str, List[int]] = field(default_factory=dict)
    # normal=普通聊天；galgame=Galgame 模式（全屏视觉小说，单角色）
    mode: str = "normal"
    # 本会话选定的开局世界书条目标题；空=按卡默认（保持卡内 pinned 状态）
    opening_entry: str = ""
    # Galgame 持久场景态：{"background":{"id","url"},"sprite":{"id","url"}}
    galgame: Dict[str, Any] = field(default_factory=dict)
    # Galgame 已合成 TTS 清单：对白文本 -> 音频 url（刷新后直接复用，不再重新合成）
    galgame_tts: Dict[str, str] = field(default_factory=dict)
    # 单窗口回复最大 token 覆盖；0=跟随用户全局设置/提供商配置
    max_tokens: int = 0
    # 本窗口禁用的工具注册名（与请求级 disabled_tools 取并集，服务端强制）
    disabled_tools: List[str] = field(default_factory=list)
    # MVU 变量状态（酒馆卡）：{enabled, initialized, char_id, initial, stat_data}；
    # 空字典=非 MVU 会话。不进 MERGED_FIELDS：长流式保存会覆盖实时状态
    mvu: Dict[str, Any] = field(default_factory=dict)
    # 仅列表摘要使用（不落盘）：所有窗口消息数之和
    message_count: int = 0

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex

    def set_title(self, title: str) -> None:
        self.title = title or self.title

    def add_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        """把一轮（可能含多次 LLM 调用）的用量累加进会话级持久计数。"""
        if not usage:
            return
        p = int(usage.get("prompt_tokens") or 0)
        c = int(usage.get("completion_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))
        hit = int(usage.get("cached_tokens") or 0)
        if not (p or c or t or hit):
            return
        cur = self.token_usage or {}
        self.token_usage = {
            "prompt_tokens": int(cur.get("prompt_tokens") or 0) + p,
            "completion_tokens": int(cur.get("completion_tokens") or 0) + c,
            "total_tokens": int(cur.get("total_tokens") or 0) + t,
            "cached_tokens": int(cur.get("cached_tokens") or 0) + hit,
        }

    def member(self, mid: str) -> Optional[RoomMember]:
        for m in self.members:
            if m.id == mid:
                return m
        return None

    def thread(self, tid: str) -> Optional[Thread]:
        for t in self.threads:
            if t.id == tid:
                return t
        return None

    def remove_member(self, mid: str) -> Optional[RoomMember]:
        """移除成员；调用方负责级联清理（其专属 DM 窗口、各 group.member_ids）。"""
        for i, m in enumerate(self.members):
            if m.id == mid:
                return self.members.pop(i)
        return None

    def remove_thread(self, tid: str) -> Optional[Thread]:
        for i, t in enumerate(self.threads):
            if t.id == tid:
                return self.threads.pop(i)
        return None


def bootstrap_room(session: Session) -> Thread:
    """把旧形态会话（顶层 messages、无 members/threads）就地升级为房间形态。

    生成一个用户座位 + 一个 AI 参与者 + 一条私聊线程，原消息移入线程。
    routes_chat 在发消息前调用：保证后台 drive 与刷新时的 get() 迁移看到
    同一形态，避免 drive 用旧形态覆盖迁移结果、线程 id 反复随机变化。
    """
    user = RoomMember(name="你", is_user=True)
    ai = RoomMember(
        name="AI",
        character_id=session.character_id,
        override=session.character_override,
    )
    session.members = [user, ai]
    thread = Thread(
        kind="dm", title="私聊", member_ids=[ai.id],
        messages=list(session.messages),
    )
    session.threads = [thread]
    session.messages = []
    # 旧消息都是这个唯一 AI 生成的：补记座位与角色卡归属，
    # 之后换卡/自动回忆按角色 id 过滤时不会把旧卡对白算给新卡
    for msg in thread.messages:
        if msg.role != "assistant":
            continue
        if not (msg.actor_id or "").strip():
            msg.actor_id = ai.id
        if not (msg.character_id or "").strip():
            msg.character_id = session.character_id or ""
    return thread