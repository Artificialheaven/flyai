"""一次性补全 helper：让 AI 做「单回合、无工具」的生成（压缩、属性建议等）。

复用现有流式适配器：以空 tool_specs 调用 stream_chat，把 CONTENT_DELTA 拼接为完整文本。
避免新增非流式 HTTP 通道，保持 providers 只需维护一条流式路径。
"""

from __future__ import annotations

from typing import List, Optional

from ..errors import ProviderError
from ..schemas import Message
from ..providers.base import StreamEventType


async def complete(provider, messages: List[Message], system_prompt: str = "",
                   max_tokens: Optional[int] = None,
                   session_id: str = "",
                   partial_ok: bool = False) -> str:
    """向模型发一条无工具请求，返回完整文本正文。

    - messages: 已有对话（Message 列表，不含 system）。
    - system_prompt: 可选的系统提示，插到最前。
    - max_tokens / session_id: 透传给适配器（短任务限长、网关会话头）。
    - partial_ok: 流在收到正文后报错（如思考模型吃满 max_tokens 被截断）时，
      保留已产出的正文而不是抛错。仅用于「有输出就能用」的短任务（标题）。
    """
    request_messages: List[Message] = []
    if system_prompt:
        request_messages.append(Message(role="system", content=system_prompt))
    request_messages.extend(messages)

    # 仅透传显式给出的参数，兼容只实现 (messages, tool_specs) 的简易 provider
    extra = {}
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    if session_id:
        extra["session_id"] = session_id

    parts: List[str] = []
    try:
        async for ev in provider.stream_chat(request_messages, tool_specs=[], **extra):
            if ev.type == StreamEventType.CONTENT_DELTA and ev.text:
                parts.append(ev.text)
    except ProviderError:
        # 思考类模型会把 reasoning 算进 max_tokens，可能在正文输出前/后
        # 因 finish_reason=length 报错。短任务允许拿截断前的正文。
        if not (partial_ok and parts):
            raise
    return "".join(parts)