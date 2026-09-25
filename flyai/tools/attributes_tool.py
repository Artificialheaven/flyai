"""update_attributes 工具：每回合结束后 AI 修改角色属性值（好感度、金钱等）。

通过 ToolContext.services['attributes']（AttributeManager）访问当前会话属性。
"""

from __future__ import annotations

from typing import Any, Dict

from .base import Tool, ToolContext, ToolResult


class UpdateAttributesTool(Tool):
    name = "update_attributes"
    description = (
        "在每回合对白结束时调用，根据本回合剧情增减角色的属性值（如好感度、金钱等）。"
        "changes 为增减量列表，每个元素含 key（属性名）与 delta（本次增减量，可为负数）；"
        "不要把整个属性改成绝对新值，只提交本次变化的增减量。"
        "修改会按设定范围自动钳制，返回更新后的全部属性。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "属性名，例如 affection / money",
                        },
                        "delta": {
                            "type": "number",
                            "description": "本次增减量，正数增加负数减少",
                        },
                    },
                    "required": ["key", "delta"],
                    "additionalProperties": False,
                },
                "description": "本回合的属性增减列表",
            }
        },
        "required": ["changes"],
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        manager = (ctx.services or {}).get("attributes")
        if manager is None:
            return ToolResult(
                ok=False, output="[工具错误] 当前会话未启用属性跟踪，无法修改属性"
            )

        changes = arguments.get("changes") or []
        if not isinstance(changes, list) or not changes:
            return ToolResult(ok=False, output="[参数错误] changes 至少需包含一项 {key, delta}")

        updated = manager.apply_changes(changes)
        return ToolResult(
            ok=True,
            output=f"[属性已更新] 当前属性 → {manager.describe()}",
            meta={"attributes": updated},
        )