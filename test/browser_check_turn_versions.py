# -*- coding: utf-8 -*-
"""CDP：历史消息编辑/重新生成 + < k/N > 版本切换（本地 8799，真实模型）。

场景（plan 第 8 节）：
1. 发 A、B 两轮 → 编辑 A：B 保留；edit 分页 1/2、reply 分页 1/2
2. B 点 🔄 → 编辑器原文发送 → reply 分页 2/2
3. A edit 分页切走再切回，B 不变
4. 刷新页面：分页/active 保留（上下文存 localStorage 跨 reload）
5. 群聊窗口无 ✏️，POST edit_message_id → 400
完成后清理测试卡/房间。
"""

import asyncio
import json
import urllib.request

import websockets


def http_json(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read().decode())


PHASE1_JS = r"""
new Promise(function (resolve) {
  var out = { steps: [] };
  function fail(e) { out.error = String(e && (e.message || e)); resolve(out); }
  function waitIdle() {
    return new Promise(function (res) {
      (function ch() { if (!state.busy) res(); else setTimeout(ch, 200); })();
    });
  }
  // 等到 finalize 的 loadThread 重画完（操作行 = 轮数）
  function waitTurns(n) {
    return new Promise(function (res) {
      (function ch() {
        if (el.messages.querySelectorAll('.turn-actions').length >= n) res();
        else setTimeout(ch, 200);
      })();
    });
  }
  function api(url, opts) {
    return fetch(url, opts || {}).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        return { status: r.status, json: j };
      });
    });
  }

  var ctx = { roomId: "", cids: [], tid: "", anchorA: "", anchorB: "" };

  api('/api/characters', {method: 'POST',
    headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
    body: JSON.stringify({name: 'ZZ版本测试甲'})})
  .then(function (r) { ctx.cids.push(r.json.id);
    return api('/api/characters', {method: 'POST',
      headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
      body: JSON.stringify({name: 'ZZ版本测试乙'})});
  }).then(function (r) { ctx.cids.push(r.json.id);
    return api('/api/rooms', {method: 'POST',
      headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
      body: JSON.stringify({character_ids: [ctx.cids[0]]})});
  }).then(function (r) {
    ctx.roomId = r.json.id;
    return refreshSessions();
  }).then(function () {
    selectSession(ctx.roomId);
    return new Promise(function (res) { setTimeout(res, 1500); });
  }).then(function () {
    ctx.tid = state.currentThreadId;
    el.input.value = '第一轮原文'; onSend();
    return waitIdle().then(function () { return waitTurns(1); });
  }).then(function () {
    el.input.value = '第二轮原文'; onSend();
    return waitIdle().then(function () { return waitTurns(2); });
  }).then(function () {
    return api('/api/rooms/' + ctx.roomId + '/threads/' + ctx.tid, {headers: authHeaders()});
  }).then(function (r) {
    ctx.anchorA = r.json.messages[0].id;
    ctx.anchorB = r.json.messages[2].id;
    localStorage.setItem('__turnTest', JSON.stringify(ctx));

    // ---- 编辑 A ----
    var wraps = el.messages.querySelectorAll('.msg.user');
    wraps[0].querySelector('.turn-edit-btn').click();
    var ta = wraps[0].querySelector('.inline-edit');
    if (!ta) throw new Error('editor did not open');
    ta.value = '第一轮改成新内容';
    wraps[0].querySelector('.inline-edit-actions .btn').click();
    return waitIdle().then(function () { return waitTurns(2); });
  }).then(function () {
    return api('/api/rooms/' + ctx.roomId + '/threads/' + ctx.tid, {headers: authHeaders()});
  }).then(function (r) {
    var t = r.json;
    out.after_edit = t.messages.map(function (m) { return m.content; });
    out.after_edit_len = t.messages.length;
    var entry = t.edits[ctx.anchorA];
    out.edit_count = entry.edits.length;
    out.edit_active = entry.edit_active + 1;
    out.reply_variants_A = entry.edits[entry.edit_active].reply_variants.length;
    out.pagers_after_edit = el.messages.querySelectorAll('.turn-pager').length;
    out.b_preserved = (t.messages[2].id === ctx.anchorB);

    // ---- 🔄 B 原文重生成 ----
    var actions = el.messages.querySelectorAll('.turn-actions');
    actions[1].querySelector('button').click();
    var bWrap = el.messages.querySelectorAll('.msg.user')[1];
    var bta = bWrap.querySelector('.inline-edit');
    out.b_editor_prefilled = bta ? bta.value : null;
    bta.value = '第二轮原文';
    bWrap.querySelector('.inline-edit-actions .btn').click();
    return waitIdle().then(function () { return waitTurns(2); });
  }).then(function () {
    return api('/api/rooms/' + ctx.roomId + '/threads/' + ctx.tid, {headers: authHeaders()});
  }).then(function (r) {
    var t = r.json;
    var slotB = t.edits[ctx.anchorB].edits[0];
    out.reply_variants_B = slotB.reply_variants.length;
    out.pagers_after_regen = el.messages.querySelectorAll('.turn-pager').length;
    function replyText(variant) {
      var t = variant.map(function (m) {
        return m.role === 'assistant' ? (m.content || '') : '';
      }).join('');
      return t;
    }
    out.B_reply_0 = replyText(slotB.reply_variants[0]);
    out.B_reply_1 = replyText(slotB.reply_variants[1]);

    // ---- A edit 分页 ‹ 再 › ----
    var aWrap = el.messages.querySelectorAll('.msg.user')[0];
    aWrap.querySelectorAll('.turn-pager .pager-btn')[0].click();
    return new Promise(function (res) { setTimeout(res, 800); });
  }).then(function () {
    return api('/api/rooms/' + ctx.roomId + '/threads/' + ctx.tid, {headers: authHeaders()});
  }).then(function (r) {
    out.A_after_back = r.json.messages[0].content;
    out.B_intact_during_switch = (r.json.messages[2].id === ctx.anchorB);
    var aWrap = el.messages.querySelectorAll('.msg.user')[0];
    aWrap.querySelectorAll('.turn-pager .pager-btn')[1].click();
    return new Promise(function (res) { setTimeout(res, 800); });
  }).then(function () {
    return api('/api/rooms/' + ctx.roomId + '/threads/' + ctx.tid, {headers: authHeaders()});
  }).then(function (r) {
    out.A_after_forward = r.json.messages[0].content;
    localStorage.setItem('__turnTest', JSON.stringify(ctx));
    out.ready_for_reload = true;
    resolve(out);
  }).catch(fail);
});
"""

PHASE2_JS = r"""
new Promise(function (resolve) {
  var out = {};
  function fail(e) { out.error = String(e && (e.message || e)); resolve(out); }
  function api(url, opts) {
    return fetch(url, opts || {}).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        return { status: r.status, json: j };
      });
    });
  }
  var ctx = JSON.parse(localStorage.getItem('__turnTest') || '{}');

  api('/api/rooms/' + ctx.roomId + '/threads/' + ctx.tid, {headers: authHeaders()})
  .then(function (r) {
    var t = r.json;
    out.after_reload = {
      A: t.messages[0].content,
      B: t.messages[2].content,
      edit_active: t.edits[ctx.anchorA].edit_active + 1,
    };
    // 打开 dm 窗口数 DOM 分页
    selectSession(ctx.roomId);
    return new Promise(function (res) { setTimeout(res, 1500); });
  }).then(function () {
    out.pagers_after_reload = el.messages.querySelectorAll('.turn-pager').length;

    // ---- 群聊 ----
    return api('/api/rooms/' + ctx.roomId + '/members', {method: 'POST',
      headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
      body: JSON.stringify({character_id: ctx.cids[1]})});
  }).then(function () {
    var members = state.roomMembers.filter(function (m) { return !m.is_user; });
    return api('/api/rooms/' + ctx.roomId + '/groups', {method: 'POST',
      headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
      body: JSON.stringify({member_ids: members.map(function (m) { return m.id; })})});
  }).then(function (r) {
    var gid = r.json.id;
    loadThread(gid);
    return new Promise(function (res) { setTimeout(function () { res(gid); }, 1200); });
  }).then(function (gid) {
    out.group_has_edit_btn = !!el.messages.querySelector('.turn-edit-btn');
    return fetch('/api/chat', {method: 'POST',
      headers: Object.assign({'Content-Type': 'application/json'}, authHeaders()),
      body: JSON.stringify({
        session_id: ctx.roomId, thread_id: gid,
        edit_message_id: ctx.anchorA, message: 'x',
      })}).then(function (rr) { out.group_edit_status = rr.status; });
  }).then(function () {
    // 清理
    return fetch('/api/sessions/' + ctx.roomId, {method: 'DELETE', headers: authHeaders()});
  }).then(function () {
    return fetch('/api/characters/' + ctx.cids[0], {method: 'DELETE', headers: authHeaders()});
  }).then(function () {
    return fetch('/api/characters/' + ctx.cids[1], {method: 'DELETE', headers: authHeaders()});
  }).then(function () {
    localStorage.removeItem('__turnTest');
    out.cleaned = true;
    resolve(out);
  }).catch(fail);
});
"""


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

        p1 = await evaljs(PHASE1_JS)
        print("PHASE1:", json.dumps(p1, ensure_ascii=False, indent=1))

        # 刷新（前端真实导航）
        await csend("Page.navigate", {"url": "http://127.0.0.1:8799/"})
        await asyncio.sleep(2.5)

        p2 = await evaljs(PHASE2_JS)
        print("PHASE2:", json.dumps(p2, ensure_ascii=False, indent=1))

        # ---- 断言 ----
        assert not p1.get("error"), p1.get("error")
        assert p1["after_edit_len"] == 4
        assert p1["after_edit"][0] == "第一轮改成新内容"
        assert p1["after_edit"][2] == "第二轮原文"
        assert p1["edit_count"] == 2 and p1["edit_active"] == 2
        assert p1["reply_variants_A"] == 1
        # 仅 edit 分页；A 只有 1 个回复不挂 reply 分页
        assert p1["pagers_after_edit"] == 1
        assert p1["b_preserved"] is True
        assert p1["b_editor_prefilled"] == "第二轮原文"
        assert p1["reply_variants_B"] == 2
        # A edit 分页 + B reply 分页
        assert p1["pagers_after_regen"] == 2
        assert p1["B_reply_0"] != p1["B_reply_1"]
        assert p1["B_reply_1"]
        assert p1["A_after_back"] == "第一轮原文"
        assert p1["B_intact_during_switch"] is True
        assert p1["A_after_forward"] == "第一轮改成新内容"

        assert not p2.get("error"), p2.get("error")
        assert p2["after_reload"]["A"] == "第一轮改成新内容"
        assert p2["after_reload"]["edit_active"] == 2
        assert p2["pagers_after_reload"] == 2
        assert p2["group_has_edit_btn"] is False
        assert p2["group_edit_status"] == 400
        assert p2["cleaned"] is True
        print("ALL ASSERTIONS PASSED")
        rt.cancel()


asyncio.run(main())
