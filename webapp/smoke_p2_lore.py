# -*- coding: utf-8 -*-
"""P2 内容库冒烟：角色卡新建/副本/编辑/导出/导入/删除，世界观新建/导出/世界书导入/删除，
以及移动端布局。真实后端 + 系统 Chrome，管理员凭据从 config/config.json 读取。

前置：webapp dev server 在 8090、FastAPI 在 8000。
运行：.venv/Scripts/python webapp/smoke_p2_lore.py

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
SHOT_DIR = ROOT / "show" / "p2-lore-smoke"
TMP_DIR = SHOT_DIR / "tmp"
SHOT_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

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
    """删除历史遗留的冒烟数据（名字以 P2冒烟 开头），失败不阻塞"""
    page.evaluate(
        """async () => {
            const token = localStorage.getItem('flyai-token');
            const headers = { Authorization: 'Bearer ' + token };
            try {
                const chars = (await (await fetch('/api/characters', { headers })).json()).characters || [];
                for (const c of chars) {
                    if ((c.name || '').startsWith('P2冒烟')) {
                        await fetch('/api/characters/' + encodeURIComponent(c.id), { method: 'DELETE', headers });
                    }
                }
                const worlds = (await (await fetch('/api/worlds', { headers })).json()).worlds || [];
                for (const w of worlds) {
                    if ((w.name || '').startsWith('P2冒烟')) {
                        await fetch('/api/worlds/' + encodeURIComponent(w.id), { method: 'DELETE', headers });
                    }
                }
            } catch (e) {}
        }"""
    )


def open_lore(page) -> None:
    page.get_by_role("button", name="内容库（角色卡 / 世界观）").click()
    expect(page.get_by_role("heading", name="内容库")).to_be_visible(timeout=8000)


def delete_root(page, name: str) -> None:
    """进入根卡详情 → 更多操作 → 删除 → 确认（连带副本），断言回到网格"""
    page.locator('[class*="rootCard"]', has_text=name).first.click()
    expect(page.get_by_text(f"「{name}」的主卡与副本")).to_be_visible(timeout=8000)
    page.get_by_role("button", name="更多操作").first.click()
    page.get_by_role("menuitem", name="删除").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="删除").click()
    expect(dialog).to_be_hidden(timeout=8000)
    expect(page.get_by_text("新建根角色卡")).to_be_visible()


def delete_world(page, name: str) -> None:
    row = page.locator('[class*="worldRow"]', has_text=name)
    row.get_by_role("button", name="删除").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="删除").click()
    expect(row).to_have_count(0, timeout=8000)


def main() -> None:
    stamp = int(time.time())
    root_name = f"P2冒烟角色 {stamp}"
    variant_name = f"P2冒烟副本 {stamp}"
    world_name = f"P2冒烟世界观 {stamp}"
    book_name = f"P2冒烟世界书 {stamp}"
    edited_intro = f"编辑后的简介 {stamp}"
    exported = TMP_DIR / f"{stamp}-character.json"
    world_exported = TMP_DIR / f"{stamp}-world.json"
    worldbook_file = TMP_DIR / f"{book_name}.json"

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

        # 1) 登录 → 侧栏内容库入口
        login(page)
        purge_smoke_data(page)
        open_lore(page)
        page.screenshot(path=str(SHOT_DIR / "1-lore-home.png"))

        # 2) 新建根角色卡
        page.locator('[class*="rootCard"]', has_text="新建根角色卡").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_label("角色名").fill(root_name)
        dialog.get_by_label("角色简介（intro）").fill("这是冒烟测试创建的角色简介。")
        dialog.get_by_role("button", name="创建").click()
        expect(dialog).to_be_hidden(timeout=10000)
        expect(page.get_by_text(f"「{root_name}」的主卡与副本")).to_be_visible(timeout=8000)
        expect(page.get_by_text("已保存角色卡")).to_be_visible()
        page.screenshot(path=str(SHOT_DIR / "2-root-detail.png"))

        # 3) 新建副本
        page.get_by_role("button", name="＋ 新建副本").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_label("角色名").fill(variant_name)
        dialog.get_by_role("button", name="创建").click()
        expect(dialog).to_be_hidden(timeout=10000)
        expect(page.get_by_text(variant_name)).to_be_visible(timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "3-variant.png"))

        # 4) 编辑主卡简介
        page.locator('[class*="variantRow"]').first.get_by_role("button", name="编辑").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        intro = dialog.get_by_label("角色简介（intro）")
        expect(intro).to_have_value("这是冒烟测试创建的角色简介。")
        intro.fill(edited_intro)
        dialog.get_by_role("button", name="保存").click()
        expect(dialog).to_be_hidden(timeout=10000)

        # 5) 导出主卡 JSON
        with page.expect_download(timeout=10000) as download_info:
            page.locator('[class*="variantRow"]').first.get_by_role("button", name="导出").click()
        download_info.value.save_as(str(exported))
        data = json.loads(exported.read_text(encoding="utf-8"))
        assert data.get("type") == "flyai-character", data.get("type")
        assert data["data"]["name"] == root_name, data["data"].get("name")
        assert data["data"]["intro"] == edited_intro

        # 6) 导入刚导出的 JSON（后端重生成 id，网格应出现第二张同名根卡）
        page.get_by_role("button", name="← 返回根角色卡").click()
        expect(page.locator('[class*="rootCard"]', has_text="新建根角色卡")).to_be_visible()
        page.locator('input[type="file"]').first.set_input_files(str(exported))
        expect(page.get_by_text("导入成功")).to_be_visible(timeout=10000)
        tiles = page.locator('[class*="rootCard"]', has_text=root_name)
        expect(tiles).to_have_count(2, timeout=10000)
        page.screenshot(path=str(SHOT_DIR / "4-imported.png"))

        # 7) 删除导入的根卡（无副本那张），主卡连带副本稍后删
        page.locator('[class*="rootCard"]', has_text=root_name).filter(has_text="0 个副本").click()
        expect(page.get_by_text(f"「{root_name}」的主卡与副本")).to_be_visible()
        page.get_by_role("button", name="更多操作").first.click()
        page.get_by_role("menuitem", name="删除").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_text(root_name)).to_be_visible()
        dialog.get_by_role("button", name="删除").click()
        expect(page.get_by_text("已删除")).to_be_visible(timeout=8000)
        expect(page.locator('[class*="rootCard"]', has_text=root_name)).to_have_count(1, timeout=8000)

        # 8) 删除创建的根卡（含副本）
        delete_root(page, root_name)
        expect(page.locator('[class*="rootCard"]', has_text=root_name)).to_have_count(0)
        page.screenshot(path=str(SHOT_DIR / "5-deleted.png"))

        # 9) 世界观：新建 → 导出
        page.get_by_role("button", name=re.compile(r"^世界观")).click()
        page.get_by_role("button", name="＋ 新建世界观").click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_label("世界观名称").fill(world_name)
        dialog.get_by_label("世界观正文（content）").fill("冒烟测试世界观正文。")
        dialog.get_by_role("button", name="创建").click()
        expect(dialog).to_be_hidden(timeout=10000)
        expect(page.locator('[class*="worldRow"]', has_text=world_name)).to_have_count(1, timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "6-worlds.png"))

        with page.expect_download(timeout=10000) as download_info:
            page.locator('[class*="worldRow"]', has_text=world_name).get_by_role(
                "button", name="导出"
            ).click()
        download_info.value.save_as(str(world_exported))
        wdata = json.loads(world_exported.read_text(encoding="utf-8"))
        assert wdata.get("type") == "flyai-world", wdata.get("type")
        assert wdata["data"]["name"] == world_name

        # 10) 导入酒馆世界书（顶层 entries，走容错解析）
        worldbook_file.write_text(
            json.dumps(
                {
                    "name": book_name,
                    "entries": {"0": {"key": ["雾"], "content": "雾都设定内容", "comment": "雾都"}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        page.get_by_role("button", name="⇪ 导入世界书 / 世界观").click()
        page.locator('input[type="file"]').first.set_input_files(str(worldbook_file))
        expect(page.get_by_text("世界书导入成功")).to_be_visible(timeout=10000)
        expect(page.locator('[class*="worldRow"]', has_text=book_name)).to_have_count(1, timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "7-worldbook-imported.png"))

        # 11) 删除两张世界（只删本脚本创建的）
        delete_world(page, world_name)
        delete_world(page, book_name)

        # 12) 返回聊天（未选中会话，落在欢迎页）
        page.get_by_role("button", name="返回聊天").click()
        expect(page.get_by_role("heading", name="开始一段新的对话")).to_be_visible(timeout=8000)
        page.screenshot(path=str(SHOT_DIR / "8-back-to-chat.png"))

        # 13) 移动端：抽屉入口 + 内容库全宽布局
        mobile = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2
        )
        mpage = mobile.new_page()
        mpage.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        mpage.on("pageerror", lambda exc: console_errors.append(str(exc)))
        login(mpage)
        mpage.get_by_role("button", name="打开会话列表").click()
        mpage.get_by_role("button", name="内容库（角色卡 / 世界观）").click()
        expect(mpage.get_by_role("heading", name="内容库")).to_be_visible(timeout=8000)
        # 抽屉关闭有过渡动画，等落位后再截图
        mpage.wait_for_timeout(600)
        mpage.screenshot(path=str(SHOT_DIR / "9-mobile-lore.png"))
        mobile.close()

        browser.close()

    js_errors = [e for e in console_errors if not any(noise in e for noise in NOISE)]
    if js_errors or unexpected_responses:
        print("JS ERRORS:", js_errors)
        print("UNEXPECTED RESPONSES:", unexpected_responses)
        sys.exit(1)
    print("P2 lore smoke OK; screenshots:", SHOT_DIR)


if __name__ == "__main__":
    main()