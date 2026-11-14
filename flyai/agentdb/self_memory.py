"""第一人称自我记忆（P3）：agent.db DAL + BM25 混合召回。

与角色维度记忆簿不同，这里是全局「我」视角：我经历过什么、承诺过什么。
不另造检索引擎，复用 memory/bm25.py 的 CJK 分词与 BM25，排序按
0.75*归一化BM25 + 0.15*importance + 0.10*60天近因。每日写配额只对新建
计费；合并/更新返回 created=False，调用方不得计费（修正 Tide 配额 bug）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..memory.bm25 import BM25, tokenize
from ..proactor.clock import local_date_str
from . import open_agent_db

TRACE_RECALL = "recall"
TRACE_REVISE = "revise"
TRACE_SUPERSEDE = "supersede"


class QuotaExceeded(Exception):
    """当日新建条数超过 daily_quota。"""


class SelfMemoryStore:
    def __init__(self, data_dir, settings: Any,
                 tzname: str = "Asia/Shanghai") -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.tzname = tzname
        # 进程内索引缓存：uid -> (dirty_version, rows)；任何写操作 bump 版本
        self._index_ver: Dict[str, int] = {}

    def _conn(self, uid: str):
        return open_agent_db(self.data_dir, uid)

    def _bump(self, uid: str) -> None:
        self._index_ver[uid] = self._index_ver.get(uid, 0) + 1

    # ---------- 配额 ----------

    def today_used(self, uid: str, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        today = local_date_str(now, self.tzname)
        conn = self._conn(uid)
        try:
            row = conn.execute(
                "SELECT created_count FROM self_memory_state WHERE date=?",
                (today,),
            ).fetchone()
            return int(row["created_count"]) if row else 0
        finally:
            conn.close()

    def _bill_create(self, conn, now: float) -> None:
        today = local_date_str(now, self.tzname)
        conn.execute(
            "INSERT INTO self_memory_state(date, created_count) VALUES(?, 1) "
            "ON CONFLICT(date) DO UPDATE SET "
            "created_count=created_count+1",
            (today,),
        )

    # ---------- 写入 ----------

    def put(self, uid: str, statement: str, tags: Optional[List[str]] = None,
            importance: float = 0.5, now: Optional[float] = None
            ) -> Tuple[Dict[str, Any], bool]:
        """新建或合并一条自我记忆，返回 (row, created)。

        与未失效条目的正文完全相同（去首尾空白后）视为同一事实，只更新
        标签/重要度并记 revise 流水，不计费；否则新建并占用当日配额。
        """
        text = (statement or "").strip()
        if not text:
            raise ValueError("statement 为空")
        text = text[:2000]
        try:
            imp = max(0.0, min(1.0, float(importance)))
        except (TypeError, ValueError):
            imp = 0.5
        tag_text = ",".join(sorted({str(t).strip() for t in (tags or []) if t}))[
            :500
        ]
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            existing = conn.execute(
                "SELECT * FROM self_memories WHERE statement=? AND superseded=0",
                (text,),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE self_memories SET tags=?, importance=? WHERE id=?",
                    (tag_text, imp, existing["id"]),
                )
                self._trace(conn, TRACE_REVISE, int(existing["id"]),
                            "tags=%s importance=%.2f" % (tag_text, imp), now)
                self._bump(uid)
                row = dict(conn.execute(
                    "SELECT * FROM self_memories WHERE id=?",
                    (existing["id"],),
                ).fetchone())
                return row, False

            if self.today_used(uid, now) >= int(self.settings.daily_quota):
                raise QuotaExceeded(
                    "今日自我记忆配额已用完（%d 条）"
                    % int(self.settings.daily_quota)
                )
            cur = conn.execute(
                "INSERT INTO self_memories(statement, tags, importance, "
                "superseded, supersedes_id, created_at, last_accessed, "
                "access_count) VALUES(?, ?, ?, 0, 0, ?, 0, 0)",
                (text, tag_text, imp, now),
            )
            self._bill_create(conn, now)
            self._bump(uid)
            row = dict(conn.execute(
                "SELECT * FROM self_memories WHERE id=?",
                (cur.lastrowid,),
            ).fetchone())
            return row, True
        finally:
            conn.close()

    def supersede(self, uid: str, memory_id: int, new_statement: str,
                  now: Optional[float] = None) -> Dict[str, Any]:
        """用新事实取代旧事实：旧条目标失效，再走正常新建（占用配额）。

        全部走短连接，避免在一个连接持有期间调用 self.put 形成嵌套连接。
        """
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            old = conn.execute(
                "SELECT * FROM self_memories WHERE id=?", (memory_id,)
            ).fetchone()
        finally:
            conn.close()
        if old is None:
            raise ValueError("记忆不存在")
        new_row, _created = self.put(
            uid, new_statement,
            tags=[t for t in (old["tags"] or "").split(",") if t],
            importance=old["importance"], now=now,
        )
        conn = self._conn(uid)
        try:
            conn.execute(
                "UPDATE self_memories SET superseded=1, supersedes_id=? "
                "WHERE id=?",
                (int(new_row["id"]), memory_id),
            )
            self._trace(conn, TRACE_SUPERSEDE, memory_id,
                        "→ #%s" % new_row["id"], now)
            self._bump(uid)
        finally:
            conn.close()
        return new_row

    def _trace(self, conn, kind: str, memory_id: int, detail: str,
               now: float) -> None:
        conn.execute(
            "INSERT INTO self_memory_traces(kind, memory_id, detail, created_at) "
            "VALUES(?, ?, ?, ?)",
            (kind, int(memory_id), (detail or "")[:500], now),
        )

    # ---------- 召回 ----------

    def _active_rows(self, uid: str) -> List[Dict[str, Any]]:
        cached = self._index_ver.get(uid)
        conn = self._conn(uid)
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM self_memories WHERE superseded=0 ORDER BY id"
            ).fetchall()]
        finally:
            conn.close()
        # 缓存版本只做失效标记；行数据本身每次重查（sqlite 足够快且配额约束规模）
        _ = cached
        return rows

    def recall(self, uid: str, keyword: str,
               now: Optional[float] = None) -> List[Dict[str, Any]]:
        """BM25 + 重要度 + 近因混合排序；记 recall 流水但不进模型上下文以外的地方。"""
        now = time.time() if now is None else now
        rows = self._active_rows(uid)
        topk = max(1, int(self.settings.recall_topk))
        if not rows:
            return []
        docs = [tokenize(r["statement"] + " " + (r.get("tags") or ""))
                for r in rows]
        bm25 = BM25(docs)
        raw = bm25.scores(tokenize(keyword or ""))
        peak = max(raw) if raw else 0.0
        scored = []
        for row, bm in zip(rows, raw):
            norm_bm = (bm / peak) if peak > 0 else 0.0
            age_days = max(0.0, (now - float(row["created_at"] or now)) / 86400.0)
            recency = max(0.0, 1.0 - age_days / 60.0)
            score = 0.75 * norm_bm + 0.15 * float(row["importance"]) \
                + 0.10 * recency
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = [r for _s, r in scored[:topk] if _s > 0] if keyword.strip() \
            else [r for _s, r in scored[:topk]]
        conn = self._conn(uid)
        try:
            for r in hits:
                conn.execute(
                    "UPDATE self_memories SET access_count=access_count+1, "
                    "last_accessed=? WHERE id=?",
                    (now, r["id"]),
                )
                self._trace(conn, TRACE_RECALL, int(r["id"]),
                            "q=%s" % keyword[:200], now)
        finally:
            conn.close()
        return hits

    def render_block(self, uid: str, keyword: str,
                     now: Optional[float] = None) -> str:
        """召回结果拼成 LIVE_CTX 用的短文本，受 recall_budget 字符预算约束。"""
        hits = self.recall(uid, keyword, now=now)
        if not hits:
            return ""
        budget = max(50, int(self.settings.recall_budget))
        lines = ["【我的自我记忆】"]
        used = len(lines[0])
        for r in hits:
            line = "- " + r["statement"]
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines) if len(lines) > 1 else ""

    # ---------- 管理/审计 ----------

    def list_all(self, uid: str, include_superseded: bool = False,
                 limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            if include_superseded:
                rows = conn.execute(
                    "SELECT * FROM self_memories ORDER BY id DESC LIMIT ?",
                    (max(1, min(1000, int(limit))),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM self_memories WHERE superseded=0 "
                    "ORDER BY id DESC LIMIT ?",
                    (max(1, min(1000, int(limit))),),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def traces(self, uid: str, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            rows = conn.execute(
                "SELECT * FROM self_memory_traces ORDER BY id DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete(self, uid: str, memory_id: int) -> bool:
        conn = self._conn(uid)
        try:
            cur = conn.execute(
                "DELETE FROM self_memories WHERE id=?", (memory_id,)
            )
            if cur.rowcount:
                self._bump(uid)
            return cur.rowcount > 0
        finally:
            conn.close()
