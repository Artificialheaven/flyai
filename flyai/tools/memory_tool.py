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

from ..providers.embedding import dot, normalize
from .base import Tool, ToolContext, ToolResult

_MAX_THREADS = 8          # 最多检索多少个包含自己的窗口
_MAX_PASSAGES = 3         # 每个窗口最多返回的命中片段数
_CONTEXT_RADIUS = 2       # 命中消息前后各带多少条上下文
_RECENT_TAIL = 8          # 无 topic 时每个窗口/记忆簿返回最近多少条
_MAX_TEXT = 120           # 单条消息最多保留多少字符
_TOTAL_BUDGET = 3000      # 整段返回给模型的字符上限
_RAG_SCORE_WINDOW = 300   # 语义检索只在最新这么多条消息内排序
_BOOK_MAX_LINES = 10      # 角色记忆簿单次最多返回多少行
_ROOM_MEM_MAX_LINES = 10  # 本房间记忆（member.memory）单次最多返回多少行


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
    """
    mode = _memory_mode(member)
    if mode:
        return mode
    if not (getattr(member, "memory_char_id", "") or "").strip():
        return "ro"
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
        # 语义（RAG）检索仅在配置了 embeddings 且给了关键词时启用
        embedder = services.get("embedder") if topic else None

        blocks: List[str] = []
        # 角色级跨会话记忆簿：挂了就先取（跨会话的长期事实优先级最高）
        book = self._book_block(services, member, topic)
        if book:
            blocks.append(book)
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
                       embedder=None, budget_start: int = 0) -> List[str]:
        thread_sets = self._candidate_sets(session, char_id)
        # 有 topic + 配置了 embeddings → RAG 语义检索；服务出错则回退关键词
        if topic and embedder is not None:
            rag = await self._rag_search(services, session.id, char_id,
                                         embedder, thread_sets, topic)
            if rag is not None:
                return rag
        # 关键词 passage / 最近尾巴
        out: List[str] = []
        used = budget_start
        for _tid, label, msgs in thread_sets:
            if used >= _TOTAL_BUDGET:
                break
            passages = self._matched_passages(msgs, topic) if topic else [msgs[-_RECENT_TAIL:]]
            for p in passages[-_MAX_PASSAGES:]:
                block = "[{0}] {1}".format(
                    label, " | ".join(f"{w}: {txt[:_MAX_TEXT]}" for w, txt, _i in p)
                )
                if used + len(block) > _TOTAL_BUDGET:
                    break
                out.append(block)
                used += len(block)
        return out

    @staticmethod
    def _candidate_sets(session, char_id: str):
        """收集该角色参与的所有窗口里的消息（过滤 tool/hidden/空），按窗口分组。

        每条消息带身份 (tid, index, sha1[:12])，供向量增量缓存判定前缀。
        返回 [(tid, label, [(who, text, ident), ...]), ...]。
        """
        from ..tools.memory_cache import item_identity

        sets: List = []
        for t in session.threads:
            if not QueryChatMemoryTool._includes(session, t, char_id):
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

    async def _rag_search(self, services, sid, char_id, embedder, thread_sets,
                          topic: str):
        """RAG 语义检索：查询向量化；消息向量走增量缓存（只 embed 新增消息），
        在最新 _RAG_SCORE_WINDOW 条内按余弦相似度取 top-k。

        任何嵌入服务异常返回 None，由调用方回退关键词检索。
        """
        cache = services.get("memory_vec_cache")
        try:
            qv = (await embedder.embed([topic]))[0]
        except Exception:
            return None
        scored = []
        try:
            for tid, label, msgs in thread_sets:
                if cache is not None:
                    desired = [(ident, text) for _w, text, ident in msgs]
                    vecs = await cache.vectors_for(
                        (sid, char_id, tid), desired, embedder
                    )
                else:
                    vecs = await embedder.embed([text for _w, text, _i in msgs])
                for (who, text, _ident), v in zip(msgs, vecs):
                    scored.append((dot(normalize(qv), normalize(v)), label, who, text))
        except Exception:
            return None
        if not scored:
            return None
        # 只在最新窗口条范围内排序（跨窗口按稳定的窗口追加顺序近似时间序）
        scored = scored[-_RAG_SCORE_WINDOW:]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = int(getattr(embedder.cfg, "top_k", 4) or 4)
        out: List[str] = []
        used = 0
        for _sim, label, who, text in scored[:top_k]:
            block = "[{0}] {1}: {2}".format(label, who, text[:_MAX_TEXT])
            if used + len(block) > _TOTAL_BUDGET:
                break
            out.append(block)
            used += len(block)
        return out

    @staticmethod
    def _matched_passages(msgs, topic: str):
        """返回 topic 命中的连续片段列表（合并重叠），每条片段含命中消息的前后少量上下文。"""
        terms = _topic_terms(topic)
        marks = [i for i, (_w, txt, _i) in enumerate(msgs)
                 if _topic_match(txt, topic, terms)]
        if not marks:
            return []
        ranges = []
        for i in marks:
            a = max(0, i - _CONTEXT_RADIUS)
            b = min(len(msgs), i + _CONTEXT_RADIUS + 1)
            if ranges and a <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], b))
            else:
                ranges.append((a, b))
        return [msgs[a:b] for a, b in ranges]

    @staticmethod
    def _includes(session, thread, char_id: str) -> bool:
        for mid in thread.member_ids:
            m = session.member(mid)
            if m is not None and not _is_user(m) and _char_id(m) == char_id:
                return True
        return False


class UpdateChatMemoryTool(Tool):
    name = "update_chat_memory"
    description = (
        "把你在当前对话里确认的新事实写进长期记忆，供以后沿用：写入本会话记忆；"
        "若为你挂载了可读写的跨会话角色记忆簿，会同时记进该簿，其它会话里的你也能记得。"
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
        if _memory_mode(member) == "rw" or force_write:
            char_store = services.get("char_memory")
            if char_store is not None:
                try:
                    char_store.append(_memory_owner(member), add)
                    note = "\n（已同步进跨会话角色记忆簿）"
                except Exception as exc:
                    note = f"\n[警告] 跨会话记忆簿写入失败: {type(exc).__name__}: {exc}"
        return ToolResult(ok=True, output=f"[记忆已更新]{note}\n{member.memory}")
