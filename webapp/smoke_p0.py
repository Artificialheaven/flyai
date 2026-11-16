# -*- coding: utf-8 -*-
"""P0 浏览器冒烟：真实后端登录 → 主布局（侧栏壳）→ 主题三态 → 刷新恢复 → 登出。
前置：webapp dev server 在 8090、FastAPI 在 8000。管理员凭据从 config/config.json 读取，不写死。
运行：.venv/Scripts/python webapp/smoke_p0.py
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
admin = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))["auth"]["admin"]
SHOT_DIR = ROOT / "show" / "p0-smoke"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

console_errors = []
unexpected_responses = []


def main() -> None:
    with sync_playwright() as p:
        # 本机无 Playwright 浏览器缓存，直接驱动系统安装的 Chrome
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        def on_response(resp):
            # 唯一预期的非 2xx：负例登录的 401
            if not resp.ok and not (resp.status == 401 and resp.url.endswith("/api/auth/login")):
                unexpected_responses.append(f"{resp.status} {resp.url}")

        page.on("response", on_response)

        page.goto("http://127.0.0.1:8090/", wait_until="networkidle")

        # 1) 登录卡片渲染
        expect(page.get_by_text("FlyAI").first).to_be_visible()
        expect(page.get_by_role("button", name="登录")).to_be_visible()
        page.screenshot(path=str(SHOT_DIR / "1-login.png"))

        # 2) 错误密码 → 内联错误
        page.get_by_label("用户名").fill(admin["username"])
        page.get_by_label("密码").fill("__wrong__")
        page.get_by_role("button", name="登录").click()
        expect(page.locator("text=用户名或密码错误").first).to_be_visible(timeout=8000)

        # 3) 真实登录 → 进入主布局
        page.get_by_label("密码").fill(admin["password"])
        page.get_by_role("button", name="登录").click()
        expect(page.get_by_role("button", name="退出")).to_be_visible(timeout=8000)
        token = page.evaluate("localStorage.getItem('flyai-token')")
        assert token and len(token) > 20, "token 未写入 localStorage"
        assert page.evaluate("localStorage.getItem('flyai-role')") == "admin"
        page.screenshot(path=str(SHOT_DIR / "2-shell-dark.png"))

        # 4) 主题三态切换
        def pick_theme(name: str) -> None:
            page.get_by_role("button", name="切换主题").click()
            page.get_by_role("option", name=name).click()

        pick_theme("浅色")
        page.wait_for_function("document.body.classList.contains('light')")
        assert not page.is_visible("[data-fly-dark-theme]")
        page.screenshot(path=str(SHOT_DIR / "3-shell-light.png"))
        pick_theme("深色")
        page.wait_for_function("document.body.hasAttribute('data-fly-dark-theme')")
        assert page.evaluate("localStorage.getItem('flyai-theme')") == "dark"

        # 5) 刷新后凭 /me 恢复会话，不回登录页
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("button", name="退出")).to_be_visible(timeout=8000)
        assert page.evaluate("localStorage.getItem('flyai-token')") == token

        # 6) 登出 → 回登录卡片，本地凭据清空
        page.get_by_role("button", name="退出").click()
        expect(page.get_by_role("button", name="登录")).to_be_visible(timeout=8000)
        assert page.evaluate("localStorage.getItem('flyai-token')") is None
        page.screenshot(path=str(SHOT_DIR / "4-after-logout.png"))

        browser.close()

    # 网络层的 401/404 控制台噪声由 response 监听精确判定，这里只留真正的 JS 错误
    js_errors = [e for e in console_errors if "Failed to load resource" not in e]
    if js_errors or unexpected_responses:
        print("JS ERRORS:", js_errors)
        print("UNEXPECTED RESPONSES:", unexpected_responses)
        sys.exit(1)
    print("P0 smoke OK; screenshots:", SHOT_DIR)


if __name__ == "__main__":
    main()
