"""属性跟踪：AttributeManager / update_attributes 工具 / 提示词注入 单测。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import AttributeDef, AttributeSettings, RoleplaySettings
from flyai.lore.attributes import AttributeManager, resolve_attribute_defs
from flyai.lore.query import build_system_prompt
from flyai.sessions.models import Session
from flyai.tools.attributes_tool import UpdateAttributesTool
from flyai.tools.base import ToolContext

DEFS = [
    AttributeDef(key="affection", label="好感度", initial=0, min=-100, max=100),
    AttributeDef(key="money", label="金钱", initial=100, min=0, max=999999),
]


def _mgr():
    return AttributeManager(Session(), DEFS)


@pytest.mark.asyncio
async def test_update_attributes_applies_and_clamps():
    tool = UpdateAttributesTool()
    ctx = ToolContext(services={"attributes": _mgr()})
    r = await tool.run({"changes": [{"key": "affection", "delta": 5}, {"key": "money", "delta": -30}]}, ctx)
    assert r.ok and "好感度(affection): 5.0" in r.output and "金钱(money): 70.0" in r.output


@pytest.mark.asyncio
async def test_update_attributes_clamps_to_bounds():
    r = await tool_run({"changes": [{"key": "affection", "delta": 999}, {"key": "money", "delta": -999}]})
    assert r.ok and "好感度(affection): 100.0" in r.output and "金钱(money): 0.0" in r.output


def tool_run(payload):
    return UpdateAttributesTool().run(payload, ToolContext(services={"attributes": _mgr()}))


@pytest.mark.asyncio
async def test_update_attributes_rejects_unknown_key():
    r = await tool_run({"changes": [{"key": "bogus", "delta": 10}]})
    assert r.ok and "bogus" not in r.output


@pytest.mark.asyncio
async def test_update_attributes_missing_service():
    ctx = ToolContext()
    r = await UpdateAttributesTool().run({"changes": [{"key": "affection", "delta": 1}]}, ctx)
    assert r.ok is False


def _rp_with_defaults():
    return RoleplaySettings(
        format_enabled=True,
        format_instruction="用（ ）标动作，用 _ 标内心。",
        attributes=AttributeSettings(enabled=True, ai_provided=True, list=DEFS),
    )


def test_no_card_prompt_is_general_assistant():
    """无角色卡：回退通用 AI 助手人格，不注入扮演格式/属性要求。"""
    from flyai.lore.query import GENERAL_ASSISTANT_PROMPT

    prompt = build_system_prompt(None, None, "你是一个助手。", roleplay=_rp_with_defaults())
    assert prompt == GENERAL_ASSISTANT_PROMPT + "\n\n你是一个助手。"
    assert "（ ）" not in prompt and "update_attributes" not in prompt


def test_build_system_prompt_injects_format_and_attrs():
    from flyai.lore.models import Character

    char = Character(name="小红", attributes=[
        {"key": "affection", "label": "好感度", "initial": 0, "min": -100, "max": 100},
        {"key": "money", "label": "金钱", "initial": 100, "min": 0, "max": 999999},
    ])
    prompt = build_system_prompt(char, None, "你是一个助手。", roleplay=_rp_with_defaults())
    assert "（ ）" in prompt and "用 _ 标内心" in prompt
    assert "好感度(affection)" in prompt and "update_attributes" in prompt
    assert prompt.endswith("你是一个助手。")


def test_card_attrs_work_when_global_disabled():
    """属性已迁移到角色卡：全局 enabled=False 时，卡自带属性仍应解析并注入提示。"""
    from flyai.lore.models import Character

    rp = RoleplaySettings(
        format_enabled=False,
        attributes=AttributeSettings(enabled=False, list=[]),
    )
    char = Character(name="小红", attributes=[
        {"key": "affection", "label": "好感度", "initial": 3, "min": 0, "max": 100},
    ])
    defs = resolve_attribute_defs(char, rp)
    assert len(defs) == 1 and defs[0].key == "affection"

    prompt = build_system_prompt(char, None, "你是一个助手。", roleplay=rp)
    assert "好感度(affection)" in prompt and "update_attributes" in prompt


def test_attribute_manager_binds_room_member():
    """群聊：管理器绑定 RoomMember，增减写入 member.attributes 并钳制。"""
    from flyai.sessions.models import RoomMember

    member = RoomMember(name="小红")
    mgr = AttributeManager(member, DEFS)
    assert member.attributes == {"affection": 0, "money": 100}
    mgr.apply_changes([{"key": "affection", "delta": 999}])
    assert member.attributes["affection"] == 100  # 钳到上限


def test_build_system_prompt_disabled_attrs_omits_tool_hint():
    rp = RoleplaySettings(
        format_enabled=False,
        attributes=AttributeSettings(enabled=False, ai_provided=True, list=[]),
    )
    prompt = build_system_prompt(None, None, "你是一个助手。", roleplay=rp)
    from flyai.lore.query import GENERAL_ASSISTANT_PROMPT

    assert prompt == GENERAL_ASSISTANT_PROMPT + "\n\n你是一个助手。"