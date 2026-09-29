"""Anthropic 原生 Messages API 适配器。

负责把内部统一消息编解码为 Anthropic 的 content blocks，并解析其流式事件。
多轮工具调用时需保留 thinking signature 签名块（thinking_blocks 原样回放）。
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Dict, List

from ..errors import ProviderError
from ..schemas import Message, ToolCall
from .base import BaseProvider, StreamEvent, StreamEventType

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseProvider):
    def stream_chat(
        self,
        messages: List[Message],
        tool_specs: List[dict],
        **kwargs,
    ) -> AsyncIterator[StreamEvent]:
        return self._stream_chat(messages, tool_specs, **kwargs)

    async def _stream_chat(
        self,
        messages: List[Message],
        tool_specs: List[dict],
    ) -> AsyncIterator[StreamEvent]:
        self._assert_key_ascii()
        url = self.cfg.base_url.rstrip("/") + "/v1/messages"
        system, body_messages = self._encode_request(messages)
        body: Dict = {
            "model": self.cfg.model,
            "max_tokens": int(self.cfg.params.get("max_tokens", 4096)),
            "messages": body_messages,
        }
        if system:
            body["system"] = system
        if tool_specs:
            body["tools"] = [self._to_anthropic_spec(spec) for spec in tool_specs]
        thinking = self.cfg.params.get("thinking")
        if thinking and thinking.get("enabled"):
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": int(thinking.get("budget_tokens", 2048)),
            }
        headers = {
            "x-api-key": self.cfg.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        async with self._client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                err_text = detail.decode("utf-8", errors="replace")[:800]
                raise ProviderError(
                    f"Anthropic 返回 HTTP {resp.status_code}", status_code=resp.status_code, detail=err_text
                )
            async for event in self._parse_stream(resp):
                yield event

    # ---- 流式事件解析 ----

    async def _parse_stream(self, resp) -> AsyncIterator[StreamEvent]:
        event_name = None
        # 工具调用按 content index 累积
        call_acc: Dict[int, Dict] = {}
        stop_reason = None
        has_tool_use = False
        input_tokens = 0
        output_tokens = 0

        async for raw in resp.aiter_lines():
            line = raw.rstrip("\r")
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
                continue
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event_name == "error":
                    raise ProviderError(f"Anthropic 错误: {obj}")
                if event_name == "message_start":
                    u = (obj.get("message") or {}).get("usage") or {}
                    input_tokens = int(u.get("input_tokens") or input_tokens)
                elif event_name == "content_block_start":
                    block = obj.get("content_block") or {}
                    btype = block.get("type")
                    idx = obj.get("index", 0)
                    if btype == "tool_use":
                        has_tool_use = True
                        call_acc[idx] = {"id": block.get("id"), "name": block.get("name"), "args": ""}
                elif event_name == "content_block_delta":
                    delta = obj.get("delta") or {}
                    dtype = delta.get("type")
                    if dtype == "text_delta":
                        yield StreamEvent(StreamEventType.CONTENT_DELTA, text=delta.get("text", ""))
                    elif dtype == "thinking_delta":
                        yield StreamEvent(StreamEventType.THINKING_DELTA, text=delta.get("thinking", ""))
                    elif dtype == "input_json_delta":
                        idx = obj.get("index", 0)
                        holder = call_acc.get(idx)
                        if holder is not None:
                            holder["args"] += delta.get("partial_json", "")
                elif event_name == "message_delta":
                    stop_reason = (obj.get("delta") or {}).get("stop_reason") or stop_reason
                    u = obj.get("usage") or {}
                    if u.get("output_tokens") is not None:
                        output_tokens = int(u.get("output_tokens"))

        if input_tokens or output_tokens:
            yield StreamEvent(
                StreamEventType.USAGE,
                usage={
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                },
            )

        # 流结束：若有工具调用则产出
        if call_acc:
            calls: List[ToolCall] = []
            for idx in sorted(call_acc):
                h = call_acc[idx]
                args: Dict = {}
                if h["args"]:
                    try:
                        args = json.loads(h["args"])
                    except json.JSONDecodeError:
                        args = {"_raw": h["args"]}
                calls.append(ToolCall(id=h["id"], name=h["name"], arguments=args))
            yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=calls)
        elif stop_reason == "max_tokens":
            raise ProviderError("模型输出达到 max_tokens 上限被截断")

    # ---- 编解码 ----

    def _to_anthropic_spec(self, spec: dict) -> Dict:
        fn = spec.get("function") or spec
        return {
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }

    def _encode_request(self, messages: List[Message]):
        """返回 (system 字符串, anthropic messages 列表)。

        规则：
        - system 消息拼成顶层 system。
        - assistant+tool_calls → text + tool_use blocks。
        - 连续多条 role=tool → 折叠成一条 user 消息含多个 tool_result blocks，保持严格交替。
        - assistant 若有 thinking_blocks，原样插回 blocks 最前（保留 signature 供多轮工具调用回放）。
        """
        system_parts: List[str] = []
        non_system: List[Message] = []
        for msg in messages:
            if msg.role == "system":
                if msg.content:
                    system_parts.append(msg.content)
            else:
                non_system.append(msg)

        final_messages: List[Dict] = []
        i = 0
        while i < len(non_system):
            msg = non_system[i]
            if msg.role == "tool":
                # 折叠连续 tool 消息为一条 user
                results: List[Dict] = []
                while i < len(non_system) and non_system[i].role == "tool":
                    tm = non_system[i]
                    block: Dict = {
                        "type": "tool_result",
                        "tool_use_id": tm.tool_call_id,
                        "content": self._tool_result_content(tm),
                    }
                    is_error = (tm.ok is False) or (
                        tm.ok is None and (tm.content or "").startswith("[工具错误]")
                    )
                    if is_error:
                        block["is_error"] = True
                    results.append(block)
                    i += 1
                final_messages.append({"role": "user", "content": results})
                continue

            if msg.role == "user":
                final_messages.append({"role": "user", "content": self._user_content(msg)})
                i += 1
                continue

            if msg.role == "assistant":
                blocks: List[Dict] = []
                # 思考块回放（extended thinking 开启时必需保留 signature）
                if self._thinking_enabled() and msg.thinking_blocks:
                    blocks.extend(msg.thinking_blocks)
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for c in msg.tool_calls:
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": c.id,
                                "name": c.name,
                                "input": c.arguments,
                            }
                        )
                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                final_messages.append({"role": "assistant", "content": blocks})
                i += 1
                continue

            # 其他未知 role 跳过
            i += 1

        system = "\n\n".join(system_parts) if system_parts else ""
        return system, final_messages

    def _tool_result_content(self, msg: Message):
        """tool_result：视觉模型下带上回贴的生成图片（text+image blocks）。"""
        if not msg.images:
            return msg.content or ""
        blocks: List[Dict] = [{"type": "text", "text": msg.content or "工具已执行"}]
        for img in msg.images:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.media_type,
                        "data": img.data,
                    },
                }
            )
        return blocks

    def _user_content(self, msg: Message):
        """user 消息：有图时编为 text+image blocks，无图保持字符串。"""
        if not msg.images:
            return msg.content or ""
        blocks: List[Dict] = [{"type": "text", "text": msg.content or "请看图"}]
        for img in msg.images:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.media_type,
                        "data": img.data,
                    },
                }
            )
        return blocks

    def _thinking_enabled(self) -> bool:
        t = self.cfg.params.get("thinking") or {}
        return bool(t.get("enabled"))