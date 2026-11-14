"""P4 外部触发端点：POST /api/ext/trigger（Bearer ext_token，不走登录态）。

鉴权与普通登录令牌完全独立：
- ext_trigger.enabled=False 或 token 为空 → 端点对外表现为 404，仿佛不存在；
- 令牌比对一律 hmac.compare_digest，缺失/不匹配 → 401；
- 请求体 64 KiB 硬上限 → 413；source/text/session 校验失败 → 400；
- 冷却冲突 → 429；入队失败已在 dispatch_trigger 内回滚冷却 → 503。
处理逻辑放在可直接调用的 handle_trigger 里，路由只做传输层适配。
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..proactor.trigger import MAX_BODY_BYTES, dispatch_trigger

logger = logging.getLogger("flyai.ext")

router = APIRouter(tags=["ext"])


def _extract_bearer(auth_header: Optional[str]) -> str:
    if not auth_header:
        return ""
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


async def handle_trigger(state, body_bytes: bytes,
                         auth_header: Optional[str]) -> Tuple[int, Dict[str, Any]]:
    """纯处理函数：返回 (http_status, body_dict)，测试可绕过 ASGI 直调。"""
    settings = state.config.ext_trigger
    if not getattr(settings, "enabled", False) or not getattr(
        settings, "token", ""
    ):
        return 404, {"ok": False, "error": "端点未启用"}
    token = _extract_bearer(auth_header)
    if not token or not hmac.compare_digest(token, settings.token):
        return 401, {"ok": False, "error": "令牌无效"}
    if len(body_bytes) > MAX_BODY_BYTES:
        return 413, {
            "ok": False,
            "error": "请求体超过 %d 字节" % MAX_BODY_BYTES,
        }
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 400, {"ok": False, "error": "请求体不是合法 JSON"}
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "请求体必须是 JSON 对象"}
    result = await dispatch_trigger(state, payload)
    return int(result["status"]), result["body"]


@router.post("/api/ext/trigger")
async def ext_trigger(request: Request):
    state = request.app.state.flyai
    body = await request.body()
    status, payload = await handle_trigger(
        state, body, request.headers.get("authorization")
    )
    return JSONResponse(status_code=status, content=payload)
