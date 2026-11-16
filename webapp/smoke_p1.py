# -*- coding: utf-8 -*-
"""P1 浏览器冒烟：新建会话 → 发消息收流 → 断线自动重连续读 → 刷新历史回放
→ 侧栏搜索/重命名/批量模式/删除。真实后端 + 系统 Chrome，管理员凭据从 config/config.json 读取。

前置：webapp dev server 在 8090、FastAPI 在 8000。
运行：.venv/Scripts/python webapp/smoke_p1.py

断线重连用进程内的 CutProxy（8092 → 8000）：Chromium 的 set_offline 断不掉已建立的
SSE 长连接（实测断网后整段回复仍能收完），只能从 TCP 层切断才能触发客户端退避重连。
本段通过 localStorage 运行期配置把 API 临时指到 8092（跨源，代理负责 CORS），
测完删掉覆盖项、刷新回同源直连。
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
admin = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))["auth"]["admin"]
SHOT_DIR = ROOT / "show" / "p1-smoke"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

PROXY_PORT = 8092
BACKEND_PORT = 8000

# 断网期间浏览器会刷网络类控制台错误，属于预期噪声，收尾时剔除
NOISE = (
    "Failed to load resource",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_CONNECTION_REFUSED",
    "ERR_EMPTY_RESPONSE",
    "ERR_SOCKET_NOT_CONNECTED",
    "NetworkError when attempting to fetch resource",
    "Load failed",
    "the network connection was lost",
)

console_errors = []
unexpected_responses = []

CORS_PREFLIGHT = (
    b"HTTP/1.1 204 No Content\r\n"
    b"Access-Control-Allow-Origin: *\r\n"
    b"Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS\r\n"
    b"Access-Control-Allow-Headers: Authorization, Content-Type\r\n"
    b"Access-Control-Max-Age: 600\r\n"
    b"Content-Length: 0\r\n"
    b"Connection: close\r\n\r\n"
)


class CutProxy:
    """极简 HTTP/1.1 转发代理（8092 → 8000），可精确切断在途 SSE 连接。

    cut() 关掉所有连接并拒绝新连接，模拟客户端断网；restore() 放行后，
    客户端的退避重连就能用 ?after=seq 从仍存活的服务端 run 续读。
    跨源访问：响应头注入 ACAO 并暴露 X-Run-Id（客户端靠它建立 run 上下文）。
    """

    def __init__(self, listen_port: int = PROXY_PORT) -> None:
        self.listen_port = listen_port
        self.offline = False
        self.conns = []
        self.lock = threading.Lock()
        self.sock = None

    def start(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.listen_port))
        self.sock.listen(64)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def cut(self) -> None:
        self.offline = True
        with self.lock:
            pairs = list(self.conns)
            self.conns = []
        for pair in pairs:
            for sock in pair:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass

    def restore(self) -> None:
        self.offline = False

    def _accept_loop(self) -> None:
        while True:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            if self.offline:
                client.close()
                continue
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    def _relay(self, client: socket.socket) -> None:
        upstream = None
        with self.lock:
            self.conns.append((client, None))
        try:
            head, body = self._read_request(client)
            if head is None:
                return
            method, path, headers = head
            if method == b"OPTIONS":
                client.sendall(CORS_PREFLIGHT)
                return

            upstream = socket.create_connection(("127.0.0.1", BACKEND_PORT))
            with self.lock:
                self.conns = [(c, upstream if c is client else u) for c, u in self.conns]
            fwd = [b"%s %s HTTP/1.1" % (method, path)]
            for line in headers:
                key = line.partition(b":")[0].strip().lower()
                if key in (b"host", b"connection", b"proxy-connection"):
                    continue
                fwd.append(line)
            fwd.append(b"Host: 127.0.0.1:%d" % BACKEND_PORT)
            fwd.append(b"Connection: close")
            upstream.sendall(b"\r\n".join(fwd) + b"\r\n\r\n" + body)

            # 响应头原样转发（保住 Content-Length / chunked，SSE 帧才能实时透传），
            # 只把 Connection 换成 close 并补 CORS 头
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = upstream.recv(65536)
                if not chunk:
                    return
                raw += chunk
            raw_head, _, rest = raw.partition(b"\r\n\r\n")
            lines = raw_head.split(b"\r\n")
            out = [lines[0]]
            for line in lines[1:]:
                if line.partition(b":")[0].strip().lower() == b"connection":
                    continue
                out.append(line)
            out.append(b"Access-Control-Allow-Origin: *")
            out.append(b"Access-Control-Expose-Headers: X-Run-Id")
            out.append(b"Connection: close")
            client.sendall(b"\r\n".join(out) + b"\r\n\r\n" + rest)

            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    return
                client.sendall(chunk)
        except OSError:
            pass
        finally:
            with self.lock:
                self.conns = [(c, u) for c, u in self.conns if c is not client]
            for sock in (client, upstream):
                try:
                    if sock is not None:
                        sock.close()
                except OSError:
                    pass

    @staticmethod
    def _read_request(client: socket.socket):
        """读一整条请求（请求行 + 头 + Content-Length 指定的 body）"""
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = client.recv(65536)
            if not chunk:
                return None, b""
            raw += chunk
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        method, path, _ = lines[0].split(b" ")
        headers = [line for line in lines[1:] if line]
        length = 0
        for line in headers:
            key, _, value = line.partition(b":")
            if key.strip().lower() == b"content-length":
                length = int(value.strip() or 0)
        while len(body) < length:
            chunk = client.recv(length - len(body))
            if not chunk:
                return None, b""
            body += chunk
        return (method, path, headers), body


def login(page) -> None:
    page.goto("http://127.0.0.1:8090/", wait_until="networkidle")
    page.get_by_label("用户名").fill(admin["username"])
    page.get_by_label("密码").fill("__wrong__")
    page.get_by_role("button", name="登录").click()
    expect(page.get_by_text("用户名或密码错误").first).to_be_visible(timeout=8000)
    page.get_by_label("密码").fill(admin["password"])
    page.get_by_role("button", name="登录").click()
    # 侧栏图标按钮与欢迎页主按钮同名，统一取侧栏那个（始终存在）
    expect(page.get_by_role("button", name="新建会话").first).to_be_visible(timeout=8000)


def send_message(page, text: str):
    """发送并等待 /api/chat 建流，返回 POST 响应对象"""
    box = page.get_by_placeholder("输入消息，Enter 发送，Shift+Enter 换行")
    box.fill(text)
    with page.expect_response(
        lambda r: r.url.endswith("/api/chat") and r.request.method == "POST", timeout=15000
    ) as resp_info:
        page.get_by_role("button", name="发送").click()
    resp = resp_info.value
    assert resp.ok, f"POST /api/chat 异常：{resp.status}"
    return resp


def wait_idle(page, timeout_ms: int = 120000) -> None:
    """停止按钮消失 = run 终态（成功或服务端收尾）"""
    expect(page.get_by_role("button", name="停止生成")).to_be_hidden(timeout=timeout_ms)


def wait_server_idle(page, sid: str, timeout_s: int = 60) -> None:
    """终态帧后服务端还有轮后收尾（心情/记忆抽取），会话锁要等 drive 返回才释放。
    真实用户打字需要时间，下一条消息按同等节奏等活跃 run 消失后再发。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        free = page.evaluate(
            """async ([sid]) => {
                const token = localStorage.getItem('flyai-token');
                let base = '';
                try {
                    base = JSON.parse(localStorage.getItem('flyai-runtime-config') || '{}').apiBase || '';
                } catch (e) {}
                const resp = await fetch(base + '/api/chat/active-run?session_id=' + encodeURIComponent(sid),
                    { headers: { Authorization: 'Bearer ' + token } });
                if (!resp.ok) return true;
                const body = await resp.json();
                return !body.run_id;
            }""",
            [sid],
        )
        if free:
            return
        page.wait_for_timeout(500)
    print("WARN: 活跃 run 仍未清空，继续发送")


def main() -> None:
    stamp = int(time.time())
    unique_title = f"P1冒烟会话 {stamp}"
    msg1 = f"P1 冒烟消息 A {stamp}，请用一句话简短回复。"
    msg2 = f"P1 冒烟消息 B（断线重连）{stamp}，请输出一段大约三百字的故事，内容随意。"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))
        page.on(
            "response",
            lambda resp: unexpected_responses.append(f"{resp.status} {resp.url}")
            if (not resp.ok and not (resp.status == 401 and resp.url.endswith("/api/auth/login")))
            else None,
        )

        # 1) 登录 + 新建会话（顺带截获服务端分配的会话 id，供活跃 run 探测）
        login(page)
        with page.expect_response(
            lambda r: r.url.endswith("/api/sessions") and r.request.method == "POST",
            timeout=15000,
        ) as created:
            page.get_by_role("button", name="新建会话").first.click()
        sid = created.value.json()["id"]
        expect(page.get_by_placeholder("输入消息，Enter 发送，Shift+Enter 换行")).to_be_visible()
        page.screenshot(path=str(SHOT_DIR / "1-new-session.png"))

        # 2) 首轮消息：流式回复到终态
        send_message(page, msg1)
        expect(page.get_by_role("button", name="停止生成")).to_be_visible(timeout=10000)
        wait_idle(page)
        expect(page.get_by_text(msg1).first).to_be_visible()
        page.screenshot(path=str(SHOT_DIR / "2-first-reply.png"))

        # 3) 侧栏重命名（覆盖菜单 + 弹窗 + 乐观更新）
        # exact=True：会话行本身也是 role=button，其可访问名称包含子按钮标签，模糊匹配会点到整行
        page.get_by_role("button", name="会话操作", exact=True).first.click()
        page.get_by_role("menuitem", name="重命名").click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("textbox").fill(unique_title)
        dialog.get_by_role("button", name="保存").click()
        expect(dialog).to_be_hidden()
        expect(page.get_by_text(unique_title).first).to_be_visible()

        # 4) 第二轮中断网：验证自动重连 + after=seq 续读
        #    API 临时切到进程内 CutProxy，TCP 层切断才能杀掉在途 SSE
        wait_server_idle(page, sid)
        proxy = CutProxy()
        proxy.start()
        try:
            page.evaluate(
                """() => localStorage.setItem('flyai-runtime-config',
                    JSON.stringify({ apiBase: 'http://127.0.0.1:%d' }))""" % PROXY_PORT
            )
            page.reload(wait_until="networkidle")
            page.get_by_placeholder("搜索会话标题").fill(unique_title)
            page.get_by_text(unique_title).first.click()
            expect(page.get_by_placeholder("输入消息，Enter 发送，Shift+Enter 换行")).to_be_visible(
                timeout=15000
            )
            send_message(page, msg2)
            expect(page.get_by_role("button", name="停止生成")).to_be_visible(timeout=10000)
            # 先收几帧再断，确保续读确实走 ?after=seq 而不是 after=0
            page.wait_for_timeout(400)
            proxy.cut()
            expect(page.get_by_text("连接中断，正在自动重连…")).to_be_visible(timeout=15000)
            page.screenshot(path=str(SHOT_DIR / "3-reconnecting.png"))
            page.wait_for_timeout(2500)
        finally:
            proxy.restore()
        # 恢复后重连成功并走到终态，提示条消失
        wait_idle(page)
        expect(page.get_by_text("连接中断，正在自动重连…")).to_be_hidden()
        expect(page.get_by_text(msg2).first).to_be_visible()
        page.screenshot(path=str(SHOT_DIR / "4-after-resume.png"))
        proxy.stop()

        # 5) 刷新：回到同源直连，历史从服务端回放，两条消息都在
        page.evaluate("() => localStorage.removeItem('flyai-runtime-config')")
        page.reload(wait_until="networkidle")
        page.get_by_placeholder("搜索会话标题").fill(unique_title)
        page.get_by_text(unique_title).first.click()
        expect(page.get_by_text(msg1).first).to_be_visible(timeout=15000)
        expect(page.get_by_text(msg2).first).to_be_visible()
        page.screenshot(path=str(SHOT_DIR / "5-history-reload.png"))

        # 6) 批量模式：进入 → 勾选行 → 退出
        page.get_by_role("button", name="批量").click()
        expect(page.get_by_text("已选 0 项")).to_be_visible()
        page.get_by_text(unique_title).first.click()
        expect(page.get_by_text("已选 1 项")).to_be_visible()
        page.get_by_role("button", name="完成").click()
        expect(page.get_by_text("已选 0 项")).to_be_hidden()

        # 7) 删除会话（同时清理冒烟数据）
        page.get_by_role("button", name="会话操作", exact=True).first.click()
        page.get_by_role("menuitem", name="删除").click()
        confirm = page.get_by_role("dialog")
        expect(confirm).to_be_visible()
        confirm.get_by_role("button", name="删除").click()
        expect(page.get_by_text(unique_title)).to_be_hidden()
        page.screenshot(path=str(SHOT_DIR / "6-deleted.png"))

        browser.close()

    js_errors = [e for e in console_errors if not any(noise in e for noise in NOISE)]
    if js_errors or unexpected_responses:
        print("JS ERRORS:", js_errors)
        print("UNEXPECTED RESPONSES:", unexpected_responses)
        sys.exit(1)
    print("P1 smoke OK; screenshots:", SHOT_DIR)


if __name__ == "__main__":
    main()
