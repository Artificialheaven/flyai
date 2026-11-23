"""EJS 提示词侧（方案 B）prepare/commit 桥。

服务端组装完最终请求骨架后，把含 ``<% %>`` 的文本单元格通过当前 ChatRun
的 SSE 流下发给客户端；客户端在 null-origin 沙箱里执行，回传渲染文本与变量
ops。服务端校验后把文本拼回骨架、把 ops 缓冲起来，本次生成成功收尾时才落盘。

约定：
- 只执行提示词骨架中标记了 ``template_source`` 的文本（系统提示词、预设、
  世界书、角色字段、PHI、LIVE_CTX、@depth 块；含 user/assistant 角色的深度块）；
  聊天历史、用户消息、工具结果中的 ``<%`` 只剥离、不执行；
- 客户端只回传 ``cell_id -> 文本``，不接受整段提示词；
- 超时/错误直接中止本次生成，绝不回退成把模板源码发给模型；
- 工具轮复用已渲染单元格；相同文本的多条消息共用同一单元格。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

from ..template.output import contains_template, strip_template_markers
from ..template.vars import TemplateOpError, apply_ops

logger = logging.getLogger("flyai.template")

MAX_CELLS = 500
MAX_CELL_INPUT_BYTES = 512 * 1024
MAX_CELL_OUTPUT_BYTES = 64 * 1024
MAX_TOTAL_OUTPUT_BYTES = 512 * 1024
MAX_CHAT_ITEMS = 50
MAX_CHAT_CHAR = 4000
MAX_CHAT_TOTAL = 200 * 1024
DEFAULT_TIMEOUT = 10.0

# 客户端沙箱限额；服务端仍会再次校验，不能只信客户端
DEFAULT_LIMITS = {
    "perCellMs": 250,
    "totalMs": 8000,
    "maxOutput": MAX_CELL_OUTPUT_BYTES,
}


class TemplatePromptError(Exception):
    """提示词侧模板无法完成：调用方中止本次生成并提示重试。"""


def cell_id(text: str) -> str:
    return "p" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _truncate(text: Any, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def _char_view(character: Any) -> Dict[str, Any]:
    if character is None:
        return {}
    get = lambda name: getattr(character, name, "") or ""  # noqa: E731
    return {
        "name": get("name"),
        "description": _truncate(get("description"), 4000),
        "personality": _truncate(get("personality"), 2000),
        "scenario": _truncate(get("scenario"), 2000),
    }


def _user_view(character: Any, fallback: str = "") -> Dict[str, Any]:
    if character is None:
        return {"name": fallback}
    return {
        "name": getattr(character, "name", "") or fallback,
        "description": _truncate(getattr(character, "description", ""), 2000),
    }


def _chat_view(history: Sequence[Any]) -> List[Dict[str, Any]]:
    """最近若干条消息的只读视图；总长封顶，避免 prepare 帧过大。"""
    items = list(history or [])[-MAX_CHAT_ITEMS:]
    out: List[Dict[str, Any]] = []
    used = 0
    base = max(0, len(history or []) - len(items))
    for offset, message in enumerate(items):
        text = getattr(message, "content", None) or ""
        text = _truncate(text, MAX_CHAT_CHAR)
        block = {
            "index": base + offset,
            "role": getattr(message, "role", "") or "",
            "name": getattr(message, "name", "") or "",
            "content": text,
        }
        used += len(text)
        if used > MAX_CHAT_TOTAL:
            break
        out.append(block)
    return out


class PromptTemplateBridge:
    """一次聊天运行内的提示词模板会话（每个 run 一个）。"""

    def __init__(
        self,
        run: Any,
        session: Any,
        store: Any,
        *,
        uid: str = "",
        history: Optional[Sequence[Any]] = None,
        char: Any = None,
        user: Any = None,
        group: Optional[Sequence[Any]] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.run = run
        self.session = session
        self.store = store
        self.uid = uid
        self.history = history if history is not None else getattr(
            session, "messages", []
        )
        self.timeout = max(0.2, float(timeout))
        self._char = _char_view(char)
        self._user = _user_view(user)
        self._group = [
            {"name": getattr(item, "name", "") or ""}
            for item in (group or [])
            if getattr(item, "name", "")
        ]
        session_vars = getattr(session, "template_vars", None)
        self._local = copy.deepcopy(
            (session_vars or {}).get("local")
            if isinstance(session_vars, dict) else {}
        ) or {}
        try:
            self._global = store.load()
        except Exception:  # 存储异常不阻断生成：当作空变量
            logger.exception("读取模板 global 变量失败")
            self._global = {}
        self._rendered: Dict[str, str] = {}
        self._ops_applied = 0
        self._ops_rejected = 0
        self._dirty_global = False
        self._finalized = False

    # ---------- 上下文 ----------

    def _stat_data(self) -> Dict[str, Any]:
        mvu = getattr(self.session, "mvu", None)
        value = (mvu or {}).get("stat_data") if isinstance(mvu, dict) else None
        return value if isinstance(value, dict) else {}

    def _context(self) -> Dict[str, Any]:
        author = (getattr(self.session, "attributes", None) or {}).get("author") or {}
        phone_mainline = author.get("phone_mainline")
        return {
            # 深拷贝：事件会缓存在 run 里供断线重放，不能在 apply_ops 时被改掉
            "scopes": {
                "message": {},
                "local": copy.deepcopy(self._local),
                "global": copy.deepcopy(self._global),
            },
            "readonly": {"stat_data": copy.deepcopy(self._stat_data())},
            "defaultScope": "local",
            "escapeOutput": False,
            "char": self._char,
            "user": self._user,
            "group": self._group,
            "chat": _chat_view(self.history),
            # 卡片手机模块通过 DALPhoneMainline.read() 注入主线往来
            "phoneMainline": phone_mainline if isinstance(phone_mainline, str) else "",
        }

    def _version(self) -> str:
        raw = json.dumps(
            {"local": self._local, "global": self._global},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    # ---------- prepare ----------

    def _collect(self, messages: Sequence[Any]) -> List[Dict[str, Any]]:
        cells: List[Dict[str, Any]] = []
        seen: set = set()
        for message in messages:
            content = getattr(message, "content", None)
            if not isinstance(content, str) or not contains_template(content):
                continue
            # 只执行提示词骨架里由卡/预设/世界书产生的消息（template_source）。
            # 聊天历史、工具结果、用户输入一律只剥离标记：既避免把用户输入
            # 当代码执行，也避免输出侧模板（含 injectPrompt 等）在提示词侧
            # 再执行一次导致生成失败或被二次注入。
            if not getattr(message, "template_source", False):
                continue
            size = len(content.encode("utf-8"))
            if size > MAX_CELL_INPUT_BYTES:
                raise TemplatePromptError("模板单元格过大（超过 512KB）")
            cid = cell_id(content)
            if cid in seen or cid in self._rendered:
                continue
            seen.add(cid)
            if len(cells) >= MAX_CELLS:
                raise TemplatePromptError("模板单元格过多")
            cells.append({
                "id": cid,
                "text": content,
                "role": getattr(message, "role", "") or "",
            })
        return cells

    async def _prepare(
        self,
        cells: List[Dict[str, Any]],
        macro_render: Optional[Any] = None,
    ) -> None:
        prepare_id = hashlib.sha256(
            (getattr(self.run, "id", "") + "|" + "|".join(c["id"] for c in cells)
             + "|" + self._version()).encode("utf-8")
        ).hexdigest()[:24]
        snapshot_global = copy.deepcopy(self._global)
        version = self._version()
        payload = {
            "prepare_id": prepare_id,
            "run_id": getattr(self.run, "id", ""),
            "cells": cells,
            "context": self._context(),
            "limits": dict(DEFAULT_LIMITS),
            "context_version": version,
            "timeout_ms": int(self.timeout * 1000),
        }
        # 先登记等待者再发事件：客户端极快回传时不会因尚未登记而 409
        waiter = self.run.open_template(prepare_id)
        await self.run.emit("template_prepare", payload)
        result = await self.run.wait_template(
            prepare_id, self.timeout, future=waiter
        )
        if result is None:
            raise TemplatePromptError(
                "模板渲染超时（客户端未在 %d 秒内返回），请重试"
                % int(round(self.timeout))
            )

        errors = result.get("errors")
        if isinstance(errors, list) and errors:
            # 上游语义：模板异常只影响该单元格，不能让整轮生成失败。
            # 这里记录告警并继续使用已成功渲染的单元格（失败单元格回退原文）。
            first = errors[0] if isinstance(errors[0], dict) else {}
            detail = _truncate(first.get("message") if isinstance(first, dict) else "", 200)
            logger.warning("EJS 模板单元格渲染失败（已降级继续）：%s", detail or "未知错误")

        if str(result.get("context_version") or "") != version:
            raise TemplatePromptError("模板变量已变化，请重试")

        # 另一会话可能在等待期间改了 global 变量：CAS 失败即中止
        try:
            if self.store.load() != snapshot_global:
                raise TemplatePromptError("模板变量已变化，请重试")
        except TemplatePromptError:
            raise
        except Exception:
            logger.exception("校验模板 global 版本失败")

        returned = result.get("cells")
        if not isinstance(returned, dict):
            raise TemplatePromptError("模板渲染结果格式错误")
        total = 0
        for cell in cells:
            value = returned.get(cell["id"])
            if not isinstance(value, str):
                # 客户端异常/超时导致单元格缺失：降级为空串继续本轮生成，
                # 而不是整轮失败（与上游“模板错误不阻断生成”一致）。
                logger.warning("EJS 单元格 %s 缺少渲染结果，已降级为空串", cell.get("id"))
                value = ""
            # EJS 输出常含 {{char}}/{{random}} 等宏：执行后统一展开
            if macro_render is not None:
                try:
                    value = str(macro_render(value))
                except Exception:
                    logger.exception("模板输出后置宏展开失败")
            # 宁可丢弃残留标记，也不把模板源码发给模型
            value = strip_template_markers(value)
            size = len(value.encode("utf-8"))
            if size > MAX_CELL_OUTPUT_BYTES:
                raise TemplatePromptError("模板单元格输出超过 64KB")
            total += size
            if total > MAX_TOTAL_OUTPUT_BYTES:
                raise TemplatePromptError("模板输出总长超过 512KB")
            self._rendered[cell["id"]] = value

        self._apply_ops(result.get("ops"))
        logger.info(
            "event=template_prepare uid=%s run=%s cells=%s ops=%s rejected=%s",
            hashlib.sha256(str(self.uid).encode("utf-8")).hexdigest()[:12],
            getattr(self.run, "id", ""), len(cells), self._ops_applied,
            self._ops_rejected,
        )

    def _apply_ops(self, ops: Any) -> None:
        if not isinstance(ops, list) or not ops:
            return
        for raw in ops[:500]:
            if not isinstance(raw, dict):
                self._ops_rejected += 1
                continue
            # 提示词侧没有单条消息作用域：message 写入并入 local
            item = raw
            if str(raw.get("scope") or "") == "message":
                item = dict(raw, scope="local")
            try:
                local, _message, gvars = apply_ops(
                    [item],
                    local=self._local,
                    message={},
                    global_vars=self._global,
                    default_scope="local",
                )
            except TemplateOpError:
                # 单条非法只丢弃该条并记审计，不中断生成
                self._ops_rejected += 1
                continue
            self._local = local
            # apply_ops 顶层浅拷贝，嵌套路径会原地改 self._global；
            # 因此按 op 的 scope 直接标记脏，不能靠比较引用判断。
            if str(item.get("scope") or "") == "global":
                self._dirty_global = True
            self._global = gvars
            self._ops_applied += 1

    # ---------- apply / finalize ----------

    async def apply(
        self,
        messages: List[Any],
        macro_render: Optional[Any] = None,
    ) -> List[Any]:
        """把消息里的模板单元格渲染并拼回；无模板时原样返回。"""
        cells = self._collect(messages)
        if cells:
            await self._prepare(cells, macro_render)
        out: List[Any] = []
        for message in messages:
            content = getattr(message, "content", None)
            if not isinstance(content, str) or not contains_template(content):
                out.append(message)
                continue
            if not getattr(message, "template_source", False):
                out.append(replace(
                    message, content=strip_template_markers(content)
                ))
                continue
            rendered = self._rendered.get(cell_id(content))
            if rendered is None:
                # 理论上 _prepare 已覆盖；防御性中止，绝不发源码
                raise TemplatePromptError("模板单元格未渲染")
            out.append(replace(message, content=rendered))
        return out

    def finalize(self) -> None:
        """生成成功收尾：变量写入会话内存并由调用方随消息一起落盘。

        失败/取消路径不调用，请求内变量变更自然丢弃。
        """
        if self._finalized:
            return
        self._finalized = True
        if self._ops_applied <= 0:
            return
        current = getattr(self.session, "template_vars", None)
        current = current if isinstance(current, dict) else {}
        self.session.template_vars = {
            "local": self._local,
            "message": current.get("message") if isinstance(
                current.get("message"), dict
            ) else {},
        }
        if self._dirty_global:
            try:
                self.store.save(self._global)
            except Exception:
                # 变量落盘失败不能让已生成的回复丢失
                logger.exception("保存模板 global 变量失败")
