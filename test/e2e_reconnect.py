"""E2E：断线重连三场景（真实 HTTP + 无头 Chromium，脚本化慢速回答，不碰真实 API）。

1. 回答中刷新页面 → 停在会话列表（带 ⏳ 提示），点进会话后全量回放并继续到终态；
2. 回答中断网 → toast 提示、指数退避自动重连、续读完成；
3. 点停止 → 后台任务取消，已生成部分保留。
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import uvicorn

FULL_TEXT = "这是一段用于测试断线重连功能的流式回答，需要足够长才能覆盖刷新窗口。"
PORT = 8013


def _free_port_wait(port):
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return True
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("server not up")


async def scripted_loop(history, provider, registry, settings, system_prompt,
                        session_id, services, web_max_iterations=None,
                        actor=None):
    """逐字慢速吐 FULL_TEXT，结束前把 assistant 消息落进 history（与真 loop 一致）。"""
    parts = []
    try:
        for ch in FULL_TEXT:
            await asyncio.sleep(0.12)
            parts.append(ch)
            yield ("content_delta", {"text": ch})
        history.append(Message(role="assistant", content=FULL_TEXT))
        yield ("done", {"reason": "stop", "iterations": 1,
                        "usage": {"prompt_tokens": 11, "completion_tokens": 22,
                                  "total_tokens": 33}})
    except (asyncio.CancelledError, GeneratorExit):
        # 与真 loop 一致：中途取消时已产出的部分落进 history
        text = "".join(parts)
        if text:
            history.append(Message(role="assistant", content=text))
        raise


import flyai.api.routes_chat as routes_chat  # noqa: E402
from flyai.app import create_app  # noqa: E402
from flyai.schemas import Message  # noqa: E402

routes_chat.run_agent_loop = scripted_loop

tmp_config = Path(__file__).resolve().parent / "tmp_reconnect_config.json"
with open("config/config.example.json", encoding="utf-8") as f:
    cfg = json.load(f)
cfg["auth"]["admin"]["password"] = "cdptest"
cfg["active_provider"] = "deepseek"  # 示例配置未设激活项，否则发消息即 400
with open(tmp_config, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

app = create_app(config_path=tmp_config)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                       log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
_free_port_wait(PORT)

import httpx  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def send_and_wait_partial(page, text, wait=1.6):
    page.fill("#input", text)
    page.click("#btn-send")
    time.sleep(wait)
    return page.evaluate(
        "state.currentAssistant ? state.currentAssistant.rawContent : ''")


def assistant_contents(msgs):
    if msgs.get("threads"):
        out = []
        for t in msgs["threads"]:
            out.extend(m["content"] for m in t["messages"]
                       if m["role"] == "assistant")
        return out
    return [m["content"] for m in msgs.get("messages", [])
            if m["role"] == "assistant"]


results = {}

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # ---- 登录 ----
    page.goto("http://127.0.0.1:%d/" % PORT)
    page.fill("#login-name", "admin")
    page.fill("#login-password", "cdptest")
    page.click("#btn-login")
    page.wait_for_selector("#input", timeout=8000)

    # ---- 场景 1：回答中刷新 ----
    partial = send_and_wait_partial(page, "场景一：刷新测试")
    assert partial and len(partial) < len(FULL_TEXT), "刷新前应只收到部分: %r" % partial
    results["A_partial_len"] = len(partial)
    resume_rid = page.evaluate("state.currentRunId")

    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=8000)
    # 刷新后不自动进入会话、不续读：标记已加载，停在会话列表
    page.wait_for_function(
        "state.pendingResume && state.pendingResume.runId === '%s' "
        "&& state.currentSessionId === null && !state.busy" % resume_rid,
        timeout=10000)
    # 用户主动点进该会话（带 ⏳ 徽标者）→ 自动续读
    page.locator(".session-item:has(.resume-flag)").first.click()
    page.wait_for_function(
        "state.busy && state.currentRunId === '%s'" % resume_rid, timeout=10000)
    # 等续读结束
    page.wait_for_function("!state.busy", timeout=20000)
    time.sleep(0.5)  # 终态后从磁盘重画
    msgs = page.evaluate("""fetch('/api/sessions/' + state.currentSessionId,
              {headers: authHeaders()})
        .then(r=>r.json())""")
    assistant_msgs = assistant_contents(msgs)
    assert assistant_msgs[-1] == FULL_TEXT, assistant_msgs[-1:]
    assert partial == FULL_TEXT[:len(partial)]
    results["A_full_after_refresh"] = True

    # ---- 场景 2：连接中途被掐断 → 自动重连 ----
    # 路由：POST /api/chat 流式 1.2s 后强制 abort（模拟断网），随即解除路由，
    # 让后续 events 续读请求正常通过
    killed = {"done": False}
    _DROP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}

    def kill_midstream(route):
        req = route.request
        if req.method == "POST" and not killed["done"]:
            killed["done"] = True
            # 自己中转上游：流式读 1.2s 后截断 fulfill，浏览器侧得到非终态的干净结束
            fwd_headers = {k: v for k, v in req.headers.items()
                           if k.lower() not in _DROP_HEADERS}
            with httpx.Client(timeout=30) as upstream_client:
                with upstream_client.stream(
                    "POST", req.url, headers=fwd_headers,
                    content=req.post_data_buffer,
                ) as upstream:
                    chunks = []
                    start = time.time()
                    for chunk in upstream.iter_bytes():
                        chunks.append(chunk)
                        if time.time() - start > 1.2:
                            break
                    up_status = upstream.status_code
                    up_headers = {k: v for k, v in upstream.headers.items()
                                  if k.lower() not in _DROP_HEADERS}
            route.fulfill(status=up_status, headers=up_headers,
                          body=b"".join(chunks))
        else:
            # events 续读 GET 与后续场景的 POST 一律放行
            try:
                route.continue_()
            except Exception:
                pass

    page.route("**/api/chat*", kill_midstream)
    partial2 = send_and_wait_partial(page, "场景二：断网测试")
    assert partial2 and len(partial2) < len(FULL_TEXT), repr(partial2)
    # reader 报错 → autoReconnect，toast 出现
    toast_txt = ""
    deadline = time.time() + 6
    while time.time() < deadline:
        time.sleep(0.3)
        toast_txt = page.evaluate(
            "Array.from(document.querySelectorAll('.toast')).map(function(t){return t.textContent}).join('|')")
        if "重连" in toast_txt:
            break
    assert "重连" in toast_txt, toast_txt
    page.wait_for_function("!state.busy", timeout=25000)
    time.sleep(0.5)
    msgs = page.evaluate("""fetch('/api/sessions/' + state.currentSessionId,
              {headers: authHeaders()})
        .then(r=>r.json())""")
    assistant_msgs = assistant_contents(msgs)
    assert assistant_msgs[-1] == FULL_TEXT, assistant_msgs[-1:]
    results["B_reconnect_toast"] = True
    results["B_full_after_offline"] = True

    # ---- 场景 3：停止 ----
    partial3 = send_and_wait_partial(page, "场景三：停止测试")
    assert partial3
    page.click("#btn-stop")
    page.wait_for_function("!state.busy", timeout=8000)
    time.sleep(0.5)
    msgs = page.evaluate("""fetch('/api/sessions/' + state.currentSessionId,
              {headers: authHeaders()})
        .then(r=>r.json())""")
    assistant_msgs = assistant_contents(msgs)
    # 取消 POST 飞行期间可能又落了 1~2 帧：落盘部分以页面所见为前缀、仍是全文前缀
    saved3 = assistant_msgs[-1]
    assert saved3.startswith(partial3) and FULL_TEXT.startswith(saved3) \
        and len(saved3) - len(partial3) <= 2, (saved3, partial3)
    results["C_partial_saved"] = True

    browser.close()

print("RESULT", json.dumps(results, ensure_ascii=False))
print("E2E_OK")
server.should_exit = True
os.remove(tmp_config)
