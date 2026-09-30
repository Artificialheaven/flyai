"""Headless CDP smoke check: load FlyAI as admin, capture console errors,
then open the empty (no-card) session and dump page state.

Run against: chrome --headless=new --remote-debugging-port=9222 ...
and FlyAI on 127.0.0.1:8799.
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
            return r.get("result", {}).get("value")

        await csend("Page.enable")
        await csend("Runtime.enable")
        await csend("Log.enable")

        await csend("Page.navigate", {"url": BASE + "/login"})
        await asyncio.sleep(0.8)
        await csend("Runtime.evaluate", {"expression": "localStorage.setItem('flyai-uid','admin')"})

        await csend("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(2.5)

        print("== initial page ==")
        print(await evaljs(
            "JSON.stringify({sessions: document.querySelectorAll('.session-item').length,"
            " meta: document.getElementById('session-meta').innerText.slice(0,200)})"))

        titles = await evaljs(
            "JSON.stringify(Array.from(document.querySelectorAll('.session-item'))"
            ".map(x=>({t:x.textContent.trim(), active:x.className.includes('active')})))")
        print("session items:", titles)

        # create a brand-new empty session (no card, no messages) and select it
        await evaljs(
            "fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json','X-User-Id':'admin'},body:'{}'})"
            ".then(r=>r.json()).then(function(s){window.__newEmpty=s.id})")
        await asyncio.sleep(0.5)
        new_id = await evaljs("window.__newEmpty")
        print("new empty sid:", new_id)
        await evaljs("refreshSessions()")
        await asyncio.sleep(0.5)
        await evaljs(
            "selectSession(window.__newEmpty)")
        await asyncio.sleep(1.5)
        print("== after opening empty no-card session ==")
        print(await evaljs(
            "JSON.stringify({curId: window.state? 'n/a': null,"
            " meta: document.getElementById('session-meta').innerText.slice(0,300),"
            " msgRows: document.querySelectorAll('#messages .msg').length,"
            " attach: document.getElementById('attach-preview').className,"
            " errbars: Array.from(document.querySelectorAll('.msg-bar')).map(b=>b.innerText),"
            " activeItems: document.querySelectorAll('.session-item.active').length})"))

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
