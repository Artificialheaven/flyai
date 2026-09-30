"""CDP 验证：token chip 持久化（刷新不丢）+ k/M 紧凑格式。

凭据只在脚本内读取并用于页面 fetch，不打印。
"""

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import websockets

BASE = "http://127.0.0.1:8011"


def _admin():
    with open("config/config.json", encoding="utf-8") as f:
        a = json.load(f)["auth"]["admin"]
    return a["username"], a.get("password", "")


async def main():
    username, password = _admin()

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
        res = r.get("result", {}).get("result", {})
        if "exceptionDetails" in r.get("result", {}):
            raise AssertionError(json.dumps(r["result"]["exceptionDetails"])[:400])
        return res.get("value")

    await cdp("Page.enable")
    await cdp("Runtime.enable")
    await cdp("Page.navigate", {"url": BASE + "/login"})
    await asyncio.sleep(1.2)

    login_js = (
        "fetch('/api/auth/login',{method:'POST',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({name:%s,password:%s})}).then(function(r){return r.json();})"
        % (json.dumps(username), json.dumps(password))
    )
    login_res = await js(login_js)
    uid = login_res["id"]
    role = login_res.get("role", "admin")
    level = login_res.get("level", 256)
    await js(
        "localStorage.setItem('flyai-uid',%s);localStorage.setItem('flyai-role',%s);"
        "localStorage.setItem('flyai-level',%s);'ok'"
        % (json.dumps(uid), json.dumps(role), json.dumps(level))
    )

    await cdp("Page.navigate", {"url": BASE + "/"})
    await asyncio.sleep(2.0)

    # 清掉上次失败留下的同名临时会话
    old_list = await js(
        "fetch('/api/sessions',{headers:{'X-User-Id':%s}})"
        ".then(function(r){return r.json();})" % json.dumps(uid)
    )
    for old in old_list.get("sessions", []):
        if old.get("title") == "CDP-token-test":
            await js(
                "fetch('/api/sessions/'+%s,{method:'DELETE',headers:{'X-User-Id':%s}})"
                % (json.dumps(old["id"]), json.dumps(uid))
            )

    # 建一个临时会话
    hdr = "{'Content-Type':'application/json','X-User-Id':%s}" % json.dumps(uid)
    created = await js(
        "fetch('/api/sessions',{method:'POST',headers:%s,"
        "body:JSON.stringify({title:'CDP-token-test'})}).then(function(r){return r.json();})" % hdr
    )
    sid = created["id"]

    # 模拟一轮结束后路由层写入的持久用量（不花 token）
    path = os.path.join("data", uid, "sessions", sid + ".json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["token_usage"] = {
        "prompt_tokens": 8000,
        "completion_tokens": 2500,
        "total_tokens": 10500,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 打开会话 → chip 应为 k 级紧凑格式（先同步列表：会话是页面外建的）
    await js("refreshSessions();'go'")
    await asyncio.sleep(1.0)
    await js("selectSession(%s);'go'" % json.dumps(sid))
    await asyncio.sleep(1.5)
    chip = await js("document.querySelector('.token-chip').textContent")
    tip = await js("document.querySelector('.token-chip').title")
    assert chip == "🪙 10.5k tokens", chip
    assert "10,500" in tip and "8,000" in tip and "2,500" in tip, tip

    # 刷新后仍在 → 持久化
    await cdp("Page.reload", {})
    await asyncio.sleep(2.5)
    await js("selectSession(%s);'go'" % json.dumps(sid))
    await asyncio.sleep(1.5)
    chip2 = await js("document.querySelector('.token-chip').textContent")
    assert chip2 == "🪙 10.5k tokens", chip2

    # M 级（注入 state 验证渲染；7,000,000）
    await js(
        "state.tokenTotals[%s]=7000000;state.tokenDetails[%s]={p:5000000,c:2000000};"
        "renderSessionMeta();'ok'" % (json.dumps(sid), json.dumps(sid))
    )
    chip3 = await js("document.querySelector('.token-chip').textContent")
    assert chip3 == "🪙 7M tokens", chip3

    # 清理临时会话
    await js(
        "fetch('/api/sessions/'+%s,{method:'DELETE',headers:{'X-User-Id':%s}})"
        ".then(function(r){return r.json();})"
        % (json.dumps(sid), json.dumps(uid))
    )

    print("ALL OK")
    print("  k 级 chip :", chip)
    print("  tooltip   :", tip.replace("\n", " | "))
    print("  刷新后    :", chip2)
    print("  M 级 chip :", chip3)


asyncio.run(main())
