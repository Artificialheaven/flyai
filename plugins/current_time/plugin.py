"""CurrentTimeTool：获取当前日期时间。

纯本地计算、无参数、无外部依赖，不存在安全边界问题。
同时给出本地时间（含星期/时区）、UTC 时间和 ISO 8601，
方便 AI 在被问「现在几点 / 今天几号 / 星期几」时直接作答。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from flyai.tools import Tool, ToolContext, ToolResult

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class CurrentTimeTool(Tool):
    name = "get_current_time"
    description = (
        "获取当前的日期和时间。无需参数，返回本地时间（含星期、时区）、UTC 时间与 ISO 8601。"
        "当用户询问现在几点、今天日期、星期几或需要时间基准时调用本工具，不要凭训练知识猜测时间。"
    )
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    async def run(self, arguments: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        output = (
            "当前时间信息：\n"
            "- 本地时间：{local}（{weekday}，时区 {tz}）\n"
            "- UTC 时间：{utc}\n"
            "- ISO 8601：{iso}"
        ).format(
            local=now_local.strftime("%Y-%m-%d %H:%M:%S"),
            weekday=_WEEKDAYS[now_local.weekday()],
            tz=str(now_local.tzinfo),
            utc=now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            iso=now_local.isoformat(),
        )
        return ToolResult(output=output, meta={"timestamp": now_local.timestamp()})
