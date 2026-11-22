"""EJS 模板（方案 B）：客户端渲染结果的提交接口与 global 变量读取。

服务端不执行 JS；这里只做权限、原文哈希、ops 校验与原子落盘。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..template import policy
from ..template.output import (
    TemplateCommitError,
    apply_output_commit,
    dump_global_vars,
    find_message,
)
from ..template.store import TemplateVarStore
from ..template.vars import TemplateOpError
from ..user import ADMIN_UID

router = APIRouter(prefix="/api", tags=["template"])
logger = logging.getLogger("flyai.template")

_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _sc(request: Request):
    return request.app.state.flyai.user(request)


def _safe_id(value: str, label: str) -> str:
    text = (value or "").strip()
    if not _SID_RE.match(text):
        raise HTTPException(400, "%s 非法" % label)
    return text


def _store(request: Request, sc) -> TemplateVarStore:
    uid = str(getattr(sc, "uid", "") or "")
    if not _SID_RE.match(uid):
        raise HTTPException(400, "用户 id 非法")
    data_dir = getattr(request.app.state.flyai, "data_dir", None)
    if data_dir is None:
        settings_path = getattr(sc, "settings_path", None)
        if settings_path is None:
            raise HTTPException(500, "无法定位用户数据目录")
        path = settings_path.parent / "template_vars.json"
    else:
        path = data_dir / "template_vars" / (uid + ".json")
    return TemplateVarStore(path)


def _uid_hash(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12]


def _flags(request: Request, sc) -> Dict[str, bool]:
    cfg = getattr(request.app.state.flyai, "config", None)
    us = getattr(sc, "user_settings", None)
    level = int(getattr(sc, "level", 0) or 0)
    is_admin = str(getattr(sc, "uid", "")) == ADMIN_UID
    return {
        "prompt": policy.prompt_enabled(cfg, us, level, is_admin),
        "output": policy.output_enabled(cfg, us, level, is_admin),
    }


class TemplateOutputBody(BaseModel):
    content: str = ""
    ops: Optional[list] = None
    errors: Optional[list] = None
    raw_hash: str = ""
    render_id: str = ""


@router.get("/template/vars")
def get_template_vars(request: Request):
    sc = _sc(request)
    flags = _flags(request, sc)
    if not (flags["prompt"] or flags["output"]):
        return {"enabled": False, "vars": {}}
    return {
        "enabled": True,
        "prompt_enabled": flags["prompt"],
        "output_enabled": flags["output"],
        "vars": _store(request, sc).load(),
    }


class TemplatePromptCommitBody(BaseModel):
    run_id: str = ""
    prepare_id: str = ""
    context_version: str = ""
    cells: Optional[dict] = None
    ops: Optional[list] = None
    errors: Optional[list] = None
    stats: Optional[dict] = None


@router.post("/template/prompt-commit")
def commit_template_prompt(body: TemplatePromptCommitBody, request: Request):
    """提示词侧：把客户端沙箱渲染出的单元格与变量 ops 交回等待中的运行。"""
    sc = _sc(request)
    if not _flags(request, sc)["prompt"]:
        raise HTTPException(403, "EJS 提示词侧模板未开启")
    run_id = _safe_id(body.run_id, "运行 ID")
    prepare_id = (body.prepare_id or "").strip()
    if not re.match(r"^[a-f0-9]{16,64}$", prepare_id):
        raise HTTPException(400, "prepare ID 非法")

    cells = body.cells if isinstance(body.cells, dict) else {}
    if len(cells) > 500:
        raise HTTPException(413, "模板单元格过多")
    total = 0
    for value in cells.values():
        if not isinstance(value, str):
            raise HTTPException(400, "单元格结果必须是字符串")
        total += len(value.encode("utf-8"))
        if total > 4 * 1024 * 1024:
            raise HTTPException(413, "模板渲染结果过大")

    state = request.app.state.flyai
    manager = getattr(state, "run_manager", None)
    run = manager.get(run_id, str(getattr(sc, "uid", "") or "")) \
        if manager is not None else None
    if run is None:
        raise HTTPException(409, "运行不存在或已结束")
    payload = {
        "context_version": body.context_version,
        "cells": cells,
        "ops": body.ops if isinstance(body.ops, list) else [],
        "errors": body.errors if isinstance(body.errors, list) else [],
        "stats": body.stats if isinstance(body.stats, dict) else {},
    }
    if not run.resolve_template(prepare_id, payload):
        raise HTTPException(409, "渲染请求不存在或已过期")

    accepted = len(cells)
    logger.info(
        "event=template_prompt_commit user=%s run=%s prepare=%s cells=%s",
        _uid_hash(str(getattr(sc, "uid", ""))), run_id, prepare_id, accepted,
    )
    return {"ok": True, "accepted": accepted}


@router.post("/sessions/{sid}/messages/{mid}/template-output")
def commit_template_output(sid: str, mid: str, body: TemplateOutputBody,
                           request: Request):
    """输出侧：客户端渲染完成后提交最终内容与变量 ops。"""
    sc = _sc(request)
    _safe_id(sid, "会话 ID")
    _safe_id(mid, "消息 ID")
    if not _flags(request, sc)["output"]:
        raise HTTPException(403, "EJS 输出侧模板未开启")
    session = sc.store.get(sid)
    if session is None:
        raise HTTPException(404, "会话不存在")
    message = find_message(session, mid)
    if message is None:
        raise HTTPException(404, "消息不存在")
    if getattr(message, "role", "") != "assistant":
        raise HTTPException(400, "只能渲染助手消息")

    store = _store(request, sc)
    before_global = store.load()
    try:
        result = apply_output_commit(
            session,
            message,
            body.model_dump() if hasattr(body, "model_dump") else body.dict(),
            global_vars=before_global,
        )
    except TemplateCommitError as exc:
        raise HTTPException(exc.status, exc.message)
    except TemplateOpError as exc:
        raise HTTPException(400, str(exc))

    global_vars = result.pop("global_vars", None)
    if isinstance(global_vars, dict) and dump_global_vars(global_vars) != dump_global_vars(before_global):
        try:
            store.save(global_vars)
        except (OSError, ValueError) as exc:
            raise HTTPException(413, "模板变量保存失败：%s" % exc)

    try:
        sc.store.save_merged(session)
    except Exception as exc:
        raise HTTPException(500, "会话保存失败：%s" % exc)

    logger.info(
        "event=template_output user=%s session=%s message=%s ops=%s idempotent=%s",
        _uid_hash(str(getattr(sc, "uid", ""))), sid, mid,
        len(body.ops or []), bool(result.get("idempotent")),
    )
    payload = {
        "ok": True,
        "idempotent": bool(result.get("idempotent")),
        "message": result.get("message"),
        "template_vars": getattr(session, "template_vars", None) or {},
    }
    return payload
