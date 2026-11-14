"""P0 fs_* 工具：沙箱读写闭环、risk 授权、public 跨会话共享、服务缺失兜底。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flyai.config import SandboxSettings
from flyai.schemas import ToolCall
from flyai.tools.base import ToolContext
from flyai.tools.fs_tools import (
    FsAppendTool,
    FsDeleteTool,
    FsListTool,
    FsReadTool,
    FsWriteTool,
)
from flyai.tools.registry import ToolRegistry


def _run(coro):
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _ctx(tmp_path, uid="u1", level=0, is_admin=False, sid="s1"):
    return ToolContext(
        uid=uid,
        level=level,
        is_admin=is_admin,
        session_id=sid,
        services={
            "data_dir": tmp_path,
            "sandbox_settings": SandboxSettings(
                max_file_kb=64, quota_mb=1,
                public_max_file_kb=64, public_quota_mb=1,
            ),
        },
    )


def _registry():
    reg = ToolRegistry()
    for t in (FsListTool(), FsReadTool(), FsWriteTool(),
              FsAppendTool(), FsDeleteTool()):
        reg.register(t)
    return reg


def _call(name, **arguments):
    return ToolCall(id="1", name=name, arguments=arguments)


def test_admin_full_lifecycle(tmp_path):
    reg = _registry()
    ctx = _ctx(tmp_path, is_admin=True)
    r = _run(reg.execute(_call("fs_write", path="notes/a.txt", content="hello"), ctx))
    assert r.ok, r.output
    r = _run(reg.execute(_call("fs_append", path="notes/a.txt", content=" world"), ctx))
    assert r.ok
    r = _run(reg.execute(_call("fs_read", path="notes/a.txt"), ctx))
    assert r.ok and r.output == "hello world"
    r = _run(reg.execute(_call("fs_list", path="notes"), ctx))
    assert r.ok and "a.txt" in r.output
    r = _run(reg.execute(_call("fs_delete", path="notes"), ctx))
    assert r.ok
    r = _run(reg.execute(_call("fs_list", path="notes"), ctx))
    assert r.ok and "为空" in r.output


def test_normal_user_write_denied_but_list_allowed(tmp_path):
    reg = _registry()
    ctx = _ctx(tmp_path, level=5)
    r = _run(reg.execute(_call("fs_write", path="a.txt", content="x"), ctx))
    assert not r.ok and "权限不足" in r.output
    # 非 risk 的读类工具不受限
    r = _run(reg.execute(_call("fs_list"), ctx))
    assert r.ok


def test_public_shared_across_sessions_admin_only(tmp_path):
    reg = _registry()
    admin = _ctx(tmp_path, uid="u1", is_admin=True, sid="s1")
    r = _run(reg.execute(
        _call("fs_write", path="public/shared.txt", content="pub"), admin
    ))
    assert r.ok, r.output
    # 另一个会话能读到同一份 public 文件
    other = _ctx(tmp_path, uid="u1", is_admin=True, sid="s2")
    r = _run(reg.execute(_call("fs_read", path="public/shared.txt"), other))
    assert r.ok and r.output == "pub"
    # 普通用户对 public 的写在注册表层即被拦
    user = _ctx(tmp_path, uid="u1", level=10, sid="s2")
    r = _run(reg.execute(
        _call("fs_write", path="public/x.txt", content="nope"), user
    ))
    assert not r.ok


def test_missing_services_returns_failure(tmp_path):
    reg = _registry()
    ctx = ToolContext(uid="u1", is_admin=True, session_id="s1")
    r = _run(reg.execute(_call("fs_list"), ctx))
    assert not r.ok and "沙箱" in r.output
