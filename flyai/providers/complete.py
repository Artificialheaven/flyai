"""一次性补全 helper：让 AI 做「单回合、无工具」的生成（压缩、属性建议等）。

复用现有流式适配器：以空 tool_specs 调用 stream_chat，把 CONTENT_DELTA 拼接为完整文本。
避免新增非流式 HTTP 通道，保持 providers 只需维护一条流式路径。
"""

from __future__ import annotations

from typing import List

from ..schemas import Message
from ..providers.base import StreamEventType


async def complete(provider, messages: List[Message], system_prompt: str = "") -> str:
    """向模型发一条无工具请求，返回完整文本正文。

    - messages: 已有对话（Message 列表，不含 system）。
    - system_prompt: 可选的系统提示，插到最前。
    """
    request_messages: List[Message] = []
    if system_prompt:
        request_messages.append(Message(role="system", content=system_prompt))
    request_messages.extend(messages)

    parts: List[str] = []
    async for ev in provider.stream_chat(request_messages, tool_specs=[]):
        if ev.type == StreamEventType.CONTENT_DELTA and ev.text:
            parts.append(ev.text)
    return "".join(parts)