"""SSE 帧格式化。"""

import json


def sse_event(event: str, data) -> str:
    """把事件名和可 JSON 序列化的 data 格式化为一条 SSE 帧。

    event: xxx
    data: {json}

    空行结尾。
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"