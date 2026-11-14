"""调度器领导锁：多进程/多 worker 部署时只允许一个实例执行后台 tick。

Windows 用 msvcrt 对锁文件加非阻塞字节锁，POSIX 用 fcntl.flock。
锁由 OS 句柄持有，进程崩溃后自动回收，不会留下死锁。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class LeadLock:
    _fh: Optional[object] = None
    _path: Optional[Path] = None

    @classmethod
    def try_acquire(cls, path: Path) -> bool:
        """尝试拿锁；已持有返回 True，被别的进程占用返回 False。"""
        if cls._fh is not None:
            return True
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                # 锁文件首字节；LK_NBLCK 非阻塞，拿不到立即抛 OSError
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        cls._fh = fh
        cls._path = path
        return True

    @classmethod
    def release(cls) -> None:
        fh = cls._fh
        if fh is None:
            return
        cls._fh = None
        cls._path = None
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            fh.close()

    @classmethod
    def held(cls) -> bool:
        return cls._fh is not None
