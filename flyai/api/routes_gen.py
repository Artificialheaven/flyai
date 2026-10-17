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


class CharacterFromTextRequest(BaseModel):
    text: str = ""


@router.post("/gen/character-from-text")
async def gen_character_from_text(body: CharacterFromTextRequest, request: Request):
    """让 AI 从任意文本（档案/小说节选/设定）解析出一张角色卡，返回编辑器预填数据。"""
    from ..lore.gen import MAX_CHAR_SOURCE_CHARS, parse_character_from_text

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "请输入角色文本")
    if len(text) > MAX_CHAR_SOURCE_CHARS:
        raise HTTPException(400, f"文本过长（上限 {MAX_CHAR_SOURCE_CHARS} 字）")
    state = request.app.state.flyai
    try:
        card = await parse_character_from_text(state, text)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(500, f"解析失败: {exc}")
    except Exception as exc:
        raise HTTPException(500, f"生成失败: {exc}")
    return {"character": card}


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