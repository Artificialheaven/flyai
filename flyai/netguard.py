"""出站地址安全边界（SSRF 防护）。

web_fetch（httpx）与 AI 浏览器（Playwright）共用同一套主机判定：
拦截 localhost / 环回 / 私网 / 链路本地 / 保留 / 组播 / 未指定地址。

注意：对域名只做字面判定，不做 DNS 解析（DNS rebinding 不在本模块覆盖范围；
浏览器侧在逐请求路由拦截层做同样的字面检查，覆盖页面 JS 发出的请求）。
"""

from __future__ import annotations

import ipaddress


def is_blocked_host(hostname: str) -> bool:
    """拦截 localhost / 内网 / 环回 / 链路本地等地址（SSRF 防护）。"""
    h = (hostname or "").strip().lower().rstrip(".")
    if not h:
        return True
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # 普通域名（非 IP 字面量）放行；浏览器/客户端会自行解析
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )
