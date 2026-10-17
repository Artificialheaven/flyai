/* FlyAI 前端 · 会话列表、提供商栏与属性查看（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

function chosenProviderId() {
  if (!state.config) return "";
  var providers = state.config.providers || [];
  if (state.role === "admin") {
    return state.config.active_provider || (providers[0] ? providers[0].id : "");
  }
  var local = localStorage.getItem("flyai-provider") || "";
  if (local && providers.some(function (p) { return p.id === local; })) return local;
  return providers[0] ? providers[0].id : "";
}
function activeProvider() {
  if (!state.config) return null;
  var id = chosenProviderId();
  return (state.config.providers || []).find(function (x) {
    return x.id === id;
  }) || null;
}
function activeProviderName() {
  var p = activeProvider();
  return p ? (p.model || p.id) : "未配置提供商";
}
function visionEnabled() {
  var p = activeProvider();
  return !!(p && p.vision);
}
function renderProviderBar() {
  el.providerBar.textContent = "◈ " + activeProviderName();
  syncAttachVisible();
}
// 📎 按钮仅视觉模型可见；切到非视觉模型时清掉待发图片
function syncAttachVisible() {
  if (!el.btnAttach) return;
  el.btnAttach.classList.toggle("hidden", !visionEnabled());
  if (!visionEnabled() && state.pendingImages.length) {
    state.pendingImages = [];
    renderPendingImages();
  }
}

/* ==== 属性数值（点击 AI 头像查看，不常驻显示） ==== */
function attrDefs() {
  var rp = state.config && state.config.roleplay;
  return (rp && rp.attributes && rp.attributes.list) ? rp.attributes.list : [];
}
function attrViewerDefs() {
  var char = effectiveChar();
  if (char && char.attributes && char.attributes.length) return char.attributes;  // 角色卡/副本自带（优先）
  return attrDefs();  // 兼容旧角色卡：回退全局默认
}
function openAttrViewer() {
  if (!el.attrBody) return;
  var vals = state.currentAttrs || {};
  var defs = attrViewerDefs();
  var html = "";
  if (!defs.length) {
    html = '<div class="av-empty">尚未配置属性（可在角色卡中定义，或沿用全局默认）。</div>';
  } else if (!Object.keys(vals).length) {
    html = '<div class="av-empty">暂无属性数据，AI 还未更新数值。</div>';
  } else {
    var rows = defs.filter(function (d) { return d.key in vals; }).map(function (d) {
      return '<div class="av-row"><span class="av-name">' + escapeHtml(d.label || d.key) +
        '<em>' + escapeHtml(d.key) + "</em></span>" +
        '<span class="av-val">' + escapeHtml(vals[d.key]) + "</span></div>";
    }).join("");
    html = rows || '<div class="av-empty">暂无属性数据。</div>';
  }
  el.attrBody.innerHTML = html;
  safeCloseDialogs();
  if (el.attrDialog && el.attrDialog.showModal) el.attrDialog.showModal();
}

/* ==== 会话 ==== */
function renderSessionList() {
  if (el.searchWrap) el.searchWrap.classList.toggle("hidden", !!state.roomView);
  if (state.batchMode) { renderBatchSessions(); return; }
  if (state.roomView) { renderRoomNav(); return; }
  renderSessions();
}
/* 顶层：会话/房间列表 */
function filteredSessions() {
  var q = state.sessionQuery.trim().toLowerCase();
  if (!q) return state.sessions;
  return state.sessions.filter(function (s) {
    return (s.title || "新会话").toLowerCase().indexOf(q) >= 0;
  });
}
/* 导入成功后：刷新列表 → 进入新会话 → 提示卡 / 素材统计 */
function afterSessionImport(s) {
  return refreshSessions().then(function () {
    return selectSession(s.id);
  }).then(function () {
    var sum = s.import_summary || {};
    var msg = "已导入「" + (s.title || "对话") + "」（复用卡 "
      + intOr(sum.reused_cards, 0) + " · 副本卡 " + intOr(sum.scoped_cards, 0);
    if (intOr(sum.assets, 0)) msg += " · 素材 " + sum.assets + " 个";
    msg += "）";
    showToast(msg, "ok", 4000);
  });
}
/* 第三方对话导入（Chatbox 备份 JSON / Markdown）成功后：刷新列表并进入 */
function afterExternalImport(resp) {
  var list = resp.sessions || [resp];
  var sum = resp.import_summary || list[0].import_summary || {};
  return refreshSessions().then(function () {
    return selectSession(list[0].id);
  }).then(function () {
    var msg;
    if (list.length > 1) {
      msg = "已从备份导入 " + list.length + " 个对话（共 "
        + intOr(sum.messages, 0) + " 条消息）";
    } else {
      msg = "已导入「" + (list[0].title || "对话") + "」（"
        + intOr(sum.messages, 0) + " 条消息";
      if (intOr(sum.images, 0)) msg += " · 图片 " + sum.images;
      msg += "）";
    }
    showToast(msg, "ok", 4000);
  });
}
/* 导入对话：FlyAI .zip 自包含包 / v2 JSON 走原接口；
   Chatbox 备份 JSON 与 Markdown 走第三方导入接口（后端自动识别） */
function importSessionFromFile(ev) {
  var input = ev.target;
  var f = input.files && input.files[0];
  if (!f) return;

  var isZip = /\.zip$/i.test(f.name) ||
    (f.type || "").indexOf("zip") >= 0;
  if (isZip) {
    var fdZip = new FormData();
    fdZip.append("file", f);
    fetchJSON("/api/sessions/import-zip", { method: "POST", body: fdZip })
      .then(afterSessionImport)
      .catch(function (e) { showToast("导入失败: " + e.message, "err"); });
    return;
  }

  var reader = new FileReader();
  reader.onload = function () {
    var obj = null;
    try { obj = JSON.parse(reader.result); }
    catch (e) { obj = null; }  // Markdown 等非 JSON：交给第三方导入接口

    if (obj && obj.type === "flyai-conversation") {
      if (obj.version !== 2) {
        showToast("该导出包版本需使用 .zip 导入", "err");
        return;
      }
      fetchJSON("/api/sessions/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(obj),
      }).then(afterSessionImport)
        .catch(function (e) { showToast("导入失败: " + e.message, "err"); });
      return;
    }

    // Chatbox 备份 JSON / 单对话 Markdown / 其他第三方格式
    var fd = new FormData();
    fd.append("file", f);
    fetchJSON("/api/sessions/import-chatbox", { method: "POST", body: fd })
      .then(afterExternalImport)
      .catch(function (e) { showToast("导入失败: " + e.message, "err"); });
  };
  reader.readAsText(f);
}

function renderSessions() {
  el.sessionList.innerHTML = "";
  if (el.searchWrap) el.searchWrap.classList.remove("hidden");
  var shown = filteredSessions();
  if (!shown.length) {
    var empty = document.createElement("div");
    empty.className = "list-empty";
    if (state.sessionQuery.trim()) {
      empty.textContent = "没有匹配的会话";
    } else {
      var ico = document.createElement("div");
      ico.className = "le-ico";
      ico.textContent = "💬";
      empty.appendChild(ico);
      var t = document.createElement("div");
      t.textContent = "还没有会话";
      empty.appendChild(t);
      var b = document.createElement("button");
      b.className = "btn small primary";
      b.textContent = "＋ 新建对话";
      b.addEventListener("click", openNewChatDialog);
      empty.appendChild(b);
    }
    el.sessionList.appendChild(empty);
    return;
  }
  if (state.sessionQuery.trim()) {
    // 搜索结果不分组
    shown.forEach(function (s) { el.sessionList.appendChild(buildSessionItem(s)); });
    return;
  }
  // 按 updated_at/created_at 分组：今天 / 昨天 / 更早
  var groups = [
    { key: "today", label: "今天", list: [] },
    { key: "yesterday", label: "昨天", list: [] },
    { key: "earlier", label: "更早", list: [] },
  ];
  shown.forEach(function (s) {
    var g = sessionDateGroup(s.updated_at || s.created_at);
    groups[g].list.push(s);
  });
  var groupFirst = true;
  groups.forEach(function (grp) {
    if (!grp.list.length) return;
    var label = document.createElement("div");
    label.className = "session-group-label";
    var labelName = document.createElement("span");
    labelName.className = "s-group-name";
    labelName.textContent = grp.label;
    label.appendChild(labelName);
    // 第一个分组（今天/昨天/更早 中最靠前的）右侧放「多选」，进入批量删除
    if (groupFirst) {
      var multi = document.createElement("button");
      multi.type = "button";
      multi.className = "s-group-multi";
      multi.textContent = "多选";
      multi.title = "多选删除";
      multi.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openBatchMode();
      });
      label.appendChild(multi);
    }
    groupFirst = false;
    el.sessionList.appendChild(label);
    grp.list.forEach(function (s) { el.sessionList.appendChild(buildSessionItem(s)); });
  });
}

function sessionDateGroup(iso) {
  if (!iso) return 2;
  var d = new Date(iso);
  if (isNaN(d.getTime())) return 2;
  var now = new Date();
  var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  var dDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  var diffDays = Math.round((startToday - dDay) / 86400000);
  if (diffDays <= 0) return 0;
  if (diffDays === 1) return 1;
  return 2;
}

function buildSessionItem(s) {
    var item = document.createElement("div");
    item.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
    var titleSpan = document.createElement("span");
    titleSpan.className = "session-title";
    titleSpan.textContent = (s.title || "新会话") + (s.threads && s.threads.length ? " 👥" : "");
    // 触屏长按标题 500ms 也可重命名（桌面端点 ✏️ 按钮）
    var pressTimer = null;
    var longPressed = false;
    titleSpan.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType === "mouse" || state.busy) return;
      pressTimer = setTimeout(function () {
        pressTimer = null;
        longPressed = true;
        startRename(item, s);
      }, 500);
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach(function (t) {
      titleSpan.addEventListener(t, function () {
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      });
    });
    item.appendChild(titleSpan);
    item.addEventListener("click", function () {
      if (longPressed) { longPressed = false; return; }
      selectSession(s.id);
    });

    if (state.pendingResume && state.pendingResume.sid === s.id) {
      var resumeFlag = document.createElement("span");
      resumeFlag.className = "resume-flag";
      resumeFlag.textContent = "⏳";
      resumeFlag.title = "有未结束的回答，点击进入续读";
      item.appendChild(resumeFlag);
    }

    var ren = document.createElement("span");
    ren.className = "ren";
    ren.textContent = "✏️";
    ren.title = "重命名";
    ren.addEventListener("click", function (ev) {
      ev.stopPropagation();
      startRename(item, s);
    });
    item.appendChild(ren);

    var pin = document.createElement("span");
    pin.className = "pin" + (s.pinned ? " on" : "");
    pin.textContent = "📌";
    pin.title = s.pinned ? "取消置顶" : "置顶";
    pin.addEventListener("click", function (ev) { ev.stopPropagation(); togglePin(s.id); });
    item.appendChild(pin);

    var del = document.createElement("span");
    del.className = "del";
    del.textContent = "🗑";
    del.addEventListener("click", function (ev) { ev.stopPropagation(); onDeleteSession(s.id); });
    item.appendChild(del);

    // 手机端：⋯ 打开底部操作清单（置顶/重命名/删除）
    var more = document.createElement("span");
    more.className = "s-more";
    more.textContent = "⋯";
    more.title = "更多操作";
    more.addEventListener("click", function (ev) {
      ev.stopPropagation();
      openSheet([
        { label: s.pinned ? "取消置顶" : "置顶", fn: function () { togglePin(s.id); } },
        { label: "重命名", fn: function () { startRename(item, s); } },
        { label: "删除", danger: true, fn: function () { onDeleteSession(s.id); } },
      ]);
    });
    item.appendChild(more);

    return item;
}

/* 内联重命名 */
function startRename(item, s) {
  if (state.busy) return;
  var titleSpan = item.querySelector(".session-title");
  var inp = document.createElement("input");
  inp.type = "text"; inp.className = "rename-input"; inp.value = s.title || "";
  titleSpan.replaceWith(inp);
  inp.focus(); inp.select();
  var finished = false;
  function commit(save) {
    if (finished) return;
    finished = true;
    var v = inp.value.trim();
    inp.replaceWith(titleSpan);
    if (save && v && v !== s.title) patchSessionMeta(s.id, { title: v });
    else renderSessionList();
  }
  inp.addEventListener("keydown", function (e) {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); commit(true); }
    else if (e.key === "Escape") { e.preventDefault(); commit(false); }
  });
  inp.addEventListener("blur", function () { commit(true); });
  inp.addEventListener("click", function (ev) { ev.stopPropagation(); });
}
/* 置顶 / 重命名统一 PATCH */
function togglePin(sid) {
  var s = null;
  state.sessions.forEach(function (x) { if (x.id === sid) s = x; });
  patchSessionMeta(sid, { pinned: !!(s && s.pinned) ? false : true });
}
function patchSessionMeta(sid, body) {
  fetchJSON("/api/sessions/" + sid, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(function () { return refreshSessions(); })
    .catch(function (e) { showToast("操作失败: " + e.message, "err"); });
}

/* ==== 批量删除会话 ==== */
function openBatchMode() {
  if (state.busy) return;
  state.batchMode = true;
  state.roomView = false;
  state.batchPicked = {};
  renderSessionList();
}
function exitBatchMode() {
  state.batchMode = false;
  state.batchPicked = {};
  renderSessionList();
}
function renderBatchSessions() {
  el.sessionList.innerHTML = "";

  // 顶部工具条：标题 + 退出
  var bar = document.createElement("div");
  bar.className = "batch-bar";
  var title = document.createElement("span");
  title.className = "batch-title";
  title.textContent = "批量删除";
  var exit = document.createElement("button");
  exit.type = "button";
  exit.className = "footer-btn small-btn";
  exit.textContent = "✕ 退出";
  exit.addEventListener("click", exitBatchMode);
  bar.appendChild(title);
  bar.appendChild(exit);
  el.sessionList.appendChild(bar);

  filteredSessions().forEach(function (s) {
    var item = document.createElement("div");
    item.className = "session-item batch-item" + (state.batchPicked[s.id] ? " picked" : "");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "batch-cb";
    cb.checked = !!state.batchPicked[s.id];
    var label = document.createElement("span");
    label.className = "batch-label";
    label.textContent = (s.title || "新会话") + (s.threads && s.threads.length ? " 👥" : "");
    item.appendChild(cb);
    item.appendChild(label);
    function toggle() {
      if (state.batchPicked[s.id]) delete state.batchPicked[s.id];
      else state.batchPicked[s.id] = true;
      renderBatchSessions();
    }
    item.addEventListener("click", function (ev) {
      if (ev.target === cb) return;  // checkbox 自身 change 会触发
      toggle();
    });
    cb.addEventListener("change", function () {
      if (!cb.checked && state.batchPicked[s.id]) delete state.batchPicked[s.id];
      else if (cb.checked) state.batchPicked[s.id] = true;
      renderBatchSessions();
    });
    el.sessionList.appendChild(item);
  });

  // 底部操作条
  var n = Object.keys(state.batchPicked).length;
  var actions = document.createElement("div");
  actions.className = "batch-actions";
  var allBtn = document.createElement("button");
  allBtn.type = "button";
  allBtn.className = "footer-btn small-btn";
  allBtn.textContent = n === state.sessions.length && n > 0 ? "全不选" : "全选";
  allBtn.addEventListener("click", function () {
    if (n === state.sessions.length && n > 0) state.batchPicked = {};
    else {
      state.batchPicked = {};
      state.sessions.forEach(function (s) { state.batchPicked[s.id] = true; });
    }
    renderBatchSessions();
  });
  var delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "footer-btn small-btn danger";
  delBtn.textContent = "删除选中 (" + n + ")";
  delBtn.disabled = n === 0;
  delBtn.addEventListener("click", confirmBatchDelete);
  actions.appendChild(allBtn);
  actions.appendChild(delBtn);
  el.sessionList.appendChild(actions);
}
function confirmBatchDelete() {
  var ids = Object.keys(state.batchPicked);
  if (!ids.length) return;
  if (!confirm("确认删除选中的 " + ids.length + " 个会话？此操作不可恢复。")) return;
  fetchJSON("/api/sessions/batch-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: ids }),
  }).then(function (r) {
    if (state.batchPicked[state.currentSessionId]) {
      state.currentSessionId = null;
      clearMessages();
    }
    state.batchMode = false;
    state.batchPicked = {};
    return refreshSessions().then(function () {
      addBatchNotice((r && r.deleted) || 0);
    });
  });
}
function addBatchNotice(n) {
  // 简单的一次性提示条
  var bar = document.createElement("div");
  bar.className = "batch-notice";
  bar.textContent = "已删除 " + n + " 个会话";
  document.body.appendChild(bar);
  setTimeout(function () { bar.classList.add("show"); }, 10);
  setTimeout(function () {
    bar.classList.remove("show");
    setTimeout(function () { bar.remove(); }, 300);
  }, 2200);
}
