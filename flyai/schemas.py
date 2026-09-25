"""内部统一消息模型。

整个 agentic loop 只操作这些内部类型，提供商差异只存在于适配器边界。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """一次模型发起的工具调用。"""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=data["id"],
            name=data["name"],
            arguments=data.get("arguments", {}),
        )

    def arguments_json(self) -> str:
        """为 OpenAI 格式生成 JSON 字符串。"""
        return json.dumps(self.arguments, ensure_ascii=False)


@dataclass
class Message:
    """统一消息。role 为 system/user/assistant/tool 之一。"""

    role: str
    content: Optional[str] = None
    # 思考文本：供前端展示与 OpenAI 适配器透传
    thinking: Optional[str] = None
    # 提供商原生思考块（Anthropic 带 signature，多轮工具调用回放必需）
    thinking_blocks: Optional[List[Dict[str, Any]]] = None
    # assistant 消息的工具调用列表
    tool_calls: Optional[List[ToolCall]] = None
    # role=tool 时对应的调用 id
    tool_call_id: Optional[str] = None
    # 工具名（可选，便于调试）
    name: Optional[str] = None
    # role=tool 时的执行结果状态（true 成功 / false 失败 / None 未知旧数据）
    ok: Optional[bool] = None
    # 群聊中 AI 自评「无需发言」时标记隐藏：仍存历史，但不展示
    hidden: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "thinking_blocks": self.thinking_blocks,
            "tool_calls": [c.to_dict() for c in self.tool_calls] if self.tool_calls else None,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "ok": self.ok,
            "hidden": self.hidden,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        calls = data.get("tool_calls")
        return cls(
            role=data["role"],
            content=data.get("content"),
            thinking=data.get("thinking"),
            thinking_blocks=data.get("thinking_blocks"),
            tool_calls=[ToolCall.from_dict(c) for c in calls] if calls else None,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            ok=data.get("ok"),
            hidden=bool(data.get("hidden", False)),
        )