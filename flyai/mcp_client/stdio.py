"""MCP stdio 传输：换行分帧的 JSON-RPC 2.0，懒连接、可重连。

约束（对齐技术书 §8.1）：
- 仅 stdio；每条消息是单行 JSON + ``\\n``，不实现 LSP 式 Content-Length 分帧；
- 连接懒建立，调用遇 EOF/超时由上层 manager 重连一次后重试；
- 锁全部惰性创建（3.9 的 asyncio.Lock 构造即绑事件循环，禁止 __init__ 构造）；
- 进程环境由上层清洗后传入，本模块不自行读取 os.environ 中的密钥。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("flyai.mcp")

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "flyai-mcp-mini", "version": "1.0"}

# mcp__<server>__<tool> 中的 tool 段只保留小写字母数字下划线，其余归并为 _
_TOOL_SAFE_RE = re.compile(r"[^a-z0-9_]+")
_SERVER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class McpError(RuntimeError):
    """MCP 调用失败的统一异常类型。"""


class McpProtocolError(McpError):
    """对端返回 JSON-RPC error 或协议帧非法。"""


class McpEOF(McpError):
    """stdio 已 EOF（对端进程退出），上层可据此重连一次。"""


def sanitize_tool_name(name: str) -> str:
    """把 server 自报的工具名规整成 mcp__ 桥接名里的安全段。"""
    text = (name or "").strip().lower()
    safe = _TOOL_SAFE_RE.sub("_", text).strip("_")
    return safe or "tool"


def valid_server_id(server_id: str) -> bool:
    return bool(_SERVER_ID_RE.match(server_id or ""))


class StdioMcpClient:
    """单个 MCP server 的一条 stdio 连接（串行请求，v1 不做并发多路复用）。"""

    def __init__(self, command: str, args: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None,
                 timeout: float = 30.0) -> None:
        self.command = command
        self.args = [str(a) for a in (args or [])]
        self.env = dict(env or {})
        self.timeout = max(1.0, float(timeout))
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._id = 0
        self._lock: Optional[asyncio.Lock] = None

    # ---------- 生命周期 ----------

    @property
    def connected(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def start(self) -> None:
        """拉起子进程并完成 initialize 握手；失败抛 McpError。"""
        if self.connected:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env or None,
            )
        except (FileNotFoundError, OSError) as exc:
            raise McpError("无法启动 MCP server %s: %s"
                           % (self.command, exc))
        self._proc = proc
        try:
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
            )
            if not isinstance(result, dict):
                raise McpProtocolError("initialize 返回结构非法")
            # 通知无 id，对端不应答
            await self._write({
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })
            logger.info("MCP server 已连接: %s %s", self.command, self.args)
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        """关掉子进程并回收；多次调用安全。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                with contextlib.suppress(Exception):
                    await proc.wait()
        # 排空 stderr 仅用于日志，不阻塞流程
        if proc.stderr is not None:
            tail = await self._drain_stderr(proc)
            if tail:
                logger.warning("MCP server %s stderr: %s",
                               self.command, tail[-500:])

    @staticmethod
    async def _drain_stderr(proc) -> str:
        async def _read():
            try:
                data = await proc.stderr.read()
                return data.decode("utf-8", "replace")
            except Exception:
                return ""

        try:
            return await asyncio.wait_for(_read(), timeout=1.0)
        except asyncio.TimeoutError:
            return ""

    # ---------- JSON-RPC ----------

    async def _write(self, frame: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise McpEOF("MCP 子进程已退出")
        try:
            proc.stdin.write(
                (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")
            )
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise McpEOF("MCP stdin 已关闭: %s" % exc)

    async def _read_frame(self) -> Dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise McpEOF("MCP 连接不存在")
        try:
            line = await asyncio.wait_for(proc.stdout.readline(),
                                          timeout=self.timeout)
        except asyncio.TimeoutError:
            # 统一收敛进 McpError 体系，manager 才能按「超时重连一次」处理
            raise McpError("MCP 响应超时（%ss）" % self.timeout)
        if not line:
            raise McpEOF("MCP stdout EOF（server 进程退出 code=%s）"
                         % proc.returncode)
        try:
            return json.loads(line.decode("utf-8", "replace"))
        except ValueError as exc:
            raise McpProtocolError("MCP 帧不是合法 JSON: %s" % exc)

    async def _request(self, method: str,
                       params: Optional[Dict[str, Any]] = None) -> Any:
        """串行化的单次请求-响应（通知帧自动跳过）。"""
        async with self._get_lock():
            self._id += 1
            msg_id = self._id
            frame: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id,
                                     "method": method}
            if params is not None:
                frame["params"] = params
            await self._write(frame)
            while True:
                data = await self._read_frame()
                if data.get("id") != msg_id:
                    # 对端主动通知：v1 不订阅，跳过等响应
                    continue
                if "error" in data:
                    err = data["error"] or {}
                    raise McpProtocolError("MCP %s 错误: %s"
                                           % (method,
                                              err.get("message") or err))
                return data.get("result")

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = (result or {}).get("tools")
        if not isinstance(tools, list):
            raise McpProtocolError("tools/list 返回结构非法")
        return [t for t in tools if isinstance(t, dict) and t.get("name")]

    async def call_tool(self, name: str,
                        arguments: Optional[Dict[str, Any]] = None
                        ) -> Dict[str, Any]:
        """返回归一化结果 {ok, text, content, is_error}。"""
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if not isinstance(result, dict):
            raise McpProtocolError("tools/call 返回结构非法")
        content = result.get("content")
        if content is None:
            content = []
        if not isinstance(content, list):
            content = [content]
        texts = [
            str(c.get("text", ""))
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return {
            "ok": not bool(result.get("isError")),
            "is_error": bool(result.get("isError")),
            "text": "\n".join(t for t in texts if t),
            "content": content,
            "raw": result,
        }
