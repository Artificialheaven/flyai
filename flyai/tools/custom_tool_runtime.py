"""AI 自定义工具运行时（P1 旗舰）：静态校验 → 隔离子进程 dry-run → 发布 → 调用。

信任模型：
- validate/publish/toggle/remove 是 risk 管理工具（注册表执行期 admin 门禁），
  自定义代码只有管理员能上线；
- 任何 AI 代码都只在短生子进程（agentdb/tool_worker.py，python -I）里 exec：
  语法/加载/运行期崩溃只影响那次子进程；超时强杀整棵进程树；主进程永不
  import AI 代码；
- 发布 = 子进程加载验证通过后才 UPSERT；失败则库里旧版原样保留（回滚）；
- 已上线工具默认同 uid 会话内全员可触发，owner_only 时收紧为仅管理员。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..agentdb import custom_tools as ctdal
from ..agentdb.custom_tools import SCOPE_USER, session_scope

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_WORKER = str(
    Path(__file__).resolve().parent.parent / "agentdb" / "tool_worker.py"
)

CONTRACT_HINT = (
    "工具代码契约：定义 def tool(args: dict, ctx: dict) -> str；"
    "args 是模型按 params_schema 传入的参数字典，"
    "ctx 提供 uid/session_id/run_id/workspace/now/dry_run；返回字符串。"
)

# 子进程环境剥离：密钥/令牌/配置路径一律不继承（python -I 已阻断 sys.path 注入）
_SECRET_MARKERS = ("AUTHORIZATION", "PASSWORD", "SECRET", "KEYSTORE")
_STRIP_EXACT = {"FLAI_CONFIG", "FLYAI_KEYSTORE_PASS"}


def _clean_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if k in _STRIP_EXACT:
            continue
        if ku.endswith("_API_KEY") or ku.endswith("_TOKEN"):
            continue
        if any(marker in ku for marker in _SECRET_MARKERS):
            continue
        env[k] = v
    return env


def validate_schema_text(text: str) -> Tuple[Optional[dict], str]:
    try:
        schema = json.loads(text)
    except (ValueError, TypeError):
        return None, "params_schema 不是合法 JSON 字符串"
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None, 'params_schema 必须是 {"type":"object", "properties":{...}}'
    if "properties" in schema and not isinstance(schema["properties"], dict):
        return None, "params_schema.properties 必须是对象"
    return schema, ""


class CustomToolRuntime:
    def __init__(self, data_dir: Path, settings: Any) -> None:
        self.data_dir = Path(data_dir)
        self.settings = settings

    # ── 子进程执行（同步阻塞，统一在 to_thread 内调用）────────────

    def run_worker(self, req: dict, timeout: float) -> dict:
        """短生子进程跑一次 validate/call。任何崩溃都转成 {ok:False,error}。"""
        with tempfile.TemporaryDirectory(prefix="fltool_") as tmp:
            result_path = os.path.join(tmp, "result.json")
            argv = [sys.executable, "-I", "-X", "utf8", _WORKER, result_path]
            popen_kw: Dict[str, Any] = dict(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_clean_env(),
            )
            if os.name == "nt":
                popen_kw["creationflags"] = _NO_WINDOW
            else:
                # 独立进程组，超时时 killpg 整组（修正 Tide 只 kill 父进程的问题）
                popen_kw["start_new_session"] = True
            proc = subprocess.Popen(argv, **popen_kw)
            try:
                out, err = proc.communicate(
                    json.dumps(req, ensure_ascii=False).encode("utf-8"),
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                self._kill_tree(proc)
                return {
                    "ok": False,
                    "stage": "timeout",
                    "error": "执行超过 %gs，已强杀进程树" % timeout,
                }
            if not os.path.exists(result_path):
                tail = (err or out or b"").decode("utf-8", "replace")[-1200:]
                return {
                    "ok": False,
                    "stage": "crash",
                    "error": "工具进程异常退出（code=%s）：\n%s"
                    % (proc.returncode, tail),
                }
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                return {"ok": False, "stage": "crash",
                        "error": "结果读取失败: %s" % e}

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, timeout=10,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass

    # ── 静态校验（不执行代码）────────────────────────────────────

    @staticmethod
    def static_check(name: str, description: str, schema_text: str,
                     code: str, code_max: int) -> str:
        if not _NAME_RE.match(name or ""):
            return "name 必须匹配 ^[a-z][a-z0-9_]{0,31}$（小写字母开头）"
        if not (description or "").strip():
            return "description 必填（告诉模型什么时候调用）"
        if len(description) > 500:
            return "description 最多 500 字"
        _, err = validate_schema_text(schema_text)
        if err:
            return err
        if not (code or "").strip():
            return "code 为空"
        if len(code) > code_max:
            return "代码超过 %d 字符上限" % code_max
        try:
            compile(code, "<custom_tool>", "exec")
        except SyntaxError as e:
            return "语法错误（未上线）：%s（第 %s 行）" % (e.msg, e.lineno)
        return ""

    # ── 上下文 ──────────────────────────────────────────────────

    def _call_ctx(self, uid: str, sid: str, run_id: str = "",
                  dry_run: bool = False) -> dict:
        return {
            "uid": uid,
            "session_id": sid,
            "run_id": run_id,
            "workspace": str(self.data_dir / uid / "workspace"),
            "now": int(time.time()),
            "dry_run": dry_run,
        }

    # ── AI 管理操作（返回给模型的文本）──────────────────────────

    async def validate(self, uid: str, sid: str, args: dict) -> str:
        name = str(args.get("name", "") or "").strip()
        desc = str(args.get("description", "") or "").strip()
        schema_text = str(args.get("params_schema", "") or "")
        code = str(args.get("code", "") or "")
        err = self.static_check(
            name, desc, schema_text, code, self.settings.code_max_chars
        )
        if err:
            return "❌ 校验失败：" + err

        test_args = None
        if args.get("test_args") is not None:
            try:
                test_args = json.loads(str(args["test_args"]))
                if not isinstance(test_args, dict):
                    return "❌ 校验失败：test_args 必须是 JSON 对象"
            except ValueError:
                return "❌ 校验失败：test_args 不是合法 JSON"

        req = {
            "code": code,
            "mode": "validate",
            "test_args": test_args,
            "ctx": self._call_ctx(uid, sid, dry_run=True),
            "lib_dirs": [],
        }
        res = await asyncio.to_thread(
            self.run_worker, req, float(self.settings.validate_timeout)
        )
        if not res.get("ok"):
            return ("❌ dry-run 失败，未上线（框架与现有工具不受影响）：\n"
                    + str(res.get("error", "未知错误"))[-1500:])
        lines = ["✅ dry-run 通过：语法/加载正常，tool(args, ctx) 契约成立。"]
        if res.get("load_print"):
            lines.append("加载期输出（建议删掉，加载应无副作用）："
                         + res["load_print"][:300])
        if test_args is not None:
            lines.append("样例调用返回：" + str(res.get("test", ""))[:800])
            if res.get("test_print"):
                lines.append("样例 stdout：" + res["test_print"][:400])
        lines.append("确认无误后调用 custom_tool_publish 正式上线。")
        return "\n".join(lines)

    async def publish(self, uid: str, sid: str, args: dict) -> str:
        name = str(args.get("name", "") or "").strip()
        desc = str(args.get("description", "") or "").strip()
        schema_text = str(args.get("params_schema", "") or "")
        code = str(args.get("code", "") or "")
        # 发布前再做一次仅加载验证（不带 test_args），保证坏代码不进库
        check = self.static_check(
            name, desc, schema_text, code, self.settings.code_max_chars
        )
        if check:
            return "❌ 发布失败：" + check
        req = {
            "code": code,
            "mode": "validate",
            "test_args": None,
            "ctx": self._call_ctx(uid, sid, dry_run=True),
            "lib_dirs": [],
        }
        res = await asyncio.to_thread(
            self.run_worker, req, float(self.settings.validate_timeout)
        )
        if not res.get("ok"):
            return ("❌ 加载验证失败，未上线（旧版原样保留）：\n"
                    + str(res.get("error", "未知错误"))[-1500:])

        scope = SCOPE_USER if str(args.get("scope", "user")) == "user" \
            else session_scope(sid or "")
        if scope != SCOPE_USER and not sid:
            return "❌ 发布失败：会话级工具需要有效 session_id"

        def _db_op():
            conn = ctdal.open_for(self.data_dir, uid)
            try:
                existing = any(
                    r["scope"] == scope and r["name"] == name
                    for r in ctdal.list_all(conn)
                )
                cap = int(self.settings.max_per_scope)
                if not existing and ctdal.count_for_scope(conn, scope) >= cap:
                    return None, "该作用域工具数已达上限 %d，先 custom_tool_remove 几个" % cap
                tool_id = ctdal.upsert_tool(
                    conn,
                    scope=scope,
                    name=name,
                    title=str(args.get("title", "") or "")[:20],
                    description=desc[:500],
                    params_schema=schema_text,
                    code=code,
                    owner_only=bool(args.get("owner_only")),
                    created_by=uid,
                )
                return tool_id, ""
            finally:
                conn.close()

        tool_id, db_err = await asyncio.to_thread(_db_op)
        if db_err:
            return "❌ 发布失败：" + db_err
        where = "用户级（全部会话）" if scope == SCOPE_USER else "仅本会话"
        owner = "仅管理员可触发；" if args.get("owner_only") else ""
        return ("✅ 工具 custom__%s（id=%s）已上线，%s生效。%s"
                "更新代码用同名重新发布（验证失败会自动保留旧版）。"
                % (name, tool_id, where, owner))

    async def list_text(self, uid: str, sid: str) -> str:
        def _load():
            conn = ctdal.open_for(self.data_dir, uid)
            try:
                return ctdal.list_all(conn)
            finally:
                conn.close()
        rows = await asyncio.to_thread(_load)
        if not rows:
            return ("当前没有自定义工具。先用 custom_tool_validate 试写并 dry-run，"
                    "再用 custom_tool_publish 上线。" + CONTRACT_HINT)
        lines = ["自定义工具（id/作用域/状态）："]
        for r in rows:
            scope = "用户级" if r["scope"] == SCOPE_USER else "会话级"
            state = "启用" if r["enabled"] else "停用"
            owner = "，仅管理员触发" if r["owner_only"] else ""
            title = r["title"] or r["description"][:30]
            lines.append("[%s] custom__%s（%s/%s%s）%s"
                         % (r["id"], r["name"], scope, state, owner, title))
        return "\n".join(lines)

    async def toggle(self, uid: str, args: dict) -> str:
        try:
            tool_id = int(args.get("id"))
        except (TypeError, ValueError):
            return "❌ id 必须是整数（custom_tool_list 里方括号编号）"
        enabled = bool(args.get("enabled"))

        def _op():
            conn = ctdal.open_for(self.data_dir, uid)
            try:
                row = ctdal.get_by_id(conn, tool_id)
                if row is None:
                    return None
                ctdal.set_enabled(conn, tool_id, enabled)
                return row
            finally:
                conn.close()
        row = await asyncio.to_thread(_op)
        if row is None:
            return "❌ 未找到该自定义工具"
        return "已%s custom__%s" % ("启用" if enabled else "停用", row["name"])

    async def remove(self, uid: str, args: dict) -> str:
        try:
            tool_id = int(args.get("id"))
        except (TypeError, ValueError):
            return "❌ id 必须是整数（custom_tool_list 里方括号编号）"

        def _op():
            conn = ctdal.open_for(self.data_dir, uid)
            try:
                row = ctdal.get_by_id(conn, tool_id)
                if row is None:
                    return None
                ctdal.delete_by_id(conn, tool_id)
                return row
            finally:
                conn.close()
        row = await asyncio.to_thread(_op)
        if row is None:
            return "❌ 未找到该自定义工具"
        return "已删除 custom__%s" % row["name"]

    # ── 模型调用已上线工具 ───────────────────────────────────────

    def call_blocking(self, row: dict, args: dict, call_ctx: dict,
                      timeout: float) -> dict:
        """同步（CustomFunctionTool 用 to_thread 包裹）。异常全部文本化。"""
        try:
            req = {
                "code": row["code"],
                "mode": "call",
                "args": args or {},
                "ctx": call_ctx,
                "lib_dirs": [],
            }
            res = self.run_worker(req, timeout)
        except Exception as e:  # 执行器自身异常也不允许抛进 agent loop
            return {"ok": False, "error": "自定义工具执行器异常: %s" % e}
        if not res.get("ok"):
            return {"ok": False,
                    "error": "自定义工具执行失败（不影响其他工具）：\n"
                    + str(res.get("error", "未知错误"))[-1000:]}
        result = res.get("result") or ""
        printed = res.get("print") or ""
        if not result and printed:
            return {"ok": True, "result": printed}
        if printed:
            return {"ok": True,
                    "result": str(result) + "\n（工具 stdout）\n" + printed[:1000]}
        return {"ok": True, "result": str(result) or "（工具无返回值）"}

    def tools_for(self, uid: str, sid: str) -> List[Any]:
        """请求级工具视图：只含该 uid 自己 agent.db 里对当前会话启用的行。"""
        from .custom_tool_manage import CustomFunctionTool

        if not getattr(self.settings, "enabled", False):
            return []
        conn = ctdal.open_for(self.data_dir, uid)
        try:
            rows = ctdal.list_enabled_for_session(conn, sid or "")
        finally:
            conn.close()
        return [CustomFunctionTool(r, self) for r in rows]
