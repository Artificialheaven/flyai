"""格式要求注入回归测试：只走系统提示词中的 format_instruction，
不再把 few-shot 示例伪装成 assistant 历史（旧书店幻觉问题）。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import RoleplaySettings
from flyai.lore.models import Character
from flyai.lore.query import build_system_prompt


def _rp(**kw):
    defaults = dict(
        format_enabled=True,
        format_instruction="内置格式规则 Z",
        format_example="（旧书店示例）「欢迎光临」",
    )
    defaults.update(kw)
    return RoleplaySettings(**defaults)


def test_format_instruction_in_system_prompt():
    c = Character(name="爱丽丝")
    prompt = build_system_prompt(c, None, "你是一个助手。", roleplay=_rp())
    assert "【对白格式要求】" in prompt and "内置格式规则 Z" in prompt


def test_format_example_never_injected():
    """即使旧配置里残留 format_example，提示词中也不出现示例剧情。"""
    c = Character(name="爱丽丝")
    prompt = build_system_prompt(c, None, "你是一个助手。", roleplay=_rp())
    assert "欢迎光临" not in prompt and "旧书店" not in prompt


def test_format_disabled_omits_instruction():
    c = Character(name="爱丽丝")
    prompt = build_system_prompt(c, None, "你是一个助手。", roleplay=_rp(format_enabled=False))
    assert "内置格式规则 Z" not in prompt


def test_no_card_no_format():
    # 无角色卡时退化为通用 AI 助手，不注入格式规则
    from flyai.lore.query import GENERAL_ASSISTANT_PROMPT

    prompt = build_system_prompt(None, None, "你是一个助手。", roleplay=_rp())
    assert prompt == GENERAL_ASSISTANT_PROMPT + "\n\n你是一个助手。"
    assert "内置格式规则 Z" not in prompt
