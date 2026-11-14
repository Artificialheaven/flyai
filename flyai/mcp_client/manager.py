"""MCP 连接管理器（P4）：admin 登记的 server → 懒连接 stdio 客户端。

职责边界（技术书 §8.1）：
- server 配置与工具 schema 缓存存 admin 的 agent.db（McpServerStore）；
- 进程内只持有存活的 StdioMcpClient，连接懒建立，遇 EOF/超时重连一次；
- 失败置 status=failed 但不清 tools_json，UI 标注缓存可能过期；
- 图片/音频结果落盘到调用者工作区 workspace/mcp/，启动时按 6h TTL 清扫；
- 桥接工具按请求 extras 注入（见 tools/mcp_tool.py），不进全局注册表。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agentdb.mcp_store import (
    STATUS_FAILED,
    STATUS_READY,
    McpServerStore,
)
from ..tools.custom_tool_runtime import _clean_env
from ..user import ADMIN_UID
from .stdio import McpError, StdioMcpClient, sanitize_tool_name

logger = logging.getLogger("flyai.mcp")

MEDIA_TTL_SECONDS = 6 * 3600
# 单条媒体 base64 上限 20 MiB，防止恶意 server 灌爆磁盘
_MEDIA_MAX_B64 = 20 * 1024 * 1024


class McpManager:
    def __init__(self, data_dir, settings: Any, uid: str = ADMIN_UID,
                 store: Optional[McpServerStore] = None) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.uid = uid
        self.store = store or McpServerStore(data_dir)
        self._clients: Dict[str, StdioMcpClient] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    # ---------- 锁与客户端构造（全部惰性，避免绑定启动期事件循环） ----------

    def _server_lock(self, server_id: str) -> asyncio.Lock:
        lock = self._locks.get(server_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[server_id] = lock
        return lock

    def _timeout(self) -> float:
        return float(getattr(self.settings, "timeout", 30.0) or 30.0)

    def _make_env(self, row: Dict[str, Any]) -> Dict[str, str]:
        # 基底是剥掉密钥的清洗环境，admin 显式配置的 env 再叠加
        env = _clean_env()
        for key, val in (row.get("env") or {}).items():
            env[str(key)] = str(val)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _build_client(self, row: Dict[str, Any]) -> StdioMcpClient:
        return StdioMcpClient(
            command=row["command"],
            args=row.get("args") or [],
            env=self._make_env(row),
            timeout=self._timeout(),
        )

    # ---------- 连接测试（设置页调用） ----------

    async def test_connection(self, server_id: str) -> Dict[str, Any]:
        row = self.store.get(self.uid, server_id)
        if row is None:
            return {"ok": False, "error": "server 不存在"}
        async with self._server_lock(server_id):
            old = self._clients.pop(server_id, None)
            if old is not None:
                with contextlib.suppress(Exception):
                    await old.close()
            client = self._build_client(row)
            try:
                await client.start()
                tools = await client.list_tools()
            except McpError as exc:
                self.store.update_connection(
                    self.uid, server_id, STATUS_FAILED,
                    tools=None, last_error=str(exc),
                )
                with contextlib.suppress(Exception):
                    await client.close()
                return {"ok": False, "error": str(exc)}
            except Exception as exc:  # 任何意外都收敛为 failed，不炸设置页
                self.store.update_connection(
                    self.uid, server_id, STATUS_FAILED,
                    tools=None,
                    last_error="%s: %s" % (type(exc).__name__, exc),
                )
                with contextlib.suppress(Exception):
                    await client.close()
                return {"ok": False,
                        "error": "%s: %s" % (type(exc).__name__, exc)}
            self.store.update_connection(
                self.uid, server_id, STATUS_READY, tools=tools, last_error=""
            )
            self._clients[server_id] = client
            return {"ok": True, "tools": tools}

    # ---------- 运行期调用 ----------

    async def _ensure_client(self, row: Dict[str, Any]) -> StdioMcpClient:
        client = self._clients.get(row["server_id"])
        if client is not None and client.connected:
            return client
        client = self._build_client(row)
        await client.start()
        self._clients[row["server_id"]] = client
        return client

    async def call(self, uid: str, server_id: str, tool_name: str,
                   arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """单次工具调用；EOF/超时等断连场景重连一次后重试，仍失败才报错。"""
        row = self.store.get(self.uid, server_id)
        if row is None or not row.get("enabled"):
            return {"ok": False, "error": "MCP server 不存在或已停用: %s"
                                          % server_id}
        async with self._server_lock(server_id):
            try:
                client = await self._ensure_client(row)
                result = await client.call_tool(tool_name, arguments)
            except McpError as exc:
                # EOF/超时/协议错误：重连一次后重试，仍失败才返回错误
                result = await self._retry_once(
                    row, tool_name, arguments, str(exc)
                )
                if result is None:
                    return {"ok": False, "error": "MCP 重连后调用失败"}
            if result.get("ok", True):
                # 调用成功：若此前是 failed/new，顺手翻回 ready（缓存不动）
                if row.get("status") != STATUS_READY:
                    self.store.update_connection(
                        self.uid, server_id, STATUS_READY,
                        tools=None, last_error="",
                    )
            media = self._save_media(uid, result.get("content") or [])
            result["media"] = media
            return result

    async def _retry_once(self, row: Dict[str, Any], tool_name: str,
                          arguments: Optional[Dict[str, Any]],
                          reason: str) -> Optional[Dict[str, Any]]:
        """断连后重连一次并重试；成功返回结果 dict，失败返回错误 dict。"""
        server_id = row["server_id"]
        logger.warning("MCP %s 连接失效（%s），重连一次后重试", server_id, reason)
        old = self._clients.pop(server_id, None)
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close()
        try:
            client = await self._ensure_client(row)
            result = await client.call_tool(tool_name, arguments)
        except Exception as exc:
            self.store.update_connection(
                self.uid, server_id, STATUS_FAILED,
                tools=None,
                last_error="%s: %s" % (type(exc).__name__, exc),
            )
            return {"ok": False, "error": "MCP 重连后仍失败: %s" % exc}
        if result.get("ok", True):
            self.store.update_connection(
                self.uid, server_id, STATUS_READY, tools=None, last_error=""
            )
        return result

    # ---------- 媒体落盘 ----------

    def _save_media(self, uid: str,
                    content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        root = self.data_dir / (uid or ADMIN_UID) / "workspace" / "mcp"
        stamp = int(time.time() * 1000)
        for idx, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind not in ("image", "audio"):
                continue
            data = block.get("data") or ""
            if not data or len(data) > _MEDIA_MAX_B64:
                continue
            try:
                raw = base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                continue
            media_type = str(block.get("mimeType") or "")
            ext = mimetypes.guess_extension(media_type.split(";")[0].strip()) \
                or (".png" if kind == "image" else ".bin")
            try:
                root.mkdir(parents=True, exist_ok=True)
                name = "%d_%d%s" % (stamp, idx, ext)
                (root / name).write_bytes(raw)
            except OSError as exc:
                logger.warning("MCP 媒体落盘失败: %s", exc)
                continue
            out.append({
                "type": kind,
                "name": name,
                "media_type": media_type,
                "path": str(root / name),
            })
        return out

    def sweep_media(self, ttl_seconds: int = MEDIA_TTL_SECONDS,
                    now: Optional[float] = None) -> int:
        """启动清扫：删除所有用户 workspace/mcp 下超 TTL 的文件，返回清理数。"""
        now = time.time() if now is None else now
        base = self.data_dir
        if not base.is_dir():
            return 0
        removed = 0
        for user_dir in base.iterdir():
            mcp_dir = user_dir / "workspace" / "mcp"
            if not mcp_dir.is_dir():
                continue
            for entry in mcp_dir.iterdir():
                try:
                    if entry.is_file() and now - entry.stat().st_mtime > ttl_seconds:
                        entry.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    # ---------- 请求级桥接工具 ----------

    def bridge_tools(self) -> List[Any]:
        """从 admin 库的工具缓存生成请求级 mcp__ 桥接工具（不触发连接）。"""
        if not getattr(self.settings, "enabled", False):
            return []
        from ..tools.mcp_tool import McpBridgeTool

        min_level = int(getattr(self.settings, "call_min_level", 0) or 0)
        tools: List[Any] = []
        for row in self.store.list_enabled(self.uid):
            cached = row.get("tools") or []
            if not cached:
                continue
            used: Dict[str, int] = {}
            for spec in cached:
                orig = str(spec.get("name") or "").strip()
                if not orig:
                    continue
                safe = sanitize_tool_name(orig)
                base_name = "mcp__%s__%s" % (row["server_id"], safe)
                count = used.get(base_name, 0)
                used[base_name] = count + 1
                bridge_name = base_name if count == 0 else \
                    "%s_%d" % (base_name, count + 1)
                tools.append(McpBridgeTool(
                    server_id=row["server_id"],
                    original_name=orig,
                    bridge_name=bridge_name,
                    spec=spec,
                    min_level=min_level,
                ))
        return tools

    async def aclose(self) -> None:
        for client in list(self._clients.values()):
            with contextlib.suppress(Exception):
                await client.close()
        self._clients.clear()
