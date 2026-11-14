/* 长跑任务弹窗（P2）：loop_requests 审批 + loop_sessions 运行态管理。
 * 经典脚本 IIFE，对外暴露 openLoopManager / updateLoopButton 两个全局钩子。
 * 数据接口：/api/chat/loop/*（审批/启动仅管理员，查询与打断限所有者或管理员）。 */

(function () {
  var API = "/api/chat/loop";
  var POLL_MS = 5000;

  var dlg = document.getElementById("loop-mgr-dialog");
  var reqList = document.getElementById("loop-request-list");
  var sessList = document.getElementById("loop-session-list");
  var reqPane = document.getElementById("loop-requests-pane");
  var sessPane = document.getElementById("loop-sessions-pane");
  var msgBar = document.getElementById("loop-mgr-msg");
  var adminScope = document.getElementById("loop-admin-scope");
  var targetInput = document.getElementById("loop-target-uid");
  var tab = "requests";
  var pollTimer = null;
  var requestSessions = [];  // 最近一次 sessions 响应，供轮询判活

  var REQ_STATUS = {
    pending: { label: "待审批", cls: "loop-st-pending" },
    approved: { label: "已通过", cls: "loop-st-done" },
    rejected: { label: "已驳回", cls: "loop-st-stopped" },
  };
  var LOOP_STATUS = {
    running: { label: "运行中", cls: "loop-st-running" },
    done: { label: "已完成", cls: "loop-st-done" },
    stopped: { label: "已停止", cls: "loop-st-stopped" },
    interrupted: { label: "已中断", cls: "loop-st-stopped" },
    error: { label: "出错", cls: "loop-st-error" },
    idle: { label: "空转结束", cls: "loop-st-idle" },
  };

  function isAdmin() { return state.role === "admin"; }

  function targetUid() {
    if (!isAdmin() || !targetInput) return "";
    return (targetInput.value || "").trim();
  }

  /* 管理员正在查看别人的数据：打断接口只认会话所有者，此时隐藏打断条 */
  function viewOther() {
    var t = targetUid();
    return !!t && t !== state.userId;
  }

  function qsUid() {
    var p = new URLSearchParams();
    var t = targetUid();
    if (t) p.set("uid", t);
    return p.toString();
  }

  function setMsg(text, kind) {
    if (!msgBar) return;
    msgBar.textContent = text || "";
    msgBar.className = "msg-bar" + (kind ? " " + kind : "");
  }

  function fmtTime(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
      + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function sessionTitle(sid) {
    var hit = (state.sessions || []).filter(function (s) { return s.id === sid; })[0];
    if (hit && hit.title) return hit.title;
    return sid.length > 12 ? sid.slice(0, 12) + "…" : sid;
  }

  function statusChip(map, st) {
    var m = map[st] || { label: st, cls: "loop-st-stopped" };
    return '<span class="loop-status ' + m.cls + '">' + escapeHtml(m.label) + "</span>";
  }

  // ---- 申请列表 ----
  function loadRequests() {
    if (!reqList) return;
    reqList.textContent = "加载中…";
    var suffix = qsUid();
    fetchJSON(API + "/requests" + (suffix ? "?" + suffix : "")).then(function (data) {
      var items = data.items || [];
      if (!items.length) {
        reqList.innerHTML = '<div class="mem-empty">暂无长跑申请。AI 可在对话中调用 loop_request 提交申请。</div>';
        return;
      }
      reqList.innerHTML = items.map(function (r) {
        var st = REQ_STATUS[r.status] || { label: r.status, cls: "loop-st-stopped" };
        var actions = "";
        if (r.status === "pending" && isAdmin()) {
          actions = '<div class="loop-actions">'
            + '<button type="button" class="btn small loop-btn-ok" data-act="approve" data-id="' + r.id + '">通过并启动</button>'
            + '<button type="button" class="btn small danger loop-btn-no" data-act="reject" data-id="' + r.id + '">驳回</button>'
            + "</div>";
        }
        return '<div class="mem-row loop-row" data-id="' + r.id + '">'
          + '<div class="mem-row-head"><span class="loop-title">#' + r.id + " · "
          + escapeHtml(sessionTitle(r.session_id)) + "</span>" + statusChip(REQ_STATUS, r.status)
          + '<span class="mem-row-meta">' + escapeHtml(fmtTime(r.created_at)) + "</span></div>"
          + '<div class="mem-row-text">' + escapeHtml(r.task) + "</div>"
          + (r.reason ? '<div class="loop-reason">申请理由：' + escapeHtml(r.reason) + "</div>" : "")
          + actions + "</div>";
      }).join("");
    }).catch(function (e) {
      reqList.innerHTML = '<div class="mem-empty">加载失败: ' + escapeHtml(e.message) + "</div>";
    });
  }

  // ---- 运行记录 ----
  function loadSessions() {
    if (!sessList) return;
    sessList.textContent = "加载中…";
    var suffix = qsUid();
    fetchJSON(API + "/sessions" + (suffix ? "?" + suffix : "")).then(function (data) {
      requestSessions = data.items || [];
      if (!requestSessions.length) {
        sessList.innerHTML = '<div class="mem-empty">暂无长跑运行记录。</div>';
        return;
      }
      var hideInterrupt = viewOther();
      sessList.innerHTML = requestSessions.map(function (s) {
        var running = s.status === "running";
        var head = '<div class="mem-row-head"><span class="loop-title">#' + s.id + " · "
          + escapeHtml(sessionTitle(s.session_id)) + "</span>" + statusChip(LOOP_STATUS, s.status)
          + '<span class="mem-row-meta">' + escapeHtml(fmtTime(s.created_at))
          + (s.ended_at ? " → " + escapeHtml(fmtTime(s.ended_at)) : "") + "</span></div>";
        var meta = '<div class="loop-meta">工具轮 <b>' + (s.rounds || 0) + "</b> · 压缩 <b>"
          + (s.compacts || 0) + "</b></div>";
        var body = '<div class="mem-row-text">' + escapeHtml(s.task) + "</div>";
        var detail = "";
        if (s.error) {
          detail = '<details class="loop-detail"><summary>错误信息</summary><div class="loop-error">'
            + escapeHtml(s.error) + "</div></details>";
        } else if (s.result) {
          var preview = s.result.length > 600 ? s.result.slice(0, 600) + "…" : s.result;
          detail = '<details class="loop-detail"><summary>最终汇报（' + s.result.length + ' 字）</summary>'
            + '<div class="loop-result">' + escapeHtml(preview) + "</div></details>";
        }
        var actions = "";
        if (running) {
          actions = '<div class="loop-run-actions">';
          if (!hideInterrupt) {
            actions += '<input type="text" class="loop-interrupt-input" placeholder="临时指示（安全点注入，不抢锁）" maxlength="2000" />'
              + '<button type="button" class="btn small loop-btn-interrupt" data-sid="'
              + escapeAttr(s.session_id) + '">打断/指示</button>';
          }
          actions += '<button type="button" class="btn small danger loop-btn-stop" data-id="'
            + s.id + '">干净停止</button></div>';
        }
        return '<div class="mem-row loop-row">' + head + meta + body + detail + actions + "</div>";
      }).join("");
    }).catch(function (e) {
      sessList.innerHTML = '<div class="mem-empty">加载失败: ' + escapeHtml(e.message) + "</div>";
    });
  }

  function refreshCurrent() {
    if (tab === "requests") loadRequests(); else loadSessions();
  }

  function hasLive() {
    return requestSessions.some(function (s) { return s.status === "running"; });
  }

  // ---- 操作：审批 / 停止 / 打断（事件委托） ----
  reqList && reqList.addEventListener("click", function (e) {
    var btn = e.target.closest(".loop-btn-ok, .loop-btn-no");
    if (!btn) return;
    var id = parseInt(btn.getAttribute("data-id"), 10);
    var approve = btn.classList.contains("loop-btn-ok");
    if (!approve && !window.confirm("确认驳回申请 #" + id + "？")) return;
    btn.disabled = true;
    var body = { uid: targetUid(), approve: approve };
    fetchJSON(API + "/requests/" + id + "/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function () {
      showToast(approve ? "已通过，长跑启动" : "已驳回", "ok");
      setMsg("");
      loadRequests();
      switchTab("sessions");
      loadSessions();
    }).catch(function (err) {
      btn.disabled = false;
      setMsg("操作失败: " + err.message, "err");
    });
  });

  sessList && sessList.addEventListener("click", function (e) {
    var stopBtn = e.target.closest(".loop-btn-stop");
    if (stopBtn) {
      var id = parseInt(stopBtn.getAttribute("data-id"), 10);
      if (!window.confirm("确认停止长跑 #" + id + "？worker 将在下个安全点退出。")) return;
      stopBtn.disabled = true;
      var body = {};
      var t = targetUid();
      if (t) body.uid = t;
      fetchJSON(API + "/sessions/" + id + "/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function () {
        showToast("已请求停止", "ok");
        loadSessions();
      }).catch(function (err) {
        stopBtn.disabled = false;
        setMsg("停止失败: " + err.message, "err");
      });
      return;
    }
    var intBtn = e.target.closest(".loop-btn-interrupt");
    if (!intBtn) return;
    var row = e.target.closest(".loop-row");
    var input = row && row.querySelector(".loop-interrupt-input");
    var text = input ? input.value.trim() : "";
    if (!text) { setMsg("请先填写指示内容", "err"); return; }
    intBtn.disabled = true;
    fetchJSON(API + "/interrupt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: intBtn.getAttribute("data-sid"), text: text }),
    }).then(function (r) {
      showToast("已投递，待安全点消费（队列 " + r.pending + " 条）", "ok");
      if (input) input.value = "";
      setMsg("");
    }).catch(function (err) {
      setMsg("投递失败: " + err.message, "err");
    }).finally(function () {
      intBtn.disabled = false;
    });
  });

  // ---- 页签 / 轮询 ----
  function switchTab(name) {
    tab = name;
    if (reqPane) reqPane.classList.toggle("hidden", name !== "requests");
    if (sessPane) sessPane.classList.toggle("hidden", name !== "sessions");
    dlg.querySelectorAll(".loop-tab").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-ltab") === name);
    });
    refreshCurrent();
  }

  dlg.querySelectorAll(".loop-tab").forEach(function (t) {
    t.addEventListener("click", function () { switchTab(t.getAttribute("data-ltab")); });
  });

  document.getElementById("btn-loop-refresh").addEventListener("click", refreshCurrent);
  document.getElementById("btn-loop-close").addEventListener("click", function () { dlg.close(); });
  targetInput && targetInput.addEventListener("change", refreshCurrent);

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(function () {
      if (document.hidden) return;
      refreshCurrent();
    }, POLL_MS);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  dlg.addEventListener("close", stopPolling);

  window.openLoopManager = function () {
    if (!dlg) return;
    setMsg("");
    if (adminScope) adminScope.classList.toggle("hidden", !isAdmin());
    if (targetInput && !isAdmin()) targetInput.value = "";
    requestSessions = [];
    dlg.showModal();
    switchTab("requests");
    loadRequests();
    startPolling();
  };

  /* 侧栏入口：长跑功能启用才出现（/api/config 对所有角色返回 loop 段） */
  window.updateLoopButton = function () {
    var btn = document.getElementById("btn-loop");
    if (!btn || !state.config) return;
    var on = !!(state.config.loop && state.config.loop.enabled);
    btn.classList.toggle("hidden", !on);
  };
})();
