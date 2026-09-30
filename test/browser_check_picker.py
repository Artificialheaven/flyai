"""Headless CDP check for the new two-level card picker and lore menu.

Verifies DOM behavior (no visual check): root grid -> variant rows,
detail dialog, selection tray, lore nav; reports console errors.
Does NOT create rooms or modify cards (dialogs cancelled at the end).
"""

import asyncio
import json
import urllib.request

BASE = "http://127.0.0.1:8799"

import websockets


def http_json(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read().decode())


async def main():
    ver = http_json("http://127.0.0.1:9222/json/version")
    events = []
    async with websockets.connect(ver["webSocketDebuggerUrl"], max_size=None) as ws:
        counter = [0]
        pending = {}

        async def send(method, params=None, session_id=None):
            counter[0] += 1
            mid = counter[0]
            fut = asyncio.get_event_loop().create_future()
            pending[mid] = fut
            msg = {"id": mid, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            await ws.send(json.dumps(msg))
            return await fut

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                if "id" in m and m["id"] in pending:
                    pending[m["id"]].set_result(m.get("result"))
                elif "method" in m:
                    events.append(m)

        rt = asyncio.create_task(reader())
        tgt = await send("Target.createTarget", {"url": "about:blank"})
        att = await send("Target.attachToTarget", {"targetId": tgt["targetId"], "flatten": True})
        sid = att["sessionId"]

        async def csend(method, params=None):
            return await send(method, params, sid)

        async def evaljs(expr):
            r = await csend("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            res = r.get("result", {})
            if res.get("subtype") == "error":
                raise RuntimeError(res.get("description"))
            return res.get("value")

        await csend("Page.enable")
        await csend("Runtime.enable")
        await csend("Log.enable")
        await csend("Network.enable")
        await csend("Network.setCacheDisabled", {"cacheDisabled": True})

        await csend("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(0.6)
        await csend("Runtime.evaluate", {"expression": "localStorage.setItem('flyai-uid','admin')"})
        await csend("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(2.5)

        results = {}

        # ---- 1. 新建对话：一级根卡网格 ----
        await evaljs("document.getElementById('btn-new-session').click()")
        await asyncio.sleep(0.4)
        results["dialog_open"] = await evaljs("document.getElementById('newchat-dialog').open")
        results["root_cards"] = await evaljs("document.querySelectorAll('#newchat-pick .root-card').length")
        results["back_hidden"] = await evaljs(
            "document.getElementById('newchat-back').className.includes('hidden')")
        results["create_disabled"] = await evaljs(
            "document.getElementById('btn-newchat-create').disabled")
        results["first_root"] = await evaljs(
            "document.querySelector('#newchat-pick .root-card .rc-name').textContent")

        # ---- 2. 进入二级副本列表 ----
        await evaljs("document.querySelector('#newchat-pick .root-card').click()")
        await asyncio.sleep(0.3)
        results["variants_shown"] = await evaljs(
            "document.querySelectorAll('#newchat-pick .variant-row').length")
        results["tags"] = await evaljs(
            "JSON.stringify(Array.from(document.querySelectorAll('#newchat-pick .vr-tag'))"
            ".map(x=>x.textContent))")
        results["flat_buttons_per_row"] = await evaljs(
            "JSON.stringify(Array.from(document.querySelectorAll('#newchat-pick .variant-row'))"
            ".map(r=>r.querySelectorAll('.flat-btn').length))")
        results["back_visible"] = await evaljs(
            "document.getElementById('newchat-back').className.includes('hidden') === false")

        # ---- 3. ... 详情弹窗 ----
        await evaljs(
            "Array.from(document.querySelectorAll('#newchat-pick .flat-btn'))"
            ".find(b=>b.textContent==='...').click()")
        await asyncio.sleep(0.3)
        results["detail_open"] = await evaljs("document.getElementById('card-detail-dialog').open")
        results["detail_has_ref"] = await evaljs(
            "document.getElementById('card-detail-body').innerText.includes('标识：')")
        results["detail_actions"] = await evaljs(
            "JSON.stringify(Array.from(document.querySelectorAll('#card-detail-actions .btn'))"
            ".map(b=>b.textContent))")
        await evaljs(
            "Array.from(document.querySelectorAll('#card-detail-actions .btn'))"
            ".find(b=>b.textContent==='关 闭').click()")
        await asyncio.sleep(0.2)

        # ---- 4. 选择 → 托盘出现，开始按钮可用；返回根级托盘仍在 ----
        await evaljs("document.querySelector('#newchat-pick .vr-pick').click()")
        await asyncio.sleep(0.2)
        results["tray_visible"] = await evaljs(
            "document.getElementById('newchat-tray').className.includes('hidden') === false")
        results["chips"] = await evaljs(
            "document.querySelectorAll('#newchat-tray .pick-chip').length")
        results["create_enabled"] = await evaljs(
            "document.getElementById('btn-newchat-create').disabled === false")
        await evaljs("document.getElementById('newchat-back').click()")
        await asyncio.sleep(0.2)
        results["tray_persists_at_roots"] = await evaljs(
            "document.getElementById('newchat-tray').className.includes('hidden') === false")
        # 取消，不创建房间
        await evaljs("document.getElementById('btn-newchat-cancel').click()")
        await asyncio.sleep(0.2)
        results["dialog_closed"] = await evaljs(
            "document.getElementById('newchat-dialog').open === false")

        # ---- 5. 内容菜单两级导航 ----
        await evaljs("document.getElementById('btn-lore').click()")
        await asyncio.sleep(0.4)
        results["lore_open"] = await evaljs("document.getElementById('lore-dialog').open")
        results["lore_root_cards"] = await evaljs(
            "document.querySelectorAll('#lore-char-body .root-card').length")
        await evaljs("document.querySelector('#lore-char-body .root-card').click()")
        await asyncio.sleep(0.3)
        results["lore_variant_rows"] = await evaljs(
            "document.querySelectorAll('#lore-char-body .lore-variant-row').length")
        results["lore_row_buttons"] = await evaljs(
            "JSON.stringify(Array.from(document.querySelectorAll('#lore-char-body .lore-variant-row'))"
            ".map(r=>Array.from(r.querySelectorAll('button')).map(b=>b.textContent)))")
        results["new_variant_btn"] = await evaljs(
            "Array.from(document.querySelectorAll('#lore-char-body button'))"
            ".some(b=>b.textContent.includes('新建副本'))")
        await evaljs(
            "Array.from(document.querySelectorAll('#lore-dialog .btn'))"
            ".find(b=>b.textContent==='完成').click()")
        await asyncio.sleep(0.2)

        print(json.dumps(results, ensure_ascii=False, indent=1))

        print("== errors captured ==")
        found = False
        for m in events:
            p = m.get("params", {})
            if m["method"] == "Runtime.exceptionThrown":
                d = p["exceptionDetails"]
                print("EXCEPTION:", d.get("text"), (d.get("exception") or {}).get("description"))
                found = True
            elif m["method"] == "Log.entryAdded" and p["entry"]["level"] == "error":
                print("LOG ERROR:", p["entry"]["text"])
                found = True
            elif m["method"] == "Runtime.consoleAPICalled" and p["type"] == "error":
                print("CONSOLE ERROR:", json.dumps(p["args"], ensure_ascii=False)[:500])
                found = True
        if not found:
            print("(none)")
        rt.cancel()


asyncio.run(main())
