"""LIVE_CTX 尾部实时块（P3 §7.5）。

所有逐轮易变内容（时钟、心情、自我记忆、未结约定、待宣布独白）拼成一条
尾部 system 消息；静态 system 前缀与历史头部保持字节稳定以命中前缀缓存。
纯渲染函数不碰 DB，DB 读取集中在 render_for_turn 并走 asyncio.to_thread。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from ..feature_prefs import self_memory_active
from . import clock

logger = logging.getLogger("flyai.live_ctx")

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
# LIVE_CTX 内最多带出的未结约定条数，避免尾部块膨胀
_COMMITMENT_LIMIT = 10


def _clock_line(now: float, tzname: str) -> str:
    local = clock.local_dt(now, tzname)
    return "【现在时刻】%s %s %s（%s）" % (
        local.strftime("%Y-%m-%d %H:%M"),
        _WEEKDAYS[local.weekday()],
        ("工作日" if clock.is_workday(now, tzname) else "休息日"),
        tzname,
    )


def _mood_lines(mood_view: Optional[Dict[str, Any]]) -> List[str]:
    if not mood_view:
        return []
    lines = ["【当前心情】%.0f/100" % float(mood_view.get("mood", 0) or 0)]
    dims = mood_view.get("dims") or {}
    if dims:
        parts = [
            "%s%+.0f" % (name, float(val)) for name, val in dims.items()
        ]
        lines.append("七绪：" + "，".join(parts))
    return lines


def _commitment_lines(items: List[Dict[str, Any]], now: float,
                      tzname: str) -> List[str]:
    if not items:
        return []
    lines = ["【未结约定】"]
    for item in items[:_COMMITMENT_LIMIT]:
        due = float(item.get("due_at") or 0)
        if due <= 0:
            continue
        overdue = "（已到点）" if due <= now else ""
        lines.append(
            "- #%s %s%s：%s" % (
                item["id"], clock.format_local(due, tzname),
                overdue, item.get("content", ""),
            )
        )
    return lines if len(lines) > 1 else []


def build_live_block(*, now: float, tzname: str,
                     mood_view: Optional[Dict[str, Any]] = None,
                     self_memory_block: str = "",
                     commitments: Optional[List[Dict[str, Any]]] = None,
                     announce_text: str = "") -> str:
    """把各实时片段拼成一条尾部 system 消息；全空时返回空串（不追加消息）。"""
    sections: List[str] = [_clock_line(now, tzname)]
    if announce_text:
        # 待宣布的内心独白：本轮注入，ChatRun 成功后才置 injected
        sections.append(announce_text)
    sections.extend(_mood_lines(mood_view))
    if self_memory_block:
        sections.append(self_memory_block.strip())
    sections.extend(_commitment_lines(commitments or [], now, tzname))
    if len(sections) == 1:
        # 只有时钟行：信息量不足以扰动前缀，仍然保留（作业/提醒类场景依赖时间感）
        pass
    return "\n".join(sections)


async def render_for_turn(state, sc, *, session_id: str,
                          character_id: str, query: str,
                          now: Optional[float] = None) -> str:
    """交互轮的 LIVE_CTX 组装入口；任何子系统失败都降级为缺失该段，不阻断聊天。

    心情段绑定 (session_id, character_id)；自我记忆是用户全局第一人称视角；
    约定只列本会话未结项。三个开关分别跟随各自配置段。
    """
    import time

    now = time.time() if now is None else now
    cfg = getattr(state, "config", None)
    tzname = getattr(getattr(cfg, "scheduler", None), "timezone", "Asia/Shanghai")

    mood_view: Optional[Dict[str, Any]] = None
    announce_text = ""
    mood_store = getattr(state, "mood_store", None)
    if mood_store is not None and getattr(getattr(cfg, "mood", None),
                                          "enabled", False) and character_id:
        try:
            mood_view = await asyncio.to_thread(
                mood_store.view, sc.uid, session_id, character_id, now
            )
            if mood_view and mood_view.get("announce") == "pending":
                announce_text = mood_view.get("announce_text") or ""
        except Exception:
            logger.exception("LIVE_CTX 心情段读取失败")
            mood_view, announce_text = None, ""

    self_block = ""
    sm_store = getattr(state, "self_memory_store", None)
    if sm_store is not None and self_memory_active(
            cfg, getattr(sc, "user_settings", None)) and query.strip():
        try:
            self_block = await asyncio.to_thread(
                sm_store.render_block, sc.uid, query, now
            )
        except Exception:
            logger.exception("LIVE_CTX 自我记忆段读取失败")
            self_block = ""

    commitments: List[Dict[str, Any]] = []
    c_store = getattr(state, "commitment_store", None)
    if c_store is not None and getattr(getattr(cfg, "proactor", None),
                                       "enabled", False):
        try:
            commitments = await asyncio.to_thread(
                c_store.list, sc.uid, session_id, "open"
            )
        except Exception:
            logger.exception("LIVE_CTX 约定段读取失败")
            commitments = []

    return build_live_block(
        now=now, tzname=tzname, mood_view=mood_view,
        self_memory_block=self_block, commitments=commitments,
        announce_text=announce_text,
    )


def had_pending_announce(live_block: str) -> bool:
    """渲染块里是否带了待宣布独白（供驱动成功后决定是否 consume）。"""
    return "内心独白·只宣布一次" in (live_block or "")
