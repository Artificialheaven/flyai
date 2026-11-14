"""自研精简 MCP 客户端（P4）：只支持 stdio + 换行分帧 JSON-RPC 2.0。

官方 mcp SDK 要求 Python >=3.10，不能进 FlyAI 的 3.9 主环境，因此 v1 只用
标准库实现 initialize / tools/list / tools/call 三个方法；streamable-http
与 SSE 传输留给后续 sidecar 方案（技术书 R8）。
"""

from .stdio import (
    McpError,
    McpProtocolError,
    StdioMcpClient,
    sanitize_tool_name,
)

__all__ = [
    "McpError",
    "McpProtocolError",
    "StdioMcpClient",
    "sanitize_tool_name",
]
