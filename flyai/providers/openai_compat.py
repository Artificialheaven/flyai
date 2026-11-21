"""OpenAI 兼容协议适配器（主适配器）。

任何实现 POST {base_url}/chat/completions 且支持流式与 tools 的提供商均可接入。
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Dict, List, Optional

import httpx

from ..errors import ProviderError
from ..schemas import Message, ToolCall
from .base import (
    OPENCODE_THINKING_OFF_EFFORT,
    OPENCODE_THINKING_ON_EFFORT,
    BaseProvider,
    StreamEvent,
    StreamEventType,
    is_opencode_gateway,
)


def _cached_tokens(usage: Dict) -> int:
    """缓存命中的输入 token：OpenAI 在 prompt_tokens_details.cached_tokens，
    DeepSeek 在 prompt_cache_hit_tokens；两者量级同义（已含在 prompt_tokens 内）。"""
    details = usage.get("prompt_tokens_details") or {}
    try:
        hit = int(details.get("cached_tokens") or 0)
    except (TypeError, ValueError):
        hit = 0
    if hit <= 0:
        try:
            hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        except (TypeError, ValueError):
            hit = 0
    return max(0, hit)


def _content_is_tool_echo(content: str, calls: List[ToolCall]) -> bool:
    """正文是否只是工具调用的回声（参数被厂商重复写进 content 的情况）。

    判定极严：去掉 ```json 围栏与 <tool_call> 标签后，整段文本必须恰好是
    JSON（对象或数组），且与本轮 tool_calls 的 name+arguments 逐个完全一致，
    避免误伤正文中恰好包含 JSON 的正常回复。
    """
    raw = (content or "").strip()
    if not raw or not calls:
        return False
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if fenced:
        raw = fenced.group(1).strip()
    raw = re.sub(r"</?tool_call[^>]*>", "", raw).strip()
    if not raw or raw[0] not in "[{":
        return False
    open_ch, close_ch = ("[", "]") if raw[0] == "[" else ("{", "}")
    left, right = raw.find(open_ch), raw.rfind(close_ch)
    if left < 0 or right <= left:
        return False
    # JSON 外侧只允许空白/标签残余，不允许夹带正文
    if raw[:left].strip() or raw[right + 1:].strip():
        return False
    try:
        obj = json.loads(raw[left:right + 1])
    except json.JSONDecodeError:
        return False
    items = obj if isinstance(obj, list) else [obj]
    if len(items) != len(calls) or not items:
        return False
    used = set()
    for item in items:
        if not isinstance(item, dict):
            return False
        name = item.get("name")
        args = item.get("arguments", item.get("input")) if name else None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                pass
        for ci, c in enumerate(calls):
            if ci in used:
                continue
            if name is None:
                # 裸参数对象：单调用轮且参数完全一致
                if len(calls) == 1 and item == c.arguments:
                    used.add(ci)
                    break
            elif c.name == name and args == c.arguments:
                used.add(ci)
                break
        else:
            return False
    return len(used) == len(calls)


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
        deep_thinking: bool = False,
        max_tokens: Optional[int] = None,
        extra_params: Optional[Dict] = None,
        session_id: str = "",
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
        # 预设采样参数：高于模型配置、低于窗口/用户级 max_tokens 覆盖；
        # max_tokens 不从此通道进入（白名单本身不含，此处再防御一次）
        for k, v in (extra_params or {}).items():
            if k != "max_tokens" and v is not None:
                body[k] = v
        # 窗口/用户级 max_tokens 覆盖：在模型配置 params 之后，优先级最高；
        # None/0 不覆盖（跟随模型配置）
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        # 深度思考开关：在基础参数之后合并，确保开关参数优先于基础 params。
        # 各厂商协议不统一，具体片段由模型配置给出：
        # - thinking_params：开关开启时合并（默认不思考的模型靠它开启）
        # - thinking_off_params：开关关闭时合并（火山/DeepSeek 新版等默认思考
        #   的模型靠它显式关闭，如 {"thinking":{"type":"disabled"}}）
        # 两者皆空时：普通厂商保留模型默认行为；opencode 网关改用
        # reasoning_effort=high/none 映射二元开关（网关默认就会思考，不显式
        # 关闭则关不掉）。
        auto_reasoning = False
        if deep_thinking:
            if getattr(self.cfg, "thinking_params", None):
                body.update(self.cfg.thinking_params)
            elif is_opencode_gateway(self.cfg.base_url):
                if "reasoning_effort" not in body:
                    body["reasoning_effort"] = OPENCODE_THINKING_ON_EFFORT
                    auto_reasoning = True
        elif getattr(self.cfg, "thinking_off_params", None):
            body.update(self.cfg.thinking_off_params)
        elif is_opencode_gateway(self.cfg.base_url):
            body["reasoning_effort"] = OPENCODE_THINKING_OFF_EFFORT
            auto_reasoning = True
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        headers.update(self._gateway_headers(session_id))

        # stream_options 拿 token 用量；个别兼容厂商不认该字段（400），去掉重试一次
        try:
            async for ev in self._run_stream(url, body, headers, True):
                yield ev
        except ProviderError as exc:
            detail = (exc.detail or "").lower()
            if exc.status_code == 400 and "stream_options" in detail:
                async for ev in self._run_stream(url, body, headers, False):
                    yield ev
            elif exc.status_code == 400 and auto_reasoning and (
                    "thinking" in detail or "reasoning" in detail
                    or "effort" in detail):
                # 极少数网关型号（如 kimi-k2.7-code）只接受开启思考，
                # reasoning_effort=none 会被拒。去掉自动注入的参数重试一次，
                # 退回模型默认行为，避免整轮对话失败。
                body.pop("reasoning_effort", None)
                async for ev in self._run_stream(url, body, headers, True):
                    yield ev
            else:
                raise

    async def _run_stream(self, url, body, headers, include_usage) -> AsyncIterator[StreamEvent]:
        req_body = dict(body)
        if include_usage:
            req_body["stream_options"] = {"include_usage": True}
        async with self._client.stream("POST", url, json=req_body, headers=headers) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                err_text = detail.decode("utf-8", errors="replace")[:800]
                raise ProviderError(
                    f"提供商返回 HTTP {resp.status_code}", status_code=resp.status_code, detail=err_text
                )
            # 工具调用按 index 累积
            call_acc: Dict[int, Dict] = {}
            content_acc: List[str] = []
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
                # 用量块（include_usage 时随末尾空 choices 块到达）
                usage = obj.get("usage")
                if usage:
                    yield StreamEvent(
                        StreamEventType.USAGE,
                        usage={
                            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                            "completion_tokens": int(usage.get("completion_tokens") or 0),
                            "cached_tokens": _cached_tokens(usage),
                        },
                    )
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    content_acc.append(delta["content"])
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
                # 个别厂商（部分中转/新版模型）会把同一次工具调用同时写进
                # content（裸 JSON/代码块/工具标签）与 tool_calls：参数因此
                # 混进回复正文。识别后下发整体替换，把回声正文清空。
                full_content = "".join(content_acc)
                if _content_is_tool_echo(full_content, calls):
                    yield StreamEvent(StreamEventType.CONTENT_REPLACE, text="")
                yield StreamEvent(StreamEventType.TOOL_CALLS, tool_calls=calls)
            elif finish_reason == "length":
                raise ProviderError("模型输出达到 token 上限被截断")

    # ---- 编解码 ----

    def _encode_request(self, messages: List[Message]) -> List[Dict]:
        out: List[Dict] = []
        for msg in messages:
            if getattr(msg, "tombstone", None):
                continue  # 撤回墓碑：兜底过滤（未知 role 本也不会被编码）
            if msg.role == "system":
                out.append({"role": "system", "content": msg.content or ""})
            elif msg.role == "user":
                out.append({"role": "user", "content": self._user_content(msg)})
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
                item = {"role": "tool", "tool_call_id": msg.tool_call_id}
                if msg.images:
                    parts = [{"type": "text", "text": msg.content or "工具已执行"}]
                    for img in msg.images:
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{img.media_type};base64,{img.data}"
                                },
                            }
                        )
                    item["content"] = parts
                else:
                    item["content"] = msg.content or ""
                out.append(item)
        return out

    def _user_content(self, msg: Message):
        """user 消息：有图时编为多模态 parts，无图保持字符串（前缀缓存友好）。"""
        if not msg.images:
            return msg.content or ""
        parts: List[Dict] = [{"type": "text", "text": msg.content or "请看图"}]
        for img in msg.images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img.media_type};base64,{img.data}"},
                }
            )
        return parts