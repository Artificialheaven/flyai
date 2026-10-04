"""外部传入 id / 文件名的统一安全收口（防路径穿越）。

存储层一律走 safe_child：name 只允许作为 base 目录下的单层文件名，
拒绝目录分隔符、盘符、父目录引用与控制字符；resolve 后必须仍在 base 内
（防 base 目录内的符号链接把路径带出去）。
路由层可先调 check_safe_name，把非法 id 变成 HTTP 400，
而不是等存储层抛 InvalidName 变成 500。
"""

from __future__ import annotations

import re
from pathlib import Path

_MAX_NAME = 100

# 模型（LLM provider / 生图模型）ID 字符集：模型 id 不进文件系统，但会渲染进
# 前端 HTML，历史上的注入点就在这里。仅字母数字开头，允许点/下划线/短横线。
# 注意：旧配置里存在含 "/" 的遗留 id，加载不校验（本规则只用于新建/改名入口）。
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")            # id（32 hex）+ .json 足够；历史长名也远不到


class InvalidName(ValueError):
    """外部 id / 文件名非法（路由层应转成 HTTP 400）。"""


def check_safe_name(name: str, what: str = "id") -> str:
    if not isinstance(name, str) or not name:
        raise InvalidName("%s 为空" % what)
    if len(name) > _MAX_NAME:
        raise InvalidName("%s 过长" % what)
    # 任何目录分隔符 / Windows 盘符 / 父目录引用都拒（正斜杠在 Windows 也是分隔符）
    if "/" in name or "\\" in name or ":" in name or ".." in name or name == ".":
        raise InvalidName("%s 含非法路径字符" % what)
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise InvalidName("%s 含控制字符" % what)
    return name


def check_model_id(mid: str, what: str = "模型 ID") -> str:
    """校验模型 id 字符集；非法抛 InvalidName（路由层转 HTTP 400）。"""
    if not isinstance(mid, str) or not _MODEL_ID_RE.match(mid):
        raise InvalidName(
            "%s 只能以字母或数字开头，仅含字母/数字/点/下划线/短横线" % what
        )
    return mid


def safe_child(base: Path, name: str) -> Path:
    """返回 base/name；name 非法或 resolve 后逃出 base 即抛 InvalidName。"""
    check_safe_name(name)
    base_r = base.resolve()
    p = (base_r / name).resolve()
    if p != base_r and base_r not in p.parents:
        raise InvalidName("路径逃逸: %r" % name)
    return p
