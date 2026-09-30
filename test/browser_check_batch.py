# -*- coding: utf-8 -*-
"""CDP：本批次前端全链路（本地 8011，fake complete）。

A. 角色编辑器：mes_example / 常驻 渲染 → 界面改范例 + 新增常驻条目 → 保存往返
   设置弹窗：7 个新字段默认值 → 预算保存往返 → 还原
B. 房间压缩：提案 diff 渲染（成员分区/新增/更新/未变折叠）→ 确认前无写入
   → 确认写入 → 标题顺序/pinned 保留 → 清理
"""

import asyncio
import json
import os
import sys

import websockets

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PHASE_JS = r"""
new Promise(function (resolve) {
  var out = { steps: [] };
  function api(url, opts) {
    return fetch(url, opts || {}).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) {
        return { status: r.status, json: j };
      });
    });
  }
  var ctx = { cid: "", roomId: "" };
  function cleanup() {
    var jobs = [];
    if (ctx.roomId) jobs.push(api('/api/sessions/' + ctx.roomId, { method: 'DELETE', headers: authHeaders() }));
    if (ctx.cid) jobs.push(api('/api/characters/' + ctx.cid, { method: 'DELETE', headers: authHeaders() }));
    return Promise.all(jobs);
  }
  function fail(e) {
    out.error = String(e && (e.message || e));
    cleanup().then(function () { resolve(out); });
  }
  function waitFor(fn, tag) {
    return new Promise(function (res, rej) {
      var n = 0;
      (function ch() {
        var ok = false;
        try { ok = fn(); } catch (e) {}
        if (ok) { res(); return; }
        n += 150;
        if (n > 20000) { rej(new Error('waitFor timeout: ' + tag)); return; }
        setTimeout(ch, 150);
      })();
    });
  }

  api('/api/characters', {
    method: 'POST',
    headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
    body: JSON.stringify({
      name: 'ZB批量测试',
      intro: '旧简介内容',
      mes_example: '{{user}}：来了？\n<START>\n{{char}}：不然呢。',
      table: [
        { title: '铁则', content: '旧铁则内容', pinned: true },
        { title: '普通条目', content: '必须保留', pinned: false }
      ]
    })
  }).then(function (r) {
    ctx.cid = r.json.id;
    return api('/api/characters');
  }).then(function (r) {
    var c = r.json.characters.find(function (x) { return x.id === ctx.cid; });
    out.stage = 'editor';
    characterEditor(c, false);
    return new Promise(function (res) { setTimeout(res, 300); });
  }).then(function () {
    var me = document.getElementById('ed-mes-example');
    out.editor_mes = me ? me.value : null;
    var pinBoxes = document.querySelectorAll('.entry-row .ed-pinned');
    out.editor_pins = Array.prototype.map.call(pinBoxes, function (c) { return c.checked; });
    me.value = '{{char}}：改后的范例';
    document.getElementById('ed-add-entry').click();
    var rows = document.querySelectorAll('.entry-row');
    var last = rows[rows.length - 1];
    last.querySelector('.ed-title').value = '界面新增';
    last.querySelector('.ed-content').value = '界面新增内容';
    last.querySelector('.ed-pinned').checked = true;
    document.getElementById('ed-save').click();
    return new Promise(function (res) { setTimeout(res, 700); });
  }).then(function () {
    return api('/api/characters');
  }).then(function (r) {
    var c = r.json.characters.find(function (x) { return x.id === ctx.cid; });
    out.saved_mes = c.mes_example;
    out.saved_titles = c.table.map(function (t) { return t.title; });
    var added = c.table.find(function (t) { return t.title === '界面新增'; });
    out.saved_new_pinned = added ? added.pinned : null;
    out.old_pinned = c.table.find(function (t) { return t.title === '铁则'; }).pinned;

    out.stage = 'settings';
    openSettings();
    return new Promise(function (res) { setTimeout(res, 300); });
  }).then(function () {
    out.set_budget = document.getElementById('set-budget-intro').value;
    out.set_threshold = document.getElementById('set-emb-threshold').value;
    out.set_keylimit = document.getElementById('set-emb-keylimit').value;
    document.getElementById('set-budget-intro').value = 42;
    saveConfig();
    return new Promise(function (res) { setTimeout(res, 600); });
  }).then(function () {
    return api('/api/config', { headers: authHeaders() });
  }).then(function (r) {
    out.cfg_intro_chars = r.json.roleplay.intro_chars;
    openSettings();
    return new Promise(function (res) { setTimeout(res, 200); });
  }).then(function () {
    document.getElementById('set-budget-intro').value = 0;
    saveConfig();
    return new Promise(function (res) { setTimeout(res, 500); });
  }).then(function () {
    out.stage = 'create-room';
    return api('/api/rooms', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify({ title: 'ZB压缩房间', character_ids: [ctx.cid] })
    });
  }).then(function (r) {
    ctx.roomId = r.json.id;
    return refreshSessions();
  }).then(function () {
    selectSession(ctx.roomId);
    return new Promise(function (res) { setTimeout(res, 800); });
  }).then(function () {
    out.stage = 'proposal';
    openCompressDialog();
    return waitFor(function () {
      return el.btnExportApply && !el.btnExportApply.classList.contains('hidden');
    }, 'apply button shown');
  }).then(function () {
    var body = el.exportBody;
    out.diff_member_open = !!body.querySelector('.member-diff[open]');
    out.diff_add_lines = body.querySelectorAll('.diff-add div').length;
    var chg = body.querySelector('.table-changed b');
    out.diff_changed_title = chg ? chg.textContent : null;
    var unc = body.querySelector('.diff-unchanged summary');
    out.diff_unchanged = unc ? unc.textContent : null;
    out.diff_subtitle = el.exportSub.textContent;
    return api('/api/sessions/' + ctx.roomId, { headers: authHeaders() });
  }).then(function (r) {
    var members = r.json.members || [];
    out.override_before = members.some(function (m) { return !!m.override; });
    out.stage = 'applying';
    el.btnExportApply.click();
    return waitFor(function () {
      return el.btnExportDownload && !el.btnExportDownload.classList.contains('hidden');
    }, 'download after apply');
  }).then(function () {
    return api('/api/sessions/' + ctx.roomId, { headers: authHeaders() });
  }).then(function (r) {
    var members = r.json.members || [];
    var ai = members.find(function (m) { return !m.is_user; });
    var t = (ai.override || {}).table || [];
    out.after_titles = t.map(function (x) { return x.title; });
    out.after_pin_tieze = t.find(function (x) { return x.title === '铁则'; }).pinned;
    out.after_pin_xinsuo = t.find(function (x) { return x.title === '新线索'; }).pinned;
    out.after_intro = (ai.override || {}).intro;
    out.download_shown = !el.btnExportDownload.classList.contains('hidden');
    out.apply_hidden = el.btnExportApply.classList.contains('hidden');
    return cleanup();
  }).then(function () {
    ctx.cid = ""; ctx.roomId = "";
    out.cleaned = true;
    resolve(out);
  }).catch(fail);
});
"""


async def main():
    import urllib.request

    with urllib.request.urlopen("http://127.0.0.1:9222/json/version") as r:
        ver = json.loads(r.read().decode())

    token = open(
        os.path.join(os.path.dirname(__file__), "temp", "zb_cdp_token.txt"),
        encoding="utf-8",
    ).read().strip()

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
        await csend("Page.navigate", {"url": "http://127.0.0.1:8011/login"})
        await asyncio.sleep(0.8)
        await csend("Runtime.evaluate",
                    {"expression": "localStorage.setItem('flyai-token','" + token + "')"})
        await csend("Page.navigate", {"url": "http://127.0.0.1:8011/"})
        await asyncio.sleep(2.5)

        p = await evaljs(PHASE_JS)
        print(json.dumps(p, ensure_ascii=False, indent=1))

        assert not p.get("error"), p.get("error")

        # A. 编辑器
        assert p["editor_mes"] == "{{user}}：来了？\n<START>\n{{char}}：不然呢。"
        assert p["editor_pins"] == [True, False]
        assert p["saved_mes"] == "{{char}}：改后的范例"
        assert "界面新增" in p["saved_titles"]
        assert p["saved_new_pinned"] is True
        assert p["old_pinned"] is True

        # A. 设置
        assert p["set_budget"] == "0"
        assert p["set_threshold"] == "0.55"
        assert p["set_keylimit"] == "8"
        assert p["cfg_intro_chars"] == 42

        # B. 压缩 diff
        assert p["diff_member_open"] is True
        assert p["diff_add_lines"] >= 2          # 新简介 + 新线索
        assert p["diff_changed_title"] == "铁则"
        assert "2 条未变" in p["diff_unchanged"]
        assert "常驻 1 条" in p["diff_unchanged"]
        assert "提案" in p["diff_subtitle"]
        assert p["override_before"] is False

        # B. 写入后
        assert p["after_titles"] == ["铁则", "普通条目", "界面新增", "新线索"]
        assert p["after_pin_tieze"] is True
        assert p["after_pin_xinsuo"] is False
        assert p["after_intro"] == "新简介：观察力敏锐的伙伴"
        assert p["download_shown"] is True
        assert p["apply_hidden"] is True
        assert p["cleaned"] is True
        print("ALL ASSERTIONS PASSED")
        rt.cancel()


asyncio.run(main())
