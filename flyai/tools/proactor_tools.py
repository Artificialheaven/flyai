"""P3 工具：第一人称自我记忆读写 + 承诺/提醒维护。

- remember_self / recall_self：用户全局「我」视角记忆（agent.db），
  与角色维度记忆簿职责分离；可见性由 self_memory 类别开关控制。
- remember_commitment / close_commitment：维护本会话承诺，
  ReminderTicker 到点经后台 ChatRun 提醒；可见性由 proactor 类别开关控制。
工具层不做功能开关裁决（注册表/feature_prefs 负责可见性），但服务缺失时
一律 ok=False，不抛异常。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from .base import Tool, ToolContext, ToolResult


class RememberSelfTool(Tool):
    """把一条第一人称事实写进全局自我记忆（受每日新建配额约束）。"""

    name = "remember_self"
    description = (
        "把关于「我自己」的长期事实存入第一人称自我记忆：我的经历、偏好、习惯、"
        "重要人际关系与承诺。一条只写一个简洁事实，会被以后的对话自动回忆。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "statement": {"type": "string",
                          "description": "第一人称陈述，如：我答应过小林周末一起看电影"},
            "tags": {"type": "array", "items": {"type": "string"},
                     "description": "可选标签，便于检索"},
            "importance": {"type": "number",
                           "description": "重要度 0.0-1.0，默认 0.5"},
        },
        "required": ["statement"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        store = ctx.services.get("self_memory_store")
        if store is None:
            return ToolResult(ok=False, output="[自我记忆未启用] 请管理员先在设置中开启。")
        statement = str(arguments.get("statement") or "").strip()
        if not statement:
            return ToolResult(ok=False, output="statement 不能为空")
        tags = arguments.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tags = [str(t) for t in tags][:10]
        try:
            importance = float(arguments.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        try:
            from ..agentdb.self_memory import QuotaExceeded

            row, created = await asyncio.to_thread(
                store.put, ctx.uid, statement, tags, importance
            )
        except QuotaExceeded as exc:
            return ToolResult(ok=False, output="[今日配额已用完] %s" % exc)
        except Exception as exc:  # 数据库异常不炸聊天轮
            return ToolResult(ok=False,
                              output="[自我记忆写入失败] %s: %s"
                                     % (type(exc).__name__, exc))
        action = "新建" if created else "合并更新（不占今日配额）"
        return ToolResult(output="已%s自我记忆 #%s。" % (action, row["id"]))


class RecallSelfTool(Tool):
    """按关键词召回第一人称自我记忆。"""

    name = "recall_self"
    description = (
        "按关键词回忆我自己的长期记忆（经历、偏好、承诺过的事）；"
        "不确定历史细节时先调用再回答，不要凭空猜测。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "检索关键词或话题"},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        store = ctx.services.get("self_memory_store")
        if store is None:
            return ToolResult(ok=False, output="[自我记忆未启用] 请管理员先在设置中开启。")
        keyword = str(arguments.get("keyword") or "").strip()
        if not keyword:
            return ToolResult(ok=False, output="keyword 不能为空")
        try:
            hits = await asyncio.to_thread(store.recall, ctx.uid, keyword)
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[自我记忆召回失败] %s: %s"
                                     % (type(exc).__name__, exc))
        if not hits:
            return ToolResult(output="没有回忆起相关的自我记忆。")
        lines = ["%d. %s" % (i + 1, r["statement"]) for i, r in enumerate(hits)]
        return ToolResult(output="回忆起 %d 条自我记忆：\n%s"
                                 % (len(hits), "\n".join(lines)))


class RememberCommitmentTool(Tool):
    """登记一条到点要提醒的承诺。"""

    name = "remember_commitment"
    description = (
        "登记一条有明确时间的承诺/待办，到点后系统会提醒你主动开口。"
        "due_at 可给 epoch 秒，或 'YYYY-MM-DD HH:MM' 本地时间，或仅 'HH:MM'。"
        "只记确实约定过时间的事，不要给自己编造日程。"
    )
    risk = False
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "承诺内容，一句话说清要做什么"},
            "due_at": {"description": "到期时间：epoch 秒或 'YYYY-MM-DD HH:MM' 或 'HH:MM'"},
        },
        "required": ["content", "due_at"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        store = ctx.services.get("commitment_store")
        if store is None:
            return ToolResult(ok=False, output="[主动行为未启用] 承诺功能不可用。")
        content = str(arguments.get("content") or "").strip()
        if not content:
            return ToolResult(ok=False, output="content 不能为空")
        from ..proactor import clock

        tzname = ctx.services.get("tzname", "Asia/Shanghai")
        import time

        due = clock.parse_due_at(arguments.get("due_at"), time.time(), tzname)
        if due is None:
            return ToolResult(ok=False,
                              output="due_at 无法解析，请给 epoch 秒或 'YYYY-MM-DD HH:MM'")
        member = ctx.services.get("member")
        owner = getattr(member, "name", "") or ""
        try:
            row = await asyncio.to_thread(
                store.add, ctx.uid, ctx.session_id, content, due, owner
            )
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[承诺登记失败] %s: %s"
                                     % (type(exc).__name__, exc))
        return ToolResult(
            output="已登记承诺 #%s，%s 到期会提醒你：%s"
                   % (row["id"], clock.format_local(float(row["due_at"]), tzname),
                      content[:100])
        )


class CloseCommitmentTool(Tool):
    """兑现/关闭一条承诺（LIVE_CTX 的未结约定里带编号）。"""

    name = "close_commitment"
    description = "承诺已经兑现或确认取消时关闭它，编号见实时上下文的未结约定列表。"
    parameters = {
        "type": "object",
        "properties": {
            "commitment_id": {"type": "integer", "description": "承诺编号 #id"},
            "cancel": {"type": "boolean",
                       "description": "true=取消（不会再提醒），false/省略=已兑现关闭"},
        },
        "required": ["commitment_id"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any],
                  ctx: ToolContext) -> ToolResult:
        store = ctx.services.get("commitment_store")
        if store is None:
            return ToolResult(ok=False, output="[主动行为未启用] 承诺功能不可用。")
        try:
            cid = int(arguments.get("commitment_id"))
        except (TypeError, ValueError):
            return ToolResult(ok=False, output="commitment_id 必须是整数")
        try:
            if arguments.get("cancel"):
                ok = await asyncio.to_thread(store.cancel, ctx.uid, cid)
                verb = "取消"
            else:
                ok = await asyncio.to_thread(store.close, ctx.uid, cid)
                verb = "关闭"
        except Exception as exc:
            return ToolResult(ok=False,
                              output="[承诺%s失败] %s: %s"
                                     % (verb, type(exc).__name__, exc))
        if not ok:
            return ToolResult(ok=False, output="承诺 #%s 不存在或已关闭" % cid)
        return ToolResult(output="承诺 #%s 已%s。" % (cid, verb))


PROACTOR_TOOLS = (
    RememberSelfTool,
    RecallSelfTool,
    RememberCommitmentTool,
    CloseCommitmentTool,
)
