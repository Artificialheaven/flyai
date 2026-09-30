"""CDP 端到端：登录令牌 / 越权访问 / 管理员改密 / 登录限速。

管理员凭据只在脚本内读取，不打印；测试用户用后即删。
"""

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import websockets

BASE = "http://127.0.0.1:8011"
results = []


async def main():
    # 临时服务器自带测试配置（见 cdp_auth_server.py），不读取真实 config
    admin_name, admin_pwd = "admin", "cdptest"

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
        r = await cdp("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result", {})
        if "exceptionDetails" in res:
            raise AssertionError(json.dumps(res["exceptionDetails"])[:300])
        return res.get("result", {}).get("value")

    async def goto(path):
        await cdp("Page.navigate", {"url": BASE + path})
        await asyncio.sleep(1.5)

    await cdp("Page.enable")
    await cdp("Runtime.enable")

    # ---- 1) 登录页：用户选择区移除 + /api/auth/names 已下线 ----
    await goto("/login")
    assert await js("!!document.getElementById('login-users')") is False
    results.append("登录页用户选择功能已移除")
    code = await js("fetch('/api/auth/names').then(function(r){return r.status;})")
    assert code == 404, code
    results.append("names 接口已移除 -> 404")

    # ---- 3) 走真实 UI 登录 ----
    await js(
        "document.getElementById('login-name').value=%s;"
        "document.getElementById('login-password').value=%s;"
        "document.getElementById('btn-login').click();'ok'"
        % (json.dumps(admin_name), json.dumps(admin_pwd))
    )
    await asyncio.sleep(2.5)
    assert await js("location.pathname") == "/"
    admin_tok = await js("localStorage.getItem('flyai-token')")
    assert admin_tok and len(admin_tok) > 20
    results.append("UI 登录成功 -> token 已写入，进入主界面")

    # ---- 4) 旧 x-user-id 头不再能访问接口 ----
    code2 = await js(
        "fetch('/api/auth/me',{headers:{'X-User-Id':'admin'}})"
        ".then(function(r){return r.status;})"
    )
    assert code2 == 401, code2
    results.append("X-User-Id: admin 头不再被接受 -> 401")

    # ---- 5) 管理员建测试用户 + 改密 + 旧会话被踢 ----
    created = await js(
        "fetch('/api/auth/users',{method:'POST',"
        "headers:{'Content-Type':'application/json','Authorization':'Bearer '+%s},"
        "body:JSON.stringify({name:'CDP-TEST-USER',password:'pw1',level:3})})"
        ".then(function(r){return r.json();})" % json.dumps(admin_tok)
    )
    test_uid = created["id"]
    tres = await js(
        "fetch('/api/auth/login',{method:'POST',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({name:'CDP-TEST-USER',password:'pw1'})})"
        ".then(function(r){return r.json();})"
    )
    test_tok = tres["token"]
    rr = await js(
        "fetch('/api/auth/users/'+%s+'/password',{method:'PATCH',"
        "headers:{'Content-Type':'application/json','Authorization':'Bearer '+%s},"
        "body:JSON.stringify({password:'pw2'})}).then(function(r){return r.json();})"
        % (json.dumps(test_uid), json.dumps(admin_tok))
    )
    assert rr["revoked_sessions"] == 1, rr
    mc = await js(
        "fetch('/api/auth/me',{headers:{'Authorization':'Bearer '+%s}})"
        ".then(function(r){return r.status;})" % json.dumps(test_tok)
    )
    assert mc == 401, mc
    oc = await js(
        "fetch('/api/auth/login',{method:'POST',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({name:'CDP-TEST-USER',password:'pw1'})})"
        ".then(function(r){return r.status;})"
    )
    assert oc == 401, oc
    nc = await js(
        "fetch('/api/auth/login',{method:'POST',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({name:'CDP-TEST-USER',password:'pw2'})})"
        ".then(function(r){return r.json();})"
    )
    assert nc.get("token"), nc
    results.append("管理员改密 -> 旧会话吊销 / 旧密码拒 / 新密码可登")

    # ---- 6) logout 真正失效 ----
    await js(
        "fetch('/api/auth/logout',{method:'POST',"
        "headers:{'Authorization':'Bearer '+%s}})" % json.dumps(nc["token"])
    )
    lc = await js(
        "fetch('/api/auth/me',{headers:{'Authorization':'Bearer '+%s}})"
        ".then(function(r){return r.status;})" % json.dumps(nc["token"])
    )
    assert lc == 401, lc
    results.append("logout 后令牌立即失效")

    # ---- 7) 无 token / 伪造 token 访问 / → 踢回登录页 ----
    await js("localStorage.removeItem('flyai-token');'ok'")
    await goto("/")
    assert await js("location.pathname") == "/login"
    await js("localStorage.setItem('flyai-token','forged-garbage');'ok'")
    await goto("/")
    assert await js("location.pathname") == "/login"
    results.append("无 token / 伪造 token 访问 / -> 跳转登录页")

    # ---- 8) 登录限速（放最后：会锁 admin key）----
    for i in range(5):
        c = await js(
            "fetch('/api/auth/login',{method:'POST',"
            "headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({name:%s,password:'wrong'})})"
            ".then(function(r){return r.status;})" % json.dumps(admin_name)
        )
        assert c == 401, (i, c)
    c = await js(
        "fetch('/api/auth/login',{method:'POST',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({name:%s,password:%s})}).then(function(r){return r.status;})"
        % (json.dumps(admin_name), json.dumps(admin_pwd))
    )
    assert c == 429, c
    results.append("连续 5 次错密码 -> 第 6 次（即使密码正确）429 锁定")

    # 清理测试用户（限速只挡 login，不挡用户管理）
    await js(
        "fetch('/api/auth/users/'+%s,{method:'DELETE',"
        "headers:{'Authorization':'Bearer '+%s}})"
        % (json.dumps(test_uid), json.dumps(admin_tok))
    )

    print("ALL OK")
    for r in results:
        print("  -", r)


asyncio.run(main())
