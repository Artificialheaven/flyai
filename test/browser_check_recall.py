# -*- coding: utf-8 -*-
"""Playwright 冒烟：撤回整轮消息（对本机 127.0.0.1:8000，admin/test）。

不调真实模型：用 /api/sessions/import 导入含两轮对话的 v2 包，
在 UI 点 🧹 撤回 → 确认 → 断言墓碑行/回复消失/重新编辑回填/刷新持久，结束清理。

用法：.venv/Scripts/python.exe test/browser_check_recall.py
"""
import json

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"

PHASE_JS = r"""
new Promise(function (resolve) {
  var out = { steps: [] };
  function fail(e) { out.error = String(e && (e.message || e)); resolve(out); }
  function waitFor(fn, ms) {
    return new Promise(function (res) {
      var t0 = Date.now();
      (function ch() {
        if (fn()) return res(true);
        if (Date.now() - t0 > (ms || 8000)) return res(false);
        setTimeout(ch, 150);
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

  var uid = "recall-" + Date.now().toString(36);
  var um = uid + "-u", am = uid + "-a", tid = uid + "-t";
  var u1 = uid + "-u1", u2 = uid + "-u2";
  function msg(id, role, content, extra) {
    return Object.assign({ id: id, role: role, content: content }, extra || {});
  }
  var pkg = {
    type: "flyai-conversation", version: 2, name: "ZZ撤回冒烟",
    data: {
      session: { id: "x", title: "ZZ撤回冒烟" },
      character: null, world: null, messages: [],
      members: [
        { id: um, name: "你", is_user: true },
        { id: am, name: "测试AI", is_user: false }
      ],
      threads: [{
        id: tid, kind: "dm", title: "私聊", member_ids: [am],
        messages: [
          msg(u1, "user", "第一轮原文"),
          msg(uid + "-a1", "assistant", "第一轮回复"),
          msg(u2, "user", "第二轮原文"),
          msg(uid + "-a2", "assistant", "第二轮回复")
        ],
      }],
      characters: [],
    },
  };

  var sid = "", anchor2 = u2;
  api("/api/sessions/import", {method: "POST",
    headers: Object.assign({"Content-Type": "application/json"}, authHeaders()),
    body: JSON.stringify(pkg)})
  .then(function (r) {
    if (r.status !== 200) throw new Error("import failed " + r.status);
    sid = r.json.id;
    out.sid = sid;
    return refreshSessions();
  }).then(function () {
    selectSession(sid);
    return waitFor(function () {
      return el.messages.querySelectorAll(".msg.user").length === 2;
    }, 8000);
  }).then(function (ok) {
    if (!ok) throw new Error("两轮用户气泡未出现");
    out.bubbles_before = el.messages.querySelectorAll(".msg.user").length;
    out.assistant_before = el.messages.querySelectorAll(".msg.assistant").length;
    out.recall_btns = el.messages.querySelectorAll(".turn-actions").length;
    // 第二轮操作行的 🧹 撤回
    var btns = el.messages.querySelectorAll(".turn-actions")[1]
      .querySelectorAll("button");
    var recallBtn = Array.prototype.find.call(btns, function (b) {
      return b.textContent.indexOf("撤回") >= 0;
    });
    if (!recallBtn) throw new Error("没有撤回按钮");
    recallBtn.click();
    return waitFor(function () { return el.confirmDialog.open; }, 3000);
  }).then(function (ok) {
    if (!ok) throw new Error("确认弹窗未出现");
    out.confirm_text = el.confirmText.textContent.slice(0, 20);
    el.btnConfirmOk.click();
    return waitFor(function () {
      return el.messages.querySelectorAll(".recall-tombstone").length === 1;
    }, 8000);
  }).then(function (ok) {
    if (!ok) throw new Error("墓碑行未出现");
    out.user_bubbles_after = el.messages.querySelectorAll(".msg.user").length;
    out.tombstone_text = el.messages.querySelector(".recall-tombstone").innerText;
    out.first_turn_kept = el.messages.querySelector(".msg.user .bubble-text")
      ? el.messages.querySelector(".msg.user .bubble-text").textContent : null;
    out.input_before_redo = el.input.value;
    // 点"重新编辑"
    el.messages.querySelector(".rt-redo").click();
    return waitFor(function () { return el.input.value === "第二轮原文"; }, 2000);
  }).then(function (ok) {
    out.input_filled = ok;
    out.pending_images_n = state.pendingImages.length;
    // 服务端落盘形态
    return api("/api/rooms/" + sid + "/threads/" + tid, {headers: authHeaders()});
  }).then(function (r) {
    if (r.status !== 200) throw new Error("thread GET " + r.status);
    var t = r.json;
    out.server_roles = t.messages.map(function (m) { return m.role; });
    out.server_stone_payload = t.messages[2]
      ? { content: t.messages[2].tombstone && t.messages[2].tombstone.content,
          id_kept: t.messages[2].id === anchor2 }
      : null;
    // 刷新页面（reload 后在 phase2 重新打开）
    localStorage.setItem("__recallTest", JSON.stringify({sid: sid, tid: tid, u2: u2}));
    out.phase1_done = true;
    resolve(out);
  }).catch(fail);
});
"""

PHASE2_JS = r"""
new Promise(function (resolve) {
  var out = {};
  function fail(e) { out.error = String(e && (e.message || e)); resolve(out); }
  function waitFor(fn, ms) {
    return new Promise(function (res) {
      var t0 = Date.now();
      (function ch() {
        if (fn()) return res(true);
        if (Date.now() - t0 > (ms || 8000)) return res(false);
        setTimeout(ch, 150);
      })();
    });
  }
  var ctx = JSON.parse(localStorage.getItem("__recallTest") || "{}");
  refreshSessions().then(function () {
    selectSession(ctx.sid);
    return waitFor(function () {
      return el.messages.querySelectorAll(".recall-tombstone").length === 1;
    }, 8000);
  }).then(function (ok) {
    out.tombstone_after_reload = ok;
    out.user_bubbles = el.messages.querySelectorAll(".msg.user").length;
    // 清理
    return fetch("/api/sessions/" + ctx.sid,
      {method: "DELETE", headers: authHeaders()});
  }).then(function (r) {
    out.cleaned = r.ok;
    localStorage.removeItem("__recallTest");
    resolve(out);
  }).catch(fail);
});
"""


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/login", wait_until="networkidle", timeout=20000)
        pg.fill("#login-name", "admin")
        pg.fill("#login-password", "test")
        pg.click("#btn-login")
        pg.wait_for_timeout(2500)

        r1 = pg.evaluate(PHASE_JS)
        print("PHASE1:", json.dumps(r1, ensure_ascii=False, indent=1))
        assert not r1.get("error"), r1["error"]
        assert r1["bubbles_before"] == 2
        assert r1["assistant_before"] == 2
        assert r1["recall_btns"] == 2
        assert r1["user_bubbles_after"] == 1
        assert "您撤回了一条消息" in r1["tombstone_text"]
        assert "重新编辑" in r1["tombstone_text"]
        assert r1["first_turn_kept"] == "第一轮原文"
        assert r1["input_before_redo"] == ""
        assert r1["input_filled"] is True
        assert r1["server_roles"] == ["user", "assistant", "tombstone"]
        assert r1["server_stone_payload"] == {"content": "第二轮原文", "id_kept": True}

        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(2500)
        r2 = pg.evaluate(PHASE2_JS)
        print("PHASE2:", json.dumps(r2, ensure_ascii=False, indent=1))
        assert not r2.get("error"), r2["error"]
        assert r2["tombstone_after_reload"] is True
        assert r2["user_bubbles"] == 1
        assert r2["cleaned"] is True

        page_errs = [e for e in errs if "favicon" not in e]
        assert not page_errs, page_errs
        b.close()
    print("ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
