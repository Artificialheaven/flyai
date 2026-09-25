/* FlyAI 前端逻辑（原生 JS，无构建/无依赖）。 */

"use strict";

var state = {
  config: null,
  sessions: [],
  characters: [],
  worlds: [],
  currentSessionId: null,
  plugins: [],
  busy: false,
  abortController: null,
  currentAssistant: null,
  currentAttrs: null,
  userId: null,
  userName: "",
  scopeCache: {},   // sessionId -> {char, world, scoped}（有效角色/世界观，含副本）
  // beta：多 AI 群聊
  roomMembers: [],    // 当前房间参与者（session.members）
  roomThreads: [],    // 当前房间窗口（session.threads + 群聊）
  roomView: false,    // 左侧栏是否处于「房间窗口导航」状态
  currentThreadId: null,
  currentSpeaker: null, // {member_id,name,character_id} 流式当前发言者
};

var el = {};

document.addEventListener("DOMContentLoaded", function () {
  cacheElements();
  bindEvents();
  initTheme();
  bootstrap();
});

/* ==== 多用户 / 登录（独立登录页，不登录不能进） ==== */
function safeCloseDialogs() {
  [el.dialog, el.loreDialog, el.userDialog].forEach(function (d) {
    if (d && d.open) d.close();
  });
}
function authHeaders(extra) {
  var h = Object.assign({}, extra || {});
  if (state.userId) h["X-User-Id"] = state.userId;
  return h;
}
function goLogin() {
  window.location.href = "/login";
}
function renderUserBadge() {
  if (el.userName) {
    el.userName.textContent = "👤 " + (state.userName || "……") + (state.role === "admin" ? "（管理员）" : "");
  }
  if (el.btnUsers) el.btnUsers.classList.toggle("hidden", state.role !== "admin");
}
function clearAuth() {
  localStorage.removeItem("flyai-uid");
  localStorage.removeItem("flyai-role");
  state.userId = null;
  state.role = "";
  state.currentSessionId = null;
  state.currentAttrs = null;
  state.roomMembers = []; state.roomThreads = []; state.currentThreadId = null; state.currentSpeaker = null;
  try { clearMessages(); if (el.threads) el.threads.classList.add("hidden"); } catch (e) {}
}
function bootstrap() {
  var saved = localStorage.getItem("flyai-uid");
  if (!saved) { goLogin(); return; }  // 不登录不能进
  state.userId = saved;
  state.role = localStorage.getItem("flyai-role") || "user";
  fetchJSON("/api/auth/me").then(function (res) {
    state.userName = res.name;
    state.role = res.role || "user";
    localStorage.setItem("flyai-role", state.role);
    renderUserBadge();
    init();
  }).catch(function () { clearAuth(); goLogin(); });
}
function doLogout() {
  fetch("/api/auth/logout", { method: "POST", headers: authHeaders() }).catch(function () {});
  clearAuth();
  goLogin();
}

/* ==== 用户管理（仅管理员） ==== */
function openUserDialog() {
  safeCloseDialogs();
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  renderUserList();
  el.userDialog.showModal();
  el.userNameInput.focus();
}
function renderUserList() {
  el.userList.innerHTML = "";
  fetchJSON("/api/auth/users", { method: "GET", headers: authHeaders() }).then(function (r) {
    (r.users || []).forEach(function (u) {
      var row = document.createElement("div");
      row.className = "user-row item";
      var name = document.createElement("span");
      name.className = "user-name";
      name.textContent = u.name;
      row.appendChild(name);
      var del = document.createElement("button");
      del.className = "btn small";
      del.textContent = "删除";
      del.addEventListener("click", function () { deleteUser(u.id, u.name, row, del); });
      row.appendChild(del);
      el.userList.appendChild(row);
    });
  }).catch(function (e) { el.userList.textContent = "加载失败: " + e.message; });
}
function deleteUser(uid, name, row, btn) {
  if (!confirm("确定删除用户「" + name + "」？其角色卡/世界观/会话数据也会被一并移除。")) return;
  btn.disabled = true;
  fetchJSON("/api/auth/users/" + encodeURIComponent(uid), { method: "DELETE", headers: authHeaders() })
    .then(function () { row.remove(); })
    .catch(function (e) {
      el.userMsg.textContent = "删除失败: " + e.message; el.userMsg.className = "msg-bar err";
      btn.disabled = false;
    });
}
function createUser() {
  var name = el.userNameInput.value.trim();
  var password = el.userPasswordInput.value;
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  if (!name) { el.userMsg.textContent = "请输入用户名"; el.userMsg.className = "msg-bar err"; return; }
  fetchJSON("/api/auth/users", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name: name, password: password }),
  }).then(function () {
    el.userMsg.textContent = "已创建用户「" + name + "」"; el.userMsg.className = "msg-bar ok";
    el.userNameInput.value = ""; el.userPasswordInput.value = "";
    renderUserList();
    el.userNameInput.focus();
  }).catch(function (e) {
    el.userMsg.textContent = "创建失败: " + e.message; el.userMsg.className = "msg-bar err";
  });
}

function cacheElements() {
  el.sessionList = document.getElementById("session-list");
  el.messages = document.getElementById("messages");
  el.sessionMeta = document.getElementById("session-meta");
  el.input = document.getElementById("input");
  el.btnSend = document.getElementById("btn-send");
  el.btnStop = document.getElementById("btn-stop");
  el.btnNew = document.getElementById("btn-new-session");
  el.btnSettings = document.getElementById("btn-settings");
  el.btnLore = document.getElementById("btn-lore");
  el.btnTheme = document.getElementById("btn-theme");
  el.providerBar = document.getElementById("provider-bar");
  el.dialog = document.getElementById("settings-dialog");
  el.providerList = document.getElementById("provider-list");
  el.btnAddProvider = document.getElementById("btn-add-provider");
  el.setMaxIters = document.getElementById("set-max-iters");
  el.setTimeout = document.getElementById("set-timeout");
  el.setReadDirs = document.getElementById("set-read-dirs");
  el.pluginChecks = document.getElementById("plugin-checks");
  el.btnConfigSave = document.getElementById("btn-config-save");
  el.btnConfigCancel = document.getElementById("btn-config-cancel");
  el.configMsg = document.getElementById("config-msg");
  el.loreDialog = document.getElementById("lore-dialog");
  el.loreBody = document.getElementById("lore-body");
  el.attrDialog = document.getElementById("attr-dialog");
  el.attrBody = document.getElementById("attr-body");
  el.btnAttrClose = document.getElementById("btn-attr-close");
  el.attrgenDialog = document.getElementById("attrgen-dialog");
  el.attrgenBody = document.getElementById("attrgen-body");
  el.attrgenMsg = document.getElementById("attrgen-msg");
  el.btnAttrgenCancel = document.getElementById("btn-attrgen-cancel");
  el.btnAttrgenAdd = document.getElementById("btn-attrgen-add");
  el.btnAttrgenConfirm = document.getElementById("btn-attrgen-confirm");
  el.exportDialog = document.getElementById("export-dialog");
  el.exportBody = document.getElementById("export-body");
  el.exportSub = document.getElementById("export-sub");
  el.btnExportClose = document.getElementById("btn-export-close");
  el.btnExportDownload = document.getElementById("btn-export-download");
  el.btnExportPublish = document.getElementById("btn-export-publish");
  el.btnNav = document.getElementById("btn-nav");
  el.overlay = document.getElementById("overlay");
  el.setRpFormat = document.getElementById("set-rp-format");
  el.setAttrEnabled = document.getElementById("set-attr-enabled");
  el.setAttrAi = document.getElementById("set-attr-ai");
  el.btnLogout = document.getElementById("btn-logout");
  el.btnJump = document.getElementById("btn-jump-bottom");
  el.userName = document.getElementById("user-name");
  el.btnUsers = document.getElementById("btn-users");
  el.userDialog = document.getElementById("user-dialog");
  el.userList = document.getElementById("user-list");
  el.userNameInput = document.getElementById("user-new-name");
  el.userPasswordInput = document.getElementById("user-new-password");
  el.userMsg = document.getElementById("user-msg");
  el.btnUserCreate = document.getElementById("btn-user-create");
  el.btnUserCancel = document.getElementById("btn-user-cancel");
  // beta：多 AI 群聊
  el.threads = document.getElementById("threads");
  el.newchatDialog = document.getElementById("newchat-dialog");
  el.newchatPick = document.getElementById("newchat-pick");
  el.newchatTitle = document.getElementById("newchat-title");
  el.newchatMsg = document.getElementById("newchat-msg");
  el.btnNewchatCancel = document.getElementById("btn-newchat-cancel");
  el.btnNewchatCreate = document.getElementById("btn-newchat-create");
  el.groupDialog = document.getElementById("group-dialog");
  el.groupPick = document.getElementById("group-pick");
  el.groupMsg = document.getElementById("group-msg");
  el.btnGroupCancel = document.getElementById("btn-group-cancel");
  el.btnGroupCreate = document.getElementById("btn-group-create");
  el.memDialog = document.getElementById("mem-dialog");
  el.memTitle = document.getElementById("mem-title");
  el.memBody = document.getElementById("mem-body");
  el.btnMemClose = document.getElementById("btn-mem-close");
}

function bindEvents() {
  el.btnSend.addEventListener("click", onSend);
  el.btnStop.addEventListener("click", onStop);
  el.btnNew.addEventListener("click", openNewChatDialog);
  el.btnSettings.addEventListener("click", openSettings);
  el.btnLore.addEventListener("click", openLoreDialog);
  el.btnTheme.addEventListener("click", toggleTheme);
  el.btnAddProvider.addEventListener("click", addProviderRow);
  el.btnConfigSave.addEventListener("click", saveConfig);
  el.btnConfigCancel.addEventListener("click", function () { if (guardDiscard()) { clearDirty(); el.dialog.close(); } });
  el.input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onSend(); }
    if (e.key === "Escape") { el.input.blur(); }
  });
  if (el.btnNav) el.btnNav.addEventListener("click", toggleNav);
  if (el.overlay) el.overlay.addEventListener("click", closeNav);
  if (el.btnLogout) el.btnLogout.addEventListener("click", doLogout);
  if (el.btnUsers) el.btnUsers.addEventListener("click", openUserDialog);
  if (el.btnJump) el.btnJump.addEventListener("click", jumpBottom);
  if (el.messages) el.messages.addEventListener("scroll", updateJumpBtn);
  if (el.btnUserCreate) el.btnUserCreate.addEventListener("click", createUser);
  if (el.btnUserCancel) el.btnUserCancel.addEventListener("click", function () { el.userDialog.close(); });
  // beta：多 AI 群聊
  if (el.btnNewchatCancel) el.btnNewchatCancel.addEventListener("click", function () { if (el.newchatDialog) el.newchatDialog.close(); });
  if (el.btnNewchatCreate) el.btnNewchatCreate.addEventListener("click", confirmNewChat);
  if (el.btnGroupCancel) el.btnGroupCancel.addEventListener("click", function () { if (el.groupDialog) el.groupDialog.close(); });
  if (el.btnGroupCreate) el.btnGroupCreate.addEventListener("click", confirmGroup);
  if (el.btnMemClose) el.btnMemClose.addEventListener("click", function () { if (el.memDialog) el.memDialog.close(); });
  if (el.btnAttrClose) el.btnAttrClose.addEventListener("click", function () { if (el.attrDialog) el.attrDialog.close(); });
  if (el.btnAttrgenCancel) el.btnAttrgenCancel.addEventListener("click", function () { if (el.attrgenDialog) el.attrgenDialog.close(); });
  if (el.btnAttrgenAdd) el.btnAttrgenAdd.addEventListener("click", function () { addAttrRow(el.attrgenBody); });
  if (el.btnAttrgenConfirm) el.btnAttrgenConfirm.addEventListener("click", doAttrgenConfirm);
  if (el.btnExportClose) el.btnExportClose.addEventListener("click", function () { if (el.exportDialog) el.exportDialog.close(); });
  if (el.btnExportDownload) el.btnExportDownload.addEventListener("click", downloadConversation);
  if (el.btnExportPublish) el.btnExportPublish.addEventListener("click", publishConversationCharacter);
  if (el.userPasswordInput) el.userPasswordInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); createUser(); }
  });
  initDialogs();
}

/* ==== 弹窗：点击空白关闭 + 未保存修改询问 ==== */
var formDirty = false;
function markDirty() { formDirty = true; }
function clearDirty() { formDirty = false; }
function guardDiscard() {
  if (formDirty) return confirm("您有未保存的修改，确定要放弃并关闭吗？");
  return true;
}
function guardedNav(fn) {
  if (!guardDiscard()) return;
  clearDirty();
  fn();
}
function initDialogs() {
  [el.dialog, el.loreDialog, el.userDialog].forEach(function (d) {
    // 点击弹窗外空白（backdrop）：目标即 dialog 元素本身
    d.addEventListener("click", function (e) {
      if (e.target === d && guardDiscard()) { clearDirty(); d.close(); }
    });
    // Esc：cancel 事件在关闭前触发，可拦截
    d.addEventListener("cancel", function (e) {
      if (!guardDiscard()) e.preventDefault();
      else clearDirty();
    });
    d.addEventListener("input", markDirty);
    d.addEventListener("change", markDirty);
  });
  // 轻量弹窗（属性查看 / 属性生成 / 压缩导出 / 新建对话 / 群聊 / 记忆）：点击空白直接关闭
  [el.attrDialog, el.attrgenDialog, el.exportDialog, el.newchatDialog, el.groupDialog, el.memDialog].forEach(function (d) {
    if (!d) return;
    d.addEventListener("click", function (e) { if (e.target === d) d.close(); });
  });
}

/* ==== 移动端抽屉 ==== */
function toggleNav() { document.body.classList.toggle("nav-open"); syncOverlay(); }
function closeNav() { document.body.classList.remove("nav-open"); syncOverlay(); }
function syncOverlay() {
  if (!el.overlay) return;
  el.overlay.classList.toggle("hidden", !document.body.classList.contains("nav-open"));
}

/* ==== 主题 ==== */
function initTheme() {
  var saved = localStorage.getItem("flyai-theme") || "dark";
  applyTheme(saved);
}
function applyTheme(theme) {
  document.body.className = "theme-" + theme;
  localStorage.setItem("flyai-theme", theme);
}
function toggleTheme() {
  applyTheme(document.body.className.indexOf("light") >= 0 ? "dark" : "light");
}

/* ==== 初始化 ==== */
function init() {
  return Promise.all([
    fetchJSON("/api/config"),
    fetchJSON("/api/sessions"),
    fetchJSON("/api/plugins"),
    fetchJSON("/api/characters"),
    fetchJSON("/api/worlds"),
  ]).then(function (res) {
    state.config = res[0];
    state.sessions = res[1].sessions || [];
    state.plugins = res[2].plugins || [];
    state.characters = res[3].characters || [];
    state.worlds = res[4].worlds || [];
    renderProviderBar();
    renderSessionList();
    renderSessionMeta();
  }).catch(function (e) {
    addErrorBar("初始化失败: " + e.message);
  });
}

function fetchJSON(url, opts) {
  opts = opts || {};
  var headers = authHeaders(opts.headers || {});
  return fetch(url, Object.assign({}, opts, { headers: headers })).then(function (r) {
    if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || r.status); });
    return r.json();
  });
}

function activeProviderName() {
  if (!state.config) return "";
  var p = (state.config.providers || []).find(function (x) { return x.id === state.config.active_provider; });
  return p ? (p.model || p.id) : "未配置提供商";
}
function renderProviderBar() {
  el.providerBar.textContent = "◈ " + activeProviderName();
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

/* ==== R7：导出 / 压缩 / 会话副本 ==== */
function downloadJSON(obj, filename) {
  var blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}
function downloadConversation() {
  var s = currentSession(); if (!s) return;
  fetch("/api/sessions/" + s.id + "/export", { headers: authHeaders() })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (obj) { downloadJSON(obj, (obj.name || "conversation") + ".json"); })
    .catch(function (e) { alert("导出对话失败: " + e.message); });
}
function openCompressDialog() {
  var s = currentSession(); if (!s) return;
  el.exportSub.textContent = "AI 正在把对话历史归纳进角色卡与世界观并分类…";
  el.exportBody.innerHTML = '<div class="sub">压缩中，请稍候…</div>';
  el.btnExportDownload.classList.add("hidden");
  el.btnExportPublish.classList.add("hidden");
  safeCloseDialogs();
  if (el.exportDialog.showModal) el.exportDialog.showModal();
  fetchJSON("/api/sessions/" + s.id + "/compress", { method: "POST" }).then(function (res) {
    el.exportSub.textContent = "压缩完成，已写入当前会话副本（库主卡不受影响）。";
    el.exportBody.innerHTML = renderCompressSummary(res.character, res.world);
    el.btnExportDownload.classList.remove("hidden");
    if (res.character && res.character.name) el.btnExportPublish.classList.remove("hidden");
  }).catch(function (e) {
    el.exportSub.textContent = "压缩失败";
    el.exportBody.innerHTML = '<div class="msg-bar err">' + escapeHtml(e.message) + "</div>";
  });
}
function renderCompressSummary(char, world) {
  var h = '<div class="cs-block"><b>📇 压缩后的角色卡</b>';
  if (char) {
    h += "<div>" + escapeHtml(char.name) + "</div>";
    if (char.intro) h += "<div class='sub'>简介：" + escapeHtml(char.intro) + "</div>";
    if (char.background) h += "<div class='sub'>经历：" + escapeHtml(char.background) + "</div>";
    if (char.table && char.table.length) h += listEl(char.table);
  } else h += "<div class='sub'>（无）</div>";
  h += "</div><div class='cs-block'><b>🌍 压缩后的世界观</b>";
  if (world) {
    if (world.content) h += "<div class='sub'>" + escapeHtml(world.content) + "</div>";
    if (world.table && world.table.length) h += listEl(world.table);
  } else h += "<div class='sub'>（无）</div>";
  h += "</div>";
  return h;
  function listEl(tbl) {
    return "<ul class='cs-list'>" + tbl.map(function (t) {
      return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
    }).join("") + "</ul>";
  }
}
function publishConversationCharacter() {
  var s = currentSession(); if (!s) return;
  fetchJSON("/api/sessions/" + s.id + "/character/publish", { method: "POST" }).then(function (res) {
    alert("已保存为角色卡：" + (res.character.name || ""));
  }).catch(function (e) { alert(e.message); });
}
function forkSessionChar() {
  var s = currentSession(); if (!s) return;
  fetchJSON("/api/sessions/" + s.id + "/character/fork", { method: "POST" }).then(function (res) {
    state.scopeCache[s.id] = { char: res.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: true };
    renderSessionMeta();
  }).catch(function (e) { alert(e.message); });
}
function restoreSessionChar() {
  var s = currentSession(); if (!s) return;
  if (!confirm("删除会话副本并还原为库主卡？")) return;
  fetchJSON("/api/sessions/" + s.id + "/character/override", { method: "DELETE" }).then(function (res) {
    state.scopeCache[s.id] = { char: res.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: false };
    renderSessionMeta();
  }).catch(function (e) { alert(e.message); });
}
function exportSessionChar() {
  var s = currentSession(); if (!s) return;
  fetch("/api/sessions/" + s.id + "/character/export", { headers: authHeaders() })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (obj) { downloadJSON(obj, ((obj.data && obj.data.name) || "character") + ".json"); })
    .catch(function (e) { alert("导出失败: " + e.message); });
}
function editSessionChar() {
  var s = currentSession(); if (!s) return;
  var char = effectiveChar();
  if (!char) return;
  characterEditor(char, true);  // 副本态：保存写入会话 override
}

/* ==== R7：AI 属性生成 ==== */
var attrgenTarget = null;
function openAttrgenDialog(seedAttrs, onConfirm) {
  el.attrgenBody.innerHTML = "";
  var rows = (seedAttrs && seedAttrs.length) ? seedAttrs : [{ key: "", label: "" }];
  rows.forEach(function (a) { addAttrRow(el.attrgenBody, a); });
  attrgenTarget = onConfirm || function () {};
  el.attrgenMsg.textContent = "";
  el.attrgenMsg.className = "msg-bar";
  el.attrgenDialog.showModal();
}
function doAttrgenConfirm() {
  var attrs = readAttrs(el.attrgenBody);
  if (!attrs.length) { el.attrgenMsg.textContent = "请至少填写一条属性的字段名"; el.attrgenMsg.className = "msg-bar err"; return; }
  var fn = attrgenTarget;
  attrgenTarget = null;
  fn(attrs);
  el.attrgenDialog.close();
}
function sessionAttrGen() {
  var s = currentSession(); if (!s) return;
  var char = effectiveChar(); if (!char) { alert("请先绑定角色再生成属性"); return; }
  var sc = state.scopeCache[s.id]; var scoped = sc ? sc.scoped : false;
  var payload = { name: char.name || "", intro: char.intro || "", background: char.background || "", existing: char.attributes || [] };
  fetchJSON("/api/gen/attributes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function (res) {
    openAttrgenDialog(res.attributes || [], function (attrs) {
      var merged = (char.attributes || []).concat(attrs);
      if (scoped) {
        var upd = JSON.parse(JSON.stringify(char)); upd.attributes = merged;
        fetchJSON("/api/sessions/" + s.id + "/character", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ character: upd }) }).then(function (rr) {
          state.scopeCache[s.id] = { char: rr.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: true };
          renderSessionMeta(); alert("已添加属性到会话副本");
        }).catch(function (e) { alert(e.message); });
      } else {
        var lib = JSON.parse(JSON.stringify(char)); lib.attributes = merged;
        fetchJSON("/api/characters/" + char.id, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(lib) }).then(function () {
          if (state.scopeCache[s.id]) state.scopeCache[s.id].char.attributes = merged;
          return refreshLore();
        }).then(function () { renderSessionMeta(); alert("已添加属性到角色卡"); }).catch(function (e) { alert(e.message); });
      }
    });
  }).catch(function (e) { alert("生成失败: " + e.message); });
}

/* ==== 角色扮演内容渲染（分段 + 样式区分）==== */
function rpEnabled() {
  var rp = state.config && state.config.roleplay;
  return !!(rp && rp.format_enabled);
}
// 把动作() / 内心________ / 对白"「」" 分别包成带样式的 span
function renderRp(text) {
  var re = /____([\s\S]*?)____|(「[^」\n]*」|“[^”\n]*”|"[^"\n]*"|『[^』\n]*』)|（[^（）\n]*）|\([^()\n]*\)/g;
  var html = "", last = 0, m;
  while ((m = re.exec(text)) !== null) {
    html += escapeHtml(text.slice(last, m.index));
    var tok = m[0], cls;
    if (tok.indexOf("____") === 0) cls = "dlg-thought";
    else if (tok.charAt(0) === "「" || tok.charAt(0) === "“" || tok.charAt(0) === '"' || tok.charAt(0) === "『") cls = "dlg-speech";
    else cls = "dlg-action";
    html += '<span class="' + cls + '">' + escapeHtml(tok) + "</span>";
    last = m.index + m[0].length;
  }
  html += escapeHtml(text.slice(last));
  return html;
}
// 设置 assistant 内容区；启用格式则渲染段落样式，否则纯文本
function setAssistantContent(a, text) {
  a.rawContent = text || "";
  if (rpEnabled()) a.contentEl.innerHTML = renderRp(a.rawContent);
  else a.contentEl.textContent = a.rawContent;
}

// 内置默认要求（角色卡/世界观编辑框预填，可覆盖）
function defaultFormatReq() {
  return (state.config && state.config.roleplay && state.config.roleplay.format_instruction) || "";
}
function defaultToolReq() {
  return (state.config && state.config.roleplay && state.config.roleplay.tool_instruction) || "";
}

/* ==== 会话 ==== */
function renderSessionList() {
  if (state.roomView) { renderRoomNav(); return; }
  renderSessions();
}
/* 顶层：会话/房间列表 */
function renderSessions() {
  el.sessionList.innerHTML = "";
  state.sessions.forEach(function (s) {
    var item = document.createElement("div");
    item.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
    item.textContent = (s.title || "新会话") + (s.threads && s.threads.length ? " 👥" : "");
    item.addEventListener("click", function () { selectSession(s.id); });
    var del = document.createElement("span");
    del.className = "del";
    del.textContent = "🗑";
    del.addEventListener("click", function (ev) { ev.stopPropagation(); onDeleteSession(s.id); });
    item.appendChild(del);
    el.sessionList.appendChild(item);
  });
}
/* 房间内：左侧栏展示该房间的各窗口（每 AI 私聊 + 群聊 + 建群/记忆），顶部返回键 */
function renderRoomNav() {
  el.sessionList.innerHTML = "";
  var back = document.createElement("div");
  back.className = "session-item back";
  back.textContent = "← 返回会话";
  back.addEventListener("click", backToSessions);
  el.sessionList.appendChild(back);
  var s = currentSession();
  var head = document.createElement("div");
  head.className = "room-head";
  head.textContent = (s && s.title) || "群聊房间";
  el.sessionList.appendChild(head);
  state.roomThreads.forEach(function (t) {
    var item = document.createElement("div");
    item.className = "session-item thread-item" + (t.id === state.currentThreadId ? " active" : "");
    item.textContent = (t.kind === "group" ? "👥 " : "💬 ") + (t.title || "窗口");
    item.addEventListener("click", function () { loadThread(t.id); });
    el.sessionList.appendChild(item);
  });
  var aiCount = (state.roomMembers || []).filter(function (m) { return !m.is_user; }).length;
  if (aiCount >= 2) {
    var add = document.createElement("div");
    add.className = "session-item add";
    add.textContent = "＋ 建群聊";
    add.addEventListener("click", openGroupDialog);
    el.sessionList.appendChild(add);
  }
  if ((state.roomMembers || []).length) {
    var mem = document.createElement("div");
    mem.className = "session-item add";
    mem.textContent = "🧠 记忆";
    mem.addEventListener("click", openMemDialog);
    el.sessionList.appendChild(mem);
  }
}
function backToSessions() {
  state.roomView = false;
  clearMessages();
  if (el.threads) el.threads.classList.add("hidden");
  renderSessionList();
  renderSessionMeta();
}

function effectiveChar() {
  var s = currentSession();
  if (!s) return null;
  var sc = state.scopeCache[s.id];
  if (sc && sc.char) return sc.char;
  return characterOf(s.character_id) || null;
}
function effectiveWorld() {
  var s = currentSession();
  if (!s) return null;
  var sc = state.scopeCache[s.id];
  if (sc && sc.world) return sc.world;
  return worldOf(s.world_id) || null;
}
function isScoped() {
  var s = currentSession();
  if (!s) return false;
  var sc = state.scopeCache[s.id];
  return sc ? !!sc.scoped : false;
}

function renderSessionMeta() {
  var s = currentSession();
  if (!s) { el.sessionMeta.innerHTML = '<span class="chip">新建会话以开始</span>'; return; }

  if (roomMode()) {
    // 房间：不显示「绑定单个角色」的选择器，仅展示成员与世界观
    var names = (state.roomMembers || []).filter(function (m) { return !m.is_user; }).map(function (m) { return m.name; });
    var world = effectiveWorld();
    var worldChip = document.createElement("span");
    worldChip.className = "chip";
    worldChip.innerHTML = "🌍 世界观 " + worldSelectOptions("world", world ? world.id : "", "无世界观");
    el.sessionMeta.innerHTML = "";
    var roomChip = document.createElement("span");
    roomChip.className = "chip";
    roomChip.innerHTML = "👥 多AI房间" + (names.length ? " · " + escapeHtml(names.join("、")) : "");
    el.sessionMeta.appendChild(roomChip);
    el.sessionMeta.appendChild(worldChip);
    el.sessionMeta.appendChild(makeMetaActions(null, false));
    var selW = el.sessionMeta.querySelector('select[data-world]');
    if (selW) selW.addEventListener("change", function () { updateSessionBinding("world_id", selW.value); });
    return;
  }

  var char = effectiveChar();
  var world = effectiveWorld();
  var scoped = isScoped();

  /* 可点击的 AI 头像 → 查看属性数值 */
  var avatarBtn = document.createElement("button");
  avatarBtn.className = "ai-avatar" + (scoped ? " scoped" : "");
  avatarBtn.title = "查看属性数值" + (scoped ? "（会话副本）" : "");
  avatarBtn.innerHTML = makeAvatarHtml(char, false);
  avatarBtn.addEventListener("click", openAttrViewer);

  var charChip = document.createElement("span");
  charChip.className = "chip";
  charChip.innerHTML = "👤 角色 " + (scoped ? "<span class='scoped-badge'>副本</span>" : "") + makeAvatarHtml(char, true) + " " + charSelectOptions("char", char ? char.id : "", "无角色");
  var worldChip = document.createElement("span");
  worldChip.className = "chip";
  worldChip.innerHTML = "🌍 世界观 " + worldSelectOptions("world", world ? world.id : "", "无世界观");
  el.sessionMeta.innerHTML = "";
  el.sessionMeta.appendChild(avatarBtn);
  el.sessionMeta.appendChild(charChip);
  el.sessionMeta.appendChild(worldChip);
  el.sessionMeta.appendChild(makeMetaActions(char, scoped));

  var selChar = el.sessionMeta.querySelector('select[data-char]');
  var selWorld = el.sessionMeta.querySelector('select[data-world]');
  if (selChar) selChar.addEventListener("change", function () { updateSessionBinding("character_id", selChar.value); });
  if (selWorld) selWorld.addEventListener("change", function () { updateSessionBinding("world_id", selWorld.value); });
}

function makeMetaActions(char, scoped) {
  var wrap = document.createElement("span");
  wrap.className = "meta-actions chip";
  var s = currentSession();
  var html = "";
  html += '<button class="ma" data-act="export" title="导出对话（消息+角色卡+世界观）">⬇ 导出</button>';
  html += '<button class="ma" data-act="compress" title="AI 压缩对话进角色卡/世界观后导出">☁ 压缩</button>';
  if (char) {
    if (scoped) {
      html += '<i class="ma-sep"></i><button class="ma" data-act="edit" title="编辑会话副本">✎ 编辑副本</button>';
      html += '<button class="ma" data-act="exportchar" title="副本导出为角色卡">⬇ 副本导出</button>';
      html += '<button class="ma" data-act="publish" title="保存为角色库中的新角色卡">💾 入库</button>';
      html += '<button class="ma" data-act="restore" title="还原为库主卡">♻ 还原</button>';
    } else {
      html += '<i class="ma-sep"></i><button class="ma" data-act="fork" title="复制一份仅当前会话可改的副本">📄 派生副本</button>';
    }
    html += '<i class="ma-sep"></i><button class="ma" data-act="attrgen" title="AI 依据角色设定建议属性">✨ AI属性</button>';
  }
  wrap.innerHTML = html;
  wrap.querySelectorAll("button[data-act]").forEach(function (b) {
    b.addEventListener("click", function () {
      MAP_ACT_TYPE[b.dataset.act] && MAP_ACT_TYPE[b.dataset.act]();
    });
  });
  return wrap;
}
var MAP_ACT_TYPE = {
  export: downloadConversation,
  compress: openCompressDialog,
  edit: editSessionChar,
  exportchar: exportSessionChar,
  publish: publishConversationCharacter,
  restore: restoreSessionChar,
  fork: forkSessionChar,
  attrgen: sessionAttrGen,
};

function charSelectOptions(kind, selected, emptyLabel) {
  var opts = ['<option value="">' + emptyLabel + '</option>'];
  state.characters.forEach(function (c) {
    opts.push('<option value="' + c.id + '"' + (c.id === selected ? " selected" : "") + '>' + escapeHtml(c.name || "未命名") + '</option>');
  });
  return '<select data-char>' + opts.join("") + '</select>';
}
function worldSelectOptions(kind, selected, emptyLabel) {
  var opts = ['<option value="">' + emptyLabel + '</option>'];
  state.worlds.forEach(function (w) {
    opts.push('<option value="' + w.id + '"' + (w.id === selected ? " selected" : "") + '>' + escapeHtml(w.name || "未命名") + '</option>');
  });
  return '<select data-world>' + opts.join("") + '</select>';
}

function currentSession() {
  return state.sessions.find(function (s) { return s.id === state.currentSessionId; });
}
function characterOf(id) { return state.characters.find(function (c) { return c.id === id; }); }
function worldOf(id) { return state.worlds.find(function (w) { return w.id === id; }); }

function refreshSessions() {
  return fetchJSON("/api/sessions").then(function (r) {
    state.sessions = r.sessions || [];
    renderSessionList();
    renderSessionMeta();
  });
}

function updateSessionBinding(field, value) {
  if (!state.currentSessionId) return;
  var body = {};
  body[field] = value;
  fetchJSON("/api/sessions/" + state.currentSessionId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(function (s) {
    var sess = currentSession();
    if (sess) { sess[field] = s[field]; }
    delete state.scopeCache[state.currentSessionId];  // 重新命中库卡
    renderSessionMeta();
  }).catch(function (e) { addErrorBar("设置失败: " + e.message); });
}

function openNewChatDialog() {
  if (state.busy) return;
  el.newchatPick.innerHTML = "";
  el.newchatTitle.value = "";
  el.newchatMsg.textContent = "";
  var chars = state.characters || [];
  if (!chars.length) {
    el.newchatMsg.textContent = "角色库还没有角色卡片，请先在「🧩 内容」里创建。";
    el.newchatMsg.className = "msg-bar err";
  }
  chars.forEach(function (c) {
    var lbl = document.createElement("label");
    lbl.className = "f-label toggle check-line";
    var cb = document.createElement("input"); cb.type = "checkbox"; cb.value = c.id;
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(" " + escapeHtml(c.name || "未命名")));
    el.newchatPick.appendChild(lbl);
  });
  safeCloseDialogs();
  el.newchatDialog.showModal();
}

function confirmNewChat() {
  var title = el.newchatTitle.value.trim();
  var ids = [];
  el.newchatPick.querySelectorAll("input:checked").forEach(function (cb) { ids.push(cb.value); });
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  var doCreate = function (url, body) {
    return fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (s) {
        state.currentSessionId = s.id;
        state.currentAttrs = null;
        el.newchatDialog.close();
        return refreshSessions();
      }).then(function () {
        selectSession(state.currentSessionId);
        el.input.focus();
      });
  };
  if (ids.length >= 1) {
    // 多 AI 房间：每个角色一条私聊窗口，后续可建群聊
    doCreate("/api/rooms", { title: title, character_ids: ids })
      .catch(function (e) { el.newchatMsg.textContent = "创建房间失败: " + e.message; el.newchatMsg.className = "msg-bar err"; });
  } else {
    // 单 AI 对话（沿用旧接口）
    doCreate("/api/sessions", { title: title })
      .catch(function (e) { el.newchatMsg.textContent = "创建失败: " + e.message; el.newchatMsg.className = "msg-bar err"; });
  }
}

function selectSession(id) {
  if (state.busy) return;
  state.currentSessionId = id;
  renderSessionList();
  closeNav();
  fetchJSON("/api/sessions/" + id).then(function (s) {
    // 更新本地 session 中的角色/世界与标题
    var sess = currentSession();
    if (sess) { sess.character_id = s.character_id; sess.world_id = s.world_id; sess.title = s.title; }
    state.currentAttrs = s.attributes || null;
    state.scopeCache[id] = { char: s.character || null, world: s.world || null, scoped: !!s.scoped };
    // beta：多 AI 房间；有窗口时左侧栏切换为该房间的窗口导航
    state.roomMembers = s.members || [];
    state.roomThreads = s.threads || [];
    state.currentSpeaker = null;
    state.currentThreadId = null;
    state.roomView = state.roomThreads.length > 0;
    renderSessionList();
    renderSessionMeta();
    if (state.roomView) {
      loadThread(state.roomThreads[0].id);
    } else {
      renderHistory(s.messages || []);
    }
  }).catch(function (e) { addErrorBar("加载会话失败: " + e.message); });
}

function memberOf(idOrName) {
  return (state.roomMembers || []).find(function (m) {
    return m.id === idOrName || m.name === idOrName || !idOrName;
  });
}
function roomMode() {
  return !!(state.roomThreads && state.roomThreads.length);
}

function renderThreads() {
  renderSessionList();  // 房间窗口改由左侧栏渲染（renderRoomNav），此函数仅作统一入口
}

function loadThread(tid) {
  if (!state.currentSessionId) return;
  state.currentThreadId = tid;
  state.currentSpeaker = null;
  renderThreads();
  fetchJSON("/api/rooms/" + state.currentSessionId + "/threads/" + tid).then(function (t) {
    var idx = -1;
    state.roomThreads.forEach(function (x, i) { if (x.id === tid) idx = i; });
    if (idx >= 0) state.roomThreads[idx] = t; else state.roomThreads.push(t);
    renderHistory(t.messages || []);
  }).catch(function (e) { addErrorBar("加载窗口失败: " + e.message); });
}

function openGroupDialog() {
  el.groupPick.innerHTML = "";
  el.groupMsg.textContent = ""; el.groupMsg.className = "msg-bar";
  var ais = (state.roomMembers || []).filter(function (m) { return !m.is_user; });
  ais.forEach(function (m) {
    var lbl = document.createElement("label");
    lbl.className = "f-label toggle check-line";
    var cb = document.createElement("input"); cb.type = "checkbox"; cb.value = m.id;
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(" " + escapeHtml(m.name)));
    el.groupPick.appendChild(lbl);
  });
  if (!state.currentSessionId || !ais.length) {
    el.groupMsg.textContent = "房间内还没有 AI 参与者。";
    el.groupMsg.className = "msg-bar err"; return;
  }
  safeCloseDialogs();
  el.groupDialog.showModal();
}

function confirmGroup() {
  var ids = [];
  el.groupPick.querySelectorAll("input:checked").forEach(function (cb) { ids.push(cb.value); });
  if (ids.length < 2) { el.groupMsg.textContent = "至少选择两个 AI 参与者"; el.groupMsg.className = "msg-bar err"; return; }
  fetchJSON("/api/rooms/" + state.currentSessionId + "/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ member_ids: ids, title: "" }),
  }).then(function (t) {
    state.roomThreads.push(t);
    el.groupDialog.close();
    renderThreads();
    loadThread(t.id);
  }).catch(function (e) { el.groupMsg.textContent = e.message; el.groupMsg.className = "msg-bar err"; });
}

function openMemDialog() {
  el.memBody.innerHTML = "";
  var ais = (state.roomMembers || []).filter(function (m) { return !m.is_user; });
  el.memTitle.textContent = "房间号 " + (state.currentSessionId || "").slice(0, 8) + " · 各参与者在本房间的记忆（各窗口共享）与属性";
  ais.forEach(function (m) {
    var ch = characterOf(m.character_id);
    var wrap = document.createElement("div");
    wrap.className = "mem-block";
    wrap.innerHTML = '<div class="mem-head">' + makeAvatarHtml(ch || m, true) + ' <b>' + escapeHtml(m.name) + "</b></div>";
    var memBox = document.createElement("textarea");
    memBox.className = "mem-memory"; memBox.rows = 2;
    memBox.placeholder = "该 AI 在本房间的记忆（各窗口共享）"; memBox.value = m.memory || "";
    var attrBox = document.createElement("textarea");
    attrBox.className = "mem-attrs"; attrBox.rows = 2;
    attrBox.placeholder = '属性 JSON，如 {"魅力":80,"好感度":50}';
    try { attrBox.value = JSON.stringify(m.attributes || {}, null, 2); } catch (e) { attrBox.value = ""; }
    var save = document.createElement("button"); save.className = "btn small"; save.textContent = "保存";
    var msg = document.createElement("div"); msg.className = "msg-bar";
    var sp = document.createElement("div"); sp.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:6px";
    sp.appendChild(save); sp.appendChild(msg);
    wrap.appendChild(memBox); wrap.appendChild(attrBox); wrap.appendChild(sp);
    save.addEventListener("click", function () {
      var attrs = {};
      try { attrs = JSON.parse(attrBox.value || "{}"); } catch (e) {
        msg.textContent = "属性 JSON 解析失败"; msg.className = "msg-bar err"; return;
      }
      if (!Array.isArray(attrs)) {
        fetchJSON("/api/rooms/" + state.currentSessionId + "/members/" + m.id, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ memory: memBox.value, attributes: attrs }),
        }).then(function (mm) {
          m.memory = mm.memory; m.attributes = mm.attributes;
          msg.textContent = "已保存"; msg.className = "msg-bar ok";
        }).catch(function (e) { msg.textContent = e.message; msg.className = "msg-bar err"; });
      } else {
        msg.textContent = "属性请提交对象，而非数组"; msg.className = "msg-bar err";
      }
    });
    el.memBody.appendChild(wrap);
  });
  safeCloseDialogs();
  el.memDialog.showModal();
}

function onDeleteSession(id) {
  if (state.busy) return;
  if (!confirm("删除该会话？")) return;
  fetchJSON("/api/sessions/" + id, { method: "DELETE" }).then(function () {
    if (state.currentSessionId === id) { state.currentSessionId = null; clearMessages(); }
    return refreshSessions();
  });
}

function clearMessages() { el.messages.innerHTML = ""; state.currentAssistant = null; }

/* ==== 内容管理 ==== */
function openLoreDialog() {
  clearDirty();
  el.loreBody.innerHTML = "<p class='sub'>管理角色卡与世界观。会话左上角可选用的角色/世界观。</p>";
  renderLoreSections();
  el.loreDialog.showModal();
}
function renderLoreSections() {
  var charItems = (state.characters || []).map(function (c) {
    return loreRow("characters", c.id, c.name || "未命名", c.avatar, (c.table || []).length + " 条内容");
  }).join("");
  var worldItems = (state.worlds || []).map(function (w) {
    return loreRow("worlds", w.id, w.name || "未命名", w.avatar, (w.table || []).length + " 条内容");
  }).join("");

  el.loreBody.innerHTML =
    "<h4>角色卡</h4>" +
    '<div class="lore-list">' + (charItems || '<div class="lore-item"><span class="l-name">暂无角色</span></div>') + "</div>" +
    '<div class="btn-row" style="margin-top:10px">' +
    '<button id="lore-new-char" class="btn small">＋ 新建角色</button>' +
    '<button id="lore-import-char" class="btn small">⇪ 导入角色</button>' +
    "</div>" +
    "<div class='spacer'></div>" +
    "<h4>世界观</h4>" +
    '<div class="lore-list">' + (worldItems || '<div class="lore-item"><span class="l-name">暂无世界观</span></div>') + "</div>" +
    '<div class="btn-row" style="margin-top:10px">' +
    '<button id="lore-new-world" class="btn small">＋ 新建世界观</button>' +
    '<button id="lore-import-world" class="btn small">⇪ 导入世界观</button>' +
    "</div>" +
    '<div class="dialog-actions"><button id="lore-close" class="btn primary">完成</button></div>';

  document.getElementById("lore-new-char").addEventListener("click", function () { newCharacter(); });
  document.getElementById("lore-new-world").addEventListener("click", function () { newWorld(); });
  document.getElementById("lore-import-char").addEventListener("click", function () { importLore("characters"); });
  document.getElementById("lore-import-world").addEventListener("click", function () { importLore("worlds"); });
  document.getElementById("lore-close").addEventListener("click", function () { el.loreDialog.close(); });
  document.querySelectorAll("[data-char-edit]").forEach(function (b) {
    b.addEventListener("click", function () { editCharacter(b.getAttribute("data-char-edit")); });
  });
  document.querySelectorAll("[data-char-export]").forEach(function (b) {
    b.addEventListener("click", function () { exportLore("characters", b.getAttribute("data-char-export")); });
  });
  document.querySelectorAll("[data-char-del]").forEach(function (b) {
    b.addEventListener("click", function () { deleteLore("characters", b.getAttribute("data-char-del"), "删除该角色？"); });
  });
  document.querySelectorAll("[data-world-edit]").forEach(function (b) {
    b.addEventListener("click", function () { editWorld(b.getAttribute("data-world-edit")); });
  });
  document.querySelectorAll("[data-world-export]").forEach(function (b) {
    b.addEventListener("click", function () { exportLore("worlds", b.getAttribute("data-world-export")); });
  });
  document.querySelectorAll("[data-world-del]").forEach(function (b) {
    b.addEventListener("click", function () { deleteLore("worlds", b.getAttribute("data-world-del"), "删除该世界观？"); });
  });
}

function exportLore(endpoint, id) {
  var type = endpoint === "characters" ? "角色卡" : "世界观";
  fetch("/api/" + endpoint + "/" + id + "/export", { headers: authHeaders() })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (obj) {
      var blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (obj.name || type) + ".json";
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
    }).catch(function (e) { alert("导出失败: " + e.message); });
}

function importLore(endpoint) {
  var input = document.createElement("input");
  input.type = "file"; input.accept = ".json,application/json";
  input.onchange = function () {
    var f = input.files[0]; if (!f) return;
    var reader = new FileReader();
    reader.onload = function () {
      var obj;
      try { obj = JSON.parse(reader.result); } catch (e) { alert("JSON 解析失败"); return; }
      fetchJSON("/api/" + endpoint + "/import", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(obj),
      }).then(function () { return refreshLore(); })
        .then(function () { renderLoreSections(); renderSessionMeta(); })
        .catch(function (e) { alert("导入失败: " + e.message); });
    };
    reader.readAsText(f);
  };
  input.click();
}

function loreRow(endpoint, id, name, avatar, tables) {
  var img = avatar ? '<img class="thumb" src="' + avatar + '" alt="" />' : '<div class="default-img">' + escapeHtml((name||"?").slice(0,1).toUpperCase()) + "</div>";
  var kind = endpoint === "characters" ? "char" : "world";
  return (
    '<div class="lore-item">' + img +
    '<div class="l-info"><div class="l-name">' + escapeHtml(name) + "</div><div class='l-tables'>" + tables + "</div></div>" +
    '<div class="l-actions">' +
    '<button class="btn small" data-' + kind + '-edit="' + id + '" title="编辑">编辑</button>' +
    '<button class="btn small" data-' + kind + '-export="' + id + '" title="导出为 JSON">导出</button>' +
    '<button class="btn small danger" data-' + kind + '-del="' + id + '">删除</button>' +
    "</div></div>"
  );
}

function makeAvatarHtml(item, small) {
  if (item && item.avatar) return '<img class="avatar" src="' + item.avatar + '" alt="" />';
  return '<img class="avatar" src="/static/default-avatar.svg" alt="" />';
}

function deleteLore(endpoint, id, msg) {
  if (!confirm(msg)) return;
  fetchJSON("/api/" + endpoint + "/" + id, { method: "DELETE" }).then(function () {
    return refreshLore();
  }).then(function () {
    renderLoreSections();
    renderSessionMeta();
  });
}
function refreshLore() {
  return Promise.all([fetchJSON("/api/characters"), fetchJSON("/api/worlds")]).then(function (r) {
    state.characters = r[0].characters || [];
    state.worlds = r[1].worlds || [];
  });
}

/* 角色编辑器 */
function newCharacter() { characterEditor(null); }
function editCharacter(id) { characterEditor(state.characters.find(function (c) { return c.id === id; })); }

function characterEditor(c, scoped) {
  clearDirty();
  scoped = !!scoped;
  var id = c ? c.id : "";
  el.loreBody.innerHTML = characterEditorHtml(c, scoped);
  // 从顶部动作条（副本编辑 / 新建）进来时对话框可能尚未打开，需先展示
  if (el.loreDialog && !el.loreDialog.open) {
    safeCloseDialogs();
    el.loreDialog.showModal();
  }
  bindEditorCommon("character", id, function (payload) {
    if (scoped) {
      return fetchJSON("/api/sessions/" + state.currentSessionId + "/character", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ character: payload }),
      });
    }
    var url = id ? "/api/characters/" + id : "/api/characters";
    var method = id ? "PUT" : "POST";
    return fetchJSON(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  }, { initial: scoped ? c : null, scoped: scoped });
}
function characterEditorHtml(c, scoped) {
  var v = c || { name: "", avatar: "", intro: "", background: "", table: [] };
  var title = scoped ? "编辑会话副本" : (c ? "编辑角色" : "新建角色");
  return (
    '<div class="dialog-actions"><button class="btn" id="ed-back">← 返回列表</button></div>' +
    "<h3>" + title + "</h3>" +
    (scoped ? '<p class="sub">此副本仅供当前会话使用，修改不影响角色库主卡。</p>' : "") +
    '<div style="display:flex;gap:16px;align-items:flex-start;margin:12px 0">' +
    '<div id="ed-avatar" class="avatar-placeholder" title="点击上传头像">' +
    (v.avatar ? '<img id="ed-avatar-img" src="' + v.avatar + '" alt="" />' : "<span>点击上传<br/>头像</span>") + "</div>" +
    '<div style="flex:1">' +
    '<label class="f-label">角色名称<input type="text" id="ed-name" value="' + escapeAttr(v.name) + '" /></label>' +
    "</div></div>" +
    '<label class="f-label">角色简介<textarea id="ed-intro" rows="2">' + escapeHtml(v.intro) + "</textarea></label>" +
    '<label class="f-label">角色经历<textarea id="ed-bg" rows="4">' + escapeHtml(v.background) + "</textarea></label>" +
    "<div class='spacer'></div>" +
    "<h4>属性（AI 每回合可维护的数值，显示在点击头像处）</h4>" +
    '<div id="ed-attrs"></div>' +
    '<button class="btn small" id="ed-add-attr">＋ 添加属性</button>' +
    '<button class="btn small" id="ed-gen-attr">✨ AI 建议属性</button>' +
    "<div class='spacer'></div>" +
    "<h4>角色要求（内置默认已填入，可按需修改）</h4>" +
    '<label class="f-label">对白格式要求<textarea id="ed-format" rows="3">' + escapeHtml(v.prompt_format || defaultFormatReq()) + "</textarea></label>" +
    '<label class="f-label">工具使用要求<textarea id="ed-tools" rows="2">' + escapeHtml(v.prompt_tools || defaultToolReq()) + "</textarea></label>" +
    "<div class='spacer'></div>" +
    "<h4>内容表（AI 通过工具可查询）</h4>" +
    '<div id="ed-tables"></div>' +
    '<button class="btn small" id="ed-add-entry">＋ 添加条目</button>' +
    '<div class="dialog-actions"><button class="btn" id="ed-cancel">取消</button><button class="btn primary" id="ed-save">保存角色</button></div>' +
    '<div id="ed-msg" class="msg-bar"></div>' +
    '<input type="file" id="ed-file" accept="image/*" class="hidden" />'
  );
}

function newWorld() { worldEditor(null); }
function editWorld(id) { worldEditor(state.worlds.find(function (w) { return w.id === id; })); }

function worldEditor(w) {
  clearDirty();
  var id = w ? w.id : "";
  el.loreBody.innerHTML = worldEditorHtml(w);
  bindEditorCommon("world", id, function (payload) {
    var url = id ? "/api/worlds/" + id : "/api/worlds";
    var method = id ? "PUT" : "POST";
    return fetchJSON(url, { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  });
}
function worldEditorHtml(w) {
  var v = w || { name: "", content: "", table: [] };
  return (
    '<div class="dialog-actions"><button class="btn" id="ed-back">← 返回列表</button></div>' +
    "<h3>" + (w ? "编辑世界观" : "新建世界观") + "</h3>" +
    '<label class="f-label">世界观名称<input type="text" id="ed-name" value="' + escapeAttr(v.name) + '" /></label>' +
    '<label class="f-label">世界观内容<textarea id="ed-intro" rows="4">' + escapeHtml(v.content) + "</textarea></label>" +
    "<div class='spacer'></div>" +
    "<h4>世界观要求（内置默认已填入，可按需修改）</h4>" +
    '<label class="f-label">对白格式要求<textarea id="ed-format" rows="3">' + escapeHtml(v.prompt_format || defaultFormatReq()) + "</textarea></label>" +
    '<label class="f-label">工具使用要求<textarea id="ed-tools" rows="2">' + escapeHtml(v.prompt_tools || defaultToolReq()) + "</textarea></label>" +
    "<div class='spacer'></div>" +
    "<h4>内容表（AI 通过工具可查询）</h4>" +
    '<div id="ed-tables"></div>' +
    '<button class="btn small" id="ed-add-entry">＋ 添加条目</button>' +
    '<div class="dialog-actions"><button class="btn" id="ed-cancel">取消</button><button class="btn primary" id="ed-save">保存世界观</button></div>' +
    '<div id="ed-msg" class="msg-bar"></div>'
  );
}

function bindEditorCommon(kind, id, saveFn, opts) {
  opts = opts || {};
  if (opts.initial) v = opts.initial;
  else v = kind === "character"
    ? (id ? state.characters.find(function (c) { return c.id === id; }) : {})
    : (id ? state.worlds.find(function (w) { return w.id === id; }) : {});
  v = v || {};

  // 内容表条目
  var tablesEl = document.getElementById("ed-tables");
  (v.table || []).forEach(addEntryRow.bind(null, tablesEl));
  document.getElementById("ed-add-entry").addEventListener("click", function () { addEntryRow(tablesEl); });
  document.getElementById("ed-cancel").addEventListener("click", function () { guardedNav(openLoreDialog); });
  document.getElementById("ed-back").addEventListener("click", function () { guardedNav(renderLoreSections); });

  // 属性编辑器（仅角色）
  var attrsEl = document.getElementById("ed-attrs");
  var addAttrBtn = document.getElementById("ed-add-attr");
  var genAttrBtn = document.getElementById("ed-gen-attr");
  if (attrsEl && addAttrBtn) {
    (v.attributes || []).forEach(addAttrRow.bind(null, attrsEl));
    addAttrBtn.addEventListener("click", function () { addAttrRow(attrsEl); });
    if (genAttrBtn) {
      genAttrBtn.addEventListener("click", function () {
        var payload = {
          name: (document.getElementById("ed-name").value || ""),
          intro: (document.getElementById("ed-intro").value || ""),
          background: (document.getElementById("ed-bg").value || ""),
          existing: readAttrs(attrsEl),
        };
        genAttrBtn.disabled = true;
        fetchJSON("/api/gen/attributes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
          .then(function (res) {
            openAttrgenDialog(res.attributes || [], function (attrs) { attrs.forEach(function (a) { addAttrRow(attrsEl, a); }); });
          })
          .catch(function (e) { alert("生成失败: " + e.message); })
          .finally(function () { genAttrBtn.disabled = false; });
      });
    }
  }

  // 头像上传（仅角色）
  var fileEl = document.getElementById("ed-file");
  var avatarEl = document.getElementById("ed-avatar");
  if (fileEl && avatarEl) {
    avatarEl.addEventListener("click", function () { fileEl.click(); });
    fileEl.addEventListener("change", function () {
      var f = fileEl.files[0];
      if (!f) return;
      var fd = new FormData();
      fd.append("file", f);
      fetch("/api/upload", { method: "POST", headers: authHeaders(), body: fd }).then(function (r) { return r.json(); }).then(function (res) {
        avatarEl.innerHTML = '<img src="' + res.url + '" alt="" />';
        avatarEl.dataset.avatar = res.url;
      }).catch(function (e) { alert("上传失败: " + e.message); });
    });
  }

  document.getElementById("ed-save").addEventListener("click", function () {
    var payload = { table: readEntries(tablesEl) };
    payload.prompt_format = document.getElementById("ed-format").value;
    payload.prompt_tools = document.getElementById("ed-tools").value;
    if (kind === "character") {
      payload.name = document.getElementById("ed-name").value;
      payload.intro = document.getElementById("ed-intro").value;
      payload.background = document.getElementById("ed-bg").value;
      payload.avatar = (avatarEl && avatarEl.dataset.avatar) || (v.avatar || "");
      if (attrsEl) payload.attributes = readAttrs(attrsEl);
    } else {
      payload.name = document.getElementById("ed-name").value;
      payload.content = document.getElementById("ed-intro").value;
      payload.avatar = "";
    }
    var msg = document.getElementById("ed-msg");
    if (!payload.name.trim()) { msg.textContent = "请填写名称"; msg.className = "msg-bar err"; return; }
    saveFn(payload).then(function (res) {
      clearDirty();
      if (opts.scoped) {
        // 副本态：只更新会话副本，不碰角色库；保存后返回主聊天视图
        var sid = state.currentSessionId;
        state.scopeCache[sid] = { char: res.character, world: state.scopeCache[sid] && state.scopeCache[sid].world, scoped: true };
        renderSessionMeta();
        if (el.loreDialog) el.loreDialog.close();
      } else {
        return refreshLore().then(function () { renderLoreSections(); renderSessionMeta(); });
      }
    }).catch(function (e) { msg.textContent = "保存失败: " + e.message; msg.className = "msg-bar err"; });
  });
}

function entryRowHtml(title, content) {
  return (
    '<div class="entry-row">' +
    '<input type="text" class="ed-title" placeholder="标题" value="' + escapeAttr(title) + '" />' +
    '<textarea class="ed-content" rows="2" placeholder="内容">' + escapeHtml(content) + "</textarea>" +
    '<button class="btn small ed-del">✕</button></div>'
  );
}
function addEntryRow(container, entry) {
  var row = document.createElement("div");
  row.innerHTML = entryRowHtml(entry ? entry.title || "" : "", entry ? entry.content || "" : "");
  row.querySelector(".ed-del").addEventListener("click", function () { row.remove(); });
  container.appendChild(row);
}
function attrRowHtml(key, label, min, max) {
  return (
    '<div class="attr-row">' +
    '<input type="text" class="at-key" placeholder="字段(key)" value="' + escapeAttr(key) + '" />' +
    '<input type="text" class="at-label" placeholder="显示名" value="' + escapeAttr(label) + '" />' +
    '<input type="number" class="at-min" placeholder="最小" value="' + escapeAttr(min) + '" />' +
    '<input type="number" class="at-max" placeholder="最大" value="' + escapeAttr(max) + '" />' +
    '<input type="number" class="at-init" placeholder="初始" value="" />' +
    '<button class="btn small ed-del">✕</button></div>'
  );
}
function addAttrRow(container, at) {
  at = at || {};
  var row = document.createElement("div");
  row.innerHTML = attrRowHtml(at.key, at.label, at.min, at.max);
  if (at.initial !== undefined) row.querySelector(".at-init").value = at.initial;
  row.querySelector(".ed-del").addEventListener("click", function () { row.remove(); });
  container.appendChild(row);
}
function readAttrs(container) {
  var out = [];
  container.querySelectorAll(".attr-row").forEach(function (row) {
    var a = {};
    a.key = (row.querySelector(".at-key").value || "").trim();
    a.label = (row.querySelector(".at-label").value || "").trim();
    var min = row.querySelector(".at-min").value, max = row.querySelector(".at-max").value, init = row.querySelector(".at-init").value;
    if (min !== "") a.min = Number(min);
    if (max !== "") a.max = Number(max);
    if (init !== "") a.initial = Number(init);
    if (a.key) out.push(a);
  });
  return out;
}
function readEntries(container) {
  var out = [];
  container.querySelectorAll(".entry-row").forEach(function (row) {
    var title = row.querySelector(".ed-title").value;
    var content = row.querySelector(".ed-content").value;
    out.push({ title: title, content: content });
  });
  return out;
}

/* ==== 历史渲染 ==== */
function renderHistory(messages) {
  clearMessages();
  var openToolRegion = null;
  messages.forEach(function (m) {
    if (m.hidden) return;  // 群聊自评「无需发言」：不展示但保留在历史
    if (m.role === "user") { openToolRegion = null; el.messages.appendChild(makeUserBubble(m.content || "")); }
    else if (m.role === "assistant") { openToolRegion = appendAssistantMsg(m); }
    else if (m.role === "tool") {
      if (openToolRegion) appendToolResultCard(openToolRegion, { id: m.tool_call_id, name: m.name, ok: (m.ok !== undefined) ? m.ok : !String(m.content||"").startsWith("[工具错误]"), output: m.content });
    }
  });
  scrollToBottom();
}
function makeUserBubble(text) {
  var wrap = document.createElement("div");
  wrap.className = "msg user";
  var bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  return wrap;
}
function makeAssistantContainer() {
  var wrap = document.createElement("div");
  wrap.className = "msg assistant";
  var inner = document.createElement("div");
  inner.className = "assistant-wrap";
  wrap.appendChild(inner);

  // 可折叠卡片头：显示 AI 头像/名字 + 思考中状态；点它展开/收起正文
  var head = document.createElement("button");
  head.type = "button";
  head.className = "as-header hidden";
  var idEl = document.createElement("span"); idEl.className = "as-id";
  var statusEl = document.createElement("span"); statusEl.className = "as-status";
  var chev = document.createElement("span"); chev.className = "as-chev"; chev.textContent = "▾";
  head.appendChild(idEl); head.appendChild(statusEl); head.appendChild(chev);
  inner.appendChild(head);

  var body = document.createElement("div");
  body.className = "as-body";
  var thinkingEl = document.createElement("details");
  thinkingEl.className = "thinking hidden";
  var summary = document.createElement("summary");
  summary.textContent = "思考过程";
  var thinkingDiv = document.createElement("div");
  thinkingEl.appendChild(summary); thinkingEl.appendChild(thinkingDiv);
  body.appendChild(thinkingEl);
  var toolRegion = document.createElement("div");
  toolRegion.className = "tool-cards";
  body.appendChild(toolRegion);
  var contentEl = document.createElement("div");
  contentEl.className = "assistant-content";
  body.appendChild(contentEl);
  inner.appendChild(body);

  var a = {
    container: wrap, head: head, idEl: idEl, statusEl: statusEl, chev: chev, bodyEl: body,
    thinkingEl: thinkingEl, thinkingDiv: thinkingDiv, toolRegion: toolRegion, contentEl: contentEl,
    rawContent: "", speakerName: "", open: true,
  };
  a.setOpen = function (open) {
    a.open = !!open;
    a.bodyEl.classList.toggle("hidden", !a.open);
    a.chev.textContent = a.open ? "▾" : "▸";
  };
  a.setSpeaker = function (name, member) {
    a.speakerName = name || "AI";
    a.head.classList.remove("hidden");
    var ch = member && member.character_id ? characterOf(member.character_id) : null;
    a.idEl.innerHTML =
      makeAvatarHtml(ch || member || null, true) +
      '<span class="spk-name">' + escapeHtml(a.speakerName) + "</span>";
  };
  a.setThinking = function (on) {
    a.statusEl.textContent = on ? "AI 思考中…" : "";
    a.container.classList.toggle("thinking", !!on);
  };
  head.addEventListener("click", function () { a.setOpen(!a.open); });
  return a;
}
function ensureAssistant() {
  if (state.currentAssistant) return state.currentAssistant;
  var a = makeAssistantContainer();
  el.messages.appendChild(a.container);
  state.currentAssistant = a;
  if (roomMode() && !a.speakerName) a.head.classList.remove("hidden");
  scrollToBottom();
  return a;
}
function appendAssistantMsg(m) {
  var a = makeAssistantContainer();
  el.messages.appendChild(a.container);
  if (m.name) {
    a.setSpeaker(m.name, memberOf(m.name));
    a.setThinking(false);
    a.setOpen(true);  // 历史记录默认展开可直接阅读
  }
  if (m.thinking) { a.thinkingEl.classList.remove("hidden"); a.thinkingEl.open = false; a.thinkingDiv.textContent = m.thinking; }
  if (m.content) setAssistantContent(a, m.content);
  if (m.tool_calls) m.tool_calls.forEach(function (c) { appendToolCallCard(a.toolRegion, c, false); });
  scrollToBottom();
  return a.toolRegion;
}

/* ==== 流式聊天 ==== */
function onSend() {
  if (state.busy) return;
  var text = el.input.value.trim();
  if (!text) return;
  var doSend = function () {
    state.busy = true;
    el.btnSend.disabled = true;
    el.btnStop.classList.remove("hidden");
    el.input.disabled = true;
    el.messages.appendChild(makeUserBubble(text));
    el.input.value = "";
    // 每个 AI 各自一条卡片：流式期间按 speaker 事件新建，不预建占位
    state.currentAssistant = null;
    state.currentSpeaker = null;
    scrollToBottom();
    state.abortController = new AbortController();
    var tid = state.currentThreadId ||
      ((state.roomThreads && state.roomThreads.length) ? state.roomThreads[0].id : undefined);
    var payload = {
      message: text,
      session_id: state.currentSessionId,
      thread_id: tid,
      provider_id: (state.config && state.config.active_provider) || "",
      tools_enabled: true,
    };
    fetch("/api/chat", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
      signal: state.abortController.signal,
    }).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return readSSE(resp.body, {
        message_start: onMsgStart,
        speaker: onSpeaker,
        speaker_skip: onSpeakerSkip,
        thinking_delta: onThinkingDelta,
        content_delta: onContentDelta,
        tool_calls: onToolCalls,
        tool_start: onToolStart,
        tool_end: onToolEnd,
        attributes: onAttributes,
        error: onStreamError,
        done: onDone,
      });
    }).catch(function (e) {
      if (e.name !== "AbortError") addErrorBar("请求失败: " + e.message);
      finalizeTurn();
    });
  };
  if (!state.currentSessionId) {
    fetchJSON("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })
      .then(function (s) { state.currentSessionId = s.id; return refreshSessions(); })
      .then(doSend);
  } else { doSend(); }
}
function onStop() { if (state.abortController) state.abortController.abort(); }

function readSSE(body, handlers) {
  if (!body) return Promise.resolve();
  var reader = body.getReader();
  var decoder = new TextDecoder();
  var buffer = "";
  function pump() {
    return reader.read().then(function (res) {
      if (res.done) { if (buffer.trim()) processFrame(buffer); return; }
      buffer += decoder.decode(res.value, { stream: true });
      for (;;) {
        var idx = buffer.indexOf("\n\n");
        if (idx < 0) break;
        var frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        processFrame(frame);
      }
      return pump();
    });
  }
  function processFrame(frame) {
    var parsed = parseFrame(frame);
    if (parsed && handlers[parsed.event]) handlers[parsed.event](parsed.data);
  }
  return pump();
}
function parseFrame(frame) {
  var event = "message"; var data = "";
  frame.split("\n").forEach(function (line) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  });
  if (!data) return null;
  try { return { event: event, data: JSON.parse(data) }; } catch (e) { return null; }
}

/* 事件处理 */
function onMsgStart(data) {
  if (data.session_id && data.session_id !== state.currentSessionId) {
    state.currentSessionId = data.session_id;
    refreshSessions();
  }
  if (data.attributes && Object.keys(data.attributes).length) {
    state.currentAttrs = data.attributes;
  }
}
function onSpeaker(data) {
  // 上一位发言者的卡片先收尾（收起），再为当前 AI 新建一张卡，避免多 AI 串进同一条消息
  var prev = state.currentAssistant;
  if (prev && prev.speakerName) { prev.setThinking(false); if (!prev.revealed) prev.setOpen(false); }
  state.currentSpeaker = data || null;
  state.currentAssistant = null;
  var a = ensureAssistant();
  a.setSpeaker((data && data.name) || "AI", memberOf(data && data.member_id));
  a.setOpen(true);
  a.setThinking(true);
}
function onSpeakerSkip(data) {
  // 该 AI 自评无需发言：把这块输出整卡移除（仍保存在历史中，reload 不展示）
  var a = state.currentAssistant;
  if (a) { a.setOpen(false); a.setThinking(false); a.container.remove(); }
  state.currentAssistant = null;
  state.currentSpeaker = null;
}
function onAttributes(data) {
  if (data && data.attributes) { state.currentAttrs = data.attributes; }
}
function onThinkingDelta(data) {
  var a = ensureAssistant(); if (!a) return;
  a.thinkingEl.classList.remove("hidden"); a.thinkingEl.open = true; a.thinkingDiv.textContent += data.text;
}
function onContentDelta(data) {
  var a = ensureAssistant(); if (!a) return;
  setAssistantContent(a, a.rawContent + (data.text || ""));
  scrollToBottom();  // 不强制：用户上翻时停留在原位
}
function onToolCalls(data) {
  var a = ensureAssistant(); if (!a) return;
  (data.calls || []).forEach(function (c) { appendToolCallCard(a.toolRegion, c, true); });
}
function appendToolCallCard(region, call, streaming) {
  var card = document.createElement("div");
  card.className = "tool-card";
  card.dataset.id = call.id;
  var tname = document.createElement("div"); tname.className = "tname"; tname.textContent = "🔧 " + call.name;
  var tstatus = document.createElement("div"); tstatus.className = "tstatus"; tstatus.textContent = streaming ? "等待执行…" : "已完成";
  card.appendChild(tname); card.appendChild(tstatus); region.appendChild(card);
}
function onToolStart(data) {
  var card = findCard(data.id); if (!card) return;
  card.classList.remove("err"); card.classList.remove("ok");
  var spin = document.createElement("span"); spin.className = "spinner";
  var tstatus = card.querySelector(".tstatus"); tstatus.innerHTML = "";
  tstatus.appendChild(spin); tstatus.appendChild(document.createTextNode(data.status_text || ("正在调用 " + data.name)));
}
function onToolEnd(data) {
  var card = findCard(data.id); if (!card) return;
  card.classList.add(data.ok ? "ok" : "err");
  card.querySelector(".tstatus").textContent = data.ok ? "✓ 完成" : "✗ 失败";
  if (data.preview) { var pre = document.createElement("div"); pre.className = "tpreview"; pre.textContent = data.preview; card.appendChild(pre); }
  scrollToBottom();  // 不强制：用户上翻时停留在原位
}
function appendToolResultCard(region, data) {
  var card = document.createElement("div");
  card.className = "tool-card " + (data.ok ? "ok" : "err");
  var tname = document.createElement("div"); tname.className = "tname"; tname.textContent = "🔧 " + (data.name || data.id || "tool");
  var tstatus = document.createElement("div"); tstatus.className = "tstatus"; tstatus.textContent = data.ok ? "✓ 完成" : "✗ 失败";
  card.appendChild(tname); card.appendChild(tstatus);
  if (data.output) { var pre = document.createElement("div"); pre.className = "tpreview"; pre.textContent = String(data.output).slice(0, 300); card.appendChild(pre); }
  region.appendChild(card);
}
function findCard(id) {
  if (!state.currentAssistant) return null;
  return state.currentAssistant.toolRegion.querySelector('.tool-card[data-id="' + id + '"]');
}
function onStreamError(data) { addErrorBar(data.message || "发生错误"); }
function onDone() { finalizeTurn(); }
function finalizeTurn() {
  var a = state.currentAssistant;
  if (a) {
    a.thinkingEl.open = false;
    a.setThinking(false);
    if (a.speakerName) a.setOpen(false);  // 群聊发言结束后折叠，点击头部展开
  }
  state.currentAssistant = null;
  state.currentSpeaker = null;
  state.busy = false;
  el.btnSend.disabled = false;
  el.btnStop.classList.add("hidden");
  el.input.disabled = false;
  el.input.focus();
  refreshSessions();
}
function addErrorBar(msg) {
  var bar = document.createElement("div");
  bar.className = "error-bar"; bar.textContent = msg;
  el.messages.appendChild(bar); scrollToBottom();
  setTimeout(function () { bar.remove(); }, 10000);
}

/* ==== 设置 ==== */
function openSettings() {
  if (!state.config) return;
  clearDirty();
  el.configMsg.textContent = ""; el.configMsg.className = "msg-bar";
  el.setMaxIters.value = state.config.tools ? state.config.tools.max_iterations : 8;
  el.setTimeout.value = state.config.tools ? state.config.tools.timeout_seconds : 60;
  el.setReadDirs.value = (state.config.tools.allowed_read_dirs || []).join("\n");
  var rp = state.config.roleplay || {};
  el.setRpFormat.checked = !!(rp.format_enabled);
  el.setAttrEnabled.checked = !!(rp.attributes && rp.attributes.enabled);
  el.setAttrAi.checked = !!(rp.attributes && rp.attributes.ai_provided);
  renderProviderRows();
  renderPluginChecks();
  el.dialog.showModal();
}
function renderProviderRows() {
  el.providerList.innerHTML = "";
  (state.config.providers || []).forEach(function (p, i) {
    var row = document.createElement("div");
    row.className = "provider-row grid";
    row.dataset.id = p.id;
    var label = document.createElement("div");
    label.className = "provider-row p-label";
    label.innerHTML = "<label style='display:flex;align-items:center;gap:6px;font-size:13px'><input type='radio' name='active-provider' value='" + p.id + "'" + (state.config.active_provider === p.id ? " checked" : "") + " /> 激活</label><span>" + escapeHtml(p.id) + "</span>";
    row.appendChild(label);
    row.appendChild(fieldRow("id", "ID", p.id));
    row.appendChild(fieldRow("type", "类型", p.type, "select"));
    row.appendChild(fieldRow("base_url", "Base URL", p.base_url));
    row.appendChild(fieldRow("api_key", "API Key", p.api_key, "password"));
    row.appendChild(fieldRow("model", "模型", p.model));
    var del = document.createElement("button"); del.className = "btn small danger"; del.textContent = "删除";
    del.style.gridColumn = "span 2";
    del.addEventListener("click", function () { row.remove(); });
    row.appendChild(del);
    el.providerList.appendChild(row);
  });
  var radios = document.querySelectorAll('input[name="active-provider"]');
  if (radios.length && Array.prototype.every.call(radios, function (r) { return !r.checked; })) {
    radios[0].checked = true;
  }
}
function fieldRow(key, label, value, kind) {
  var wrap = document.createElement("label");
  wrap.className = "pfield";
  var span = document.createElement("span"); span.textContent = label;
  var input;
  if (kind === "select") {
    input = document.createElement("select");
    ["openai", "anthropic"].forEach(function (t) {
      var o = document.createElement("option"); o.value = t; o.textContent = t; if (t === value) o.selected = true;
      input.appendChild(o);
    });
  } else {
    input = document.createElement("input");
    input.type = kind || "text"; input.value = value || "";
  }
  input.dataset.key = key;
  wrap.appendChild(span); wrap.appendChild(input);
  return wrap;
}
function addProviderRow() {
  var row = document.createElement("div");
  row.className = "provider-row grid";
  var label = document.createElement("div"); label.className = "provider-row p-label"; label.innerHTML = "<span style='color:var(--accent)'>新提供商</span>";
  row.appendChild(label);
  row.appendChild(fieldRow("id", "ID", ""));
  row.appendChild(fieldRow("type", "类型", "openai", "select"));
  row.appendChild(fieldRow("base_url", "Base URL", ""));
  row.appendChild(fieldRow("api_key", "API Key", "", "password"));
  row.appendChild(fieldRow("model", "模型", ""));
  var del = document.createElement("button"); del.className = "btn small danger"; del.textContent = "删除";
  del.style.gridColumn = "span 2";
  del.addEventListener("click", function () { row.remove(); });
  row.appendChild(del);
  el.providerList.appendChild(row);
}
function renderPluginChecks() {
  el.pluginChecks.innerHTML = "";
  state.plugins.forEach(function (pl) {
    var lbl = document.createElement("label");
    lbl.className = "f-label";
    var chk = document.createElement("input"); chk.type = "checkbox"; chk.checked = true; chk.dataset.plugin = pl.name;
    lbl.appendChild(chk);
    lbl.appendChild(document.createTextNode(" " + pl.name + (pl.description ? " — " + pl.description : "")));
    el.pluginChecks.appendChild(lbl);
  });
}
function saveConfig() {
  var providers = [];
  var radioChecked = el.providerList.querySelector('input[name="active-provider"]:checked');
  Array.prototype.forEach.call(el.providerList.querySelectorAll(".provider-row"), function (row) {
    var idInput = row.querySelector('input[data-key="id"]');
    var typeSel = row.querySelector('select[data-key="type"]');
    var urlInput = row.querySelector('input[data-key="base_url"]');
    var keyInput = row.querySelector('input[data-key="api_key"]');
    var modelInput = row.querySelector('input[data-key="model"]');
    var pval = {
      id: idInput ? idInput.value.trim() : "",
      type: typeSel ? typeSel.value : "openai",
      base_url: urlInput ? urlInput.value.trim() : "",
      api_key: keyInput ? keyInput.value : "",
      model: modelInput ? modelInput.value.trim() : "",
    };
    if (pval.id) providers.push(pval);
  });
  var active = radioChecked ? radioChecked.value : (providers[0] ? providers[0].id : "");
  var readDirs = el.setReadDirs.value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
  var tools = { enabled: true, max_iterations: parseInt(el.setMaxIters.value, 10) || 8, timeout_seconds: parseInt(el.setTimeout.value, 10) || 60, allowed_read_dirs: readDirs };
  var roleplay = {
    format_enabled: !!el.setRpFormat.checked,
    attributes: { enabled: !!el.setAttrEnabled.checked, ai_provided: !!el.setAttrAi.checked },
  };
  fetchJSON("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active_provider: active, providers: providers, tools: tools, roleplay: roleplay }),
  }).then(function (cfg) {
    state.config = cfg;
    el.configMsg.textContent = "已保存"; el.configMsg.className = "msg-bar ok";
    el.dialog.close();
    renderProviderBar();
    return fetchJSON("/api/plugins");
  }).then(function (r) { state.plugins = r.plugins || []; })
    .catch(function (e) { el.configMsg.textContent = "保存失败: " + e.message; el.configMsg.className = "msg-bar err"; });
}

/* ==== 工具函数 ==== */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function escapeAttr(s) { return escapeHtml(s); }
function scrollToBottom(force) {
  var m = el.messages;
  if (force || (m.scrollHeight - m.scrollTop - m.clientHeight) < 120) m.scrollTop = m.scrollHeight;
  updateJumpBtn();
}
/* 距底部多远时显示「跳到底部」按钮 */
function updateJumpBtn() {
  if (!el.btnJump) return;
  var m = el.messages;
  var near = (m.scrollHeight - m.scrollTop - m.clientHeight) < 80;
  el.btnJump.classList.toggle("hidden", near);
}
function jumpBottom() {
  scrollToBottom(true);
  updateJumpBtn();
}