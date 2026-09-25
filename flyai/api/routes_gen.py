"""AI 生成类接口（无归属的生成操作）：属性建议。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["gen"])


class AttrGenRequest(BaseModel):
    name: str = ""
    intro: str = ""
    background: str = ""
    existing: List[Dict[str, Any]] = []


@router.post("/gen/attributes")
async def gen_attributes(body: AttrGenRequest, request: Request):
    """让 AI 依据角色设定建议属性，返回可编辑的提议列表。"""
    from ..lore.gen import suggest_attributes

    state = request.app.state.flyai
    try:
        attrs = await suggest_attributes(
            state, body.name, body.intro, body.background, body.existing or []
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"生成失败: {exc}")
    return {"attributes": attrs}