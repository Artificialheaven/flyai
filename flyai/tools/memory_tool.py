"""记忆工具：让 AI 检索自己参与的其它对话，并更新本会话（本房间）的长期记忆。

- query_chat_memory  : 只检索包含该角色的群聊/私聊窗口里与关键词相关的片段（passage）。
- update_chat_memory : 把新确认的事实写进该角色在当前会话的记忆（RoomMember.memory），立即持久化。

所需上下文由路由注入到 ToolContext.services：
  session -> 当前正在对话的 Session 对象（查询用它的线程；写记忆用同一引用，避免被流程末次 save 覆盖）
  member  -> 当前正在扮演的角色 RoomMember
  store   -> 当前用户的 JsonSessionStore（仅 update_chat_memory 持久化时用）
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from ..providers.embedding import dot, normalize
from .base import Tool, ToolContext, ToolResult

_MAX_THREADS = 8          # 最多检索多少个包含自己的窗口
_MAX_PASSAGES = 3         # 每个窗口最多返回的命中片段数
_CONTEXT_RADIUS = 2       # 命中消息前后各带多少条上下文
_RECENT_TAIL = 8          # 无 topic 时每个窗口返回最近多少条
_MAX_TEXT = 120           # 单条消息最多保留多少字符
_TOTAL_BUDGET = 3000      # 整段返回给模型的字符上限
_RAG_MAX_EMBED = 300      # 语义检索最多向量化的消息条数


def _char_id(member) -> str:
    return (getattr(member, "character_id", "") or "").strip()


def _is_user(member) -> bool:
    return bool(getattr(member, "is_user", False))


class QueryChatMemoryTool(Tool):
    name = "query_chat_memory"
    description = (
        "在当前这个会话（本房间）里，检索你这名角色参与的私聊/群聊窗口中语义最相关的对话片段，"
        "用来确认或回忆之前发生过的事。只返回最相关的若干条消息（含窗口来源），不返回整段历史。"
        "topic 填要回忆的主题或关键词（可留空：返回各窗口最近几条消息）。"
        "只检索包含你自己的那些窗口，不会读到其它角色专属或与你无关的对话，也不会跨到别的会话。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "检索关键词，会只返回命中该关键词的对话片段（含少量上下文）；留空则返回各窗口最近的消息。例如：约定、旅行的日期",
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

        try:
            thread_blocks = await self._collect(session, char_id, topic, embedder)
        except Exception as exc:  # 检索一律兜底，不让工具炸掉对话流
            return ToolResult(ok=False, output=f"[工具错误] 查询失败: {type(exc).__name__}: {exc}")

        if not thread_blocks:
            return ToolResult(ok=True, output="没有找到包含你的其它对话记录。")
        return ToolResult(ok=True, output="\n\n".join(thread_blocks))

    async def _collect(self, session, char_id: str, topic: str, embedder=None) -> List[str]:
        thread_sets = self._candidate_sets(session, char_id)
        # 有 topic + 配置了 embeddings → RAG 语义检索；服务出错则回退关键词
        if topic and embedder is not None:
            rag = await self._rag_search(embedder, thread_sets, topic)
            if rag is not None:
                return rag
        # 关键词 passage / 最近尾巴
        out: List[str] = []
        used = 0
        for label, msgs in thread_sets:
            if used >= _TOTAL_BUDGET:
                break
            passages = self._matched_passages(msgs, topic) if topic else [msgs[-_RECENT_TAIL:]]
            for p in passages[-_MAX_PASSAGES:]:
                block = "[{0}] {1}".format(
                    label, " | ".join(f"{w}: {txt[:_MAX_TEXT]}" for w, txt in p)
                )
                if used + len(block) > _TOTAL_BUDGET:
                    break
                out.append(block)
                used += len(block)
        return out

    @staticmethod
    def _candidate_sets(session, char_id: str):
        """收集该角色参与的所有窗口里的消息（过滤 tool/hidden/空），按窗口分组。"""
        sets: List = []
        for t in session.threads:
            if not QueryChatMemoryTool._includes(session, t, char_id):
                continue
            msgs = []
            for m in t.messages:
                if m.role not in ("user", "assistant"):
                    continue
                if m.hidden:
                    continue
                text = (m.content or "").strip()
                if not text:
                    continue
                who = m.name or ("你" if m.role == "user" else "AI")
                msgs.append((who, text))
            if msgs:
                label = t.title or ("群聊" if t.kind == "group" else "私聊")
                sets.append((label, msgs))
        return sets[:_MAX_THREADS]

    async def _rag_search(self, embedder, thread_sets, topic: str):
        """RAG 语义检索：把查询与每条消息向量化，余弦相似度取 top-k。

        返回命中消息列表；任何嵌入服务异常返回 None，由调用方回退到关键词检索。
        """
        items: List[tuple] = []
        for label, msgs in thread_sets:
            for who, text in msgs:
                items.append((label, who, text))
        if not items:
            return None
        try:
            qv = (await embedder.embed([topic]))[0]
            texts = [it[2] for it in items][:_RAG_MAX_EMBED]
            vecs = await embedder.embed(texts)
        except Exception:
            return None
        qn = normalize(qv)
        scored = []
        for (label, who, text), v in zip(items[:len(vecs)], vecs):
            sim = dot(qn, normalize(v))
            scored.append((sim, label, who, text))
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
        marks = [i for i, (_w, txt) in enumerate(msgs) if topic in txt.lower()]
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
        "把你在当前对话里确认的新事实写进你的长期记忆，供以后的对话沿用。"
        "add 为要记住的一句话或条目。写入后立即生效并永久保存。"
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
        return ToolResult(ok=True, output=f"[记忆已更新]\n{member.memory}")