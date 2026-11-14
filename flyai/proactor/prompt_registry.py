"""提示词注册表（P4）：7 个分区 + 占位符白名单 + 覆盖渲染。

结构照抄 Tide 的 (key, 允许占位符, 默认文本, 说明)，但分区从 20 个缩到
FlyAI 实际用到的 7 个（技术书 §8.4）。默认文本与改造前的硬编码逐字一致，
未设置覆盖时行为不变；未知 ``{...}`` 占位符原样保留，避免误伤提示词里
本来就想给模型看的花括号内容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..agentdb.prompt_store import PromptOverrideStore
from ..user import ADMIN_UID

# 与 mood.py / proactor.py / routes_chat.py / self_memory.py 旧硬编码逐字一致
DEFAULT_MOOD_WRAP = (
    "（内心独白·只宣布一次）刚刚的互动让你心情{direction}：{reason}"
)
DEFAULT_WAKE_NOTICE = (
    "【系统唤醒】现在是你主动开启话题的时机：基于最近的对话，"
    "用一句口语化的话联系对方；如果确实没什么可说，回复 [静默]。"
)
DEFAULT_SPEAKUP_NOTICE = (
    "【主动搭话】挑一个轻松自然的话题主动说点什么，"
    "贴合人设与最近的对话；不想发言就回复 [静默]。"
)
# 恒等模板：默认保持「任务原文直送」的旧行为，管理员可自行加包装指令
DEFAULT_LOOP_START = "{task}"
DEFAULT_LOOP_CONTINUE = "{notice}"
DEFAULT_COMPACT = (
    "你是长跑任务的上下文压缩器。把下面的工作历史压写成简短客观的工作纪要："
    "已完成的动作、关键结论、待办事项与约束，保留数字、文件名、ID 等硬信息，"
    "不要寒暄、不要臆造未发生的事。"
)
DEFAULT_SELF_MEMORY_HEADER = "【我的自我记忆】"

KEY_MOOD_WRAP = "mood_wrap"
KEY_WAKE_NOTICE = "wake_notice"
KEY_SPEAKUP_NOTICE = "speakup_notice"
KEY_LOOP_START = "loop_start"
KEY_LOOP_CONTINUE = "loop_continue"
KEY_COMPACT = "compact"
KEY_SELF_MEMORY_HEADER = "self_memory_header"

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class PromptPartition:
    key: str
    placeholders: Tuple[str, ...]
    default: str
    desc: str


PROMPT_PARTITIONS: Dict[str, PromptPartition] = {
    KEY_MOOD_WRAP: PromptPartition(
        KEY_MOOD_WRAP, ("direction", "reason"),
        DEFAULT_MOOD_WRAP,
        "心情显著变化时注入的内心独白（只宣布一次）",
    ),
    KEY_WAKE_NOTICE: PromptPartition(
        KEY_WAKE_NOTICE, (),
        DEFAULT_WAKE_NOTICE,
        "主动唤醒（保底/概率）时给模型的隐藏通知",
    ),
    KEY_SPEAKUP_NOTICE: PromptPartition(
        KEY_SPEAKUP_NOTICE, (),
        DEFAULT_SPEAKUP_NOTICE,
        "主动搭话命中时给模型的隐藏通知",
    ),
    KEY_LOOP_START: PromptPartition(
        KEY_LOOP_START, ("task",),
        DEFAULT_LOOP_START,
        "长跑任务首条消息的包装模板，默认原样透传任务",
    ),
    KEY_LOOP_CONTINUE: PromptPartition(
        KEY_LOOP_CONTINUE, ("notice",),
        DEFAULT_LOOP_CONTINUE,
        "长跑中用户打断指示的包装模板，默认原样透传",
    ),
    KEY_COMPACT: PromptPartition(
        KEY_COMPACT, (),
        DEFAULT_COMPACT,
        "长跑滚动压缩时摘要模型的 system 指令",
    ),
    KEY_SELF_MEMORY_HEADER: PromptPartition(
        KEY_SELF_MEMORY_HEADER, (),
        DEFAULT_SELF_MEMORY_HEADER,
        "LIVE_CTX 自我记忆分块的标题行",
    ),
}


def render_text(template: str, placeholders: Tuple[str, ...],
                values: Dict[str, Any]) -> str:
    """只替换白名单占位符；未知 {..} 与缺值占位符原样保留。"""
    allowed = set(placeholders)

    def _sub(match: "re.Match") -> str:
        name = match.group(1)
        if name not in allowed or name not in values:
            return match.group(0)
        return str(values[name])

    return _PLACEHOLDER_RE.sub(_sub, template)


class PromptRegistry:
    """admin agent.db 上的覆盖层 + 进程内缓存（设置保存后 invalidate）。"""

    def __init__(self, data_dir, uid: str = ADMIN_UID,
                 store: Optional[PromptOverrideStore] = None) -> None:
        self.data_dir = data_dir
        self.uid = uid
        self.store = store or PromptOverrideStore(data_dir)
        self._cache: Optional[Dict[str, str]] = None

    def invalidate(self) -> None:
        self._cache = None

    def _load(self) -> Dict[str, str]:
        if self._cache is None:
            try:
                self._cache = self.store.all(self.uid)
            except Exception:
                # agent.db 尚未就绪等极端情况：退回全默认，不阻断主流程
                self._cache = {}
        return self._cache

    def template(self, key: str) -> str:
        partition = PROMPT_PARTITIONS.get(key)
        if partition is None:
            raise KeyError("未知提示词分区: %s" % key)
        return self._load().get(key, partition.default)

    def render(self, key: str, **values: Any) -> str:
        partition = PROMPT_PARTITIONS.get(key)
        if partition is None:
            raise KeyError("未知提示词分区: %s" % key)
        return render_text(self.template(key), partition.placeholders, values)

    def set_override(self, key: str, text: str) -> None:
        if key not in PROMPT_PARTITIONS:
            raise KeyError("未知提示词分区: %s" % key)
        self.store.set(self.uid, key, text)
        self.invalidate()

    def reset_override(self, key: str) -> bool:
        ok = self.store.delete(self.uid, key)
        self.invalidate()
        return ok

    def describe(self) -> List[Dict[str, Any]]:
        """设置页用：分区元信息 + 当前覆盖文本（无覆盖给空串）。"""
        overrides = self._load()
        rows = {r["key"]: r for r in self.store.list_rows(self.uid)}
        out: List[Dict[str, Any]] = []
        for key, partition in PROMPT_PARTITIONS.items():
            row = rows.get(key)
            out.append({
                "key": key,
                "desc": partition.desc,
                "placeholders": list(partition.placeholders),
                "default": partition.default,
                "override": overrides.get(key, ""),
                "updated_at": row["updated_at"] if row else 0,
            })
        return out
