"""会话工作区文件沙箱（同步核心，纯逻辑可单测），移植自 Tide 设计。

- 会话根 data/{uid}/workspace/sessions/{sid}/，公共根
  data/{uid}/workspace/public/（跨会话共享素材，独立配额）
- 禁绝对路径、盘符、``..``；resolve 后做双根校验（含 symlink realpath）
- 会话目录与 public 各自独立的单文件大小 / 总配额
调用方（fs 工具）用 asyncio.to_thread 包裹，避免阻塞事件循环。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, List, Tuple

_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")


class SandboxError(PermissionError):
    pass


def _parts(rel: str) -> List[str]:
    rel = (rel or ".").strip().replace("\\", "/")
    if rel.startswith("/") or _DRIVE_RE.match(rel):
        raise SandboxError("禁止使用绝对路径")
    parts: List[str] = []
    for p in rel.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise SandboxError("禁止使用 .. 越界")
        parts.append(p)
    return parts


class Sandbox:
    def __init__(
        self,
        session_root: Path,
        public_root: Path,
        max_file_kb: int = 512,
        quota_mb: int = 10,
        public_writable: bool = False,
        public_max_file_kb: int = 4096,
        public_quota_mb: int = 50,
    ) -> None:
        self.session_root = Path(session_root).resolve()
        self.public_root = Path(public_root).resolve()
        self.max_file_bytes = int(max_file_kb) * 1024
        self.quota_bytes = int(quota_mb) * 1024 * 1024
        # public 默认只读；工厂按调用者是否管理员决定是否开放写
        self.public_writable = bool(public_writable)
        self.public_max_file_bytes = int(public_max_file_kb) * 1024
        self.public_quota_bytes = int(public_quota_mb) * 1024 * 1024

    # ── 路径校验 ────────────────────────────────────────────────

    def resolve(self, rel: str, for_write: bool = False) -> Tuple[Path, bool]:
        """返回 (绝对路径, 是否 public 共享区)。越界拒绝；public 只读时拒写。"""
        parts = _parts(rel)
        is_public = bool(parts and parts[0] == "public")
        if is_public:
            base, sub = self.public_root, parts[1:]
        else:
            base, sub = self.session_root, parts
        target = base.joinpath(*sub) if sub else base
        real = target.resolve()
        if base != real and base not in real.parents:
            raise SandboxError("路径越界")
        # symlink 二次校验：realpath 必须仍在允许的两个根之内
        if (
            real != target
            and not self._under(real, self.session_root)
            and not self._under(real, self.public_root)
        ):
            raise SandboxError("链接路径越界")
        if for_write and is_public and not self.public_writable:
            raise SandboxError("public 公共素材库当前为只读，禁止写入")
        return real, is_public

    @staticmethod
    def _under(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    # ── 配额（会话目录 / public 各自独立记账）──────────────────

    @staticmethod
    def _dir_used(root: Path) -> int:
        total = 0
        if not root.exists():
            return 0
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def _check_write(
        self, target: Path, content: bytes, overwrite: bool, is_public: bool
    ) -> None:
        if is_public:
            cap, quota, used_root = (
                self.public_max_file_bytes,
                self.public_quota_bytes,
                self.public_root,
            )
        else:
            cap, quota, used_root = (
                self.max_file_bytes,
                self.quota_bytes,
                self.session_root,
            )
        if len(content) > cap:
            kind = "public 素材" if is_public else "文件"
            raise SandboxError(f"{kind}单文件不得超过 {cap // 1024}KiB")
        old = target.stat().st_size if overwrite and target.exists() else 0
        if self._dir_used(used_root) - old + len(content) > quota:
            area = "public 公共库" if is_public else "会话工作区"
            raise SandboxError(f"{area}总配额 {quota // 1024 // 1024}MiB 已满")

    # ── 操作 ────────────────────────────────────────────────────

    def list_dir(self, rel: str = ".") -> List[Dict[str, object]]:
        target, _ = self.resolve(rel)
        if not target.exists():
            return []
        if not target.is_dir():
            raise SandboxError("不是目录")
        items: List[Dict[str, object]] = []
        for p in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name))[:200]:
            items.append(
                {
                    "name": p.name + ("/" if p.is_dir() else ""),
                    "type": "dir" if p.is_dir() else "file",
                    "size": p.stat().st_size if p.is_file() else 0,
                }
            )
        return items

    def read_file(self, rel: str) -> str:
        target, is_public = self.resolve(rel)
        if target.is_dir():
            raise SandboxError("目标是目录")
        data = target.read_bytes()
        cap = self.public_max_file_bytes if is_public else self.max_file_bytes
        if len(data) > cap:
            raise SandboxError("文件过大")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data[:64].hex() + "…(二进制文件)"

    def write_file(self, rel: str, content: str, append: bool = False) -> int:
        target, is_public = self.resolve(rel, for_write=True)
        data = (content or "").encode("utf-8")
        if append and target.exists():
            self._check_write(
                target, target.read_bytes() + data, True, is_public
            )
            with target.open("ab") as f:
                f.write(data)
        else:
            self._check_write(target, data, target.exists(), is_public)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return target.stat().st_size

    def delete(self, rel: str) -> bool:
        target, _ = self.resolve(rel, for_write=True)
        # 禁止删除两个根目录本身（一条 delete('.') 清空整个工作区的兜底）
        if target in (self.session_root, self.public_root):
            raise SandboxError("禁止删除工作区根目录")
        if not target.exists():
            return False
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True
