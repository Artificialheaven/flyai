"""CDP：验证甘雨会话历史在修复后能渲染全部 AI 生图（线上真实数据）。

背景：旧版 routes_chat 曾把「带工具调用的空白 assistant 中间消息」误标
hidden，导致刷新后工具结果（图片）丢失。修复包含：
- routes_chat：清理空白回合时排除带 tool_calls 的消息；
- agent/loop：纯空白思考落库为 None；
- app.js renderHistory：hidden 的 assistant 若带 tool_calls 仍渲染。

本脚本在页面里模拟修复版条件（去掉误标的 hidden）后调用现有
renderHistory，断言 3 张生图均创建并加载成功。
"""

import asyncio
import json

import urllib.request

import websockets

SID = "e4258609b99140229ad9d67e5c4afdd0"


def http_json(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read().decode())


async def main():
    ver = http_json("http://127.0.0.1:9222/json/version")
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
        await csend("Network.setCacheDisabled", {"cacheDisabled": True})
        await csend("Page.navigate", {"url": "http://fly.eastcloud.top/"})
        await asyncio.sleep(0.6)
        await csend("Runtime.evaluate", {"expression": "localStorage.setItem('flyai-uid','admin')"})
        await csend("Page.navigate", {"url": "http://fly.eastcloud.top/"})
        await asyncio.sleep(3.0)

        result = await evaljs("""
        fetch('/api/sessions/""" + SID + """',
              {headers: {'X-User-Id': state.userId}})
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var msgs = d.threads[0].messages;
            msgs.forEach(function (m) {
              // 等价于新版 renderHistory：带工具调用的 hidden 消息恢复渲染
              if (m.role === 'assistant' && m.tool_calls && m.tool_calls.length) m.hidden = false;
            });
            renderHistory(msgs);
            return new Promise(function (resolve) {
              setTimeout(function () {
                var imgs = Array.from(document.querySelectorAll('.gen-img'));
                resolve({
                  gen_imgs: imgs.length,
                  loaded: imgs.filter(function (i) { return i.naturalWidth > 0; }).length,
                  srcs: imgs.map(function (i) { return i.src.split('/').pop(); }),
                });
              }, 3000);
            });
          })
        """)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        assert result["gen_imgs"] == 3 and result["loaded"] == 3
        rt.cancel()


asyncio.run(main())
