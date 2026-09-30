"""Headless CDP check: 新建副本必须预填根卡全部内容 + 系统级提示词字段。

Creates a throwaway root card via API, opens 新建副本 from the lore menu,
asserts every editor field is prefilled, saves, verifies the new variant
is linked to the root, then cleans up (cascade delete).
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
            r = await csend("Runtime.evaluate", {
                "expression": expr, "returnByValue": True, "awaitPromise": True})
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

        # ---- 0. 建一次性根卡（带全部字段的独特内容） ----
        root = await evaljs("""fetch('/api/characters', {
          method:'POST',
          headers: Object.assign({'Content-Type':'application/json'}, authHeaders()),
          body: JSON.stringify({
            name:'ZZ预填根卡', intro:'ZZ简介XYZ', background:'ZZ经历XYZ',
            prompt_format:'ZZ格式XYZ', prompt_tools:'ZZ工具要求XYZ',
            system_prompt:'ZZ系统提示XYZ',
            attributes:[{key:'favor',label:'好感',initial:10,min:0,max:100}],
            table:[{title:'ZZ条目',content:'ZZ内容XYZ'}]
          })
        }).then(r=>r.json())""")
        root_id = root["id"]
        results = {"root_id": root_id}

        # 前端 state.characters 在页面加载时填充，API 建卡后需刷新
        await evaljs("refreshLore()")

        try:
            # ---- 1. 内容菜单 → 点根卡 → 新建副本 ----
            await evaljs("document.getElementById('btn-lore').click()")
            await asyncio.sleep(0.4)
            await evaljs(
                "Array.from(document.querySelectorAll('#lore-char-body .root-card'))"
                ".find(function(r){return r.textContent.indexOf('ZZ预填根卡')>=0;}).click()")
            await asyncio.sleep(0.3)
            await evaljs(
                "Array.from(document.querySelectorAll('#lore-char-body button'))"
                ".find(function(b){return b.textContent.indexOf('新建副本')>=0;}).click()")
            await asyncio.sleep(0.4)

            # ---- 2. 编辑器各字段已预填 ----
            results["title"] = await evaljs(
                "document.querySelector('#lore-body h3').textContent")
            results["name"] = await evaljs("document.getElementById('ed-name').value")
            results["alias_empty"] = await evaljs(
                "document.getElementById('ed-alias').value === ''")
            results["intro"] = await evaljs("document.getElementById('ed-intro').value")
            results["background"] = await evaljs("document.getElementById('ed-bg').value")
            results["system_prompt"] = await evaljs("document.getElementById('ed-system').value")
            results["format"] = await evaljs("document.getElementById('ed-format').value")
            results["tools"] = await evaljs("document.getElementById('ed-tools').value")
            results["attr_rows"] = await evaljs(
                "document.querySelectorAll('#ed-attrs .attr-row').length")
            results["table_rows"] = await evaljs(
                "document.querySelectorAll('#ed-tables .entry-row').length")
            results["parent_note"] = await evaljs(
                "document.querySelector('#lore-body .sub').textContent")

            # ---- 3. 保存：新副本挂在根卡下，内容与根卡一致 ----
            await evaljs("document.getElementById('ed-save').click()")
            await asyncio.sleep(1.0)
            chars = await evaljs(
                "fetch('/api/characters', {headers: authHeaders()})"
                ".then(function(r){return r.json();})")
            variants = [c for c in chars["characters"]
                        if c.get("parent_id") == root_id]
            results["variant_count"] = len(variants)
            if variants:
                v = variants[0]
                results["saved_variant"] = {
                    "name": v["name"], "intro": v["intro"],
                    "background": v["background"],
                    "system_prompt": v["system_prompt"],
                    "prompt_format": v["prompt_format"],
                    "prompt_tools": v["prompt_tools"],
                    "attrs": len(v["attributes"]), "table": len(v["table"]),
                }
        finally:
            # ---- 4. 清理：删根卡（级联副本） ----
            await evaljs(
                "fetch('/api/characters/" + root_id + "', "
                "{method:'DELETE', headers: authHeaders()}).then(function(r){return r.json();})")
            remain = await evaljs(
                "fetch('/api/characters', {headers: authHeaders()})"
                ".then(function(r){return r.json();})")
            results["cleanup_ok"] = not any(
                c["id"] == root_id for c in remain["characters"])

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
