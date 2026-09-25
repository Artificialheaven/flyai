"""AI 生成类功能：属性建议、对话压缩（自动分类）。

统一走一次性补全（providers.complete），模型只回 JSON。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ..providers.complete import complete
from ..schemas import Message
from .models import Character, World

_DEFAULT_ATTR_SYS = (
    "你是一名角色扮演卡属性设计师。依据给出的角色设定，为角色设计 3~8 个"
    "适合剧情推进的数值属性（如好感度、金钱、体力、魅力、声望等），"
    "只输出 JSON 数组，不要任何解释或 Markdown。"
    '数组元素形如 {"key": "英文标识", "label": "中文显示名", "initial": 初始值, "min": 下限, "max": 上限}。'
)

_DEFAULT_COMPRESS_SYS = (
    "你是一名剧情整理员。阅读一段对话历史，把其中沉淀下来的信息归纳进角色卡与世界设定。"
    '只输出一个 JSON 对象：{"character": {"intro": "角色简介", "background": "角色经历/记忆变化", '
    '"table": [{"title": "...", "content": "..."}]}, "world": {"content": "世界观", '
    '"table": [{"title": "...", "content": "..."}]}}。'
    "自动分类：关于这个角色本人的归入 character，关于环境/世界/规则的归入 world；"
    "table 用于零散但重要的设定条目。没有变化的字段省略即可；不要编造对话中没出现过的信息。"
)


def _scan_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json(text: str):
    """从模型输出中稳健地解析出首个完整 JSON 对象/数组。"""
    if not text or not text.strip():
        raise ValueError("AI 返回为空")
    t = re.sub(r"^```(?:json)?\s*", "", text.strip()).strip()
    t = re.sub(r"\s*```$", "", t).strip()
    block = _scan_balanced(t, "{", "}")
    arr = _scan_balanced(t, "[", "]")
    for cand in (t, block, arr):
        if not cand:
            continue
        try:
            return json.loads(cand)
        except (ValueError, TypeError):
            continue
    raise ValueError("AI 返回无法解析为 JSON")


def _norm_attr(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = str(d.get("key", "")).strip()
    if not key:
        return None
    out: Dict[str, Any] = {"key": key}
    label = str(d.get("label", "") or "").strip()
    if label:
        out["label"] = label
    for f in ("min", "max", "initial"):
        v = d.get(f)
        if v is not None and v != "":
            try:
                out[f] = float(v)
            except (TypeError, ValueError):
                pass
    return out


async def suggest_attributes(
    state, name: str, intro: str, background: str, existing: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """让 AI 依据角色设定提议属性，返回规范化后的列表。"""
    provider = state.get_provider()
    if provider is None:
        raise RuntimeError("未配置可用提供商，请先在设置中添加")
    existing_txt = "、".join(
        f"{a.get('label') or a.get('key')}({a.get('key')})" for a in (existing or [])
    ) or "（无）"
    user = (
        f"角色名称：{name or '未知'}\n\n【角色简介】\n{intro or '（空）'}\n\n"
        f"【角色经历】\n{background or '（空）'}\n\n已有属性：{existing_txt}\n\n"
        "请据此设计属性并仅输出 JSON 数组。"
    )
    text = await complete(provider, [Message(role="user", content=user)], system_prompt=_DEFAULT_ATTR_SYS)
    data = _extract_json(text)
    if isinstance(data, dict) and isinstance(data.get("attributes"), list):
        data = data["attributes"]
    if not isinstance(data, list):
        raise ValueError("AI 输出的属性不是数组")
    out: List[Dict[str, Any]] = []
    seen_keys = {a.get("key") for a in (existing or []) if a.get("key")}
    for item in data:
        if not isinstance(item, dict):
            continue
        norm = _norm_attr(item)
        if norm and norm.get("key") not in seen_keys:
            out.append(norm)
        elif norm:
            out.append(norm)  # 允许与已有 key 重复，前端会提示
    return out


def _messages_script(session) -> str:
    lines = []
    for m in session.messages:
        role = m.role
        c = (m.content or "").strip()
        if not c:
            continue
        lines.append(f"{role}: {c}")
    return "\n".join(lines) if lines else "（本对话暂无消息）"


async def compress_conversation(
    state, session, character: Character, world: World
) -> Tuple[Character, World]:
    """让 AI 把对话历史归纳进角色卡与世界设定，返回更新后的对象（不落库）。"""
    provider = state.get_provider()
    if provider is None:
        raise RuntimeError("未配置可用提供商，请先在设置中添加")

    cur_char = character.to_dict() if character else {}
    cur_world = world.to_dict() if world else {}
    user = (
        "【当前角色卡】\n"
        f"名称：{cur_char.get('name') or '（无）'}\n简介：{cur_char.get('intro') or '（空）'}\n"
        f"经历：{cur_char.get('background') or '（空）'}\n"
        f"角色条目：{cur_char.get('table') and json.dumps(cur_char['table'], ensure_ascii=False) or '（空）'}\n\n"
        "【当前世界观】\n"
        f"内容：{cur_world.get('content') or '（空）'}\n"
        f"世界条目：{cur_world.get('table') and json.dumps(cur_world['table'], ensure_ascii=False) or '（空）'}\n\n"
        "【对话历史】\n" + _messages_script(session) + "\n\n" +
        "请把对话中新增且有价值的信息归纳进去，合并而非替换原有资料，仅输出 JSON。"
    )
    text = await complete(provider, [Message(role="user", content=user)], system_prompt=_DEFAULT_COMPRESS_SYS)
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是对象")

    c_data = data.get("character") if isinstance(data.get("character"), dict) else {}
    w_data = data.get("world") if isinstance(data.get("world"), dict) else {}

    new_char = _apply_char(cur_char, c_data, character)
    new_world = _apply_world(cur_world, w_data, world)
    return new_char, new_world


def _apply_char(cur: Dict[str, Any], c_data: Dict[str, Any], base: Optional[Character]) -> Character:
    merged = dict(cur)
    for field in ("intro", "background"):
        if c_data.get(field):
            merged[field] = str(c_data[field]).strip()
    table = c_data.get("table")
    if isinstance(table, list) and table:
        merged["table"] = [{"title": str(t.get("title", "")), "content": str(t.get("content", ""))} for t in table if isinstance(t, dict)]
    new = Character.from_dict(merged)
    return new


def _apply_world(cur: Dict[str, Any], w_data: Dict[str, Any], base: Optional[World]) -> World:
    merged = dict(cur)
    if w_data.get("content"):
        merged["content"] = str(w_data["content"]).strip()
    table = w_data.get("table")
    if isinstance(table, list) and table:
        merged["table"] = [{"title": str(t.get("title", "")), "content": str(t.get("content", ""))} for t in table if isinstance(t, dict)]
    new = World.from_dict(merged)
    return new