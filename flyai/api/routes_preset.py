"""模型预设：私有列表 / 增删改 / 切换启用 / 酒馆预设导入导出。

预设按用户隔离（data/{uid}/presets/*.json）；当前启用 id 存于
UserSettings.active_preset_id。市场的发布/安装见 routes_market。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..ids import InvalidName, check_safe_name
from ..preset.models import Preset
from ..preset.tavern import (
    PresetImportError,
    preset_from_text,
    preset_to_envelope,
)

router = APIRouter(prefix="/api/presets", tags=["presets"])


def _sc(request: Request):
    return request.app.state.flyai.user(request)


def _safe_pid(pid: str) -> str:
    try:
        return check_safe_name(pid, "预设 ID")
    except InvalidName:
        raise HTTPException(400, "预设 ID 非法")


class PresetBlockPayload(BaseModel):
    name: str = ""
    content: str = ""
    enabled: bool = True
    role: str = "system"
    position: str = "main"
    depth: int = 4
    insert_order: int = 100
    identifier: str = ""


class PresetPayload(BaseModel):
    name: str
    note: str = ""
    params: Dict[str, Any] = {}
    max_tokens: int = 0
    assistant_prefill: str = ""
    blocks: List[PresetBlockPayload] = []


def _build_preset(payload: PresetPayload, pid: str = "") -> Preset:
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    data["source"] = "flyai"
    if pid:
        data["id"] = pid
    p = Preset.from_dict(data)
    if not p.name.strip():
        raise HTTPException(400, "预设名称不能为空")
    if p.max_tokens < 0 or p.max_tokens > 1_000_000:
        raise HTTPException(400, "max_tokens 超出范围")
    return p


@router.get("")
def list_presets(request: Request):
    sc = _sc(request)
    active = getattr(sc.user_settings, "active_preset_id", "") or ""
    return {"presets": [p.to_dict() for p in sc.presets.list()],
            "active_preset_id": active}


@router.post("")
def create_preset(payload: PresetPayload, request: Request):
    sc = _sc(request)
    p = _build_preset(payload)
    p.id = uuid.uuid4().hex
    sc.presets.save(p)
    return p.to_dict()


@router.put("/{pid}")
def update_preset(pid: str, payload: PresetPayload, request: Request):
    sc = _sc(request)
    _safe_pid(pid)
    cur = sc.presets.get(pid)
    if cur is None:
        raise HTTPException(404, "预设不存在")
    p = _build_preset(payload, pid=pid)
    p.source = cur.source  # 来源标记不因编辑改变
    p.created_at = cur.created_at
    sc.presets.save(p)
    return p.to_dict()


@router.delete("/{pid}")
def delete_preset(pid: str, request: Request):
    sc = _sc(request)
    _safe_pid(pid)
    if not sc.presets.delete(pid):
        raise HTTPException(404, "预设不存在")
    if (sc.user_settings.active_preset_id or "") == pid:
        sc.user_settings.active_preset_id = ""
        sc.save_user_settings()
    return {"ok": True}


class ActiveBody(BaseModel):
    id: str = ""


@router.post("/active")
def set_active(body: ActiveBody, request: Request):
    sc = _sc(request)
    pid = (body.id or "").strip()
    if pid and sc.presets.get(pid) is None:
        raise HTTPException(404, "预设不存在")
    sc.user_settings.active_preset_id = pid
    sc.save_user_settings()
    return {"active_preset_id": pid}


@router.post("/{pid}/clone")
def clone_preset(pid: str, request: Request):
    sc = _sc(request)
    _safe_pid(pid)
    cur = sc.presets.get(pid)
    if cur is None:
        raise HTTPException(404, "预设不存在")
    data = cur.to_dict()
    data["id"] = uuid.uuid4().hex
    data["name"] = (cur.name + " 副本")[:60]
    p = Preset.from_dict(data)
    sc.presets.save(p)
    return p.to_dict()


@router.get("/{pid}/export")
def export_preset(pid: str, request: Request):
    sc = _sc(request)
    _safe_pid(pid)
    p = sc.presets.get(pid)
    if p is None:
        raise HTTPException(404, "预设不存在")
    body = preset_to_envelope(p)
    return JSONResponse(
        content=body,
        headers={"Content-Disposition": f'attachment; filename="preset-{p.id[:8]}.json"'},
    )


@router.post("/import")
async def import_preset(request: Request, file: UploadFile = File(...)):
    """导入酒馆 OpenAI 预设（.json/.txt，脏 JSON 容错）或 FlyAI 预设信封。"""
    sc = _sc(request)
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "预设文件过大（>10MB）")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "文件不是 UTF-8 文本")
    fallback = Path(file.filename or "").stem
    try:
        p, stats = preset_from_text(text, name=fallback)
    except PresetImportError as e:
        raise HTTPException(400, "预设解析失败：%s" % e)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, "预设 JSON 解析失败：%s" % e)
    if not p.blocks and not p.params and not p.assistant_prefill:
        raise HTTPException(400, "预设里没有可用的参数或 prompt 块")
    p.id = uuid.uuid4().hex
    sc.presets.save(p)
    return {"preset": p.to_dict(), "stats": stats}
