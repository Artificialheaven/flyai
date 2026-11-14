"""P3 统一时钟：显式时区、TZ 无关、可注入 now。

部署机可能没装 tzdata（Windows 上 ZoneInfo 找不到 Asia/Shanghai），又不允许
引入新依赖，因此对常见时区内置固定偏移兜底。关键约束：所有调度判定都显式
传入 tz，绝不读取机器本地时区，改系统 TZ 不影响裁决结果（测试锁定此语义）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# 无 tzdata 时的兜底偏移（小时）。覆盖配置默认与国内常见场景；
# 未列出的时区名退回 UTC，保证行为可预测而不是悄悄用机器本地时间。
_FALLBACK_OFFSETS = {
    "Asia/Shanghai": 8,
    "Asia/Tokyo": 9,
    "Asia/Singapore": 8,
    "Asia/Hong_Kong": 8,
    "Asia/Taipei": 8,
    "Asia/Seoul": 9,
    "Asia/Bangkok": 7,
    "Asia/Kolkata": 5.5,
    "Europe/London": 0,
    "Europe/Berlin": 1,
    "America/New_York": -5,
    "America/Los_Angeles": -8,
    "UTC": 0,
}

_cache = {}


def get_zone(tzname: Optional[str]):
    """把时区名解析成 tzinfo；优先系统库，缺失时用固定偏移兜底。"""
    name = (tzname or "Asia/Shanghai").strip() or "Asia/Shanghai"
    if name in _cache:
        return _cache[name]
    try:
        zone = ZoneInfo(name)
    except Exception:
        offset = _FALLBACK_OFFSETS.get(name, 0)
        zone = timezone(timedelta(hours=offset))
    _cache[name] = zone
    return zone


def local_dt(now: float, tzname: str) -> datetime:
    """epoch 秒 → 指定时区的 naive datetime（仅取挂钟字段做判定）。"""
    return datetime.fromtimestamp(float(now), tz=get_zone(tzname)).replace(tzinfo=None)


def local_date_str(now: float, tzname: str) -> str:
    return local_dt(now, tzname).date().isoformat()


def parse_hhmm(text: str):
    """'HH:MM' → (hour, minute)；非法返回 None。"""
    try:
        hh, mm = str(text).split(":", 1)
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def in_quiet_hours(now: float, quiet: str, tzname: str) -> bool:
    """当前挂钟时间是否落在静默时段（支持跨午夜，如 23:00-07:00）。"""
    if not quiet or "-" not in quiet:
        return False
    start_s, end_s = quiet.split("-", 1)
    start, end = parse_hhmm(start_s), parse_hhmm(end_s)
    if start is None or end is None:
        return False
    cur = local_dt(now, tzname)
    cur_min = cur.hour * 60 + cur.minute
    s_min, e_min = start[0] * 60 + start[1], end[0] * 60 + end[1]
    if s_min == e_min:
        return False
    if s_min < e_min:
        return s_min <= cur_min < e_min
    # 跨午夜：起点之后或终点之前
    return cur_min >= s_min or cur_min < e_min


def is_workday(now: float, tzname: str) -> bool:
    """周一=0 … 周日=6；周末不算工作日。"""
    return local_dt(now, tzname).weekday() < 5


def parse_due_at(value, now: float, tzname: str) -> Optional[float]:
    """把工具/API 给的到期时间解析成 epoch 秒；非法返回 None。

    接受：epoch 秒（int/float 或纯数字字符串）、'YYYY-MM-DD HH:MM'、
    'YYYY-MM-DD HH:MM:SS'、仅 'HH:MM'（今天该时刻，已过则明天）。
    解析一律按显式 tzname 的挂钟时间，不读机器本地时区。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) > 0 else None
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        try:
            num = float(text)
        except ValueError:
            return None
        return num if num > 0 else None
    zone = get_zone(tzname)
    from datetime import datetime

    fmts = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M")
    for fmt in fmts:
        try:
            naive = datetime.strptime(text, fmt)
            return naive.replace(tzinfo=zone).timestamp()
        except ValueError:
            continue
    hm = parse_hhmm(text)
    if hm is not None:
        local = local_dt(now, tzname)
        cand = local.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if cand <= local:
            from datetime import timedelta

            cand += timedelta(days=1)
        return cand.replace(tzinfo=zone).timestamp()
    return None


def format_local(epoch: float, tzname: str) -> str:
    """epoch → 'YYYY-MM-DD HH:MM' 本地挂钟字符串。"""
    return local_dt(epoch, tzname).strftime("%Y-%m-%d %H:%M")
