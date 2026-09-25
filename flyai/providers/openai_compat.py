"""OpenAI 兼容协议适配器（主适配器）。

任何实现 POST {base_url}/chat/completions 且支持流式与 tools 的提供商均可接入。
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Dict, List

import httpx

from ..errors import ProviderError
from ..schemas import Message, ToolCall
from .base import BaseProvider, StreamEvent, StreamEventType


class OpenAICompatProvider(BaseProvider):
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
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": self.cfg.model,
            "stream": True,
            "messages": self._encode_request(messages),
        }
        if tool_specs:
            body["tools"] = tool_specs
            body["tool_choice"] = "auto"
        body.update(self.cfg.params)
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}

        async with self._client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                err_text = detail.decode("utf-8", errors="replace")[:800]
                raise ProviderError(
                    f"提供商返回 HTTP {resp.status_code}", status_code=resp.status_code, detail=err_text
                )
            # 工具调用按 index 累积
            call_acc: Dict[int, Dict] = {}
            finish_reason = None
            async for raw in resp.aiter_lines():
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if obj.get("error"):
                    raise ProviderError(f"提供商错误: {obj['error']}")
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield StreamEvent(StreamEventType.CONTENT_DELTA, text=delta["content"])
                # 思考内容：不同厂商字段名不同（reasoning_content / reasoning）
                reasoning = delta.get("reasoning_content")
                if reasoning is None:
                    reasoning = delta.get("reasoning")
                if reasoning:
                    yield StreamEvent(StreamEventType.THINKING_DELTA, text=reasoning)

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    holder = call_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        holder["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        holder["name"] = fn["name"]
                    if fn.get("arguments"):
                        holder["args"] += fn["arguments"]

            # 流结束：组装完整工具调用列表
            if call_acc:
                calls: List[ToolCall] = []
                for idx in sorted(call_acc):
                    h = call_acc[idx]
                    args: Dict = {}
                    raw_args = h["args"]
                    if raw_args:
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {"_raw": raw_args}
                    calls.append(ToolCall(id=h["id"] or f"call_{idx}", name=h["name"], arguments=args))
                yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=calls)
            elif finish_reason == "length":
                raise ProviderError("模型输出达到 token 上限被截断")

    # ---- 编解码 ----

    def _encode_request(self, messages: List[Message]) -> List[Dict]:
        out: List[Dict] = []
        for msg in messages:
            if msg.role == "system":
                out.append({"role": "system", "content": msg.content or ""})
            elif msg.role == "user":
                out.append({"role": "user", "content": msg.content or ""})
            elif msg.role == "assistant":
                item: Dict = {"role": "assistant", "content": msg.content or None}
                if msg.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments_json()},
                        }
                        for c in msg.tool_calls
                    ]
                out.append(item)
            elif msg.role == "tool":
                out.append(
                    {"role": "tool", "tool_call_id": msg.tool_call_id, "content": msg.content or ""}
                )
        return out