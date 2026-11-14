"""监测进程管理器（P4）：detached 脚本进程的启动/探活/停止/reconcile。

信任模型（技术书 §8.3）：
- 脚本必须由 AI 先经沙箱写进会话工作区：.py、相对路径、禁 public/；
- 启动方式全平台 detached，主进程退出不带走 watcher，stdin 接 DEVNULL，
  stdout/stderr 重定向到会话工作区 .watchers/<ts>.log；
- 子进程环境只显式注入五个 TRIGGER_* 变量，其余走 _clean_env() 剥密钥；
- agent.db watchers 表是权威登记，重启 reconcile 只探活不自动重启；
- 每会话运行中上限由 config.watchers.max_per_session 约束。
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agentdb.watcher_store import (
    STATUS_RUNNING,
    STATUS_STOPPED,
    WatcherStore,
)
from ..tools.custom_tool_runtime import _clean_env

logger = logging.getLogger("flyai.watchers")

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_STILL_ACTIVE = 259

_TRIGGER_VARS = (
    "TRIGGER_URL",
    "TRIGGER_TOKEN",
    "TRIGGER_SESSION",
    "TRIGGER_SOURCE",
    "PYTHONIOENCODING",
)


class WatcherError(RuntimeError):
    """watcher 启动/停止的可展示错误。"""


def is_process_alive(pid: int) -> bool:
    """跨平台探活：Windows OpenProcess+退出码 259，POSIX kill(pid, 0)。"""
    pid = int(pid or 0)
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                handle, ctypes.byref(code)
            ):
                return False
            return int(code.value) == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        with contextlib.suppress(Exception):
            os.kill(pid, signal.SIGKILL)


class WatcherManager:
    def __init__(self, data_dir, settings: Any, ext_settings: Any,
                 user_store: Any, store: Optional[WatcherStore] = None) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.ext_settings = ext_settings
        self.user_store = user_store
        self.store = store or WatcherStore(data_dir)

    # ---------- 路径与校验 ----------

    def session_root(self, uid: str, session_id: str) -> Path:
        return self.data_dir / uid / "workspace" / "sessions" / session_id

    def _validate_script(self, uid: str, session_id: str,
                         script_path: str) -> Path:
        rel = (script_path or "").strip().replace("\\", "/")
        if not rel:
            raise WatcherError("script_path 为空")
        if not rel.endswith(".py"):
            raise WatcherError("监测脚本必须是 .py 文件")
        if rel.startswith("/") or ":\\" in script_path or "://" in rel:
            raise WatcherError("script_path 必须是会话工作区内的相对路径")
        parts = [p for p in rel.split("/") if p]
        if not parts or any(p in ("", "..") for p in parts):
            raise WatcherError("script_path 含非法路径段")
        if parts[0] == "public" or parts[0].startswith("."):
            # public 跨会话共享、点目录（含 .watchers）都不放脚本
            raise WatcherError("禁止使用 public/ 或点目录作为监测脚本")
        target = (self.session_root(uid, session_id) / Path(*parts)).resolve()
        root = self.session_root(uid, session_id).resolve()
        try:
            target.relative_to(root)
        except (OSError, ValueError):
            raise WatcherError("脚本路径越出会话工作区")
        if not target.is_file():
            raise WatcherError("脚本不存在，请先经沙箱写入: %s" % rel)
        return target

    def _trigger_env(self, session_id: str, source: str) -> Dict[str, str]:
        env = _clean_env()
        allowed = {
            "TRIGGER_URL": (self.ext_settings.base_url or "").rstrip("/")
                           + "/api/ext/trigger",
            "TRIGGER_TOKEN": self.ext_settings.token or "",
            "TRIGGER_SESSION": session_id,
            "TRIGGER_SOURCE": source,
            "PYTHONIOENCODING": "utf-8",
        }
        for key in _TRIGGER_VARS:
            env[key] = str(allowed[key])
        return env

    # ---------- 启动/停止（同步阻塞，工具层 to_thread 包裹） ----------

    def start(self, uid: str, session_id: str, script_path: str,
              source: str = "watcher", now: Optional[float] = None
              ) -> Dict[str, Any]:
        from .trigger import valid_source

        source = (source or "watcher").strip()[:64]
        if not valid_source(source):
            raise WatcherError(
                "source 只允许字母数字及 _.-，长度 1-64"
            )
        if not getattr(self.ext_settings, "enabled", False) \
                or not getattr(self.ext_settings, "token", ""):
            raise WatcherError("外部触发未启用或未配置 token，watcher 无法回调")
        target = self._validate_script(uid, session_id, script_path)

        cap = int(getattr(self.settings, "max_per_session", 5) or 5)
        if self.store.count_running(uid, session_id) >= cap:
            raise WatcherError(
                "该会话运行中的 watcher 已达上限 %d，先停止几个" % cap
            )

        log_dir = self.session_root(uid, session_id) / ".watchers"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / ("%d.log" % int((now or time.time()) * 1000))
        log_fh = open(log_path, "ab")
        popen_kw: Dict[str, Any] = dict(
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=self._trigger_env(session_id, source),
            cwd=str(self.session_root(uid, session_id)),
        )
        if os.name == "nt":
            popen_kw["creationflags"] = (
                _DETACHED_PROCESS
                | _CREATE_NEW_PROCESS_GROUP
                | _CREATE_NO_WINDOW
            )
        else:
            popen_kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", str(target)], **popen_kw
            )
        except OSError as exc:
            log_fh.close()
            raise WatcherError("启动失败: %s" % exc)
        finally:
            try:
                log_fh.close()
            except OSError:
                pass
        row = self.store.register(
            uid, session_id, script_path, proc.pid,
            log_path=str(log_path), source=source, now=now,
        )
        logger.info("watcher 已启动 uid=%s sid=%s pid=%s script=%s",
                    uid, session_id, proc.pid, script_path)
        return row

    def stop(self, uid: str, watcher_id: int,
             now: Optional[float] = None) -> Dict[str, Any]:
        row = self.store.get(uid, int(watcher_id))
        if row is None:
            raise WatcherError("watcher 不存在: %s" % watcher_id)
        if row["status"] == STATUS_RUNNING and is_process_alive(row["pid"]):
            _kill_tree(int(row["pid"]))
        self.store.mark_stopped(uid, int(watcher_id), now=now)
        return self.store.get(uid, int(watcher_id))

    def list(self, uid: str, session_id: Optional[str] = None,
             status: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self.store.list(uid, session_id=session_id, status=status)
        for row in rows:
            if row["status"] == STATUS_RUNNING:
                row["alive"] = is_process_alive(row["pid"])
            else:
                row["alive"] = False
        return rows

    # ---------- 重启 reconcile ----------

    def reconcile(self, uid: str, now: Optional[float] = None) -> int:
        """探活登记为 running 的 watcher，死透即置 stopped；不自动重启。"""
        changed = 0
        for row in self.store.list_running(uid):
            if not is_process_alive(int(row["pid"])):
                if self.store.mark_stopped(uid, int(row["id"]), now=now):
                    changed += 1
                    logger.warning(
                        "watcher 已死亡并标记 stopped uid=%s id=%s pid=%s",
                        uid, row["id"], row["pid"],
                    )
        return changed

    def reconcile_all(self, now: Optional[float] = None) -> int:
        """启动期：扫 admin + 全部用户的 agent.db。"""
        uids = [u.id for u in self.user_store.list()]
        from ..user import ADMIN_UID

        if ADMIN_UID not in uids:
            uids.append(ADMIN_UID)
        changed = 0
        for uid in uids:
            try:
                changed += self.reconcile(uid, now=now)
            except Exception:
                logger.exception("watcher reconcile 失败 uid=%s", uid)
        return changed
