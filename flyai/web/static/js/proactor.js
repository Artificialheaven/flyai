/* P3 心情 / 主动行为管理弹窗。
 * 经典脚本 IIFE，对外暴露 openMoodManager / openProactiveManager /
 * updateProactorButtons 三个全局钩子。数据接口：/api/proactor/*
 * （普通用户只操作自己的数据，管理员可填目标 uid 代管）。 */

(function () {
  var API = "/api/proactor";
  var MOOD_DIMS = ["开心", "踏实", "甜蜜", "心疼", "担心", "难过", "吃醋"];
  var JOB_MODE_LABEL = { once: "单次", daily: "每天", weekly: "每周" };
  var COMMIT_STATUS_LABEL = {
    open: { label: "待提醒", cls: "loop-st-pending" },
    closed: { label: "已完成", cls: "loop-st-done" },
    cancelled: { label: "已取消", cls: "loop-st-stopped" },
  };

  var moodDlg = document.getElementById("mood-mgr-dialog");
  var proDlg = document.getElementById("proactive-mgr-dialog");

  function isAdmin() { return state.role === "admin"; }

  function scopeSuffix(input) {
    if (!isAdmin() || !input) return "";
    var t = (input.value || "").trim();
    return t ? ("?uid=" + encodeURIComponent(t)) : "";
  }

  function scopeBody(input, extra) {
    var body = extra || {};
    if (isAdmin() && input && (input.value || "").trim()) body.uid = input.value.trim();
    return body;
  }

  function setMsg(bar, text, kind) {
    if (!bar) return;
    bar.textContent = text || "";
    bar.className = "msg-bar" + (kind ? " " + kind : "");
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

  function fillSessionSelect(sel, selectedSid) {
    if (!sel) return;
    var sessions = state.sessions || [];
    sel.innerHTML = sessions.map(function (s) {
      return '<option value="' + escapeAttr(s.id) + '"'
        + (s.id === selectedSid ? " selected" : "") + ">"
        + escapeHtml(s.title || s.id) + "</option>";
    }).join("");
    if (!sel.value && sessions.length) sel.value = sessions[0].id;
  }

  function statusChip(map, st) {
    var m = map[st] || { label: st, cls: "loop-st-stopped" };
    return '<span class="loop-status ' + m.cls + '">' + escapeHtml(m.label) + "</span>";
  }

  function get(url) { return fetchJSON(API + url); }
  function post(url, body) {
    return fetchJSON(API + url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }
  function del(url) {
    return fetchJSON(API + url, { method: "DELETE" });
  }

  /* ================= 心情管理 ================= */

  var moodSel = document.getElementById("mood-session-sel");
  var moodCid = document.getElementById("mood-cid-input");
  var moodCurrent = document.getElementById("mood-current");
  var moodTimeline = document.getElementById("mood-timeline");
  var moodMsg = document.getElementById("mood-mgr-msg");
  var moodScope = document.getElementById("mood-admin-scope");
  var moodUid = document.getElementById("mood-target-uid");

  function moodUidQuery() {
    var t = isAdmin() && moodUid ? (moodUid.value || "").trim() : "";
    return t ? ("&uid=" + encodeURIComponent(t)) : "";
  }

  function renderMoodCurrent(v) {
    if (!v || !v.row_id) {
      moodCurrent.innerHTML = '<div class="mem-empty">该角色还没有心情记录，显示基线 '
        + escapeHtml(String(v ? v.mood : "—")) + "。</div>";
      return;
    }
    var dims = v.dims || {};
    var chips = MOOD_DIMS.filter(function (k) { return dims[k] != null; }).map(function (k) {
      return '<span class="pro-dim">' + escapeHtml(k) + " " + escapeHtml(String(dims[k])) + "</span>";
    }).join("");
    var announce = "";
    if (v.announce === "pending") {
      announce = '<div class="pro-announce">🕊 待宣布内心独白：'
        + escapeHtml(v.announce_text || "（无文本）") + "</div>";
    } else if (v.announce === "injected") {
      announce = '<div class="pro-sub">内心独白已宣布过一次。</div>';
    }
    moodCurrent.innerHTML = '<div>当前心情 <span class="pro-mood-num">'
      + escapeHtml(String(v.mood)) + '</span> / 100</div>'
      + (chips ? '<div class="pro-dim-row">' + chips + "</div>" : '<div class="pro-sub">七维均已衰减至静默区。</div>')
      + announce;
  }

  function renderMoodTimeline(items) {
    if (!items.length) {
      moodTimeline.innerHTML = '<div class="mem-empty">暂无时间线。</div>';
      return;
    }
    moodTimeline.innerHTML = items.map(function (r) {
      var dims = {};
      try { dims = JSON.parse(r.dims || "{}"); } catch (e) {}
      var dimText = MOOD_DIMS.filter(function (k) { return dims[k] != null; })
        .map(function (k) { return k + " " + dims[k]; }).join("，");
      return '<div class="mem-row loop-row">'
        + '<div class="mem-row-head"><span class="loop-title">Δ '
        + escapeHtml(String(r.delta)) + "</span>"
        + '<span class="mem-row-meta">' + escapeHtml(fmtTime(r.created_at))
        + " · " + escapeHtml(r.source || "") + " · 置信 " + escapeHtml(String(r.confidence)) + "</span></div>"
        + (r.reason ? '<div class="mem-row-text">' + escapeHtml(r.reason) + "</div>" : "")
        + (dimText ? '<div class="pro-sub">' + escapeHtml(dimText) + "</div>" : "")
        + "</div>";
    }).join("");
  }

  function queryMood() {
    var sid = moodSel.value;
    var cid = (moodCid.value || "").trim();
    if (!sid) { setMsg(moodMsg, "请先选择会话", "err"); return; }
    if (!cid) { setMsg(moodMsg, "请填写角色 ID", "err"); return; }
    setMsg(moodMsg, "");
    var q = "?session_id=" + encodeURIComponent(sid)
      + "&character_id=" + encodeURIComponent(cid) + moodUidQuery();
    Promise.all([get("/mood/view" + q), get("/mood/timeline" + q)]).then(function (res) {
      renderMoodCurrent(res[0]);
      renderMoodTimeline(res[1].items || []);
    }).catch(function (e) {
      setMsg(moodMsg, "查询失败: " + e.message, "err");
    });
  }

  window.openMoodManager = function () {
    if (!moodDlg) return;
    setMsg(moodMsg, "");
    if (moodScope) moodScope.classList.toggle("hidden", !isAdmin());
    if (moodUid && !isAdmin()) moodUid.value = "";
    fillSessionSelect(moodSel, state.currentSessionId);
    var cur = (state.sessions || []).filter(function (s) { return s.id === moodSel.value; })[0];
    moodCid.value = cur && cur.character_id ? cur.character_id : "";
    moodCurrent.innerHTML = "";
    moodTimeline.innerHTML = '<div class="mem-empty">选择会话与角色后点查询。</div>';
    moodDlg.showModal();
  };

  moodDlg && moodDlg.addEventListener("click", function (e) {
    if (e.target.closest("#btn-mood-query")) { queryMood(); return; }
    var clearBtn = e.target.closest("#btn-mood-clear");
    if (!clearBtn) return;
    var sid = moodSel.value;
    var cid = (moodCid.value || "").trim();
    if (!sid || !cid) { setMsg(moodMsg, "请先选择会话并填写角色 ID", "err"); return; }
    if (!window.confirm("确认清除该角色的心情状态与全部时间线？")) return;
    post("/mood/clear", scopeBody(moodUid, {
      session_id: sid, character_id: cid,
    })).then(function () {
      showToast("已清除", "ok");
      queryMood();
    }).catch(function (err) { setMsg(moodMsg, "清除失败: " + err.message, "err"); });
  });
  moodSel && moodSel.addEventListener("change", function () {
    var cur = (state.sessions || []).filter(function (s) { return s.id === moodSel.value; })[0];
    if (cur && cur.character_id) moodCid.value = cur.character_id;
  });
  document.getElementById("btn-mood-close").addEventListener("click", function () {
    moodDlg.close();
  });

  /* ================= 主动行为 ================= */

  var proScope = document.getElementById("pro-admin-scope");
  var proUid = document.getElementById("pro-target-uid");
  var proMsg = document.getElementById("pro-mgr-msg");
  var proSessionSel = document.getElementById("pro-session-sel");
  var proOptin = document.getElementById("pro-optin");
  var pollList = document.getElementById("pro-poll-list");
  var jobList = document.getElementById("pro-job-list");
  var commitList = document.getElementById("pro-commit-list");
  var smList = document.getElementById("pro-sm-list");
  var smTracesMode = false;

  function proSuffix(extra) {
    var t = isAdmin() && proUid ? (proUid.value || "").trim() : "";
    var parts = [];
    if (extra) parts.push(extra);
    if (t) parts.push("uid=" + encodeURIComponent(t));
    return parts.length ? ("?" + parts.join("&")) : "";
  }

  function switchProTab(name) {
    proDlg.querySelectorAll(".pro-tab").forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-ptab") === name);
    });
    ["pro", "jobs", "commit", "sm"].forEach(function (k) {
      var pane = document.getElementById("pro-pane-" + k);
      if (pane) pane.classList.toggle("hidden", k !== name);
    });
    if (name === "pro") loadProTab();
    if (name === "jobs") loadJobs();
    if (name === "commit") loadCommitments();
    if (name === "sm") loadSelfMemories();
  }

  // ---- 会话 opt-in + 轮询 ----

  function loadProTab() {
    var sid = proSessionSel.value;
    if (!sid) return;
    get("/proactive/state?session_id=" + encodeURIComponent(sid)
      + (isAdmin() && proUid && proUid.value.trim()
        ? "&uid=" + encodeURIComponent(proUid.value.trim()) : "")).then(function (r) {
      proOptin.checked = !!(r.state && r.state.enabled);
    }).catch(function (e) { setMsg(proMsg, "状态读取失败: " + e.message, "err"); });
    loadPolls();
  }

  function renderPolls(items) {
    if (!items.length) {
      pollList.innerHTML = '<div class="mem-empty">该会话暂无自建轮询。</div>';
      return;
    }
    pollList.innerHTML = items.map(function (p) {
      return '<div class="mem-row loop-row" data-id="' + p.id + '">'
        + '<div class="mem-row-head"><span class="loop-title">' + escapeHtml(p.title || ("#" + p.id))
        + "</span>" + (p.enabled ? '<span class="loop-status loop-st-running">启用</span>'
          : '<span class="loop-status loop-st-stopped">停用</span>')
        + '<span class="mem-row-meta">概率 ' + escapeHtml(String(p.probability))
        + "% · 保底 " + escapeHtml(String(p.wake_every_n))
        + " · 门限 " + escapeHtml(String(p.active_hours)) + "h"
        + " · 未中 " + escapeHtml(String(p.ticks_since_fire)) + "</span></div>"
        + '<div class="pro-row-actions">'
        + '<button type="button" class="btn small" data-act="poll-toggle">'
        + (p.enabled ? "停用" : "启用") + "</button>"
        + '<button type="button" class="btn small danger" data-act="poll-del">删除</button>'
        + "</div></div>";
    }).join("");
  }

  function loadPolls() {
    var sid = proSessionSel.value;
    if (!sid) return;
    get("/polls?session_id=" + encodeURIComponent(sid)
      + (isAdmin() && proUid && proUid.value.trim()
        ? "&uid=" + encodeURIComponent(proUid.value.trim()) : ""))
      .then(function (r) { renderPolls(r.items || []); })
      .catch(function (e) { pollList.innerHTML = '<div class="mem-empty">加载失败: ' + escapeHtml(e.message) + "</div>"; });
  }

  // ---- 定时作业 ----

  function renderJobs(items) {
    if (!items.length) {
      jobList.innerHTML = '<div class="mem-empty">暂无定时作业。</div>';
      return;
    }
    jobList.innerHTML = items.map(function (j) {
      return '<div class="mem-row loop-row" data-id="' + j.id + '">'
        + '<div class="mem-row-head"><span class="loop-title">#' + j.id + " · "
        + escapeHtml(sessionTitle(j.session_id)) + "</span>"
        + (j.enabled ? '<span class="loop-status loop-st-running">启用</span>'
          : '<span class="loop-status loop-st-stopped">停用</span>')
        + '<span class="mem-row-meta">' + escapeHtml(JOB_MODE_LABEL[j.mode] || j.mode)
        + " " + escapeHtml(j.hhmm) + (j.mode === "weekly" ? " · 周" + (j.weekday + 1) : "")
        + "</span></div>"
        + '<div class="mem-row-text">' + escapeHtml(j.brief) + "</div>"
        + '<div class="pro-sub">下次：' + escapeHtml(j.next_run_local || "—")
        + (j.last_status ? " · 上次：" + escapeHtml(j.last_status) : "") + "</div>"
        + '<div class="pro-row-actions">'
        + '<button type="button" class="btn small" data-act="job-toggle">'
        + (j.enabled ? "停用" : "启用") + "</button>"
        + '<button type="button" class="btn small danger" data-act="job-del">删除</button>'
        + "</div></div>";
    }).join("");
  }

  function loadJobs() {
    get("/jobs" + proSuffix()).then(function (r) {
      renderJobs(r.items || []);
    }).catch(function (e) {
      jobList.innerHTML = '<div class="mem-empty">加载失败: ' + escapeHtml(e.message) + "</div>";
    });
  }

  // ---- 承诺 ----

  function renderCommitments(items) {
    if (!items.length) {
      commitList.innerHTML = '<div class="mem-empty">暂无承诺记录。</div>';
      return;
    }
    commitList.innerHTML = items.map(function (c) {
      var overdue = c.status === "open" && c.due_at && c.due_at <= Date.now() / 1000;
      var actions = "";
      if (c.status === "open") {
        actions = '<div class="pro-row-actions">'
          + '<button type="button" class="btn small" data-act="commit-close">标记完成</button>'
          + '<button type="button" class="btn small danger" data-act="commit-cancel">取消</button>'
          + "</div>";
      }
      return '<div class="mem-row loop-row" data-id="' + c.id + '">'
        + '<div class="mem-row-head"><span class="loop-title">#' + c.id + " · "
        + escapeHtml(sessionTitle(c.session_id)) + "</span>"
        + statusChip(COMMIT_STATUS_LABEL, c.status)
        + '<span class="mem-row-meta">' + escapeHtml(fmtTime(c.due_at))
        + (overdue ? "（已到点）" : "") + "</span></div>"
        + '<div class="mem-row-text">' + escapeHtml(c.content) + "</div>"
        + (c.owner_name ? '<div class="pro-sub">登记人：' + escapeHtml(c.owner_name) + "</div>" : "")
        + actions + "</div>";
    }).join("");
  }

  function loadCommitments() {
    get("/commitments" + proSuffix()).then(function (r) {
      renderCommitments(r.items || []);
    }).catch(function (e) {
      commitList.innerHTML = '<div class="mem-empty">加载失败: ' + escapeHtml(e.message) + "</div>";
    });
  }

  // ---- 自我记忆 ----

  function renderSelfMemories(items) {
    if (!items.length) {
      smList.innerHTML = '<div class="mem-empty">暂无自我记忆。</div>';
      return;
    }
    smList.innerHTML = items.map(function (m) {
      return '<div class="mem-row loop-row" data-id="' + m.id + '">'
        + '<div class="mem-row-head"><span class="loop-title">#' + m.id
        + " · 重要度 " + escapeHtml(String(m.importance)) + "</span>"
        + '<span class="mem-row-meta">' + escapeHtml(fmtTime(m.created_at))
        + " · 引用 " + escapeHtml(String(m.access_count || 0)) + "</span></div>"
        + '<div class="mem-row-text">' + escapeHtml(m.statement) + "</div>"
        + (m.tags ? '<div class="pro-sub">标签：' + escapeHtml(m.tags) + "</div>" : "")
        + '<div class="pro-row-actions">'
        + '<button type="button" class="btn small" data-act="sm-sup">取代</button>'
        + '<button type="button" class="btn small danger" data-act="sm-del">删除</button>'
        + "</div></div>";
    }).join("");
  }

  function renderTraces(items) {
    if (!items.length) {
      smList.innerHTML = '<div class="mem-empty">暂无审计流水。</div>';
      return;
    }
    smList.innerHTML = items.map(function (t) {
      return '<div class="mem-row loop-row">'
        + '<div class="mem-row-head"><span class="loop-title">' + escapeHtml(t.kind)
        + " · #" + escapeHtml(String(t.memory_id)) + "</span>"
        + '<span class="mem-row-meta">' + escapeHtml(fmtTime(t.created_at)) + "</span></div>"
        + (t.detail ? '<div class="mem-row-text">' + escapeHtml(t.detail) + "</div>" : "")
        + "</div>";
    }).join("");
  }

  function loadSelfMemories() {
    smTracesMode = false;
    var btn = document.getElementById("btn-sm-traces");
    if (btn) btn.textContent = "审计流水";
    get("/self-memory" + proSuffix()).then(function (r) {
      renderSelfMemories(r.items || []);
    }).catch(function (e) {
      smList.innerHTML = '<div class="mem-empty">加载失败: ' + escapeHtml(e.message) + "</div>";
    });
  }

  // ---- 事件委托 ----

  proDlg.addEventListener("click", function (e) {
    var tabBtn = e.target.closest(".pro-tab");
    if (tabBtn) { switchProTab(tabBtn.getAttribute("data-ptab")); return; }
    if (e.target.closest("#btn-pro-refresh")) {
      switchProTab(proDlg.querySelector(".pro-tab.active").getAttribute("data-ptab"));
      return;
    }
    var act = e.target.closest("[data-act]");
    if (!act) return;
    var row = e.target.closest(".mem-row");
    var id = row ? parseInt(row.getAttribute("data-id"), 10) : 0;
    var name = act.getAttribute("data-act");
    var uidSuffix = isAdmin() && proUid && proUid.value.trim()
      ? "?uid=" + encodeURIComponent(proUid.value.trim()) : "";

    if (name === "poll-toggle" || name === "poll-del") {
      if (name === "poll-del") {
        if (!window.confirm("确认删除轮询 #" + id + "？")) return;
        del("/polls/" + id + uidSuffix).then(function () { showToast("已删除", "ok"); loadPolls(); })
          .catch(function (err) { setMsg(proMsg, "删除失败: " + err.message, "err"); });
      } else {
        var wasEnabled = act.textContent.trim() === "停用";
        post("/polls/" + id + "/toggle", scopeBody(proUid, { enabled: !wasEnabled }))
          .then(function () { loadPolls(); })
          .catch(function (err) { setMsg(proMsg, "操作失败: " + err.message, "err"); });
      }
      return;
    }
    if (name === "job-toggle" || name === "job-del") {
      if (name === "job-del") {
        if (!window.confirm("确认删除作业 #" + id + "？")) return;
        del("/jobs/" + id + uidSuffix).then(function () { showToast("已删除", "ok"); loadJobs(); })
          .catch(function (err) { setMsg(proMsg, "删除失败: " + err.message, "err"); });
      } else {
        var jobWasOn = act.textContent.trim() === "停用";
        post("/jobs/" + id + "/toggle", scopeBody(proUid, { enabled: !jobWasOn }))
          .then(function () { loadJobs(); })
          .catch(function (err) { setMsg(proMsg, "操作失败: " + err.message, "err"); });
      }
      return;
    }
    if (name === "commit-close" || name === "commit-cancel") {
      var path = name === "commit-close" ? "close" : "cancel";
      post("/commitments/" + id + "/" + path, scopeBody(proUid, {}))
        .then(function () { showToast("已更新", "ok"); loadCommitments(); })
        .catch(function (err) { setMsg(proMsg, "操作失败: " + err.message, "err"); });
      return;
    }
    if (name === "sm-del") {
      if (!window.confirm("确认删除记忆 #" + id + "？")) return;
      del("/self-memory/" + id + uidSuffix).then(function () { showToast("已删除", "ok"); loadSelfMemories(); })
        .catch(function (err) { setMsg(proMsg, "删除失败: " + err.message, "err"); });
      return;
    }
    if (name === "sm-sup") {
      var text = window.prompt("输入取代旧事实的新陈述：");
      if (!text || !text.trim()) return;
      post("/self-memory/" + id + "/supersede", scopeBody(proUid, { statement: text.trim() }))
        .then(function () { showToast("已取代（占用今日配额）", "ok"); loadSelfMemories(); })
        .catch(function (err) { setMsg(proMsg, "操作失败: " + err.message, "err"); });
    }
  });

  // opt-in 开关
  proOptin && proOptin.addEventListener("change", function () {
    var sid = proSessionSel.value;
    post("/proactive/enable", scopeBody(proUid, {
      session_id: sid, enabled: proOptin.checked,
    })).then(function () {
      showToast(proOptin.checked ? "本会话已开启主动行为" : "已关闭", "ok");
    }).catch(function (err) {
      setMsg(proMsg, "开关失败: " + err.message, "err");
      proOptin.checked = !proOptin.checked;
    });
  });
  proSessionSel && proSessionSel.addEventListener("change", loadProTab);
  proUid && proUid.addEventListener("change", function () {
    switchProTab(proDlg.querySelector(".pro-tab.active").getAttribute("data-ptab"));
  });

  // 新建轮询
  document.getElementById("btn-poll-add").addEventListener("click", function () {
    var title = (document.getElementById("poll-title").value || "").trim();
    if (!title) { setMsg(proMsg, "轮询标题不能为空", "err"); return; }
    post("/polls", scopeBody(proUid, {
      session_id: proSessionSel.value,
      title: title,
      probability: parseInt(document.getElementById("poll-prob").value, 10) || 10,
      wake_every_n: parseInt(document.getElementById("poll-n").value, 10) || 20,
      active_hours: parseFloat(document.getElementById("poll-hours").value) || 12,
    })).then(function () {
      showToast("轮询已创建", "ok");
      document.getElementById("poll-title").value = "";
      setMsg(proMsg, "");
      loadPolls();
    }).catch(function (err) { setMsg(proMsg, "创建失败: " + err.message, "err"); });
  });

  // 新建作业
  document.getElementById("btn-job-add").addEventListener("click", function () {
    var mode = document.getElementById("job-mode").value;
    var weekday = parseInt(document.getElementById("job-weekday").value, 10);
    post("/jobs", scopeBody(proUid, {
      session_id: document.getElementById("job-session-sel").value,
      mode: mode,
      hhmm: document.getElementById("job-hhmm").value || "09:00",
      weekday: mode === "weekly" ? weekday : -1,
      brief: (document.getElementById("job-brief").value || "").trim(),
    })).then(function () {
      showToast("作业已创建", "ok");
      document.getElementById("job-brief").value = "";
      setMsg(proMsg, "");
      loadJobs();
    }).catch(function (err) { setMsg(proMsg, "创建失败: " + err.message, "err"); });
  });
  document.getElementById("job-mode").addEventListener("change", function (e) {
    document.getElementById("job-weekday").disabled = e.target.value !== "weekly";
  });

  // 登记承诺
  document.getElementById("btn-commit-add").addEventListener("click", function () {
    var content = (document.getElementById("commit-content").value || "").trim();
    var due = (document.getElementById("commit-due").value || "").trim();
    if (!content || !due) { setMsg(proMsg, "承诺内容和到点时间都不能为空", "err"); return; }
    post("/commitments", scopeBody(proUid, {
      session_id: document.getElementById("commit-session-sel").value,
      content: content,
      due_at: due,
    })).then(function () {
      showToast("承诺已登记", "ok");
      document.getElementById("commit-content").value = "";
      setMsg(proMsg, "");
      loadCommitments();
    }).catch(function (err) { setMsg(proMsg, "登记失败: " + err.message, "err"); });
  });

  // 新建自我记忆
  document.getElementById("btn-sm-add").addEventListener("click", function () {
    var statement = (document.getElementById("sm-statement").value || "").trim();
    if (!statement) { setMsg(proMsg, "事实陈述不能为空", "err"); return; }
    var tags = (document.getElementById("sm-tags").value || "")
      .split(/[,，]/).map(function (t) { return t.trim(); }).filter(Boolean);
    post("/self-memory", scopeBody(proUid, {
      statement: statement,
      tags: tags,
      importance: parseFloat(document.getElementById("sm-imp").value) || 0.5,
    })).then(function (r) {
      showToast(r.created ? "记忆已新建" : "同事实已存在，已合并更新（不占配额）", "ok");
      document.getElementById("sm-statement").value = "";
      setMsg(proMsg, "");
      loadSelfMemories();
    }).catch(function (err) { setMsg(proMsg, "创建失败: " + err.message, "err"); });
  });

  // 审计流水切换
  document.getElementById("btn-sm-traces").addEventListener("click", function () {
    var btn = document.getElementById("btn-sm-traces");
    smTracesMode = !smTracesMode;
    if (smTracesMode) {
      get("/self-memory/traces" + proSuffix("limit=200")).then(function (r) {
        renderTraces(r.items || []);
        btn.textContent = "返回记忆列表";
      }).catch(function (e) { setMsg(proMsg, "流水读取失败: " + e.message, "err"); });
    } else {
      loadSelfMemories();
    }
  });

  document.getElementById("btn-pro-close").addEventListener("click", function () {
    proDlg.close();
  });

  window.openProactiveManager = function () {
    if (!proDlg) return;
    setMsg(proMsg, "");
    if (proScope) proScope.classList.toggle("hidden", !isAdmin());
    if (proUid && !isAdmin()) proUid.value = "";
    // 自我记忆页签随功能开关显隐
    var smOn = !!(state.config && state.config.self_memory && state.config.self_memory.enabled);
    document.getElementById("pro-tab-sm").classList.toggle("hidden", !smOn);
    fillSessionSelect(proSessionSel, state.currentSessionId);
    fillSessionSelect(document.getElementById("job-session-sel"), state.currentSessionId);
    fillSessionSelect(document.getElementById("commit-session-sel"), state.currentSessionId);
    document.getElementById("job-weekday").disabled =
      document.getElementById("job-mode").value !== "weekly";
    proDlg.showModal();
    switchProTab("pro");
  };

  /* 侧栏入口：心情按钮随 mood 段，主动行为按钮随 proactor/self_memory 段显隐 */
  window.updateProactorButtons = function () {
    var moodBtn = document.getElementById("btn-mood");
    var proBtn = document.getElementById("btn-proactive");
    if (!state.config) return;
    if (moodBtn) {
      moodBtn.classList.toggle("hidden",
        !(state.config.mood && state.config.mood.enabled));
    }
    if (proBtn) {
      var proOn = !!(state.config.proactor && state.config.proactor.enabled);
      var smOn = !!(state.config.self_memory && state.config.self_memory.enabled);
      proBtn.classList.toggle("hidden", !(proOn || smOn));
    }
  };
})();
