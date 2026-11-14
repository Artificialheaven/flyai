"""自定义工具一次性子进程执行器。

本文件永不被主进程 import，由 CustomToolRuntime 以
``python -I -X utf8 tool_worker.py <result_path>`` 拉起：
- -I 隔离模式：忽略 PYTHONPATH、用户 site、cwd 入 sys.path，
  本文件只允许使用标准库，禁止 import 任何项目包；
- argv[1] = 结果文件路径，协议走临时文件，工具 print 不污染协议；
- stdin = 一个 JSON：{code, args, ctx, mode, test_args, lib_dirs}；
- 代码在独立 namespace 里 exec，进程崩溃/段错误只死这个子进程。

3.9 约束：本模块在 -I 子进程里同样运行于 3.9，禁用 PEP 604 求值注解。
"""

import io
import json
import sys
import traceback
from typing import Any, Dict


class _ContractError(Exception):
    pass


def _coerce(rv: Any) -> str:
    """工具返回值归一化：str 原样 / None→'' / dict·list→JSON / 其余 str()。"""
    if isinstance(rv, str):
        return rv
    if rv is None:
        return ""
    if isinstance(rv, (dict, list)):
        return json.dumps(rv, ensure_ascii=False)
    return str(rv)


def _write(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class _redirect:
    """同时重定向 stdout/stderr 的上下文管理器（本地最小实现）。"""

    def __init__(self, out: io.StringIO, err: io.StringIO) -> None:
        self._out, self._err = out, err
        self._old = (sys.stdout, sys.stderr)

    def __enter__(self) -> "_redirect":
        sys.stdout, sys.stderr = self._out, self._err
        return self

    def __exit__(self, *exc: Any) -> bool:
        sys.stdout, sys.stderr = self._old
        return False


def main() -> int:
    result_path = sys.argv[1]
    try:
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception as e:  # 协议错误也要落结果文件，主进程按文件缺失另判 crash
        _write(result_path, {"ok": False, "stage": "protocol",
                             "error": "请求解析失败: %s" % e})
        return 1

    # v1 不开放 lib_dirs；预留入口（-I 下只有显式 insert 的路径可信）
    for p in req.get("lib_dirs") or []:
        if p and p not in sys.path:
            sys.path.insert(0, p)

    mode = req.get("mode", "call")
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    try:
        ns: Dict[str, Any] = {}
        # exec 期的 print 也收进报告（上线代码不应在加载期产生副作用输出）
        with _redirect(stdout_buf, stderr_buf):
            exec(compile(req.get("code", ""), "<custom_tool>", "exec"), ns)
        info: Dict[str, Any] = {
            "ok": True,
            "loaded": True,
            "load_print": stdout_buf.getvalue()[-1000:],
        }
        fn = ns.get("tool")
        if not callable(fn):
            raise _ContractError(
                "代码必须定义可调用函数：def tool(args: dict, ctx: dict) -> str"
            )
        if mode == "validate":
            if req.get("test_args") is not None:
                call_out = io.StringIO()
                ctx = dict(req.get("ctx") or {})
                ctx["dry_run"] = True
                with _redirect(call_out, stderr_buf):
                    rv = fn(dict(req["test_args"]), ctx)
                info["test"] = _coerce(rv)
                info["test_print"] = call_out.getvalue()[-2000:]
            else:
                info["test"] = None
        else:
            call_out = io.StringIO()
            with _redirect(call_out, stderr_buf):
                rv = fn(dict(req.get("args") or {}),
                        dict(req.get("ctx") or {}))
            info["result"] = _coerce(rv)
            info["print"] = call_out.getvalue()[-2000:]
        _write(result_path, info)
        return 0
    except Exception:
        _write(result_path, {
            "ok": False,
            "stage": mode,
            "error": traceback.format_exc(limit=8),
            "print": stdout_buf.getvalue()[-1000:],
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
