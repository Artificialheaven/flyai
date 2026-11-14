"""P3 聊天驱动接线：轮后心情判定、宣布消费、自我记忆抽取。

所有入口都吞异常并记日志——后台增强能力永远不能把已成功的聊天轮变成失败。
sqlite 短连接统一走 asyncio.to_thread；判定模型失败按零增量处理（mood 模块
已有同款保证，这里再加一层任务级兜底）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from ..lore.gen import _extract_json
from ..providers.complete import complete
from ..schemas import Message
from ..agentdb.self_memory import QuotaExceeded
from . import mood as mood_mod

logger = logging.getLogger("flyai.proactor.integrate")

SELF_EXTRACT_SYS = (
    "你是第一人称自我记忆抽取器。从最新对话里抽取值得「我」长期记住的事实："
    "我的经历、偏好、习惯、与重要人物的关系、我亲口做出的承诺。"
    "只输出一个 JSON 数组，每个元素形如 "
    '{"statement": "第一人称陈述句", "tags": ["标签"], "importance": 0.0到1.0}；'
    "没有值得长期记住的内容就输出 []。每条简洁客观，不记客套话、不臆造未发生的事。"
)


# ---------- 判定模型路由 ----------

def resolve_judge_provider(state, fallback, mood_cfg):
    """judge_provider_id 留空回落当前 provider；judge_model 不同则派生临时实例。"""
    pid = (getattr(mood_cfg, "judge_provider_id", "") or "").strip()
    provider = fallback
    if pid:
        named = state.get_provider(pid)
        if named is not None:
            provider = named
    if provider is None:
        return None
    model = (getattr(mood_cfg, "judge_model", "") or "").strip()
    if not model or getattr(provider.cfg, "model", "") == model:
        return provider
    cache = getattr(state, "_judge_providers", None)
    if cache is None:
        return provider
    key = (getattr(provider.cfg, "id", ""), model)
    if key not in cache:
        from ..providers.factory import create_provider

        derived_cfg = provider.cfg.model_copy(update={"model": model})
        cache[key] = create_provider(derived_cfg)
    return cache[key]


# ---------- 轮后心情 ----------

async def handle_post_turn_mood(state, sc, *, session_id: str,
                                character_id: str, transcript: str,
                                provider, had_pending: bool,
                                now: Optional[float] = None) -> None:
    """ChatRun 成功后的心情收尾：消费宣布 → 判定 → 落锚。"""
    cfg = getattr(state.config, "mood", None)
    if cfg is None or not cfg.enabled or not character_id:
        return
    now = time.time() if now is None else now
    store = getattr(state, "mood_store", None)
    if store is None:
        return
    try:
        if had_pending:
            await asyncio.to_thread(
                store.consume_pending, sc.uid, session_id, character_id, now
            )
        judge = resolve_judge_provider(state, provider, cfg)
        if judge is None:
            return
        current = await asyncio.to_thread(
            store.view, sc.uid, session_id, character_id, now
        )
        judgement = await mood_mod.judge_mood(judge, transcript, current)
        if not judgement.get("delta") and not judgement.get("dims"):
            return
        await asyncio.to_thread(
            store.record,
            sc.uid, session_id, character_id,
            delta=float(judgement.get("delta", 0) or 0),
            dims=judgement.get("dims") or {},
            reason=str(judgement.get("reason", "") or ""),
            confidence=int(judgement.get("confidence", 0) or 0),
            source="human", now=now,
        )
    except Exception:
        logger.exception("轮后心情处理失败 sid=%s cid=%s", session_id, character_id)


# ---------- 轮后自我记忆抽取 ----------

async def extract_self_after_turn(state, sc, transcript: str,
                                  now: Optional[float] = None) -> None:
    """从最新对话抽取第一人称自我记忆；配额用尽即停，不报错给用户。"""
    cfg = getattr(state.config, "self_memory", None)
    if cfg is None or not cfg.enabled or not transcript.strip():
        return
    store = getattr(state, "self_memory_store", None)
    provider = state.get_provider()
    if store is None or provider is None:
        return
    now = time.time() if now is None else now
    try:
        text = await complete(
            provider,
            [Message(role="user", content=transcript[-6000:])],
            system_prompt=SELF_EXTRACT_SYS,
        )
        try:
            parsed = _extract_json(text)
        except Exception:
            parsed = []
        if not isinstance(parsed, list):
            return
        for item in parsed:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("statement", "") or "").strip()
            if not statement:
                continue
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            tags = [str(t) for t in tags][:10]
            try:
                importance = float(item.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            try:
                await asyncio.to_thread(
                    store.put, sc.uid, statement, tags, importance, now
                )
            except QuotaExceeded:
                logger.info("自我记忆当日配额用尽 uid=%s", sc.uid)
                return
    except Exception:
        logger.exception("自我记忆抽取失败")


def transcript_from_history(history, *, head: int = 0,
                            actor_id: Optional[str] = None,
                            limit_chars: int = 6000) -> str:
    """把一轮（或窗口尾部）消息拼成判定/抽取用的纯文本对话。"""
    parts: List[str] = []
    for m in history[head:]:
        role = getattr(m, "role", "")
        if role not in ("user", "assistant") or getattr(m, "hidden", False):
            continue
        if actor_id and role == "assistant" \
                and (getattr(m, "actor_id", "") or "") not in ("", actor_id):
            continue
        text = (getattr(m, "content", "") or "").strip()
        if not text:
            continue
        who = "我" if role == "user" else (getattr(m, "name", "") or "角色")
        parts.append("%s: %s" % (who, text))
    joined = "\n".join(parts)
    return joined[-limit_chars:]
