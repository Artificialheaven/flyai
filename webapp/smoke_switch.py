# -*- coding: utf-8 -*-
"""切换验收冒烟：确认 FastAPI 同源（:8000）提供的是新版 SPA 构建产物。
登录 → 新建会话 → 发一条短消息收流 → /login 单入口兼容。不依赖 dev server。
运行：.venv/Scripts/python webapp/smoke_switch.py
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
admin = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))["auth"]["admin"]
SHOT_DIR = ROOT / "show" / "p1-smoke"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "http://127.0.0.1:8000"

console_errors = []


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        # 1) 根路径是新 SPA：登录表单可见，静态资源 200
        page.goto(BASE + "/", wait_until="networkidle")
        page.get_by_label("用户名").fill(admin["username"])
        page.get_by_label("密码").fill(admin["password"])
        page.get_by_role("button", name="登录").click()
        expect(page.get_by_role("button", name="新建会话").first).to_be_visible(timeout=10000)
        page.screenshot(path=str(SHOT_DIR / "switch-1-login.png"))

        # 2) 新建 + 一句话对话，确认同源部署下流式链路可用
        page.get_by_role("button", name="新建会话").first.click()
        box = page.get_by_placeholder("输入消息，Enter 发送，Shift+Enter 换行")
        expect(box).to_be_visible()
        box.fill("切换验收：请只回复 OK 两个字母。")
        page.get_by_role("button", name="发送").click()
        expect(page.get_by_role("button", name="停止生成")).to_be_visible(timeout=15000)
        expect(page.get_by_role("button", name="停止生成")).to_be_hidden(timeout=60000)
        expect(page.get_by_text("OK").first).to_be_visible(timeout=15000)
        page.screenshot(path=str(SHOT_DIR / "switch-2-reply.png"))

        # 3) /login 返回同一 SPA（旧书签/APK 外壳兼容）
        resp = page.request.get(BASE + "/login")
        assert resp.ok and "<div id=\"root\">" in resp.text(), "/login 未返回 SPA 入口"

        browser.close()

    if console_errors:
        print("JS ERRORS:", console_errors)
        sys.exit(1)
    print("switch smoke OK; screenshots:", SHOT_DIR)


if __name__ == "__main__":
    main()