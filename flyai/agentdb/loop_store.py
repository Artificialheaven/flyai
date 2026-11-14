"""长跑模式（P2）的 agent.db 数据访问层：申请审批 + 运行状态机。

同步 API，调用方在 asyncio.to_thread 内使用。每个 uid 一个 agent.db，
行级不需要 uid 列；loop_sessions 是重启 reconcile 的权威依据，
弥补纯内存 ChatRun 重启无痕迹的缺口。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import open_agent_db

# 申请状态
REQ_PENDING = "pending"
REQ_APPROVED = "approved"
REQ_REJECTED = "rejected"
REQUEST_STATUSES = (REQ_PENDING, REQ_APPROVED, REQ_REJECTED)

# 运行状态
LOOP_RUNNING = "running"
LOOP_DONE = "done"
LOOP_STOPPED = "stopped"
LOOP_INTERRUPTED = "interrupted"
LOOP_ERROR = "error"
LOOP_IDLE = "idle"
LOOP_STATUSES = (
    LOOP_RUNNING, LOOP_DONE, LOOP_STOPPED,
    LOOP_INTERRUPTED, LOOP_ERROR, LOOP_IDLE,
)


def open_for(data_dir, uid: str):
    return open_agent_db(data_dir, uid)


# ---------- loop_requests ----------

def create_request(conn, *, session_id: str, task: str, reason: str,
                  requester_uid: str) -> int:
    cur = conn.execute(
        "INSERT INTO loop_requests(session_id, task, reason, requester_uid, "
        "status, created_at) VALUES(?, ?, ?, ?, ?, ?)",
        (session_id, task, reason, requester_uid, REQ_PENDING, time.time()),
    )
    return int(cur.lastrowid)


def list_requests(conn, status: Optional[str] = None) -> List[Dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM loop_requests WHERE status=? ORDER BY id DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM loop_requests ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_request(conn, request_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM loop_requests WHERE id=?", (request_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def decide_request(conn, request_id: int, approve: bool,
                  decided_by: str) -> Optional[Dict[str, Any]]:
    """审批：仅 pending 可裁决，返回更新后的行（不存在/已裁决返回 None）。"""
    row = get_request(conn, request_id)
    if row is None or row["status"] != REQ_PENDING:
        return None
    status = REQ_APPROVED if approve else REQ_REJECTED
    conn.execute(
        "UPDATE loop_requests SET status=?, decided_by=?, decided_at=? WHERE id=?",
        (status, decided_by, time.time(), request_id),
    )
    row["status"] = status
    row["decided_by"] = decided_by
    return row


# ---------- loop_sessions ----------

def create_session(conn, *, session_id: str, task: str,
                   request_id: int = 0) -> int:
    cur = conn.execute(
        "INSERT INTO loop_sessions(session_id, task, status, request_id, "
        "rounds, compacts, created_at) VALUES(?, ?, ?, ?, 0, 0, ?)",
        (session_id, task, LOOP_RUNNING, request_id, time.time()),
    )
    return int(cur.lastrowid)


def get_session(conn, loop_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM loop_sessions WHERE id=?", (loop_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_sessions(conn, status: Optional[str] = None,
                  limit: int = 100) -> List[Dict[str, Any]]:
    if status:
        rows = conn.execute(
            "SELECT * FROM loop_sessions WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM loop_sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def running_session_for(conn, session_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM loop_sessions WHERE session_id=? AND status=? "
        "ORDER BY id DESC LIMIT 1",
        (session_id, LOOP_RUNNING),
    ).fetchone()
    return dict(row) if row is not None else None


def update_progress(conn, loop_id: int, *, rounds: int, compacts: int,
                    compact_note: str) -> None:
    conn.execute(
        "UPDATE loop_sessions SET rounds=?, compacts=?, compact_note=? WHERE id=?",
        (rounds, compacts, compact_note[:8000], loop_id),
    )


def finish_session(conn, loop_id: int, status: str, *,
                   result: str = "", error: str = "") -> bool:
    if status not in LOOP_STATUSES or status == LOOP_RUNNING:
        raise ValueError("非法终态: %r" % status)
    cur = conn.execute(
        "UPDATE loop_sessions SET status=?, result=?, error=?, ended_at=? "
        "WHERE id=? AND status=?",
        (status, result[:20000], error[:4000], time.time(),
         loop_id, LOOP_RUNNING),
    )
    return cur.rowcount > 0


def stop_session(conn, loop_id: int) -> bool:
    """干净停止：只改状态，不 cancel 任务；worker 下一轮开头读到后自行退出。"""
    cur = conn.execute(
        "UPDATE loop_sessions SET status=?, ended_at=? WHERE id=? AND status=?",
        (LOOP_STOPPED, time.time(), loop_id, LOOP_RUNNING),
    )
    return cur.rowcount > 0


def reconcile_interrupted(conn) -> int:
    """进程重启后把遗留 running 全部置 interrupted，返回处理行数。"""
    cur = conn.execute(
        "UPDATE loop_sessions SET status=?, ended_at=? WHERE status=?",
        (LOOP_INTERRUPTED, time.time(), LOOP_RUNNING),
    )
    return cur.rowcount
