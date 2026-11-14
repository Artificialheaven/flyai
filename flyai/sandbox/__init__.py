"""AI 文件沙箱包：双根目录 + 路径校验 + 配额。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import Sandbox, SandboxError

__all__ = ["Sandbox", "SandboxError", "build_user_sandbox"]


def build_user_sandbox(
    data_dir: Path,
    uid: str,
    session_id: str,
    settings: Any,
    public_writable: bool = False,
) -> Sandbox:
    """按用户/会话构建沙箱。

    会话根：data/{uid}/workspace/sessions/{sid}/
    公共根：data/{uid}/workspace/public/
    settings 为 config.SandboxSettings（鸭子类型，测试可传简单对象）。
    """
    base = Path(data_dir) / uid / "workspace"
    return Sandbox(
        session_root=base / "sessions" / session_id,
        public_root=base / "public",
        max_file_kb=getattr(settings, "max_file_kb", 512),
        quota_mb=getattr(settings, "quota_mb", 10),
        public_writable=public_writable,
        public_max_file_kb=getattr(settings, "public_max_file_kb", 4096),
        public_quota_mb=getattr(settings, "public_quota_mb", 50),
    )
