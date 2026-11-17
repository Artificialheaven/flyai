"""DeepSeek 官方工具接口客户端：搜索 / 抓取 / TTS / ASR。

独立于聊天模型提供商，只用管理员配置的 DeepSeek Key（仅服务器保存）。
每次调用新建 httpx 客户端，配置保存后立即生效；测试可注入 MockTransport。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .errors import DeepSeekError

DEFAULT_BASE_URL = "https://api.deepseek.com"
SERVICE_FLAGS = {
    "search": "search_enabled",
    "fetch": "fetch_enabled",
    "tts": "tts_enabled",
    "asr": "asr_enabled",
}


def configured(cfg) -> bool:
    """DeepSeek 段是否具备基本可用条件（总开关 + Key）。"""
    if cfg is None or not getattr(cfg, "enabled", False):
        return False
    return bool((getattr(cfg, "api_key", "") or "").strip())


def usable(cfg, service: str) -> bool:
    """某服务当前是否应优先走 DeepSeek。"""
    if not configured(cfg):
        return False
    flag = SERVICE_FLAGS.get(service)
    return bool(getattr(cfg, flag, True)) if flag else True


def _assert_key(cfg) -> str:
    key = (getattr(cfg, "api_key", "") or "").strip()
    if not key:
        raise DeepSeekError("未配置 DeepSeek API Key", status_code=400)
    if any(ord(c) > 127 for c in key):
        raise DeepSeekError("DeepSeek API Key 含非 ASCII 字符", status_code=400)
    return key


def _tools_url(base_url: str, path: str) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    prefix = base if base.endswith("/v1") else base + "/v1"
    return prefix + "/tools/" + path.lstrip("/")


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code")
            if msg:
                return str(msg)
        msg = data.get("message")
        if msg:
            return str(msg)
    text = (resp.text or "").strip()
    return text[:300] or ("HTTP %s" % resp.status_code)


def _results_list(data: Any) -> List[Dict[str, Any]]:
    """兼容 ``{results: [...]}`` / ``{data: [...]}`` / 裸列表三种返回。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("results", "data", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                nested = val.get("results")
                if isinstance(nested, list):
                    return [x for x in nested if isinstance(x, dict)]
    return []


class DeepSeekToolsClient:
    """薄封装；所有方法失败时抛 :class:`DeepSeekError`。"""

    def __init__(self, cfg, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.cfg = cfg
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        c = self.cfg
        kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(float(getattr(c, "timeout_seconds", 60) or 60),
                                     connect=10.0),
        }
        proxy = (getattr(c, "proxy", "") or "").strip()
        if proxy:
            kwargs["proxy"] = proxy
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def _request(self, path: str, *, json_body: Optional[dict] = None,
                       files: Optional[dict] = None,
                       headers: Optional[dict] = None,
                       raw: bool = False):
        cfg = self.cfg
        key = _assert_key(cfg)
        url = _tools_url(getattr(cfg, "base_url", DEFAULT_BASE_URL), path)
        hdrs = {"Authorization": "Bearer " + key}
        if headers:
            hdrs.update(headers)
        try:
            async with self._client() as client:
                resp = await client.post(url, json=json_body, files=files,
                                         headers=hdrs)
        except httpx.HTTPError as exc:
            raise DeepSeekError("DeepSeek 接口请求失败: %s" % exc) from exc
        if resp.status_code >= 400:
            raise DeepSeekError(_error_message(resp), status_code=resp.status_code)
        if raw:
            return resp.content
        try:
            return resp.json()
        except ValueError as exc:
            raise DeepSeekError("DeepSeek 接口返回了非 JSON 响应") from exc

    async def search(self, query: str, count: int = 8) -> List[Dict[str, Any]]:
        data = await self._request("search", json_body={
            "query": query,
            "count": max(1, min(int(count or 8), 10)),
        })
        return _results_list(data)

    async def fetch(self, url: str) -> Dict[str, str]:
        data = await self._request("fetch", json_body={"url": url})
        if not isinstance(data, dict):
            raise DeepSeekError("DeepSeek 抓取接口返回格式异常")
        return {
            "title": str(data.get("title") or ""),
            "content": str(data.get("content") or ""),
        }

    async def tts(self, text: str, voice: str = "stella",
                  fmt: str = "wav") -> bytes:
        fmt = fmt if fmt in ("wav", "pcm") else "wav"
        voice = voice if voice in ("stella", "tide") else "stella"
        audio = await self._request("tts", raw=True, json_body={
            "text": text,
            "voice_id": voice,
            "format": fmt,
            "filter_markdown": True,
        })
        if not audio:
            raise DeepSeekError("DeepSeek 语音合成返回空音频")
        return audio

    async def asr(self, file_bytes: bytes, filename: str = "audio.wav",
                  content_type: str = "audio/wav") -> Dict[str, Any]:
        data = await self._request("asr", files={
            "file": (filename, file_bytes, content_type),
        })
        if not isinstance(data, dict):
            raise DeepSeekError("DeepSeek 语音识别返回格式异常")
        return {
            "text": str(data.get("text") or ""),
            "duration_ms": int(data.get("duration_ms") or 0),
        }