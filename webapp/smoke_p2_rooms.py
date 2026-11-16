# -*- coding: utf-8 -*-
"""P2 房间冒烟：新建房间（多选根卡）、线程页签、记忆与属性保存、添加成员、
建群聊、@ 提及候选、窗口重命名/删除，以及移动端房间布局。

真实后端 + 系统 Chrome，管理员凭据从 config/config.json 读取。
前置：webapp dev server 在 8090、FastAPI 在 8000。
运行：.venv/Scripts/python webapp/smoke_p2_rooms.py

只操作本脚本用时间戳命名的数据，结束时全部删除，不碰已有内容。
"""
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
admin = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))["auth"]["admin"]
SHOT_DIR = ROOT / "show" / "p2-rooms-smoke"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

PREFIX = "P2房间冒烟"
NOISE = ("Failed to load resource",)

console_errors = []
unexpected_responses = []


def login(page) -> None:
    page.goto("http://127.0.0.1:8090/", wait_until="networkidle")
    page.get_by_label("用户名").fill(admin["username"])
    page.get_by_label("密码").fill(admin["password"])
    page.get_by_role("button", name="登录").click()
    expect(page.get_by_role("button", name="新建会话").first).to_be_visible(timeout=8000)


def purge_smoke_data(page) -> None:
    """删除历史遗留的冒烟会话与角色（名称以 P2房间冒烟 开头），失败不阻塞"""
    page.evaluate(
        """async (prefix) => {
            const token = localStorage.getItem('flyai-token');
            const headers = { Authorization: 'Bearer ' + token };
            try {
                const sessions = (await (await fetch('/api/sessions', { headers })).json()).sessions || [];
                for (const s of sessions) {
                    if ((s.title || '').startsWith(prefix)) {
                        await fetch('/api/sessions/' + encodeURIComponent(s.id), { method: 'DELETE', headers });
                    }
                }
                const chars = (await (await fetch('/api/characters', { headers })).json()).characters || [];
                for (const c of chars) {
                    if ((c.name || '').startsWith(prefix)) {
                        await fetch('/api/characters/' + encodeURIComponent(c.id), { method: 'DELETE', headers });
                    }
                }
            } catch (e) {}
        }""",
        PREFIX,
    )


def create_fixture_characters(page, names) -> None:
    page.evaluate(
        """async ({ names }) => {
            const token = localStorage.getItem('flyai-token');
            const headers = { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' };
            for (const name of names) {
                const resp = await fetch('/api/characters', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ name, intro: 'P2 房间冒烟测试角色', background: '', first_mes: '' }),
                });
                if (!resp.ok) throw new Error('创建角色失败 ' + resp.status);
            }
        }""",
        {"names": names},
    )


def find_room_id(page, title: str) -> str:
    return page.evaluate(
        """async (title) => {
            const token = localStorage.getItem('flyai-token');
            const headers = { Authorization: 'Bearer ' + token };
            const sessions = (await (await fetch('/api/sessions', { headers })).json()).sessions || [];
            const hit = sessions.find((s) => s.title === title);
            if (!hit) throw new Error('未找到会话 ' + title);
            return hit.id;
        }""",
        title,
    )


def select_root_card(page, dialog, name: str) -> None:
    """在新建对话里进入某张根卡并勾选主卡"""
    dialog.locator('[class*="rootCard"]', has_text=name).first.click()
    expect(dialog.get_by_text(f"选择「{name}」的主卡或副本")).to_be_visible(timeout=8000)
    dialog.locator('[class*="variantRow"]', has_text=name).first.click()
    page.get_by_role("button", name="← 返回根角色卡").click()


def open_room_menu(page):
    page.get_by_role("button", name="会话设置").click()
    panel = page.get_by_role("dialog", name="会话设置")
    expect(panel).to_be_visible(timeout=8000)
    return panel


def main() -> None:
    stamp = int(time.time())
    room_title = f"{PREFIX}房间 {stamp}"
    char_a = f"{PREFIX}甲 {stamp}"
    char_b = f"{PREFIX}乙 {stamp}"
    char_c = f"{PREFIX}丙 {stamp}"
    memory_text = f"冒烟记忆 {stamp}"
    group_title = f"冒烟群聊 {stamp}"
    renamed_title = f"改名窗口 {stamp}"

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

        # 1) 登录 → 准备三张角色卡 → 打开新建对话
        login(page)
        purge_smoke_data(page)
        create_fixture_characters(page, [char_a, char_b, char_c])
        page.get_by_role("button", name="新建对话").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible(timeout=8000)
        dialog.get_by_label("会话名称").fill(room_title)

        # 2) 跨根卡多选两张主卡 → 创建房间
        select_root_card(page, dialog, char_a)
        select_root_card(page, dialog, char_b)
        expect(dialog.get_by_role("button", name="创建房间（2）")).to_be_enabled()
        page.screenshot(path=str(SHOT_DIR / "1-pick-cards.png"))
        dialog.get_by_role("button", name=re.compile(r"^创建房间")).click()
        expect(dialog).to_be_hidden(timeout=15000)
        expect(page.get_by_role("heading", name=room_title)).to_be_visible(timeout=15000)
        tabs = page.get_by_role("tab")
        expect(tabs).to_have_count(2, timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "2-room-tabs.png"))

        rid = find_room_id(page, room_title)

        # 3) 记忆与属性：填第一位成员的记忆/属性并保存
        panel = open_room_menu(page)
        page.screenshot(path=str(SHOT_DIR / "3-room-menu.png"))
        panel.get_by_role("button", name=re.compile("记忆与属性")).click()
        mem_dialog = page.get_by_role("dialog", name="记忆与属性")
        expect(mem_dialog).to_be_visible(timeout=8000)
        blocks = mem_dialog.locator('[class*="block"]')
        expect(blocks).to_have_count(2, timeout=8000)
        first_block = blocks.first
        first_block.locator("textarea").nth(0).fill(memory_text)
        first_block.locator("textarea").nth(1).fill('{"好感度": 50}')
        first_block.get_by_role("button", name="保存").click()
        expect(first_block.get_by_text("已保存", exact=True)).to_be_visible(timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "4-memory-saved.png"))

        # 后端真值：记忆与属性已落库
        detail = page.evaluate(
            """async (rid) => {
                const token = localStorage.getItem('flyai-token');
                const headers = { Authorization: 'Bearer ' + token };
                return await (await fetch('/api/sessions/' + encodeURIComponent(rid), { headers })).json();
            }""",
            rid,
        )
        ai_members = [m for m in detail["members"] if not m.get("is_user")]
        assert any(m.get("memory") == memory_text for m in ai_members), [m.get("memory") for m in ai_members]
        saved = next(m for m in ai_members if m.get("memory") == memory_text)
        assert saved.get("attributes", {}).get("好感度") == 50, saved.get("attributes")
        # 头部关闭按钮与底部「关闭」同名，取底部那个
        mem_dialog.get_by_role("button", name="关闭", exact=True).last.click()
        expect(mem_dialog).to_be_hidden(timeout=8000)

        # 4) 添加成员：第三张卡 → 自动多一条私聊窗口
        panel = open_room_menu(page)
        panel.get_by_role("button", name=re.compile("添加成员")).click()
        add_dialog = page.get_by_role("dialog", name="添加成员")
        expect(add_dialog).to_be_visible(timeout=8000)
        add_dialog.locator("label", has_text=char_c).first.click()
        add_dialog.get_by_role("button", name="添加（1）").click()
        expect(add_dialog).to_be_hidden(timeout=15000)
        expect(page.get_by_text("已添加 1 位成员")).to_be_visible(timeout=8000)
        expect(page.get_by_role("tab")).to_have_count(3, timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "5-member-added.png"))

        # 5) 建群聊：默认全选三位 → 新建窗口并自动切过去
        panel = open_room_menu(page)
        panel.get_by_role("button", name=re.compile("建群聊")).click()
        group_dialog = page.get_by_role("dialog", name="建群聊")
        expect(group_dialog).to_be_visible(timeout=8000)
        for name in (char_a, char_b, char_c):
            expect(group_dialog.locator("label", has_text=name).locator("input")).to_be_checked()
        group_dialog.get_by_placeholder("群聊名称（留空自动生成）").fill(group_title)
        page.screenshot(path=str(SHOT_DIR / "6-group-dialog.png"))
        group_dialog.get_by_role("button", name="建群").click()
        expect(group_dialog).to_be_hidden(timeout=15000)
        group_tab = page.get_by_role("tab", name=group_title)
        expect(group_tab).to_be_visible(timeout=8000)
        expect(group_tab).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("tab")).to_have_count(4, timeout=8000)

        # 6) 群聊输入 @ 弹出三位成员候选（不发送，避免真实 LLM 调用）
        input_box = page.get_by_placeholder("输入消息，Enter 发送，Shift+Enter 换行")
        input_box.fill("@")
        listbox = page.get_by_role("listbox")
        expect(listbox).to_be_visible(timeout=8000)
        expect(listbox.get_by_role("option")).to_have_count(3, timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "7-mentions.png"))
        input_box.fill("")

        # 7) 重命名当前（群聊）窗口
        panel = open_room_menu(page)
        panel.get_by_role("button", name=re.compile("重命名当前窗口")).click()
        rename_dialog = page.get_by_role("dialog", name="重命名窗口")
        expect(rename_dialog).to_be_visible(timeout=8000)
        rename_input = rename_dialog.get_by_placeholder("窗口名称")
        expect(rename_input).to_have_value(group_title)
        rename_input.fill(renamed_title)
        rename_dialog.get_by_role("button", name="保存").click()
        expect(rename_dialog).to_be_hidden(timeout=10000)
        expect(page.get_by_text("窗口已重命名")).to_be_visible(timeout=8000)
        renamed_tab = page.get_by_role("tab", name=renamed_title)
        expect(renamed_tab).to_be_visible(timeout=8000)
        expect(renamed_tab).to_have_attribute("aria-selected", "true")
        page.screenshot(path=str(SHOT_DIR / "8-renamed.png"))

        # 8) 删除当前窗口 → 回到私聊窗口
        panel = open_room_menu(page)
        panel.get_by_role("button", name=re.compile("删除当前窗口")).click()
        delete_dialog = page.get_by_role("dialog", name="删除窗口")
        expect(delete_dialog).to_be_visible(timeout=8000)
        expect(delete_dialog).to_contain_text(renamed_title)
        delete_dialog.get_by_role("button", name="删除").click()
        expect(delete_dialog).to_be_hidden(timeout=10000)
        expect(page.get_by_text(re.compile(r"已删除窗口"))).to_be_visible(timeout=8000)
        expect(page.get_by_role("tab")).to_have_count(3, timeout=8000)
        expect(page.get_by_role("tab", name=renamed_title)).to_have_count(0)
        page.screenshot(path=str(SHOT_DIR / "9-thread-deleted.png"))

        # 9) 移动端：打开房间、会话设置与记忆弹窗
        mobile = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2
        )
        mpage = mobile.new_page()
        mpage.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        mpage.on("pageerror", lambda exc: console_errors.append(str(exc)))
        login(mpage)
        mpage.get_by_role("button", name="打开会话列表").click()
        mpage.get_by_text(room_title).first.click()
        expect(mpage.get_by_role("heading", name=room_title)).to_be_visible(timeout=15000)
        mpage.wait_for_timeout(600)
        mpage.screenshot(path=str(SHOT_DIR / "10-mobile-room.png"))
        mpage.get_by_role("button", name="会话设置").click()
        expect(mpage.get_by_role("dialog", name="会话设置")).to_be_visible(timeout=8000)
        mpage.screenshot(path=str(SHOT_DIR / "11-mobile-menu.png"))
        mpage.get_by_role("dialog", name="会话设置").get_by_role(
            "button", name=re.compile("记忆与属性")
        ).click()
        expect(mpage.get_by_role("dialog", name="记忆与属性")).to_be_visible(timeout=8000)
        mpage.screenshot(path=str(SHOT_DIR / "12-mobile-memory.png"))
        mobile.close()

        # 10) 清理：只删本脚本创建的房间与角色
        purge_smoke_data(page)
        browser.close()

    js_errors = [e for e in console_errors if not any(noise in e for noise in NOISE)]
    if js_errors or unexpected_responses:
        print("JS ERRORS:", js_errors)
        print("UNEXPECTED RESPONSES:", unexpected_responses)
        sys.exit(1)
    print("P2 rooms smoke OK; screenshots:", SHOT_DIR)


if __name__ == "__main__":
    main()