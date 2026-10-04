"""SSE 帧格式化。"""

import json
from typing import Optional


def sse_event(event: str, data, event_id: Optional[int] = None) -> str:
    """把事件名和可 JSON 序列化的 data 格式化为一条 SSE 帧。

    id: N（可选，断线重连时客户端据此告诉服务端已收到哪一帧）
    event: xxx
    data: {json}

    空行结尾。
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    head = ("id: %d\n" % event_id) if event_id is not None else ""
    return f"{head}event: {event}\ndata: {payload}\n\n"
