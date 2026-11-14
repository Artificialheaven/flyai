"""外部触发（P4）：冷却记账 + 事件入队到后台 ChatRun。

端点本身在 api/routes_ext.py，这里放与传输无关的纯逻辑，方便直接单测：
- source 正则白名单、text 长度上限；
- 每 (session_id, source) 一个内存冷却键，先记冷却再入队，
  入队失败回滚（技术书 §8.2），避免失败事件白白吃掉冷却窗口；
- 会话归属按用户库反查：外部 token 不代表任何登录用户。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional, Tuple

from ..user import ADMIN_UID, UserScope
from . import runner

logger = logging.getLogger("flyai.trigger")

SOURCE_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
MAX_TEXT_LEN = 2000
MAX_BODY_BYTES = 64 * 1024


def valid_source(source: str) -> bool:
    return bool(SOURCE_RE.fullmatch(source or ""))


class TriggerCooldown:
    """(session_id, source) -> 上次放行时间戳；纯内存，重启即清空。"""

    def __init__(self, cooldown_seconds: int = 30) -> None:
        self.cooldown_seconds = int(cooldown_seconds)
        self._marks: Dict[Tuple[str, str], float] = {}

    @staticmethod
    def _key(session_id: str, source: str) -> Tuple[str, str]:
        return session_id, source

    def remaining(self, session_id: str, source: str,
                  now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        last = self._marks.get(self._key(session_id, source))
        if last is None:
            return 0.0
        return max(0.0, self.cooldown_seconds - (now - last))

    def record(self, session_id: str, source: str,
               now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._marks[self._key(session_id, source)] = now

    def rollback(self, session_id: str, source: str) -> None:
        self._marks.pop(self._key(session_id, source), None)

    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        dead = [
            k for k, ts in self._marks.items()
            if now - ts > self.cooldown_seconds
        ]
        for k in dead:
            self._marks.pop(k, None)


def _find_owner(state, session_id: str) -> Optional[Tuple[str, UserScope]]:
    """在 admin + 全部用户里找持有该会话的作用域。"""
    uids = [u.id for u in state.user_store.list()]
    if ADMIN_UID not in uids:
        uids.append(ADMIN_UID)
    for uid in uids:
        try:
            if uid in getattr(state, "_scopes", {}):
                sc = state._scopes[uid]
            else:
                sc = UserScope.build(
                    uid, state.config, state.data_dir, state.user_store
                )
            if sc.store.get(session_id) is not None:
                return uid, sc
        except Exception:
            logger.exception("外部触发：作用域构建失败 uid=%s", uid)
    return None


async def dispatch_trigger(state, payload: Dict[str, Any],
                           now: Optional[float] = None
                           ) -> Dict[str, Any]:
    """处理一条已通过 Bearer 鉴权的触发事件。

    返回 {status, body}：status 是 HTTP 状态码，body 是可直接 JSON 化的 dict。
    """
    settings = state.config.ext_trigger
    cooldown: TriggerCooldown = state.trigger_cooldown
    now = time.time() if now is None else now

    session_id = str(payload.get("session_id") or "").strip()
    source = str(payload.get("source") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not session_id:
        return {"status": 400, "body": {"ok": False,
                                        "error": "session_id 为空"}}
    if not valid_source(source):
        return {"status": 400,
                "body": {"ok": False,
                         "error": "source 只允许字母数字及 _.-，长度 1-64"}}
    if not text:
        return {"status": 400, "body": {"ok": False, "error": "text 为空"}}
    if len(text) > MAX_TEXT_LEN:
        return {"status": 400,
                "body": {"ok": False,
                         "error": "text 超过 %d 字" % MAX_TEXT_LEN}}

    owner = _find_owner(state, session_id)
    if owner is None:
        return {"status": 404,
                "body": {"ok": False, "error": "会话不存在"}}
    uid, _sc = owner

    wait = cooldown.remaining(session_id, source, now=now)
    if wait > 0:
        return {"status": 429,
                "body": {"ok": False, "error": "冷却中",
                         "retry_after": int(wait) + 1}}

    # 先记冷却再入队；fire 失败回滚，让下一次事件可以立即重试
    cooldown.record(session_id, source, now=now)
    brief = "【外部事件：%s】%s" % (source, text)
    try:
        result = await runner.fire(
            state, uid, session_id,
            kind="ext-trigger", brief=brief,
            allow_silent=False, with_history=True,
        )
    except Exception as exc:  # fire 自身承诺不抛，这里再兜一层
        logger.exception("外部触发入队异常")
        cooldown.rollback(session_id, source)
        return {"status": 503,
                "body": {"ok": False, "error": "入队异常: %s" % exc}}
    if not result.get("ok"):
        cooldown.rollback(session_id, source)
        return {"status": 503,
                "body": {"ok": False, "error": "入队失败",
                         "reason": result.get("reason", "unknown")}}
    logger.info("外部事件已入队 sid=%s source=%s run=%s",
                session_id, source, result.get("run_id"))
    return {"status": 202,
            "body": {"ok": True, "run_id": result.get("run_id")}}
