"""CDP 验证：零用量新会话 chip 也显示 🪙 0 tokens。"""

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import websockets

BASE = "http://127.0.0.1:8011"


async def main():
    with open("config/config.json", encoding="utf-8") as f:
        a = json.load(f)["auth"]["admin"]
    username, password = a["username"], a.get("password", "")

    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
    page = next(t for t in tabs if t["type"] == "page")
    ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=8 * 1024 * 1024)
    counter = [0]

    async def cdp(method, params=None):
        counter[0] += 1
        mid = counter[0]
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == mid:
                return msg

    async def js(expr):
        r = await cdp(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
        )
        res = r.get("result", {})
        if "exceptionDetails" in res:
            raise AssertionError(json.dumps(res["exceptionDetails"])[:400])
        return res.get("result", {}).get("value")

    await cdp("Page.enable")
    await cdp("Runtime.enable")
    await cdp("Page.navigate", {"url": BASE + "/login"})
    await asyncio.sleep(1.2)

    login_res = await js(
        "fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({name:%s,password:%s})}).then(function(r){return r.json();})"
        % (json.dumps(username), json.dumps(password))
    )
    uid = login_res["id"]
    await js(
        "localStorage.setItem('flyai-uid',%s);localStorage.setItem('flyai-role','admin');"
        "localStorage.setItem('flyai-level','256');'ok'" % json.dumps(uid)
    )

    await cdp("Page.navigate", {"url": BASE + "/"})
    await asyncio.sleep(2.0)

    # 清理可能的残留
    lst = await js(
        "fetch('/api/sessions',{headers:{'X-User-Id':%s}}).then(function(r){return r.json();})"
        % json.dumps(uid)
    )
    for old in lst.get("sessions", []):
        if old.get("title") in ("CDP-zero-test", "CDP-token-test"):
            await js(
                "fetch('/api/sessions/'+%s,{method:'DELETE',headers:{'X-User-Id':%s}})"
                % (json.dumps(old["id"]), json.dumps(uid))
            )

    # 新建零用量会话并打开
    hdr = "{'Content-Type':'application/json','X-User-Id':%s}" % json.dumps(uid)
    created = await js(
        "fetch('/api/sessions',{method:'POST',headers:%s,body:JSON.stringify({title:'CDP-zero-test'})})"
        ".then(function(r){return r.json();})" % hdr
    )
    sid = created["id"]
    await js("refreshSessions()" )
    await asyncio.sleep(1.0)
    await js("selectSession(%s)" % json.dumps(sid))
    await asyncio.sleep(1.5)

    chip = await js("document.querySelector('.token-chip').textContent")
    assert chip == "🪙 0 tokens", chip

    await js(
        "fetch('/api/sessions/'+%s,{method:'DELETE',headers:{'X-User-Id':%s}})"
        % (json.dumps(sid), json.dumps(uid))
    )
    print("ALL OK ->", chip)


asyncio.run(main())
