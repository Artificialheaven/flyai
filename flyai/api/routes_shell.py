"""管理员终端：在 Web 端打开一个交互式 shell。

POC 下服务本身运行在 PRoot 用户态里，因此这个 shell 天然就是「进入 proot」；
服务器部署下则是服务进程所在主机的 shell。仅管理员可用：WS 建连后第一条
消息必须是通过校验的登录令牌，避免把令牌放进 URL（会被访问日志记录）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..user import ADMIN_UID

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shell"])

READ_SIZE = 65536
AUTH_TIMEOUT = 10.0


def _shell_command() -> List[str]:
    """POC（Linux）优先 bash；Windows 开发机回落到 cmd.exe。"""
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    for candidate in ("/bin/bash", "/bin/sh"):
        if os.path.exists(candidate):
            return [candidate, "-i"]
    return ["/bin/sh"]


def _child_env() -> dict:
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _working_dir() -> Optional[str]:
    # POC 里项目根固定在 /opt/flyai；其它环境沿用服务进程 cwd
    return "/opt/flyai" if os.path.isdir("/opt/flyai") else None


def _state_of(ws: WebSocket) -> Any:
    return getattr(ws.app.state, "flyai", None)


async def _authenticate(ws: WebSocket, state: Any) -> bool:
    """等待首条 auth 消息并校验管理员令牌。"""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return False
    try:
        msg = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(msg, dict) or msg.get("type") != "auth":
        return False
    token = str(msg.get("token") or "")
    sessions = getattr(state, "auth_sessions", None)
    rec = sessions.resolve(token) if sessions is not None else None
    if not rec or str(rec.get("uid")) != ADMIN_UID:
        return False
    return True


@router.websocket("/api/shell")
async def shell_socket(ws: WebSocket):
    await ws.accept()
    state = _state_of(ws)
    if not await _authenticate(ws, state):
        await ws.close(code=4401)
        return
    await ws.send_text(json.dumps({"type": "ready"}))
    try:
        if os.name == "posix":
            await _serve_posix(ws)
        else:
            await _serve_pipes(ws)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - 终端异常不应影响其它连接
        logger.warning("终端会话异常: %s: %s", type(exc).__name__, exc)
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# ---- 输入协议 ----
# 客户端发 JSON 文本帧：{"type":"input","data":"ls\n"} / {"type":"resize","cols":..,"rows":..}
# 服务端发二进制帧（原始终端字节流）；ready 等控制帧为 JSON 文本。

def _parse_message(message: str) -> dict:
    try:
        payload = json.loads(message)
    except ValueError:
        return {"type": "input", "data": message}
    return payload if isinstance(payload, dict) else {"type": "input", "data": message}


def _encode_input(data: str) -> bytes:
    if os.name == "nt":
        # cmd.exe 经管道读取时按 \r\n 断行
        data = data.replace("\r\n", "\n").replace("\n", "\r\n")
    return data.encode("utf-8", "ignore")


def _preexec_controlling_tty(slave_fd: int) -> None:
    """子进程独立会话并接管 pty 作为控制终端（bash 的作业控制/信号依赖它）。"""
    import fcntl
    import termios

    os.setsid()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
    except OSError:
        pass


async def _settle_tasks(tasks: List["asyncio.Task"]) -> None:
    """取消未结束的泵任务并取走全部异常，避免 “exception was never retrieved”。"""
    for task in tasks:
        if not task.done():
            task.cancel()
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, BaseException) and not isinstance(
            result, (asyncio.CancelledError, WebSocketDisconnect)
        ):
            logger.info("终端任务结束: %s: %s", type(result).__name__, result)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
    except (OSError, ProcessLookupError):
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (OSError, ProcessLookupError):
            pass
    # 管道传输在事件循环关闭前显式释放（Windows Proactor 否则会报未关闭警告）
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, RuntimeError):
                pass


async def _serve_posix(ws: WebSocket) -> None:
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    try:
        proc = await asyncio.create_subprocess_exec(
            *_shell_command(),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=_working_dir(),
            env=_child_env(),
            preexec_fn=lambda: _preexec_controlling_tty(slave),
        )
    except Exception:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    loop = asyncio.get_running_loop()
    # 有界队列：客户端读得慢时丢弃部分输出，避免服务端内存无上限增长
    queue = asyncio.Queue(maxsize=256)  # type: asyncio.Queue

    def on_readable() -> None:
        try:
            data = os.read(master, READ_SIZE)
        except OSError:
            data = b""
        if data:
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                pass
        else:
            # 结束哨兵不能被丢弃，否则输出泵永远等不到 EOF
            loop.create_task(queue.put(None))

    loop.add_reader(master, on_readable)
    tasks = [
        asyncio.create_task(_pump_output(ws, queue)),
        asyncio.create_task(_pump_input_posix(ws, master, fcntl, struct, termios)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _settle_tasks(tasks)
        loop.remove_reader(master)
        try:
            os.close(master)
        except OSError:
            pass
        await _terminate(proc)


async def _pump_output(ws: WebSocket, queue: "asyncio.Queue") -> None:
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        await ws.send_bytes(chunk)


async def _pump_input_posix(
    ws: WebSocket, master: int, fcntl: Any, struct: Any, termios: Any
) -> None:
    while True:
        payload = _parse_message(await ws.receive_text())
        kind = payload.get("type") or "input"
        if kind == "input":
            data = str(payload.get("data") or "")
            if data:
                view = memoryview(data.encode("utf-8", "ignore"))
                while view:
                    written = os.write(master, view)
                    view = view[written:]
        elif kind == "resize":
            try:
                cols = max(20, min(int(payload.get("cols") or 80), 400))
                rows = max(5, min(int(payload.get("rows") or 24), 200))
            except (TypeError, ValueError):
                continue
            try:
                fcntl.ioctl(
                    master,
                    termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0),
                )
            except OSError:
                pass


async def _serve_pipes(ws: WebSocket) -> None:
    """非 POSIX 开发环境：无 pty，用管道跑一个交互 shell（功能受限）。"""
    proc = await asyncio.create_subprocess_exec(
        *_shell_command(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=_working_dir(),
        env=_child_env(),
    )
    tasks = [
        asyncio.create_task(_pump_pipe_output(ws, proc)),
        asyncio.create_task(_pump_pipe_input(ws, proc)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await _settle_tasks(tasks)
        await _terminate(proc)


async def _pump_pipe_output(ws: WebSocket, proc: asyncio.subprocess.Process) -> None:
    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            return
        await ws.send_bytes(chunk)


async def _pump_pipe_input(ws: WebSocket, proc: asyncio.subprocess.Process) -> None:
    assert proc.stdin is not None
    while True:
        payload = _parse_message(await ws.receive_text())
        if (payload.get("type") or "input") != "input":
            continue  # 无 pty 时忽略 resize
        data = str(payload.get("data") or "")
        if not data:
            continue
        proc.stdin.write(_encode_input(data))
        await proc.stdin.drain()
