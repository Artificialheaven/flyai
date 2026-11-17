"""记忆工具：让 AI 检索自己参与的其它窗口 / 跨会话记忆簿，并更新长期记忆。

- query_chat_memory  : 检索该角色在本房间已有的记忆条目、其参与的私聊/群聊窗口里
                       语义最相关的片段；若挂载了角色记忆簿（跨会话），一并检索其中条目。
- update_chat_memory : 把新确认的事实写进该角色在本会话的记忆（RoomMember.memory），
                       挂载为「读写」时同步追加进角色级跨会话记忆簿，立即持久化。

所需上下文由路由注入到 ToolContext.services：
  session          -> 当前 Session 对象（查询用它的线程）
  member           -> 当前扮演角色的 RoomMember
  store            -> 当前用户 SessionStore（update 持久化用）
  memory_vec_cache -> MemoryVectorCache：消息向量按 (会话,角色,窗口) 增量缓存
  char_memory      -> CharacterMemoryStore：角色级跨会话记忆簿
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List

from .base import Tool, ToolContext, ToolResult

_MAX_THREADS = 8          # 最多检索多少个包含自己的窗口
_MAX_PASSAGES = 3         # 每个窗口最多返回多少条命中消息
_RECENT_TAIL = 8          # 无 topic 时每个窗口/记忆簿返回最近多少条
_MAX_TEXT = 120           # 单条消息最多保留多少字符
_TOTAL_BUDGET = 3000      # 整段返回给模型的字符上限
_GLOBAL_TOP_K = 6         # 无 embedder 配置时跨窗口返回的总条数
_BOOK_MAX_LINES = 10      # 角色记忆簿单次最多返回多少行
_ROOM_MEM_MAX_LINES = 10  # 本房间记忆（member.memory）单次最多返回多少行
_LEDGER_MAX_LINES = 6     # 剧情台账单次最多返回多少行


def _char_id(member) -> str:
    return (getattr(member, "character_id", "") or "").strip()


def _is_user(member) -> bool:
    return bool(getattr(member, "is_user", False))


def _memory_mode(member) -> str:
    return (getattr(member, "memory_mode", "") or "").strip()


def _memory_owner(member) -> str:
    """记忆簿归属角色 id：显式指定优先，否则用座位自己的角色 id。"""
    return (getattr(member, "memory_char_id", "") or "").strip() or _char_id(member)


def _topic_terms(topic: str) -> List[str]:
    """多关键词主题拆词（空格/逗号/顿号等分隔）；检索按 OR 命中。
    模型常一次给「用户身份 自我介绍 约定」这样的多个关键词。"""
    return [t for t in re.split(r"[\s,，、;；|]+", topic) if t]


def _topic_match(text: str, topic: str, terms) -> bool:
    """整句命中优先，否则任一词命中（terms 已随 topic 一起小写化）。"""
    low = text.lower()
    if topic in low:
        return True
    return any(t in low for t in terms)


def _book_read_mode(member) -> str:
    """记忆簿的读取模式：显式挂载（rw/ro）优先；
    未显式挂载且归属就是本角色自身时，默认按只读可见——
    同一角色在不同房间之间天然延续记忆；
    挂载别人卡的簿（副本/派生卡）则必须显式选择挂载，默认不读。
    没有角色归属的座位（未绑定角色卡且未挂载）不读任何簿，
    避免落到匿名的 ".json" 公共簿。
    """
    if not _memory_owner(member):
        return ""
    mode = _memory_mode(member)
    if mode:
        return mode
    if not (getattr(member, "memory_char_id", "") or "").strip():
        return "ro"
    return ""


def _book_write_mode(member) -> str:
    """记忆簿的写入模式：显式挂载（rw/ro）优先；
    未显式挂载且归属就是本角色自身时，默认按读写——
    自己的跨会话记忆簿本就应在各房间延续（自动整理与 update_chat_memory
    默认即可写入，否则用户会觉得「记忆功能没生效」）；
    挂载别人卡的簿（副本/派生卡）必须显式选成读写，默认不可写。
    没有角色归属的座位（未绑定角色卡且未挂载）不写任何簿，
    避免落到匿名的 ".json" 公共簿。
    """
    if not _memory_owner(member):
        return ""
    mode = _memory_mode(member)
    if mode:
        return mode
    if not (getattr(member, "memory_char_id", "") or "").strip():
        return "rw"
    return ""


class QueryChatMemoryTool(Tool):
    name = "query_chat_memory"
    description = (
        "检索与你这名角色相关的记忆：你在这个房间里已经记下的记忆条目、"
        "在当前会话（本房间）里你参与的私聊/群聊窗口中，"
        "语义最相关的对话片段；若为你挂载了跨会话的角色记忆簿，也会包含其中的相关条目。"
        "只返回最相关的若干条（含来源标注），不返回整段历史。"
        "topic 填要回忆的主题或关键词（可留空：返回最近的消息与记忆簿条目）。"
        "只检索包含你自己的那些窗口，不会读到其它角色专属或与你无关的对话。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "检索关键词，只返回语义/关键词命中的片段（含少量上下文）；留空则返回最近的消息。例如：约定、旅行的日期",
            }
        },
        "required": [],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        services = ctx.services or {}
        session = services.get("session")
        member = services.get("member")
        if session is None or member is None:
            return ToolResult(ok=False, output="[工具错误] 当前对话没有可用的记忆查询上下文")
        char_id = _char_id(member)
        if not char_id:
            return ToolResult(ok=False, output="[工具错误] 当前参与者未绑定角色卡，无法检索其它对话")
        if _is_user(member):
            return ToolResult(ok=False, output="[参数错误] 该参与者是用户而不是 AI")

        topic = str(arguments.get("topic") or "").strip().lower()
        # 语义路在配置了 embeddings 且给了关键词时启用；BM25 路始终可用
        embedder = services.get("embedder") if topic else None

        blocks: List[str] = []
        # 角色级跨会话记忆簿：挂了就先取（跨会话的长期事实优先级最高）
        book = self._book_block(services, member, topic)
        if book:
            blocks.append(book)
        # 剧情台账：物品/任务/伏笔的当前状态（与记忆簿同一挂载）
        ledger_block = self._ledger_block(services, member, topic)
        if ledger_block:
            blocks.append(ledger_block)
        # 本房间记忆（member.memory）：原文同时完整注入在系统提示词里，
        # 但 AI 主动「回忆」时只信工具结果——不在这里给它，它就判定记忆为空，
        # 系统提示词里那份不会被当作检索证据
        room = self._room_memory_block(member, topic)
        if room:
            blocks.append(room)
        try:
            blocks.extend(
                await self._collect(
                    services, session, char_id, topic, embedder,
                    budget_start=sum(len(b) for b in blocks),
                    member_id=getattr(member, "id", "") or "",
                )
            )
        except Exception as exc:  # 检索一律兜底，不让工具炸掉对话流
            return ToolResult(ok=False, output=f"[工具错误] 查询失败: {type(exc).__name__}: {exc}")

        if not blocks:
            return ToolResult(ok=True, output="没有找到包含你的记忆记录。")
        return ToolResult(ok=True, output="\n\n".join(blocks))

    @staticmethod
    def _book_block(services, member, topic: str):
        """角色记忆簿（跨会话）命中条目；无存储/无条目/不该可见返回 None。"""
        mode = _book_read_mode(member)
        if mode not in ("rw", "ro"):
            return None
        store = services.get("char_memory")
        if store is None:
            return None
        entries = store.load(_memory_owner(member))
        if not entries:
            return None
        if topic:
            terms = _topic_terms(topic)
            picked = [e for e in entries if _topic_match(e, topic, terms)]
        else:
            picked = entries[-_RECENT_TAIL:]
        if not picked:
            return None
        picked = picked[-_BOOK_MAX_LINES:]
        tag = "（只读）" if mode == "ro" else ""
        return "[角色记忆簿·跨会话{0}]\n{1}".format(
            tag, "\n".join(e[:_MAX_TEXT] for e in picked)
        )

    @staticmethod
    def _ledger_block(services, member, topic: str):
        """剧情台账命中条目；无存储/无条目/不该可见返回 None。"""
        from ..memory.ledger import match_items, open_summary

        mode = _book_read_mode(member)
        if mode not in ("rw", "ro"):
            return None
        store = services.get("char_memory")
        if store is None:
            return None
        ledger = store.load_ledger(_memory_owner(member))
        if topic:
            items = match_items(ledger, _topic_terms(topic),
                                limit=_LEDGER_MAX_LINES)
        else:
            items = open_summary(ledger, limit_per_kind=2)
        if not items:
            return None
        lines = [
            "- [{0}/{1}] {2}: {3}".format(
                it.kind, it.status, it.name, it.state
            )
            for it in items[:_LEDGER_MAX_LINES]
        ]
        return "[剧情台账]\n" + "\n".join(s[:_MAX_TEXT] for s in lines)

    @staticmethod
    def _room_memory_block(member, topic: str):
        """本房间记忆（RoomMember.memory 的条目行）命中内容；无记忆/无命中返回 None。

        原文同时完整出现在系统提示词的【你在这个聊天里的记忆】里，这里每条截断到
        _MAX_TEXT 即可——工具结果的作用是让 AI 确认「自己确实有这份记忆」，细节看系统提示词。
        """
        raw = (getattr(member, "memory", "") or "").strip()
        if not raw:
            return None
        lines = []
        for ln in raw.splitlines():
            s = ln.strip()
            if s.startswith("- "):
                s = s[2:].strip()
            if s:
                lines.append(s)
        if not lines:
            return None
        if topic:
            terms = _topic_terms(topic)
            picked = [s for s in lines if _topic_match(s, topic, terms)]
        else:
            picked = lines[-_ROOM_MEM_MAX_LINES:]
        if not picked:
            return None
        picked = picked[-_ROOM_MEM_MAX_LINES:]
        return "[本房间记忆]\n" + "\n".join(s[:_MAX_TEXT] for s in picked)

    async def _collect(self, services, session, char_id: str, topic: str,
                       embedder=None, budget_start: int = 0,
                       member_id: str = "") -> List[str]:
        thread_sets = self._candidate_sets(session, char_id, member_id)
        out: List[str] = []
        used = budget_start

        if topic:
            # 混合排序：向量余弦 + BM25 双路 RRF（embedder 缺失/失败自动 BM25 单路），
            # 覆盖全部历史（消息向量走增量缓存，不产生重复 embedding 开销）
            hits = await self._rank_all(
                services, session.id, char_id, thread_sets, topic, embedder
            )
            log = services.get("recall_logs")
            if log is not None:
                try:
                    log.add(kind="active", topic=topic, hits=len(hits))
                except Exception:
                    pass
            for _score, label, who, text in hits:
                block = "[{0}] {1}: {2}".format(
                    label, who, text[:_MAX_TEXT]
                )
                if used + len(block) > _TOTAL_BUDGET:
                    continue  # 可能后面有条更短的放得下
                out.append(block)
                used += len(block)
            return out

        # 无 topic：每个窗口取最近尾巴
        for _tid, label, msgs in thread_sets:
            if used >= _TOTAL_BUDGET:
                break
            tail = msgs[-_RECENT_TAIL:]
            block = "[{0}] {1}".format(
                label,
                " | ".join(
                    f"{w}: {txt[:_MAX_TEXT]}" for w, txt, _i in tail
                ),
            )
            if used + len(block) > _TOTAL_BUDGET:
                continue
            out.append(block)
            used += len(block)
        return out

    async def _rank_all(self, services, sid, char_id, thread_sets,
                        topic: str, embedder) -> List:
        """逐窗口混合排序（向量缓存键按窗口隔离），全局按分数取 top-k。

        返回 [(score, label, who, text), ...]。
        """
        from ..memory.recall import Candidate, hybrid_rank

        cache = services.get("memory_vec_cache")
        all_scored: List = []
        for tid, label, msgs in thread_sets:
            cands = [
                Candidate(
                    text=text, kind="message", position=i,
                    label=label, who=who, identity=ident,
                )
                for i, (who, text, ident) in enumerate(msgs)
            ]
            ranked = await hybrid_rank(
                cands, topic,
                embedder=embedder,
                vec_cache=cache,
                cache_key=(sid, char_id, tid),
            )
            for cand, score in ranked[:_MAX_PASSAGES]:
                all_scored.append((score, label, cand.who, cand.text))

        all_scored.sort(key=lambda x: x[0], reverse=True)
        top_k = _GLOBAL_TOP_K
        if embedder is not None:
            try:
                top_k = int(getattr(embedder.cfg, "top_k", _GLOBAL_TOP_K)
                            or _GLOBAL_TOP_K)
            except (AttributeError, TypeError, ValueError):
                top_k = _GLOBAL_TOP_K
        return all_scored[:max(1, top_k)]

    @staticmethod
    def _candidate_sets(session, char_id: str, member_id: str = ""):
        """收集该角色参与的所有窗口里的消息（过滤 tool/hidden/空），按窗口分组。

        传 member_id 时按座位精确匹配窗口（同一张卡挂了多个座位时，不会把
        另一个座位的私聊窗口也回顾进来）；否则回退按 character_id 匹配。
        每条消息带身份 (tid, index, sha1[:12])，供向量增量缓存判定前缀。
        返回 [(tid, label, [(who, text, ident), ...]), ...]。
        """
        from ..tools.memory_cache import item_identity

        sets: List = []
        for t in session.threads:
            if not QueryChatMemoryTool._includes(session, t, char_id, member_id):
                continue
            msgs = []
            for idx, m in enumerate(t.messages):
                if m.role not in ("user", "assistant"):
                    continue
                if m.hidden:
                    continue
                text = (m.content or "").strip()
                if not text:
                    continue
                who = m.name or ("你" if m.role == "user" else "AI")
                ident = item_identity(t.id, idx, text)
                msgs.append((who, text, ident))
            if msgs:
                label = t.title or ("群聊" if t.kind == "group" else "私聊")
                sets.append((t.id, label, msgs))
        return sets[:_MAX_THREADS]

    @staticmethod
    def _includes(session, thread, char_id: str, member_id: str = "") -> bool:
        if member_id:
            return member_id in thread.member_ids
        for mid in thread.member_ids:
            m = session.member(mid)
            if m is not None and not _is_user(m) and _char_id(m) == char_id:
                return True
        return False


class UpdateChatMemoryTool(Tool):
    name = "update_chat_memory"
    description = (
        "把你在当前对话里确认的新事实写进长期记忆，供以后沿用：写入本会话记忆；"
        "你自己名下的跨会话角色记忆簿会同时记入，其它会话里的你也能记得"
        "（仅当你被只读挂载到别人的记忆簿时，才只写本会话）。"
        "add 为要记住的一句话或条目，写入后立即生效并永久保存。"
        "适合记录：对方告诉你的重要信息、约定、时间、地点，以及你或对方的状态变化等仍需长期记得的内容。"
        "不要反复写入完全相同的内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "add": {
                "type": "string",
                "description": "要追加记忆的一句话/条目，例如：用户是猫娘老板娘，经营一家魔法杂货铺。",
            }
        },
        "required": ["add"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        services = ctx.services or {}
        session = services.get("session")
        member = services.get("member")
        store = services.get("store")
        add = str(arguments.get("add") or "").strip()
        if not add:
            return ToolResult(ok=False, output="[参数错误] 请提供要记住的内容（add）")
        if session is None or member is None or store is None:
            return ToolResult(ok=False, output="[工具错误] 当前对话没有可用的 AI 记忆上下文")
        if _is_user(member):
            return ToolResult(ok=False, output="[参数错误] 该参与者是用户而不是 AI")

        stamp = date.today().isoformat()
        if member.memory and member.memory.strip():
            member.memory = f"{member.memory.strip()}\n- [{stamp}] {add}"
        else:
            member.memory = f"- [{stamp}] {add}"

        try:
            store.save(session)
        except Exception as exc:
            return ToolResult(ok=False, output=f"[工具错误] 记忆保存失败: {type(exc).__name__}: {exc}")

        note = ""
        # 角色记忆簿：读写挂载时同步追加；「记忆存档」（book_force_write）是
        # 用户显式触发的一次性记入，即使本座位是只读/未挂载也写入；
        # 其余情况只写本会话，不报错
        force_write = bool(services.get("book_force_write"))
        owner = _memory_owner(member)
        if owner and (_book_write_mode(member) == "rw" or force_write):
            char_store = services.get("char_memory")
            if char_store is not None:
                try:
                    char_store.append(owner, add)
                    note = "\n（已同步进跨会话角色记忆簿）"
                except Exception as exc:
                    note = f"\n[警告] 跨会话记忆簿写入失败: {type(exc).__name__}: {exc}"
        return ToolResult(ok=True, output=f"[记忆已更新]{note}\n{member.memory}")
