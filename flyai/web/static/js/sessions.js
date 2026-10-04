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
/* 导入 FlyAI 导出包：读文件 → POST /api/sessions/import → 进入新会话 */
function importSessionFromFile(ev) {
  var input = ev.target;
  var f = input.files && input.files[0];
  if (!f) return;
  var reader = new FileReader();
  reader.onload = function () {
    var obj;
    try { obj = JSON.parse(reader.result); }
    catch (e) { showToast("文件不是有效的 JSON", "err"); return; }
    fetchJSON("/api/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(obj),
    }).then(function (s) {
      return refreshSessions().then(function () {
        return selectSession(s.id);
      }).then(function () {
        var sum = s.import_summary || {};
        showToast("已导入「" + (s.title || "对话") + "」"
          + "（复用卡 " + intOr(sum.reused_cards, 0)
          + " · 副本卡 " + intOr(sum.scoped_cards, 0) + "）", "ok", 4000);
      });
    }).catch(function (e) { showToast("导入失败: " + e.message, "err"); });
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
  shown.forEach(function (s) {
    var item = document.createElement("div");
    item.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
    var titleSpan = document.createElement("span");
    titleSpan.className = "session-title";
    titleSpan.textContent = (s.title || "新会话") + (s.threads && s.threads.length ? " 👥" : "");
    titleSpan.addEventListener("dblclick", function (ev) { ev.stopPropagation(); startRename(item, s); });
    // 触屏没有双击：长按标题 500ms 重命名
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
    el.sessionList.appendChild(item);
  });
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
