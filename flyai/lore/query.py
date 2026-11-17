"""内容表查询器：角色/世界观内容表的统一检索与系统提示词构建。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import AttributeDef, RoleplaySettings
from ..providers.embedding import dot, normalize
from .attributes import resolve_attribute_defs
from .budget import fit_blocks, fit_budget
from .models import Character, LoreEntry, World
from . import trigger as _trigger

KIND_CHARACTER = "character"
KIND_WORLD = "world"

_RAG_MAX_EMBED = 300   # 语义检索最多向量化的条目数
_EMBED_TEXT = 1000     # 每条目用于向量化的文本截断长度
_KEYWORD_LIMIT_DEFAULT = 8

_TERM_SPLIT_RE = re.compile(r"[\s,，、;；/]+")

# 未指定角色卡时的默认人格：通用 AI 助手（替代角色扮演设定）
GENERAL_ASSISTANT_PROMPT = (
    "你是一个热情、细腻的 AI 助手，以热情细腻的风格提供帮助，"
    "力求让对话体验更舒适，为用户的需求提供灵活帮助。"
)


class LoreQuerier:
    """绑定到单个会话的活动角色/世界观，供 query_lore 工具检索。"""

    def __init__(self, character: Character, world: World):
        self.character = character
        self.world = world
        # per-kind 条目向量缓存：{kind: {"sig", "entries", "texts", "vecs"}}
        self._vec_cache: Dict[str, Dict[str, Any]] = {}
        self._vec_locks: Dict[str, asyncio.Lock] = {}

    def list_titles(self, kind: str) -> List[str]:
        table = self._table(kind)
        return [e.title for e in table]

    def search(self, kind: str, keyword: str, limit: int = _KEYWORD_LIMIT_DEFAULT) -> Optional[List[LoreEntry]]:
        """关键词检索：标题精确 > 标题子串 > 正文命中，按分数降序、原序兜底。

        空 keyword 返回整表（工具层空 keyword 只列标题）。
        """
        table = self._table(kind)
        kw = (keyword or "").strip().lower()
        if not kw:
            return table
        terms = [t for t in _TERM_SPLIT_RE.split(kw) if t]
        if not terms:
            return table

        scored: List[Tuple[int, int, LoreEntry]] = []
        for idx, e in enumerate(table):
            title_l = (e.title or "").lower()
            content_l = (e.content or "").lower()
            keys_l = [str(k).lower() for k in (e.keys or [])]
            score = 0
            for term in terms:
                if title_l == term:
                    score += 100
                c = title_l.count(term)
                if c:
                    score += 10 * c
                # 触发词精确相等权重仅次于标题，子串命中与正文同级偏上
                for kl in keys_l:
                    if kl == term:
                        score += 50
                    elif term in kl:
                        score += 5
                score += content_l.count(term)
            if score > 0:
                scored.append((score, idx, e))
        scored.sort(key=lambda x: (-x[0], x[1]))
        limit = int(limit or _KEYWORD_LIMIT_DEFAULT)
        return [e for _s, _i, e in scored[:limit]]

    def _table(self, kind: str) -> List[LoreEntry]:
        if kind == KIND_CHARACTER and self.character:
            return self.character.table
        if kind == KIND_WORLD and self.world:
            return self.world.table
        return []

    def available_kinds(self) -> List[str]:
        kinds = []
        if self.character:
            kinds.append(KIND_CHARACTER)
        if self.world:
            kinds.append(KIND_WORLD)
        return kinds

    # ---- 语义检索 ----

    @staticmethod
    def _table_signature(entries: List[LoreEntry]) -> str:
        blob = json.dumps(
            [{"title": e.title, "content": e.content, "pinned": e.pinned,
              "keys": list(e.keys or [])} for e in entries],
            ensure_ascii=False,
        )
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    def _kind_lock(self, kind: str) -> asyncio.Lock:
        lock = self._vec_locks.get(kind)
        if lock is None:
            lock = asyncio.Lock()
            self._vec_locks[kind] = lock
        return lock

    async def entry_vectors(self, kind: str, embedder) -> Tuple[List[LoreEntry], List[str], List[List[float]]]:
        """返回该 kind 的 (条目, 向量化文本, 已归一向量)，按表签名缓存。"""
        async with self._kind_lock(kind):
            entries = self._table(kind)[:_RAG_MAX_EMBED]
            sig = self._table_signature(entries)
            cached = self._vec_cache.get(kind)
            if cached and cached.get("sig") == sig:
                return cached["entries"], cached["texts"], cached["vecs"]

            texts = [
                ((e.title or "") + "\n" + " ".join(e.keys or [])
                 + "\n" + (e.content or ""))[:_EMBED_TEXT]
                for e in entries
            ]
            raw_vecs = await embedder.embed(texts)
            vecs = [normalize(v) for v in raw_vecs]
            self._vec_cache[kind] = {"sig": sig, "entries": entries, "texts": texts, "vecs": vecs}
            return entries, texts, vecs

    async def semantic_search(
        self,
        kind: str,
        keyword: str,
        embedder,
        top_k: int,
        threshold: float,
    ) -> Optional[List[Tuple[float, LoreEntry]]]:
        """语义检索：embed 失败返回 None（回退关键词）；全不达标返回空列表。"""
        try:
            qv = (await embedder.embed([keyword]))[0]
            entries, _texts, vecs = await self.entry_vectors(kind, embedder)
        except Exception:
            return None
        qn = normalize(qv)
        scored = [(dot(qn, v), e) for e, v in zip(entries, vecs)]
        if threshold and threshold > 0:
            scored = [item for item in scored if item[0] >= threshold]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[: int(top_k or 4)]

    async def trigger_block(self, text: str, embedder=None) -> str:
        """扫描本轮用户消息，返回自动注入的【相关设定】块（无命中返回 ""）。

        关键词/模糊命中优先；配了 embedder 时再用语义检索补足至多
        SEMANTIC_TOP_UP 条关键词未命中的相关条目。pinned 条目不参与
        （已由常驻块注入）。
        """
        text = (text or "").strip()
        if not text:
            return ""
        tables = [
            (KIND_CHARACTER, self.character.table if self.character else []),
            (KIND_WORLD, self.world.table if self.world else []),
        ]
        picked = _trigger.keyword_hits(tables, text)
        if embedder is not None and len(picked) < _trigger.MAX_TRIGGER_ENTRIES:
            picked.extend(
                await self._semantic_triggers(tables, text, embedder, picked)
            )
        return _trigger.render_trigger_block(_trigger.dedupe(picked))

    def depth_block_specs(self, rng=None) -> List[Dict[str, Any]]:
        """收集 pinned + position="depth" 的角色/世界条目（酒馆 @depth 注入）。

        每条按 probability 掷骰（0-100）；返回注入规格
        ``{title, content, role, depth, order}``，宏替换与真正插入由
        调用方完成（需要角色/用户名与会话消息）。
        """
        import random as _random

        r = rng or _random
        tables = [
            self.character.table if self.character else [],
            self.world.table if self.world else [],
        ]
        specs: List[Dict[str, Any]] = []
        for table in tables:
            for e in table:
                if not e.pinned or e.position != "depth":
                    continue
                if not (e.content or "").strip():
                    continue
                if r.random() * 100 >= e.probability:
                    continue
                specs.append({
                    "title": e.title,
                    "content": e.content,
                    "role": e.role if e.role in ("user", "assistant") else "system",
                    "depth": max(0, int(e.depth or 0)),
                    "order": int(e.insert_order or 100),
                })
        return specs

    async def _semantic_triggers(self, tables, text, embedder,
                                 already) -> List[Tuple[str, LoreEntry]]:
        """关键词未命中条目里，按向量余弦补足至多 SEMANTIC_TOP_UP 条。"""
        try:
            qv = normalize((await embedder.embed([text]))[0])
        except Exception:
            return []
        threshold = float(getattr(embedder.cfg, "score_threshold", 0.55) or 0.0)
        hit_ids = {id(e) for _k, e in already}
        out: List[Tuple[str, LoreEntry]] = []
        budget = _trigger.SEMANTIC_TOP_UP
        for kind, _table in tables:
            if budget <= 0:
                break
            try:
                entries, _texts, vecs = await self.entry_vectors(kind, embedder)
            except Exception:
                continue
            scored = []
            for e, v in zip(entries, vecs):
                if e.pinned or id(e) in hit_ids:
                    continue
                scored.append((dot(qv, v), e))
            scored.sort(key=lambda x: x[0], reverse=True)
            for score, e in scored[:_trigger._SEMANTIC_FILL_LIMIT]:
                if threshold and score < threshold:
                    break
                out.append((kind, e))
                hit_ids.add(id(e))
                budget -= 1
                if budget <= 0:
                    break
        return out


# ---- 对话范例 / 开场白 ----

def substitute_chat_macros(text: str, char_name: str, user_name: str = "用户") -> str:
    """替换 {{char}}/{{user}} 宏（忽略大小写与花括号内空白）。开场白与范例共用。"""
    cn = char_name or "角色"
    un = user_name or "用户"
    text = re.sub(r"\{\{\s*char\s*\}\}", cn, text or "", flags=re.IGNORECASE)
    text = re.sub(r"\{\{\s*user\s*\}\}", un, text, flags=re.IGNORECASE)
    return text


def parse_mes_example(raw: str, char_name: str, user_name: str = "用户") -> List[str]:
    """把 <START> 分隔的范例原文拆成轮次，并替换 {{char}}/{{user}}。"""
    rounds = [p.strip() for p in re.split(r"<START>", raw or "", flags=re.IGNORECASE) if p.strip()]
    return [substitute_chat_macros(r, char_name, user_name) for r in rounds]


_MES_EXAMPLE_HEADER = (
    "以下是该角色的对话风格范例，其中的剧情并未真实发生，仅供模仿语气、节奏与格式，"
    "不要把范例内容当作已经发生过的事："
)


# ---- 统一提示词构建 ----

@dataclass
class PromptOptions:
    """build_prompt 的选项。audience 非 None 即表示群聊成员路径。"""

    base: str = ""
    roleplay: Optional[RoleplaySettings] = None
    user_character: Optional[Character] = None
    # —— 群聊成员专属 ——
    audience: Optional[List[str]] = None
    memory: str = ""
    current_attributes: Optional[Dict[str, Any]] = None
    cross_memory_hint: bool = False


def build_prompt(character: Optional[Character], world: Optional[World], opts: PromptOptions) -> str:
    """把角色卡 + 世界观（含常驻条目/范例/预算截断）并入系统提示词。"""
    rp = opts.roleplay
    parts: List[str] = []

    if character:
        parts.append(f"你正在扮演角色「{character.name}」,以角色的身份、语气与记忆与用户对话。")
        pinned = _pinned_block(character.table, "角色常驻资料", rp)
        if pinned:
            parts.append(pinned)
        if character.intro:
            parts.append(f"【角色简介】\n{fit_budget(character.intro, _budget(rp, 'intro_chars'))}")
        if character.background:
            parts.append(
                f"【角色经历】\n{fit_budget(character.background, _budget(rp, 'background_chars'))}"
            )
        example = _mes_example_block(character, opts)
        if example:
            parts.append(example)
        # 与旧构建器一致：角色 query_lore 引导无条件注入
        parts.append(
            "角色内容记录表中还有更详细的资料，需要时调用工具 query_lore(kind='character', keyword=…) 查询。"
        )
    else:
        # 无角色卡：回退到通用 AI 助手人格，不使用扮演口吻
        parts.append(GENERAL_ASSISTANT_PROMPT)

    persona = _persona_block(opts.user_character)
    if persona:
        parts.append(persona)

    if world and world.content:
        parts.append(f"【世界观】\n{fit_budget(world.content, _budget(rp, 'world_chars'))}")
    if world:
        wpinned = _pinned_block(world.table, "世界常驻设定", rp)
        if wpinned:
            parts.append(wpinned)
        if world.table:
            parts.append(
                "世界设定表的更多细节可通过 query_lore(kind='world', keyword=…) 查询。"
            )

    if opts.audience is not None:
        names = "、".join(opts.audience)
        parts.append(
            f"【在场】你正与{names}以及用户在一个聊天窗口里自由群聊。"
            "这是多人轮流发言的场合，每个在场者都要参与进来，请以你的角色自然、主动地回应当前话题与用户的发言。"
            "不要替别人说话，也不要复读或机械重复别人已经说过的话。"
            "历史记录里，其他在场者说的话会以「名字：内容」的形式出现，那些不是你说的，"
            "不要把它们当成自己的台词或经历；你自己回复时直接输出正文，不要加自己的名字前缀。"
        )
        parts.append(
            "【回应要求】轮到你的回合，请务必以自己的角色开口说点什么——哪怕只是一句简短的感想、"
            "一个反应、一句反问或一个动作都可以，尽量简短自然，不要长篇大论、不要抢别人的话。"
            "只有在极少数情况下（例如这条消息纯粹点的是别人的名字、或所聊的是与你完全无关的私人话题），"
            "你才回复『略』来表示跳过。不要因为在你之前已有人开口就跟着沉默。"
        )

    if opts.memory:
        parts.append(f"【你在这个聊天里的记忆】\n{opts.memory}")

    if opts.audience is not None and opts.cross_memory_hint:
        parts.append(
            "你拥有跨对话记忆能力：可用工具 query_chat_memory 检索你自己参与的其它聊天窗口来确认过往；"
            "遇到值得长期记住的新事实（重要信息、约定、状态变化等），用工具 update_chat_memory 把条目写进你的记忆。"
        )

    cur_attr_text = _current_attributes_text(opts.current_attributes)
    if cur_attr_text:
        parts.append(f"【你的属性】\n{cur_attr_text}")

    # 角色卡自定义的系统级提示词（高优先级系统指令）
    if character and character.system_prompt and character.system_prompt.strip():
        parts.append(f"【系统提示】\n{character.system_prompt.strip()}")

    # 无角色卡也无世界观时：以有用助手身份回答，不注入扮演格式/工具要求
    if character or world:
        req = _requirements_block(character, world, rp)
        if req:
            parts.append(req)

    if character:
        attr = _attribute_block(character, rp)
        if attr:
            parts.append(attr)

    if not parts:
        return opts.base
    if opts.base:
        parts.append(opts.base)
    return "\n\n".join(parts)


def _budget(rp: Optional[RoleplaySettings], field_name: str) -> int:
    if rp is None:
        return 0
    return int(getattr(rp, field_name, 0) or 0)


def _pinned_block(table: List[LoreEntry], label: str, rp: Optional[RoleplaySettings]) -> Optional[str]:
    """渲染钉选条目为常驻块；无钉选返回 None。"""
    blocks = [f"『{e.title or '(无标题)'}』\n{e.content or '(空内容)'}" for e in (table or []) if e.pinned]
    if not blocks:
        return None
    kept, _dropped = fit_blocks(blocks, _budget(rp, "pinned_chars"))
    if not kept:
        return None
    return f"【{label}】\n" + "\n\n".join(kept)


def _mes_example_block(character: Character, opts: PromptOptions) -> Optional[str]:
    """渲染对话范例块；无范例返回 None。"""
    if not (character.mes_example or "").strip():
        return None
    user_name = opts.user_character.name if opts.user_character else "用户"
    rounds = parse_mes_example(character.mes_example, character.name, user_name)
    if not rounds:
        return None
    kept, _dropped = fit_blocks(rounds, _budget(opts.roleplay, "mes_example_chars"))
    if not kept:
        return None
    return "【对话范例】\n" + _MES_EXAMPLE_HEADER + "\n" + "\n\n".join(kept)


def _current_attributes_text(attributes: Optional[Dict[str, Any]]) -> str:
    if not attributes:
        return ""
    lines = "；".join(f"{k}={v}" for k, v in attributes.items() if not str(k).startswith("_"))
    return lines


def build_system_prompt(
    character: Character,
    world: World,
    base: str,
    roleplay: Optional[RoleplaySettings] = None,
    user_character: Optional[Character] = None,
) -> str:
    """把角色卡 + 世界观并入系统提示词。无角色/世界则返回 base。

    user_character：用户扮演的自设（persona），可选。设置后告诉 AI 用户正以该角色登场。
    """
    return build_prompt(
        character,
        world,
        PromptOptions(base=base, roleplay=roleplay, user_character=user_character),
    )


def build_member_system_prompt(
    name: str,
    memory: str,
    attributes: Optional[dict],
    audience: Optional[List[str]],
    character: Character = None,
    world: World = None,
    base: str = "",
    roleplay: Optional[RoleplaySettings] = None,
    user_character: Optional[Character] = None,
) -> str:
    """Beta：为群聊中单个 AI 参与者构建系统提示词。

    在统一构建器上叠加该 AI 的本窗口记忆、当前属性与在场名单。
    name 参数保留兼容（扮演句使用角色卡名 character.name）。
    """
    return build_prompt(
        character,
        world,
        PromptOptions(
            base=base,
            roleplay=roleplay,
            user_character=user_character,
            audience=audience,
            memory=memory or "",
            current_attributes=attributes,
            cross_memory_hint=bool(audience),
        ),
    )


def _persona_block(user_character: Optional[Character]) -> Optional[str]:
    """组装「用户扮演的自设（persona）」提示片段。无自设返回 None。"""
    if user_character is None:
        return None
    name = user_character.name or "用户"
    lines = [
        f"【你对话的用户】用户正在扮演角色「{name}」，把自己当作这个人物本身来对待："
        f"把用户所说的话都当作「{name}」说的，始终用「{name}」称呼他，并围绕他扮演的这一人物展开互动。"
    ]
    if user_character.intro:
        lines.append(f"【用户简介】\n{user_character.intro}")
    if user_character.background:
        lines.append(f"【用户经历】\n{user_character.background}")
    try:
        if user_character.attributes:
            import json

            keys = [a.get("key", "") for a in user_character.attributes if (a or {}).get("key")]
            if keys:
                lines.append(f"【用户属性】\n{'、'.join(k for k in keys if k)}")
    except Exception:
        pass
    return "\n\n".join(lines)


def _requirements_block(
    character: Optional[Character],
    world: Optional[World],
    roleplay: Optional[RoleplaySettings],
) -> Optional[str]:
    """组装「对白格式要求 + 工具使用要求」。

    角色卡/世界观各自可覆盖内置默认（prompt_format / prompt_tools）：
    - 对白格式：取角色卡的 prompt_format，其次内置 format_instruction（需 format_enabled）。
    - 工具使用：取角色卡 → 世界观 → 内置 tool_instruction（内置需 format_enabled）。
    """
    blocks: List[str] = []

    fmt = None
    if character and character.prompt_format and character.prompt_format.strip():
        fmt = character.prompt_format.strip()
    elif roleplay and roleplay.format_enabled and roleplay.format_instruction:
        fmt = roleplay.format_instruction.strip()
    if fmt:
        blocks.append(f"【对白格式要求】\n{fmt}")

    tools = None
    if character and character.prompt_tools and character.prompt_tools.strip():
        tools = character.prompt_tools.strip()
    elif world and world.prompt_tools and world.prompt_tools.strip():
        tools = world.prompt_tools.strip()
    elif roleplay and roleplay.format_enabled and roleplay.tool_instruction:
        tools = roleplay.tool_instruction.strip()
    if tools:
        blocks.append(f"【工具使用要求】\n{tools}")

    if not blocks:
        return None
    return "\n\n".join(blocks)


def _attribute_block(character: Optional[Character], roleplay: Optional[RoleplaySettings]) -> Optional[str]:
    """组装属性跟踪的系统提示片段，按当前角色解析出的属性定义描述。

    角色卡自带属性时即注入（不看全局开关）；旧卡回退全局 list 时
    仍需全局 enabled（resolve_attribute_defs 内部已处理）。
    """
    own_attrs = bool(character and getattr(character, "attributes", None))
    if not own_attrs and (roleplay is None or not roleplay.attributes.enabled):
        return None
    if roleplay is None:
        roleplay = RoleplaySettings()
    defs: List[AttributeDef] = resolve_attribute_defs(character, roleplay)
    if not defs:
        return None
    labels = "、".join(f"{d.label or d.key}({d.key})" for d in defs) or "（未配置）"
    if roleplay.attributes.ai_provided:
        return (
            f"当前角色带有可随剧情变化的属性（包括：{labels}）。"
            "在每回合对白结束后，根据本回合剧情发展调用工具 update_attributes 提交各属性的增减量（如好感度、金钱等变化），"
            "没有明显变化时可以不调用。不要直接把属性改成绝对新值，只提交本次增减。"
        )
    return (
        f"当前角色带有属性（包括：{labels}），如剧情需要，可在合适时机用工具 update_attributes 调整。"
    )
