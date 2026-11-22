"""酒馆卡扩展接口：外链插图服务端代理（方案 4，默认关闭）与受控网络代理。

前端默认直连外链图片（方案 1，受每用户开关控制）。本代理仅在管理员
开启 `config.st_image.enabled` 且用户权限等级达到 `min_level` 时可用；
响应类型只放行常见位图，逐跳重定向都做 DNS/内网校验并钉住连接 IP，
复用 netguard 的 SSRF 防护。

`/api/st/net-proxy`（L4-C）把卡内 fetch 变成「宿主代发」：默认关闭，
域名策略为「任意公网 HTTPS 或白名单」并集，同样逐跳做 SSRF 校验；
请求头只透传 Accept/Content-Type，Cookie/Authorization 一律丢弃。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import math
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..netguard import BlockedHost, pin_request, resolve_and_check
from ..user import ADMIN_UID

router = APIRouter(prefix="/api/st", tags=["st"])
log = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_MAX_BYTES = 8 * 1024 * 1024
_TIMEOUT = 20.0
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_ALLOWED_TYPES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif",
    "image/webp", "image/avif",
})
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---- 受控网络代理（L4-C）----
_NET_METHODS = frozenset({"GET", "HEAD", "POST"})
_NET_UPLOAD_MAX = 256 * 1024
_NET_INFLIGHT_MAX = 4
# 超出并发时先排队而不是立即 429；等待要覆盖一次上游超时（15s），
# 否则海外/高延迟链路上排队请求会先于上游完成而误报 429。
_NET_INFLIGHT_WAIT = 20.0
# 只透传这两个请求头；Cookie/Authorization/Referer/Origin 等一律丢弃
_NET_REQUEST_HEADERS = frozenset({"accept", "content-type"})
_NET_RATE_WINDOW = 60.0

# (uid, get|post) → 滑动窗口内的时间戳；uid → 并发信号量。
# 进程内状态，重启即清空。
_net_rate: Dict[Tuple[str, str], Deque[float]] = {}
_net_sems: Dict[str, asyncio.Semaphore] = {}
# 复用一个连接池：高延迟链路上避免每个资源都重新 DNS/TCP/TLS
_net_client: Optional[httpx.AsyncClient] = None


def _get_net_client() -> httpx.AsyncClient:
    global _net_client
    if _net_client is None or getattr(_net_client, "is_closed", False):
        _net_client = httpx.AsyncClient(
            timeout=None,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=64,
                max_keepalive_connections=16,
                keepalive_expiry=30.0,
            ),
        )
    return _net_client


async def aclose_net_client() -> None:
    global _net_client
    if _net_client is not None:
        await _net_client.aclose()
        _net_client = None


def _check_access(sc, cfg):
    seg = getattr(cfg, "st_image", None)
    if seg is None or not bool(getattr(seg, "enabled", False)):
        raise HTTPException(404, "图片代理未开启")
    if sc.uid == ADMIN_UID:
        return seg
    required = int(getattr(seg, "min_level", 256) or 0)
    level = int(getattr(sc, "level", 0) or 0)
    if level < required:
        raise HTTPException(
            403,
            "权限等级不足（图片代理需要 Lv%d，当前 Lv%d）" % (required, level),
        )
    return seg


def _host_allowed(host: str, domains: List[str]) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if not domains:
        return True
    return any(h == d or h.endswith("." + d) for d in domains)


async def _fetch_image(url: str, domains: List[str]) -> Tuple[bytes, str]:
    """逐跳校验并下载图片；只接受位图类型，超过上限直接拒绝。"""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise HTTPException(400, "仅支持 http/https 图片地址")
            if not _host_allowed(parsed.hostname, domains):
                raise HTTPException(403, "图片域名不在白名单内")
            try:
                ips = await resolve_and_check(parsed.hostname)
            except BlockedHost as exc:
                raise HTTPException(403, str(exc))
            req = client.build_request(
                "GET", url,
                headers={"User-Agent": _UA, "Accept": "image/*",
                         "Referer": "%s://%s/" % (parsed.scheme, parsed.hostname)},
            )
            pin_request(req, ips[0])
            resp = await client.send(req, stream=True)
            try:
                location = (
                    resp.headers.get("location")
                    if resp.status_code in _REDIRECT_CODES else ""
                )
                if location:
                    url = str(httpx.URL(url).join(location))
                    continue
                if resp.status_code != 200:
                    raise HTTPException(
                        502, "图片下载失败（HTTP %d）" % resp.status_code
                    )
                ctype = (resp.headers.get("content-type") or "")
                ctype = ctype.split(";", 1)[0].strip().lower()
                if ctype not in _ALLOWED_TYPES:
                    raise HTTPException(415, "目标不是受支持的图片类型")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_BYTES:
                        raise HTTPException(413, "图片超过 8MB 上限")
                return bytes(body), ctype
            finally:
                await resp.aclose()
        raise HTTPException(502, "重定向次数过多")


@router.get("/img-proxy")
async def img_proxy(request: Request, url: str = ""):
    """代理外链插图：返回原始图片字节，前端用 Bearer 令牌从 blob 渲染。"""
    state = request.app.state.flyai
    sc = state.user(request)
    seg = _check_access(sc, state.config)
    target = (url or "").strip()
    if not target:
        raise HTTPException(400, "缺少图片地址")
    domains = list(getattr(seg, "allowed_domains", []) or [])
    body, ctype = await _fetch_image(target, domains)
    return Response(
        content=body,
        media_type=ctype,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Referrer-Policy": "no-referrer",
        },
    )


# ---- 受控网络代理（L4-C）----

class NetProxyPayload(BaseModel):
    method: str = "GET"
    url: str = ""
    # 只接受 Accept / Content-Type；其余键在服务端丢弃
    headers: Optional[Dict[str, str]] = None
    body_base64: Optional[str] = None


def _check_net_access(sc, cfg):
    """返回 st_net 配置段；未开启、等级不足或用户开关关闭时分别 404/403。"""
    seg = getattr(cfg, "st_net", None)
    if seg is None or not bool(getattr(seg, "enabled", False)):
        raise HTTPException(404, "网络代理未开启")
    if sc.uid != ADMIN_UID:
        required = int(getattr(seg, "min_level", 0) or 0)
        level = int(getattr(sc, "level", 0) or 0)
        if level < required:
            raise HTTPException(
                403,
                "权限等级不足（网络代理需要 Lv%d，当前 Lv%d）" % (required, level),
            )
    us = getattr(sc, "user_settings", None)
    if not bool(getattr(us, "st_net_proxy_enabled", False)):
        raise HTTPException(403, "用户未开启卡内网络代理")
    return seg


def _net_host_allowed(scheme: str, host: str, allow_any_https: bool,
                      domains: List[str]) -> bool:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if domains and any(h == d or h.endswith("." + d) for d in domains):
        return True
    return bool(allow_any_https) and scheme == "https"


def _net_rate_check(uid: str, per_minute: int, kind: str = "post") -> None:
    now = time.monotonic()
    window = _net_rate.setdefault((uid, kind), deque())
    while window and now - window[0] >= _NET_RATE_WINDOW:
        window.popleft()
    if len(window) >= per_minute:
        # 告诉客户端窗口最早一条还有多久过期；批量请求可据此退避重试
        retry_after = max(
            1, int(math.ceil(_NET_RATE_WINDOW - (now - window[0])))
        )
        raise HTTPException(
            429, "请求过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    window.append(now)


def _net_sem(uid: str) -> asyncio.Semaphore:
    sem = _net_sems.get(uid)
    if sem is None:
        sem = asyncio.Semaphore(_NET_INFLIGHT_MAX)
        _net_sems[uid] = sem
    return sem


async def _net_acquire(uid: str, timeout: float = _NET_INFLIGHT_WAIT):
    """并发超限时排队等待，尽量让批量请求自然排空而不是立刻 429。"""
    sem = _net_sem(uid)
    try:
        await asyncio.wait_for(sem.acquire(), timeout)
    except asyncio.TimeoutError:
        raise HTTPException(
            429, "并发请求过多，请稍后再试",
            headers={"Retry-After": "1"},
        )
    return sem


def _net_clean_headers(raw: Optional[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in (raw or {}).items():
        name = str(key or "").strip().lower()
        if name not in _NET_REQUEST_HEADERS:
            continue
        text = str(value or "")[:1024]
        if "\r" in text or "\n" in text:
            continue
        out[name] = text
    return out


async def _fetch_net(seg, method: str, url: str, headers: Dict[str, str],
                     body: bytes) -> Tuple[int, Dict[str, str], bytes, bool]:
    """逐跳校验并代发请求；返回状态码、响应头子集、响应体与截断标志。"""
    allow_any = bool(getattr(seg, "allow_any_public_https", True))
    domains = list(getattr(seg, "allowed_domains", []) or [])
    max_bytes = int(getattr(seg, "max_bytes", 5 * 1024 * 1024) or 5 * 1024 * 1024)
    timeout = float(getattr(seg, "timeout", 15.0) or 15.0)
    client = _get_net_client()
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(400, "仅支持 http/https 地址")
        if parsed.username or parsed.password:
            raise HTTPException(400, "地址不能包含用户名或密码")
        if not _net_host_allowed(
            parsed.scheme, parsed.hostname, allow_any, domains
        ):
            raise HTTPException(403, "域名不在网络代理白名单内")
        try:
            ips = await resolve_and_check(parsed.hostname)
        except BlockedHost as exc:
            raise HTTPException(403, str(exc))
        req_headers = {"User-Agent": _UA}
        req_headers.update(headers)
        req = client.build_request(
            method, url, headers=req_headers,
            content=(body if method == "POST" else None),
            timeout=timeout,
        )
        pin_request(req, ips[0])
        resp = await client.send(req, stream=True)
        try:
            location = (
                resp.headers.get("location")
                if resp.status_code in _REDIRECT_CODES else ""
            )
            if location:
                url = str(httpx.URL(url).join(location))
                if resp.status_code == 303 or (
                    resp.status_code in (301, 302) and method != "HEAD"
                ):
                    method = "GET"
                    body = b""
                continue
            data = bytearray()
            truncated = False
            async for chunk in resp.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    truncated = True
                    del data[max_bytes:]
                    break
            out_headers = {}
            for name in ("content-type", "content-length"):
                value = resp.headers.get(name)
                if value:
                    out_headers[name] = value
            return resp.status_code, out_headers, bytes(data), truncated
        finally:
            await resp.aclose()
    raise HTTPException(502, "重定向次数过多")


@router.post("/net-proxy")
async def net_proxy(payload: NetProxyPayload, request: Request):
    """卡内 fetch 的受控代理：宿主按完整 URL 确认后由服务端代发。

    不绕过 SSRF 防护，也不转发任何宿主凭据；响应体按配置上限截断后
    以 base64 返回，审计日志只记 host/method/status/字节数/耗时。
    """
    state = request.app.state.flyai
    sc = state.user(request)
    seg = _check_net_access(sc, state.config)

    method = (payload.method or "GET").strip().upper()
    if method not in _NET_METHODS:
        raise HTTPException(400, "仅支持 GET/HEAD/POST")
    url = (payload.url or "").strip()
    if not url:
        raise HTTPException(400, "缺少请求地址")

    body = b""
    if payload.body_base64:
        if method != "POST":
            raise HTTPException(400, "仅 POST 支持请求体")
        try:
            body = base64.b64decode(payload.body_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "请求体不是合法的 base64")
        if len(body) > _NET_UPLOAD_MAX:
            raise HTTPException(413, "请求体超过 256KB 上限")

    # 静态资源 GET/HEAD 单独用更宽松的窗口，避免卡启动批量加载撞 429
    is_get = method in ("GET", "HEAD")
    per_minute = int(
        getattr(seg, "per_minute_get", 600) if is_get
        else getattr(seg, "per_minute", 60)
    ) or (600 if is_get else 60)
    sem = await _net_acquire(sc.uid)
    started = time.monotonic()
    host = (urlsplit(url).hostname or "").lower()
    try:
        # 拿到并发名额后再计入每分钟窗口：排队等待的时间不占用额度
        _net_rate_check(sc.uid, per_minute, "get" if is_get else "post")
        status, headers, data, truncated = await _fetch_net(
            seg, method, url, _net_clean_headers(payload.headers), body
        )
    finally:
        sem.release()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    # 审计只记 host（不含 path/query），避免把卡内业务数据写进日志
    log.info(
        "st net-proxy uid=%s host=%s method=%s status=%d bytes=%d ms=%d",
        sc.uid, host, method, status, len(data), elapsed_ms,
    )
    return {
        "status": status,
        "headers": headers,
        "body_base64": base64.b64encode(data).decode("ascii"),
        "truncated": truncated,
    }
