"""CDP：验证成员显示名别名优先（本地服务）。

建一张根卡 + 一个别名「小雨」的副本，构造座位对象后验证：
- memberDisplayName 命中别名
- makeAssistantContainer().setSpeaker 渲染的标题为别名
完成后清理测试卡。
"""

import asyncio
import json

import urllib.request

import websockets


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
        await csend("Page.navigate", {"url": "http://127.0.0.1:8799/"})
        await asyncio.sleep(0.6)
        await csend("Runtime.evaluate", {"expression": "localStorage.setItem('flyai-uid','admin')"})
        await csend("Page.navigate", {"url": "http://127.0.0.1:8799/"})
        await asyncio.sleep(2.5)

        result = await evaljs("""
        fetch('/api/characters', {
          method: 'POST', headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
          body: JSON.stringify({name: 'ZZ甘雨'})
        }).then(function (r) { return r.json(); }).then(function (root) {
          return fetch('/api/characters', {
            method: 'POST', headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
            body: JSON.stringify({name: 'ZZ甘雨', alias: '小雨', parent_id: root.id})
          }).then(function (r) { return r.json(); }).then(function (variant) {
            return {root: root, variant: variant};
          });
        }).then(function (ids) {
          return refreshLore().then(function () {
            var seat = {id: 'seat1', name: 'ZZ甘雨', character_id: ids.variant.id};
            var a = makeAssistantContainer();
            a.setSpeaker('ZZ甘雨', seat);
            var out = {
              member_display: memberDisplayName(seat),
              speaker_rendered: a.idEl.querySelector('.spk-name').textContent,
              no_alias_seat: memberDisplayName({id: 's2', name: '原名', character_id: ''}),
            };
            // 清理
            return fetch('/api/characters/' + ids.root.id,
                         {method: 'DELETE', headers: authHeaders()}).then(function () { return out; });
          });
        })
        """)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        assert result["member_display"] == "小雨"
        assert result["speaker_rendered"] == "小雨"
        assert result["no_alias_seat"] == "原名"
        rt.cancel()


asyncio.run(main())
