"""长跑模式（P2）：LoopContext 状态字典、LoopManager 状态机与压缩纯逻辑。

设计要点（对齐技术书 §6）：
- run_agent_loop 只认 LoopContext 与两个 services 钩子（loop_state/
  loop_compact），不直接碰 sqlite/provider，保持引擎可单测；
- 四个保护机制：每 N 轮强制汇报、空转自停、滚动压缩、干净停止，
  阈值全部在 LoopSettings，默认值与书一致（5/3/40000/4）；
- loop_sessions 是重启 reconcile 的权威状态，纯内存 ChatRun 只管传输。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from ..agentdb import loop_store

# 上下文超长报错识别（中英文厂商文案不一，宽松匹配 + 数字提取）
_OVERFLOW_RE = re.compile(
    r"context[ _]?(?:length|window)|maximum context|too many tokens|"
    r"上下文[长度超]|超出.*上下文|context_length_exceeded",
    re.IGNORECASE,
)
_NUM_RE = re.compile(r"(\d{3,})")

# 字符→token 估算：中日韩单字接近 1 token 甚至更多，除以 1.5 只高估不低估
_CHARS_PER_TOKEN = 1.5
_COMPACT_RESERVE_OUTPUT = 30000

# 普通聊天上下文护栏：达到这些窗口字数时提示用户可考虑压缩；
# 用户不处理时，超过硬上限的请求仅转发最近的历史（落盘历史不变，对用户不可见）。
CONTEXT_WARN_THRESHOLDS = (200_000, 400_000, 600_000, 800_000)
CONTEXT_TRUNCATE_CHARS = 900_000


@dataclass
class LoopContext:
    task: str
    loop_db_id: int
    finished: bool = False
    finish_text: str = ""
    since_spoke: int = 0       # 距上次汇报的工具轮数
    idle_turns: int = 0        # 连续无工具操作轮数
    total_rounds: int = 0
    compacts: int = 0
    compact_note: str = ""
    context_limit: int = 0     # 从超长报错中学到的模型上下文上限
    stop_status: str = ""      # DB 状态非 running 时由 loop_state 钩子填入

    def terminal_reason(self) -> str:
        if self.finished:
            return loop_store.LOOP_DONE
        return self.stop_status or loop_store.LOOP_INTERRUPTED


# ---------- 纯逻辑：体积估算与压缩切点 ----------

def history_char_size(history: List[Any]) -> int:
    total = 0
    for m in history:
        total += len(getattr(m, "content", None) or "")
        for tc in getattr(m, "tool_calls", None) or []:
            total += len(str(getattr(tc, "arguments", None) or ""))
    return total


def estimate_tokens(chars: int) -> int:
    return int(chars / _CHARS_PER_TOKEN) + 1


def parse_context_overflow(exc: BaseException) -> Optional[int]:
    """从提供商异常里识别上下文超长并提取 token 上限；不匹配返回 None。"""
    text = "%s: %s" % (type(exc).__name__, exc)
    if not _OVERFLOW_RE.search(text):
        return None
    nums = [int(n) for n in _NUM_RE.findall(text)]
    if not nums:
        return 0  # 确认超长但拿不到数字：触发压缩，不更新 context_limit
    # 文案里的数字可能是 requested/limit/max 混用，取最大作为窗口估计
    return max(nums)


def should_compact(history: List[Any], ctx: LoopContext,
                   settings: Any) -> bool:
    chars = history_char_size(history)
    if chars >= int(settings.compact_char_threshold):
        return True
    window = int(ctx.context_limit or getattr(settings, "context_window", 0) or 0)
    if window <= 0:
        return False
    budget = window - _COMPACT_RESERVE_OUTPUT
    if budget <= 0:
        return False
    return estimate_tokens(chars) >= int(budget * settings.compact_token_ratio)


def compact_cut_point(history: List[Any], keep_rounds: int) -> int:
    """保留最近 keep_rounds 个完整工具轮，返回尾部起始下标。

    工具轮 = 带 tool_calls 的 assistant 及其后续 tool 消息；切点永远落在
    assistant 边界，绝不会把 tool_calls 与对应 tool 响应拆开。
    工具轮不足 keep_rounds 时回退为保留最旧的一个完整工具轮（仍需给大
    体积工具结果让路）；完全没有工具轮时返回 len(history)（无可压缩内容）。
    """
    seen = 0
    oldest_round = -1
    for i in range(len(history) - 1, -1, -1):
        m = history[i]
        if getattr(m, "role", "") == "assistant" and getattr(m, "tool_calls", None):
            seen += 1
            oldest_round = i
            if seen == keep_rounds:
                return i
    return oldest_round if oldest_round >= 0 else len(history)


def render_head_for_summary(history: List[Any], cut: int) -> str:
    """把切点之前的消息渲染成给摘要模型的纯文本纪要素材。"""
    labels = {"user": "用户", "assistant": "助手", "tool": "工具结果",
              "system": "系统"}
    lines = []
    for m in history[:cut]:
        role = labels.get(getattr(m, "role", ""), getattr(m, "role", "?"))
        content = getattr(m, "content", None) or ""
        if not content and getattr(m, "tool_calls", None):
            calls = ", ".join(
                "%s(%s)" % (c.name, str(c.arguments)[:200])
                for c in m.tool_calls
            )
            content = "调用工具：" + calls
        if content:
            lines.append("【%s】%s" % (role, content))
    return "\n".join(lines)[-30000:]


def render_interrupt_notice(items: List[dict]) -> str:
    lines = ["用户在长跑过程中发来以下指示，必须优先处理："]
    for it in items:
        who = it.get("name") or "用户"
        lines.append("- （%s）%s" % (who, it.get("text", "")))
    return "\n".join(lines)


# ---------- LoopManager：每用户 agent.db 的同步状态机 ----------

class LoopManager:
    def __init__(self, data_dir, settings: Any) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings

    def _conn(self, uid: str):
        return loop_store.open_for(self.data_dir, uid)

    # 申请-审批
    def request_create(self, uid: str, session_id: str, task: str,
                       reason: str) -> int:
        conn = self._conn(uid)
        try:
            return loop_store.create_request(
                conn, session_id=session_id, task=task,
                reason=reason, requester_uid=uid,
            )
        finally:
            conn.close()

    def request_list(self, uid: str, status: Optional[str] = None):
        conn = self._conn(uid)
        try:
            return loop_store.list_requests(conn, status)
        finally:
            conn.close()

    def request_get(self, uid: str, request_id: int):
        conn = self._conn(uid)
        try:
            return loop_store.get_request(conn, request_id)
        finally:
            conn.close()

    def decide(self, uid: str, request_id: int, approve: bool,
               decided_by: str):
        conn = self._conn(uid)
        try:
            return loop_store.decide_request(
                conn, request_id, approve, decided_by
            )
        finally:
            conn.close()

    # 运行态
    def session_create(self, uid: str, session_id: str, task: str,
                       request_id: int = 0) -> int:
        conn = self._conn(uid)
        try:
            return loop_store.create_session(
                conn, session_id=session_id, task=task, request_id=request_id
            )
        finally:
            conn.close()

    def session_get(self, uid: str, loop_id: int):
        conn = self._conn(uid)
        try:
            return loop_store.get_session(conn, loop_id)
        finally:
            conn.close()

    def session_list(self, uid: str, status: Optional[str] = None):
        conn = self._conn(uid)
        try:
            return loop_store.list_sessions(conn, status)
        finally:
            conn.close()

    def running_for(self, uid: str, session_id: str):
        conn = self._conn(uid)
        try:
            return loop_store.running_session_for(conn, session_id)
        finally:
            conn.close()

    def refresh(self, uid: str, ctx: LoopContext) -> LoopContext:
        """安全点调用：落进度并读停止状态（worker 轮询的干净停止机制）。"""
        conn = self._conn(uid)
        try:
            loop_store.update_progress(
                conn, ctx.loop_db_id,
                rounds=ctx.total_rounds, compacts=ctx.compacts,
                compact_note=ctx.compact_note,
            )
            row = loop_store.get_session(conn, ctx.loop_db_id)
            if row is not None and row["status"] != loop_store.LOOP_RUNNING:
                ctx.stop_status = row["status"]
        finally:
            conn.close()
        return ctx

    def finish(self, uid: str, ctx: LoopContext, status: str,
               result: str = "", error: str = "") -> None:
        conn = self._conn(uid)
        try:
            loop_store.update_progress(
                conn, ctx.loop_db_id,
                rounds=ctx.total_rounds, compacts=ctx.compacts,
                compact_note=ctx.compact_note,
            )
            loop_store.finish_session(
                conn, ctx.loop_db_id, status, result=result, error=error
            )
        finally:
            conn.close()

    def stop(self, uid: str, loop_id: int) -> bool:
        conn = self._conn(uid)
        try:
            return loop_store.stop_session(conn, loop_id)
        finally:
            conn.close()

    def reconcile(self, uid: str) -> int:
        conn = self._conn(uid)
        try:
            return loop_store.reconcile_interrupted(conn)
        finally:
            conn.close()

    def reconcile_all(self) -> int:
        """启动时扫描全部用户库：遗留 running 一律置 interrupted。"""
        total = 0
        if not self.data_dir.exists():
            return 0
        for user_dir in sorted(self.data_dir.iterdir()):
            db = user_dir / "agent" / "agent.db"
            if not db.exists():
                continue
            try:
                total += self.reconcile(user_dir.name)
            except Exception:  # 单个坏库不阻断启动
                continue
        return total
