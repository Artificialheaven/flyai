"""内部统一消息模型。

整个 agentic loop 只操作这些内部类型，提供商差异只存在于适配器边界。
"""

from __future__ import annotations

import json
import uuid
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
class ImageAttachment:
    """用户消息附带的一张图片（仅视觉/多模态模型使用）。

    data 为不含 data: 前缀的 base64 字符串；适配器在边界处自行拼装。
    """

    media_type: str = "image/jpeg"
    data: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"media_type": self.media_type, "data": self.data}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImageAttachment":
        return cls(media_type=data.get("media_type", "image/jpeg"), data=data.get("data", ""))


@dataclass
class Message:
    """统一消息。role 为 system/user/assistant/tool 之一。"""

    role: str
    # 消息唯一 id：版本/编辑功能以「用户消息 id」锚定一整轮
    id: str = ""
    content: Optional[str] = None
    # 思考文本：供前端展示与 OpenAI 适配器透传
    thinking: Optional[str] = None
    # 提供商原生思考块（Anthropic 带 signature，多轮工具调用回放必需）
    thinking_blocks: Optional[List[Dict[str, Any]]] = None
    # 本轮思考总用时（秒）：历史回读据此展示「已思考（用时 N 秒）」；旧数据为 None
    thinking_secs: Optional[float] = None
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
    # 群聊发言座位 id：该消息（含同一回合的工具调用/结果）由哪个 AI 座位产生。
    # 必须落盘：刷新/重启后 _actor_view 靠它把别人的工具回合与思考隔离开
    actor_id: Optional[str] = None
    # 产生该 AI 消息的角色卡 id：座位换卡后旧消息仍归属旧卡，
    # 自动回忆据此按角色 id 过滤（同座位不同卡不互相串回忆）。
    # 旧数据可能为空，消费方需回落到 actor_id / name 判断
    character_id: Optional[str] = None
    # 本轮自动召回的记忆条目（仅前端折叠组展示用；发给模型时各适配器忽略，
    # 召回正文已在当时拼进系统提示词）
    recall: Optional[List[Dict[str, Any]]] = None
    # user 消息附带的图片（视觉模型）
    images: Optional[List[ImageAttachment]] = None
    # 撤回墓碑：非 None 时此消息是一条"已撤回轮"占位（role 固定为 "tombstone"），
    # 载荷仅用于前端"重新编辑"回填；任何模型视图/脚本化消费都必须过滤
    tombstone: Optional[Dict[str, Any]] = None
    # MVU 变量快照：该条助手消息生成后的 stat_data；用于回退/重算当前状态。
    # 非 MVU 会话保持 None（旧数据兼容）
    stat_data: Optional[Dict[str, Any]] = None
    # 消息来源：空串=用户/AI 正常产生；"card"=酒馆卡沙箱在用户确认下发起
    origin: str = ""
    # EJS 模板（方案 B）：原文、是否已由客户端渲染、该消息的 message 作用域变量
    raw_content: Optional[str] = None
    template_rendered: Optional[bool] = None
    template_vars: Optional[Dict[str, Any]] = None
    # 客户端提交的幂等 id：重复提交同一份结果不再执行
    template_render_id: Optional[str] = None
    # 酒馆卡消息扩展字段（ST 的 message.extra）：卡内 createChatMessages 写入，
    # 刷新后由 getChatMessages 读回（如约会大作战用它标记 1 楼开场与建档副本）。
    st_extra: Optional[Dict[str, Any]] = None
    # 提示词骨架来源标记（不落盘）：系统提示词/PHI/LIVE_CTX/@depth 等
    # 卡与预设文本。EJS 提示词侧只执行这些消息，聊天历史与工具结果只剥离。
    template_source: Optional[bool] = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "thinking": self.thinking,
            "thinking_blocks": self.thinking_blocks,
            "thinking_secs": self.thinking_secs,
            "tool_calls": [c.to_dict() for c in self.tool_calls] if self.tool_calls else None,
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "ok": self.ok,
            "hidden": self.hidden,
            "actor_id": self.actor_id,
            "character_id": self.character_id,
            "recall": self.recall,
            "images": [i.to_dict() for i in self.images] if self.images else None,
            "tombstone": self.tombstone,
            "stat_data": self.stat_data,
            "origin": self.origin,
            "raw_content": self.raw_content,
            "template_rendered": self.template_rendered,
            "template_vars": self.template_vars,
            "template_render_id": self.template_render_id,
            "st_extra": self.st_extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        calls = data.get("tool_calls")
        return cls(
            role=data["role"],
            id=data.get("id", ""),
            content=data.get("content"),
            thinking=data.get("thinking"),
            thinking_blocks=data.get("thinking_blocks"),
            thinking_secs=data.get("thinking_secs"),
            tool_calls=[ToolCall.from_dict(c) for c in calls] if calls else None,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            ok=data.get("ok"),
            # 保留 None：from_dict→to_dict 必须幂等，heal 用签名比对识别孤儿消息
            hidden=data.get("hidden"),
            actor_id=data.get("actor_id"),
            character_id=data.get("character_id"),
            recall=data.get("recall"),
            images=(
                [ImageAttachment.from_dict(i) for i in data.get("images")]
                if data.get("images")
                else None
            ),
            tombstone=data.get("tombstone"),
            stat_data=data.get("stat_data"),
            origin=data.get("origin") or "",
            raw_content=data.get("raw_content"),
            template_rendered=data.get("template_rendered"),
            template_vars=data.get("template_vars"),
            template_render_id=data.get("template_render_id"),
            st_extra=data.get("st_extra"),
        )