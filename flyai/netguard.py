"""出站地址安全边界（SSRF 防护）。

web_fetch（httpx）与 AI 浏览器（Playwright）共用同一套主机判定：
拦截 localhost / 环回 / 私网 / 链路本地 / 保留 / 组播 / 未指定地址，
另含 Python 3.9 ip_address.is_private 漏掉的 CGNAT 100.64.0.0/10。

域名（nip.io 之类）在请求前先做 DNS 解析，解析出的任一 IP 命中拦截
即拒绝；httpx 请求再把连接 IP 钉到已校验的地址（pin_request），
保留原 Host 头与 TLS SNI/证书名校验，防解析校验与实际连接之间的
DNS rebinding。浏览器侧在逐请求路由拦截层做同样的解析校验
（Chromium 自行连接，TOCTOU 仍有残余窗口），并对非标准 IP 字面量
（0x7f000001 / 127.1 / 八进制等 OS 会识别的写法）按归一化后地址判定。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import List, Union
from urllib.request import getproxies, proxy_bypass

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# Python 3.9 的 is_private 未覆盖 CGNAT（RFC 6598，常见于云主机内网/元数据代理）
_EXTRA_V4_BLOCKS = ("100.64.0.0/10",)


class BlockedHost(Exception):
    """目标主机命中 SSRF 拦截（内网/本机/解析失败/重定向到内网等）。"""


def _norm(hostname: str) -> str:
    return (hostname or "").strip().lower().rstrip(".")


def is_blocked_ip(ip: IPAddress) -> bool:
    """IP 对象是否属于拦截范围。"""
    if ip.version == 6:
        # IPv4-mapped IPv6（::ffff:127.0.0.1）：按内嵌 v4 判定
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return is_blocked_ip(mapped)
        # 6to4（2002::/16）：第 3~6 字节内嵌 v4 出口地址
        if (int(ip) >> 112) == 0x2002:
            embedded = ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
            if is_blocked_ip(embedded):
                return True
    elif any(ip in ipaddress.ip_network(n) for n in _EXTRA_V4_BLOCKS):
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _ip_literal(hostname: str):
    """把主机名当 IP 字面量解析；非 IP（普通域名）返回 None。

    ipaddress 不认的非标准写法（hex/十进制整数/八进制/短格式）交给
    inet_aton 归一化——这些写法操作系统会按真实 IP 解析，不能放行。
    """
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(hostname))
    except OSError:
        return None


def is_blocked_host(hostname: str) -> bool:
    """拦截 localhost / 内网 / 环回 / 链路本地 / 非标准内网字面量。"""
    h = _norm(hostname)
    if not h:
        return True
    if h == "localhost" or h.endswith(".localhost"):
        return True
    ip = _ip_literal(h)
    if ip is None:
        # 普通域名（非 IP 字面量）放行字面判定；DNS 层由 resolve_and_check 收口
        return False
    return is_blocked_ip(ip)


async def resolve_ips(hostname: str) -> List[IPAddress]:
    """DNS 解析域名（A + AAAA），去重返回。NXDOMAIN / 无地址抛 BlockedHost。"""
    h = _norm(hostname)
    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(h, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedHost("DNS 解析失败: %s" % h) from exc
    out: List[IPAddress] = []
    seen = set()
    for _fam, _typ, _proto, _canon, sockaddr in infos:
        raw = sockaddr[0]
        ip_str = raw.split("%", 1)[0]  # 去掉 IPv6 zone id
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        key = str(ip)
        if key not in seen:
            seen.add(key)
            out.append(ip)
    if not out:
        raise BlockedHost("域名没有可用的解析地址: %s" % h)
    return out


async def resolve_and_check(hostname: str) -> List[IPAddress]:
    """主机名（IP 字面量或域名）SSRF 全量校验，返回可用于连接的 IP 列表。

    域名解析出的任一 IP 命中拦截即拒绝（含 nip.io 等把内网 IP 编码进域名的写法）。
    """
    h = _norm(hostname)
    if not h:
        raise BlockedHost("主机名为空")
    if h == "localhost" or h.endswith(".localhost"):
        raise BlockedHost("不允许访问 localhost")
    ip = _ip_literal(h)
    if ip is not None:
        if is_blocked_ip(ip):
            raise BlockedHost("不允许访问内网或本机地址: %s" % h)
        return [ip]
    ips = await resolve_ips(h)
    bad = [str(ip) for ip in ips if is_blocked_ip(ip)]
    if bad:
        raise BlockedHost("域名 %s 解析到内网或本机地址: %s" % (h, ", ".join(bad)))
    return ips


def proxy_in_use(hostname: str, scheme: str = "https") -> bool:
    """httpx（trust_env）是否会为该目标走系统/环境 HTTP 代理。

    与 httpx 的取代理逻辑一致：getproxies()（Windows 会读系统注册表），
    再按方案和 NO_PROXY 判断。代理可能是用户在系统里为翻墙/内网配置的，
    服务器进程即使没有 HTTPS_PROXY 环境变量也会命中。
    """
    try:
        info = getproxies()
        keys = (scheme, "all") if scheme in ("http", "https") else ("all",)
        if not any(info.get(k) for k in keys):
            return False
        if str(info.get("no") or "").strip() == "*":
            return False
        try:
            return not proxy_bypass(hostname)
        except Exception:
            return True
    except Exception:
        return False


def pin_request(request, ip: IPAddress) -> None:
    """把已构造的 httpx 请求改连指定 IP（必须事先通过 resolve_and_check）。

    保留原主机名用于虚拟主机与 TLS：重写 URL 主机、固定 Host 头，
    HTTPS 时通过 httpcore 的 sni_hostname 扩展维持原 SNI 与证书名校验。

    有 HTTP(S) 代理时不做 IP 钉住：httpcore 的 HTTP 代理通道不处理
    sni_hostname 扩展，URL 改成 IP 后 TLS 会按 IP 校验证书直接失败
    （CERTIFICATE_VERIFY_FAILED: IP address mismatch）。代理会自行解析
    域名，SSRF 的解析校验仍由调用方在请求前完成。
    """
    url = request.url
    host = url.host
    if proxy_in_use(host, url.scheme):
        return
    request.url = url.copy_with(host=str(ip))
    # httpx 在构造时已写入 host 头；显式覆盖，避免发成 IP
    request.headers["host"] = host
    if url.scheme == "https":
        # 作为 TLS server_hostname（SNI + 证书匹配）。必须传 str 而非 bytes：
        # anyio ≥4.13 的 idna2008_resolve 会对其再调 .encode("ascii")，
        # 传 bytes 直接 AttributeError；stdlib ssl 与旧版 anyio 两种都接受。
        request.extensions["sni_hostname"] = host
