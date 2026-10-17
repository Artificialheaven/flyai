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
    "内容表 table 按标题合并：已存在的标题只给需要更新的 content；新设定用新标题追加；"
    "不要列出、不要改写你没提到的标题（未提到的条目会原样保留）；不要输出 pinned 字段；"
    "严禁通过省略标题来删除条目。"
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


_DEFAULT_TEXT_PARSE_SYS = (
    "你是一名角色扮演卡制作师。用户会给你一段任意文本（角色档案、百科片段、小说节选、同人设定等），"
    "请从中提取并合理组织成一张角色扮演角色卡，只输出一个 JSON 对象，不要任何解释或 Markdown：\n"
    '{"name": "角色名", '
    '"intro": "角色简介：外貌、身份、性格概述，第二人称设定文本", '
    '"background": "角色经历/当前处境", '
    '"first_mes": "开场白：以该角色身份对用户说的第一段话（可带动作神态描写），用 {{char}} 代指自己、{{user}} 代指用户", '
    '"mes_example": "2~4 轮范例对白，轮与轮之间单独一行写 <START>，用 {{char}}/{{user}} 标记说话者", '
    '"post_history_instructions": "仅当原文明确包含扮演要求/文风红线时提炼为每轮自检指令，否则空字符串", '
    '"table": [{"title": "条目标题", "content": "不适合放进简介/经历的重要设定（能力、关系、物品等）"}], '
    '"attributes": [{"key": "英文标识", "label": "中文显示名", "initial": 初始值, "min": 下限, "max": 上限}]}\n'
    "要求：严格依据原文，不要编造原文没有的设定；信息不足的字段给空字符串或空数组；"
    "table 控制在 12 条以内、只放真正重要的设定；attributes 给 0~6 个适合剧情推进的数值属性，没有合适的就空数组；"
    "first_mes 要能自然开启一场对话，不要写成旁白梗概。"
)

MAX_CHAR_SOURCE_CHARS = 20000

_TEXT_STR_FIELDS = (
    "name", "intro", "background", "first_mes", "mes_example",
    "post_history_instructions",
)


def _norm_text_table(items: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        if content:
            out.append({"title": title or "(无标题)", "content": content})
    return out[:30]


async def parse_character_from_text(state, source: str) -> Dict[str, Any]:
    """让 AI 从任意文本解析角色卡字段，返回未保存的卡 dict（无 id，供编辑器预填）。"""
    provider = state.get_provider()
    if provider is None:
        raise RuntimeError("未配置可用提供商，请先在设置中添加")
    user = (
        "请根据以下文本制作角色卡，仅输出 JSON 对象。\n\n"
        "【原文开始】\n" + source + "\n【原文结束】"
    )
    text = await complete(
        provider, [Message(role="user", content=user)],
        system_prompt=_DEFAULT_TEXT_PARSE_SYS,
    )
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是 JSON 对象")

    card: Dict[str, Any] = {f: str(data.get(f, "") or "").strip() for f in _TEXT_STR_FIELDS}
    if not card["name"]:
        raise ValueError("AI 未能从文本中识别出角色名称，请补充更明确的角色资料后重试")
    card["table"] = _norm_text_table(data.get("table"))
    attrs: List[Dict[str, Any]] = []
    if isinstance(data.get("attributes"), list):
        for item in data["attributes"]:
            if isinstance(item, dict):
                norm = _norm_attr(item)
                if norm:
                    attrs.append(norm)
    card["attributes"] = attrs[:8]
    return card


def _message_lines(m, with_names: bool) -> List[str]:
    """把一条消息展开为文本行：包含 content、工具调用与工具结果。"""
    prefix = f"[{m.name}] " if (with_names and (m.name or "")) else ""
    role = m.role
    if role == "assistant":
        lines = []
        c = (m.content or "").strip()
        if c:
            lines.append(prefix + c)
        for tc in (m.tool_calls or []):
            try:
                args = json.dumps(tc.arguments, ensure_ascii=False)
            except Exception:
                args = ""
            lines.append(f"{prefix}[调用工具 {tc.name}({args})]")
        return lines
    if role == "tool":
        c = (m.content or "").strip()
        if not c:
            return []
        return [f"[工具结果({m.name or ''}) {c}]"]
    c = (m.content or "").strip()
    if not c:
        return []
    return [f"{prefix}{role}: {c}"]


def messages_to_script(messages, with_names: bool = False) -> str:
    """消息列表 → 纯文本脚本（含工具调用/结果；跳过 hidden）。"""
    lines: List[str] = []
    for m in messages:
        if getattr(m, "hidden", False):
            continue
        lines.extend(_message_lines(m, with_names))
    return "\n".join(lines) if lines else "（本对话暂无消息）"


def _messages_script(session) -> str:
    """单 AI 会话：读 session.messages；已迁移进线程的旧会话回退到主私聊窗口。

    store.get() 会把带消息的旧会话自动迁移为成员+线程、并清空 session.messages，
    不回退就会压缩空历史。
    """
    msgs = session.messages
    if not msgs and session.threads:
        from ..sessions.resolve import primary_thread

        pt = primary_thread(session)
        if pt is not None:
            msgs = pt.messages
    return messages_to_script(msgs)


async def build_conversation_compression(
    state, session, character: Character, world: World
) -> Dict[str, Any]:
    """让 AI 把对话历史归纳进角色卡与世界设定，返回原始 JSON dict（不落库）。"""
    provider = state.get_provider()
    if provider is None:
        raise RuntimeError("未配置可用提供商，请先在设置中添加")

    cur_char = character.to_dict() if character else {}
    cur_world = world.to_dict() if world else {}
    user = (
        "【当前角色卡】\n"
        f"名称：{cur_char.get('name') or '（无）'}\n简介：{cur_char.get('intro') or '（空）'}\n"
        f"经历：{cur_char.get('background') or '（空）'}\n"
        f"角色条目标题：{_table_titles(cur_char.get('table')) or '（无）'}\n\n"
        "【当前世界观】\n"
        f"内容：{cur_world.get('content') or '（空）'}\n"
        f"世界条目标题：{_table_titles(cur_world.get('table')) or '（无）'}\n\n"
        "【对话历史】\n" + _messages_script(session) + "\n\n" +
        "请把对话中新增且有价值的信息归纳进去，按标题合并而非替换原有资料，仅输出 JSON。"
    )
    text = await complete(provider, [Message(role="user", content=user)], system_prompt=_DEFAULT_COMPRESS_SYS)
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是对象")
    return data


def _table_titles(table) -> str:
    if not table:
        return ""
    return "、".join(str(t.get("title", "")) for t in table if isinstance(t, dict))


def apply_conversation_compression(
    character: Optional[Character], world: Optional[World], data: Dict[str, Any]
) -> Tuple[Character, World]:
    """把压缩原始 JSON 合并进当前角色/世界，返回新对象（不落库）。"""
    from .merge import merge_character_patch, merge_world_patch

    c_data = data.get("character") if isinstance(data.get("character"), dict) else {}
    w_data = data.get("world") if isinstance(data.get("world"), dict) else {}
    base_char = character.to_dict() if character else {}
    base_world = world.to_dict() if world else {}
    new_char = Character.from_dict(merge_character_patch(base_char, c_data))
    new_world = World.from_dict(merge_world_patch(base_world, w_data))
    return new_char, new_world


async def compress_conversation(
    state, session, character: Character, world: World
) -> Tuple[Character, World]:
    """旧入口：生成 + 合并（不落库）。新代码请直接调用 build/apply 两段。"""
    data = await build_conversation_compression(state, session, character, world)
    return apply_conversation_compression(character, world, data)


_DEFAULT_ROOM_COMPRESS_SYS = (
    "你是一名剧情整理员。阅读一个多 AI 聊天房间中各窗口的对话历史，"
    "把其中沉淀下来的信息分别归纳进每个 AI 角色的资料，以及房间共享的世界设定。"
    '只输出一个 JSON 对象：{"members": [{"member_id": "AI 参与者ID", '
    '"character": {"name": "该参与者当前角色名", "intro": "角色简介", '
    '"background": "角色经历/记忆变化", '
    '"table": [{"title": "...", "content": "..."}]}}], '
    '"world": {"content": "世界观", "table": [{"title": "...", "content": "..."}]}}。'
    "对话行的 [名字] 前缀标明发言者；member_id 必须取自给出的参与者清单，"
    "character.name 必须与该 member_id 括号内给出的参与者名字一致，"
    "严禁把 A 参与者的资料写到 B 的 member_id 下。"
    "没有变化的字段或成员省略即可；不要编造对话中没出现过的信息。"
    "内容表 table 按标题合并：已存在的标题只给需要更新的 content；新设定用新标题追加；"
    "不要列出、不要改写你没提到的标题（未提到的条目会原样保留）；不要输出 pinned 字段；"
    "严禁通过省略标题来删除条目。"
)


async def compress_room(state, session, lore) -> Dict[str, Any]:
    """让 AI 按窗口/发言者归纳房间历史，返回 {members:[{member_id,character}], world}。

    不落库；由调用方经 apply_room_compression 写入成员副本。
    """
    from ..sessions.resolve import member_character

    provider = state.get_provider()
    if provider is None:
        raise RuntimeError("未配置可用提供商，请先在设置中添加")

    member_ids: List[str] = []
    parts: List[str] = []
    for m in session.members:
        if m.is_user:
            continue
        member_ids.append(m.id)
        char = member_character(m, lore)
        summary = "（无角色卡）"
        if char is not None:
            titles = _table_titles([e.to_dict() for e in char.table])
            summary = (
                f"简介：{char.intro or '（空）'}；经历：{char.background or '（空）'}"
                f"；现有条目标题：{titles or '（无）'}"
            )
        parts.append(f"参与者 member_id={m.id}（{m.name}）当前资料：{summary}")

    for t in session.threads:
        parts.append(
            f"【窗口 {t.title or t.kind}】\n" + messages_to_script(t.messages, with_names=True)
        )

    from ..sessions.resolve import effective_world

    cur_world = effective_world(session, lore)
    if cur_world is not None:
        wtitles = _table_titles([e.to_dict() for e in cur_world.table])
        parts.append(f"当前世界现有条目标题：{wtitles or '（无）'}")

    parts.append("参与者ID 清单：" + "、".join(member_ids))
    parts.append("请把对话中新增且有价值的信息按参与者归纳，合并而非替换原有资料，仅输出 JSON。")
    user = "\n\n".join(parts)
    text = await complete(
        provider, [Message(role="user", content=user)], system_prompt=_DEFAULT_ROOM_COMPRESS_SYS
    )
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError("AI 输出不是对象")
    return data