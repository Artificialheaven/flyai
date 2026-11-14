"""心情系统（P3）：半衰期数学、来源限幅、七绪衰减与「只宣布一次」状态机。

纯函数部分不依赖 DB，先可测后接线；MoodStore 是每用户 agent.db 的同步 DAL，
调用方在 asyncio.to_thread 内使用。修正 Tide 的时序缺陷：宣布文本先置
pending，等 ChatRun 成功后再 consume 落 injected，模型轮失败不会永久丢失。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..agentdb import open_agent_db
from ..lore.gen import _extract_json
from ..providers.complete import complete
from ..schemas import Message
from .clock import local_date_str

logger = logging.getLogger("flyai.mood")

MOOD_HALF_LIFE = 3 * 3600      # 心情值 3 小时
DIM_HALF_LIFE = 90 * 60        # 七绪 90 分钟
MOOD_DIMS: Tuple[str, ...] = (
    "开心", "踏实", "甜蜜", "心疼", "担心", "难过", "吃醋",
)
# 增量来源限幅：真人对话 ±10 / 主动搭话 ±5 / 后台任务 ±3 / 问候 ±5
SOURCE_CAPS = {"human": 10, "talk": 5, "work": 3, "greet": 5}
DEFAULT_SOURCE_CAP = 3
DIM_MIN, DIM_MAX = -50.0, 100.0
MOOD_MIN, MOOD_MAX = 0.0, 100.0
ANNOUNCE_PENDING = "pending"
ANNOUNCE_INJECTED = "injected"


# ---------- 纯数学 ----------

def decay(anchor: float, updated_at: float, now: float, half_life: float,
          baseline: float = 0.0) -> float:
    """读时指数衰减：向 baseline 回归，half_life 后差距减半。"""
    dt = max(0.0, float(now) - float(updated_at))
    return float(baseline) + (float(anchor) - float(baseline)) * 0.5 ** (dt / half_life)


def clamp_delta(delta: float, source: str) -> float:
    """按来源对增量限幅，未登记来源按最严档（后台任务 ±3）。"""
    cap = SOURCE_CAPS.get(source, DEFAULT_SOURCE_CAP)
    return max(-cap, min(cap, float(delta)))


def decayed_dims(dims: Dict[str, float], updated_at: float,
                 now: float) -> Dict[str, float]:
    """七绪按 90 分钟半衰期向 0 衰减，衰减后绝对值 <0.5 的维度抹除。"""
    out: Dict[str, float] = {}
    for name, anchor in dims.items():
        if name not in MOOD_DIMS:
            continue
        val = decay(anchor, updated_at, now, DIM_HALF_LIFE, 0.0)
        if abs(val) >= 0.5:
            out[name] = round(val, 3)
    return out


def gate_announce(*, delta: float, confidence: int, reason: str,
                  today_count: int, settings: Any) -> bool:
    """显著波动 + 置信度达标 + 有理由 + 当天未超上限，才允许宣布。"""
    if abs(delta) < int(settings.delta_threshold):
        return False
    if int(confidence) < int(settings.min_confidence):
        return False
    if not (reason or "").strip():
        return False
    if today_count >= int(settings.daily_cap):
        return False
    return True


def parse_judgement(text: str) -> Dict[str, Any]:
    """解析判定模型输出（宽松 JSON），非法字段归一化；不抛异常。"""
    try:
        data = _extract_json(text) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        delta = float(data.get("delta", 0) or 0)
    except (TypeError, ValueError):
        delta = 0.0
    dims: Dict[str, float] = {}
    raw_dims = data.get("dims") or {}
    if isinstance(raw_dims, dict):
        for name, val in raw_dims.items():
            if name not in MOOD_DIMS:
                continue
            try:
                dims[name] = float(val)
            except (TypeError, ValueError):
                continue
    try:
        confidence = int(float(data.get("confidence", 0) or 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    return {
        "delta": delta,
        "dims": dims,
        "reason": str(data.get("reason", "") or "")[:300],
        "confidence": confidence,
    }


JUDGE_SYS = (
    "你是角色心情判定器。根据最新对话判断角色心情值与七绪的变化，"
    "只输出一个 JSON 对象：\n"
    '{"delta": 数值, "dims": {"开心": 数值}, "reason": "一句话理由", '
    '"confidence": 0-100}\n'
    "规则：delta 是整体心情值增量（-10~10，正=变好）；dims 的键只能取自"
    "开心/踏实/甜蜜/心疼/担心/难过/吃醋，值为该情绪增量（负=减弱）；"
    "对话没有情绪线索时 delta 与 dims 都给 0；不要编造。"
)


async def judge_mood(provider, transcript: str, current: Dict[str, Any]) -> Dict[str, Any]:
    """调便宜模型做一次判定；任何失败归一为零增量，不向调用方抛异常。"""
    user = (
        "【当前心情值】%.1f\n【当前七绪】%s\n\n【最新对话】\n%s"
        % (
            float(current.get("mood", 70) or 70),
            json.dumps(current.get("dims") or {}, ensure_ascii=False),
            transcript[-6000:],
        )
    )
    try:
        text = await complete(
            provider, [Message(role="user", content=user)],
            system_prompt=JUDGE_SYS,
        )
        return parse_judgement(text)
    except Exception:
        logger.exception("心情判定失败，按零增量处理")
        return {"delta": 0.0, "dims": {}, "reason": "", "confidence": 0}


# ---------- DAL ----------

class MoodStore:
    """每用户 agent.db 的心情状态机（同步 API，短连接）。"""

    def __init__(self, data_dir, settings: Any, tzname: str = "Asia/Shanghai"):
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.tzname = tzname
        # P4：可选提示词注册表，None 时用内置默认文本（行为与 P3 一致）
        self.prompts = None

    def _mood_wrap(self, direction: str, reason: str) -> str:
        if self.prompts is None:
            return ("（内心独白·只宣布一次）刚刚的互动让你心情%s：%s"
                    % (direction, reason))
        return self.prompts.render(
            "mood_wrap", direction=direction, reason=reason
        )

    def _conn(self, uid: str):
        return open_agent_db(self.data_dir, uid)

    def _row(self, conn, session_id: str, character_id: str):
        return conn.execute(
            "SELECT * FROM mood_state WHERE session_id=? AND character_id=?",
            (session_id, character_id),
        ).fetchone()

    def view(self, uid: str, session_id: str, character_id: str,
             now: Optional[float] = None) -> Dict[str, Any]:
        """读时衰减后的当前心情视图（不写库）。"""
        now = time.time() if now is None else now
        baseline = float(self.settings.baseline)
        conn = self._conn(uid)
        try:
            row = self._row(conn, session_id, character_id)
            if row is None:
                return {"mood": baseline, "dims": {}, "announce": "", "row_id": 0}
            d = dict(row)
            mood = decay(d["mood"], d["updated_at"] or now, now,
                         MOOD_HALF_LIFE, baseline)
            try:
                anchors = json.loads(d.get("dims") or "{}")
            except (TypeError, ValueError):
                anchors = {}
            return {
                "mood": round(mood, 2),
                "dims": decayed_dims(anchors, d["updated_at"] or now, now),
                "announce": d.get("announce_status", "") or "",
                "announce_text": d.get("announce_text", "") or "",
                "row_id": d["id"],
            }
        finally:
            conn.close()

    def _today_count(self, conn, row, now: float) -> int:
        today = local_date_str(now, self.tzname)
        if row is not None and row["inject_date"] == today:
            return int(row["inject_count"] or 0)
        return 0

    def record(self, uid: str, session_id: str, character_id: str, *,
               delta: float, dims: Dict[str, float], reason: str,
               confidence: int, source: str,
               now: Optional[float] = None) -> Dict[str, Any]:
        """落一次判定：时间线 + 锚点重定 + 宣布资格裁决，返回更新后视图。"""
        now = time.time() if now is None else now
        settings = self.settings
        baseline = float(settings.baseline)
        clamped = clamp_delta(delta, source)
        conn = self._conn(uid)
        try:
            row = self._row(conn, session_id, character_id)
            if row is None:
                anchor_mood, updated_at = baseline, now
                anchor_dims: Dict[str, float] = {}
                today_count = 0
            else:
                d = dict(row)
                updated_at = d["updated_at"] or now
                anchor_mood = decay(d["mood"], updated_at, now,
                                    MOOD_HALF_LIFE, baseline)
                try:
                    anchor_dims = json.loads(d.get("dims") or "{}")
                except (TypeError, ValueError):
                    anchor_dims = {}
                anchor_dims = decayed_dims(anchor_dims, updated_at, now)
                today_count = self._today_count(conn, row, now)

            new_mood = max(MOOD_MIN, min(MOOD_MAX, anchor_mood + clamped))
            # 七绪增量同样按来源限幅后叠加，再做区间约束
            for name, dd in (dims or {}).items():
                if name not in MOOD_DIMS:
                    continue
                d_clamped = clamp_delta(dd, source)
                anchor_dims[name] = round(
                    max(DIM_MIN, min(DIM_MAX,
                                     anchor_dims.get(name, 0.0) + d_clamped)),
                    3,
                )
            # 与读时衰减同一规则：绝对值不足 0.5 的维度不落锚
            anchor_dims = {k: v for k, v in anchor_dims.items()
                           if abs(v) >= 0.5}

            announce_text = ""
            announce_status = ""
            if gate_announce(delta=clamped, confidence=confidence, reason=reason,
                             today_count=today_count, settings=settings):
                announce_status = ANNOUNCE_PENDING
                direction = "上扬" if clamped > 0 else "低落"
                announce_text = self._mood_wrap(direction, reason)

            dims_json = json.dumps(anchor_dims, ensure_ascii=False)
            if row is None:
                conn.execute(
                    "INSERT INTO mood_state(session_id, character_id, mood, dims, "
                    "updated_at, inject_count, inject_date, announce_status, "
                    "announce_text) VALUES(?, ?, ?, ?, ?, 0, '', ?, ?)",
                    (session_id, character_id, new_mood, dims_json, now,
                     announce_status, announce_text),
                )
            else:
                conn.execute(
                    "UPDATE mood_state SET mood=?, dims=?, updated_at=?, "
                    "announce_status=?, announce_text=? WHERE id=?",
                    (new_mood, dims_json, now, announce_status,
                     announce_text, row["id"]),
                )
            conn.execute(
                "INSERT INTO mood_timeline(session_id, character_id, delta, dims, "
                "reason, confidence, source, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, character_id, clamped,
                 json.dumps(dims or {}, ensure_ascii=False), reason,
                 int(confidence), source, now),
            )
            self._trim_timeline(conn, session_id, character_id)
            return {
                "mood": round(new_mood, 2),
                "dims": anchor_dims,
                "announce": announce_status,
                "announce_text": announce_text,
                "delta": clamped,
            }
        finally:
            conn.close()

    def _trim_timeline(self, conn, session_id: str, character_id: str) -> None:
        keep = max(20, int(self.settings.timeline_keep or 200))
        conn.execute(
            "DELETE FROM mood_timeline WHERE session_id=? AND character_id=? "
            "AND id NOT IN (SELECT id FROM mood_timeline WHERE session_id=? "
            "AND character_id=? ORDER BY id DESC LIMIT ?)",
            (session_id, character_id, session_id, character_id, keep),
        )

    def consume_pending(self, uid: str, session_id: str,
                        character_id: str, now: Optional[float] = None) -> bool:
        """ChatRun 成功后调用：pending → injected，当天注入计数 +1。"""
        now = time.time() if now is None else now
        conn = self._conn(uid)
        try:
            row = self._row(conn, session_id, character_id)
            if row is None or row["announce_status"] != ANNOUNCE_PENDING:
                return False
            today = local_date_str(now, self.tzname)
            count = int(row["inject_count"] or 0)
            if row["inject_date"] != today:
                count = 0
            conn.execute(
                "UPDATE mood_state SET announce_status=?, announce_text='', "
                "inject_date=?, inject_count=? WHERE id=?",
                (ANNOUNCE_INJECTED, today, count + 1, row["id"]),
            )
            return True
        finally:
            conn.close()

    def clear_pending(self, uid: str, session_id: str,
                      character_id: str) -> None:
        """ChatRun 失败时调用：保留 pending 语义以外的清理入口。

        当前策略是失败不消费、下轮重试，因此本方法仅在用户手动清空时使用。
        """
        conn = self._conn(uid)
        try:
            row = self._row(conn, session_id, character_id)
            if row is not None and row["announce_status"]:
                conn.execute(
                    "UPDATE mood_state SET announce_status='', announce_text='' "
                    "WHERE id=?",
                    (row["id"],),
                )
        finally:
            conn.close()

    def timeline(self, uid: str, session_id: str, character_id: str,
                 limit: int = 100) -> List[Dict[str, Any]]:
        conn = self._conn(uid)
        try:
            rows = conn.execute(
                "SELECT * FROM mood_timeline WHERE session_id=? AND character_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, character_id, max(1, min(500, int(limit)))),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def clear(self, uid: str, session_id: str, character_id: str) -> None:
        """人工清空（R7 缓解：误判可人工纠正）。"""
        conn = self._conn(uid)
        try:
            conn.execute(
                "DELETE FROM mood_state WHERE session_id=? AND character_id=?",
                (session_id, character_id),
            )
            conn.execute(
                "DELETE FROM mood_timeline WHERE session_id=? AND character_id=?",
                (session_id, character_id),
            )
        finally:
            conn.close()
