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
  authToken: null,  // 登录令牌（Bearer），唯一访问凭证
  userId: null,
  userName: "",
  userLevel: 0,     // 权限等级 0-255；admin 256
  imageModelId: "", // 普通用户选用的生图模型（localStorage 持久化）
  sdEngine: false,  // 是否使用本地 Stable Diffusion 生图（localStorage 持久化）
  scopeCache: {},   // sessionId -> {char, world, scoped}（有效角色/世界观，含副本）
  // beta：多 AI 群聊
  roomMembers: [],    // 当前房间参与者（session.members）
  roomThreads: [],    // 当前房间窗口（session.threads + 群聊）
  roomView: false,    // 左侧栏是否处于「房间窗口导航」状态
  currentThreadId: null,
  currentSpeaker: null, // {member_id,name,character_id} 流式当前发言者
  pendingImages: [],    // 待发送图片 [{media_type, data(base66)}]
  newChat: null,        // 选卡临时态 {selected: {id: card}, rootId: ""}
  loreNav: "",          // 内容菜单角色卡两级导航：""=根卡列表，否则为根卡 id
  // 历史编辑/重生成
  turnNodes: {},        // anchor -> {userEl, editing}
  streamInsertBefore: null,  // 旧轮流式：新回复插在该节点之前；null=追加到底
  turnEditAnchor: null,     // 处于编辑/重生成流式中的轮 anchor
  // 批量删除会话
  batchMode: false,
  batchPicked: {},          // 选中的会话 id -> true
  // 侧栏搜索 / 滚动跟随 / 失败恢复
  sessionQuery: "",
  stickBottom: true,        // 用户贴底时新内容才自动滚动
  pendingSend: null,        // 本次已发送 {text, images}，请求失败时回填
  draftTimer: null,
  tokenTotals: {},          // 会话 id -> 累计总 token（含服务端持久值 + 本页面新增）
  tokenDetails: {},         // 会话 id -> {p, c} 输入/输出明细，供 chip 悬停提示
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
  if (state.authToken) h["Authorization"] = "Bearer " + state.authToken;
  return h;
}
function goLogin() {
  window.location.href = "/login";
}
function renderUserBadge() {
  if (el.userName) {
    var tag = state.role === "admin" ? "（管理员 Lv256）" : "（Lv" + state.userLevel + "）";
    el.userName.textContent = "👤 " + (state.userName || "……") + tag;
  }
  if (el.btnUsers) el.btnUsers.classList.toggle("hidden", state.role !== "admin");
}
function clearAuth() {
  localStorage.removeItem("flyai-token");
  localStorage.removeItem("flyai-uid");
  localStorage.removeItem("flyai-role");
  state.authToken = null;
  state.userId = null;
  state.role = "";
  state.currentSessionId = null;
  state.currentAttrs = null;
  state.roomMembers = []; state.roomThreads = []; state.currentThreadId = null; state.currentSpeaker = null;
  try { clearMessages(); if (el.threads) el.threads.classList.add("hidden"); } catch (e) {}
}
function bootstrap() {
  var token = localStorage.getItem("flyai-token");
  if (!token) { goLogin(); return; }  // 不登录不能进
  state.authToken = token;
  state.imageModelId = localStorage.getItem("flyai-image-model") || "";
  state.sdEngine = localStorage.getItem("flyai-sd-engine") === "sd";
  state.topbarCollapsed = localStorage.getItem("flyai-topbar-collapsed") === "1";
  fetchJSON("/api/auth/me").then(function (res) {
    state.userId = res.id;
    localStorage.setItem("flyai-uid", res.id);  // 供 owner 比对/调试，非凭证
    state.userName = res.name;
    state.role = res.role || "user";
    state.userLevel = parseInt(res.level, 10) || 0;
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
      // 等级：改完即 PATCH
      var levelWrap = document.createElement("label");
      levelWrap.className = "user-level";
      levelWrap.textContent = "Lv ";
      var level = document.createElement("input");
      level.type = "number";
      level.min = "0"; level.max = "255";
      level.value = String(u.level == null ? 0 : u.level);
      level.addEventListener("change", function () {
        changeUserLevel(u.id, level);
      });
      levelWrap.appendChild(level);
      row.appendChild(levelWrap);
      // 改密码：点击后行内展开密码输入
      var pwdBtn = document.createElement("button");
      pwdBtn.className = "btn small";
      pwdBtn.textContent = "🔑 改密";
      var pwdBox = makePasswordResetBox(u.id, u.name);
      pwdBtn.addEventListener("click", function () {
        pwdBtn.classList.add("hidden");
        row.appendChild(pwdBox);
        pwdBox.querySelector("input").focus();
      });
      pwdBox.addEventListener("done", function () {
        pwdBox.remove();
        pwdBtn.classList.remove("hidden");
      });
      row.appendChild(pwdBtn);
      var del = document.createElement("button");
      del.className = "btn small";
      del.textContent = "删除";
      del.addEventListener("click", function () { deleteUser(u.id, u.name, row, del); });
      row.appendChild(del);
      el.userList.appendChild(row);
    });
  }).catch(function (e) { el.userList.textContent = "加载失败: " + e.message; });
}
function changeUserLevel(uid, input) {
  var v = parseInt(input.value, 10);
  if (isNaN(v)) v = 0;
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  fetchJSON("/api/auth/users/" + encodeURIComponent(uid), {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ level: v }),
  }).then(function (r) {
    input.value = String(r.level);
    el.userMsg.textContent = "等级已更新为 " + r.level;
    el.userMsg.className = "msg-bar ok";
  }).catch(function (e) {
    el.userMsg.textContent = "等级修改失败: " + e.message;
    el.userMsg.className = "msg-bar err";
  });
}
/* 行内密码重置框：新密码输入 + 确认/取消；确认走 PATCH .../password */
function makePasswordResetBox(uid, name) {
  var box = document.createElement("span");
  box.className = "pwd-reset";
  var input = document.createElement("input");
  input.type = "password";
  input.placeholder = "输入新密码（留空=无密码）";
  input.autocomplete = "new-password";
  var ok = document.createElement("button");
  ok.className = "btn small primary";
  ok.textContent = "确认";
  var cancel = document.createElement("button");
  cancel.className = "btn small";
  cancel.textContent = "取消";
  function finish() { box.dispatchEvent(new Event("done")); }
  function submit() {
    var password = input.value;
    ok.disabled = true;
    fetchJSON("/api/auth/users/" + encodeURIComponent(uid) + "/password", {
      method: "PATCH",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ password: password }),
    }).then(function (r) {
      el.userMsg.textContent = "已重置「" + name + "」的密码，其现有登录已全部失效"
        + "（吊销 " + intOr(r.revoked_sessions, 0) + " 个会话）";
      el.userMsg.className = "msg-bar ok";
      finish();
    }).catch(function (e) {
      el.userMsg.textContent = "密码重置失败: " + e.message;
      el.userMsg.className = "msg-bar err";
      ok.disabled = false;
    });
  }
  ok.addEventListener("click", submit);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
    if (e.key === "Escape") finish();
  });
  cancel.addEventListener("click", finish);
  box.appendChild(input);
  box.appendChild(ok);
  box.appendChild(cancel);
  return box;
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
  var level = parseInt(el.userLevelInput.value, 10);
  if (isNaN(level)) level = 0;
  el.userMsg.textContent = ""; el.userMsg.className = "msg-bar";
  if (!name) { el.userMsg.textContent = "请输入用户名"; el.userMsg.className = "msg-bar err"; return; }
  fetchJSON("/api/auth/users", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name: name, password: password, level: level }),
  }).then(function () {
    el.userMsg.textContent = "已创建用户「" + name + "」"; el.userMsg.className = "msg-bar ok";
    el.userNameInput.value = ""; el.userPasswordInput.value = "";
    el.userLevelInput.value = "0";
    renderUserList();
    el.userNameInput.focus();
  }).catch(function (e) {
    el.userMsg.textContent = "创建失败: " + e.message; el.userMsg.className = "msg-bar err";
  });
}

function cacheElements() {
  el.sessionList = document.getElementById("session-list");
  el.sessionSearch = document.getElementById("session-search");
  el.searchWrap = document.querySelector(".search-wrap");
  el.messages = document.getElementById("messages");
  el.sessionMeta = document.getElementById("session-meta");
  el.topbar = document.getElementById("topbar");
  el.topbarTitle = document.getElementById("topbar-title");
  el.btnTopbarToggle = document.getElementById("btn-topbar-toggle");
  el.input = document.getElementById("input");
  el.btnSend = document.getElementById("btn-send");
  el.btnStop = document.getElementById("btn-stop");
  el.btnAttach = document.getElementById("btn-attach");
  el.imageFile = document.getElementById("image-file");
  el.attachPreview = document.getElementById("attach-preview");
  el.btnNew = document.getElementById("btn-new-session");
  el.btnSettings = document.getElementById("btn-settings");
  el.btnLore = document.getElementById("btn-lore");
  el.btnBatchDelete = document.getElementById("btn-batch-delete");
  el.btnTheme = document.getElementById("btn-theme");
  el.providerBar = document.getElementById("provider-bar");
  el.dialog = document.getElementById("settings-dialog");
  el.providerList = document.getElementById("provider-list");
  el.btnAddProvider = document.getElementById("btn-add-provider");
  el.setMaxIters = document.getElementById("set-max-iters");
  el.setTimeout = document.getElementById("set-timeout");
  el.setReadDirs = document.getElementById("set-read-dirs");
  el.pluginChecks = document.getElementById("plugin-checks");
  el.setEmbBase = document.getElementById("set-emb-base");
  el.setEmbKey = document.getElementById("set-emb-key");
  el.setEmbModel = document.getElementById("set-emb-model");
  el.setEmbTopk = document.getElementById("set-emb-topk");
  el.setImgEnabled = document.getElementById("set-img-enabled");
  el.setImgSize = document.getElementById("set-img-size");
  el.imageModelList = document.getElementById("image-model-list");
  el.btnAddImageModel = document.getElementById("btn-add-image-model");
  // Stable Diffusion 引擎
  el.btnSd = document.getElementById("btn-sd");
  el.setSdEnabled = document.getElementById("set-sd-enabled");
  el.setSdBase = document.getElementById("set-sd-base");
  el.setSdFnindex = document.getElementById("set-sd-fnindex");
  el.setSdWidth = document.getElementById("set-sd-width");
  el.setSdHeight = document.getElementById("set-sd-height");
  el.setSdSteps = document.getElementById("set-sd-steps");
  el.setSdCfg = document.getElementById("set-sd-cfg");
  el.setSdSampler = document.getElementById("set-sd-sampler");
  el.setSdSchedule = document.getElementById("set-sd-schedule");
  el.setSdNegative = document.getElementById("set-sd-negative");
  el.settingsSub = document.getElementById("settings-sub");
  el.settingsUser = document.getElementById("settings-user");
  el.settingsAdmin = document.getElementById("settings-admin");
  el.myProviderList = document.getElementById("my-provider-list");
  el.sharedProviderList = document.getElementById("shared-provider-list");
  el.myImageList = document.getElementById("my-image-list");
  el.sharedImageList = document.getElementById("shared-image-list");
  el.btnAddMyProvider = document.getElementById("btn-add-my-provider");
  el.btnAddMyImage = document.getElementById("btn-add-my-image");
  el.btnUserSettingsClose = document.getElementById("btn-user-settings-close");
  el.userConfigMsg = document.getElementById("user-config-msg");
  el.setPxEnabled = document.getElementById("set-px-enabled");
  el.setPxProxy = document.getElementById("set-px-proxy");
  el.setPxR18 = document.getElementById("set-px-r18");
  el.setPxNum = document.getElementById("set-px-num");
  el.setWsEnabled = document.getElementById("set-ws-enabled");
  el.setWsProxy = document.getElementById("set-ws-proxy");
  el.setWsNum = document.getElementById("set-ws-num");
  el.setWsPagechars = document.getElementById("set-ws-pagechars");
  el.setBrowserEnabled = document.getElementById("set-browser-enabled");
  el.setBrowserHeadless = document.getElementById("set-browser-headless");
  el.setBrowserProxy = document.getElementById("set-browser-proxy");
  el.setBrowserSteps = document.getElementById("set-browser-steps");
  el.setBrowserIdle = document.getElementById("set-browser-idle");
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
  el.exportMsg = document.getElementById("export-msg");
  el.btnExportClose = document.getElementById("btn-export-close");
  el.btnExportDownload = document.getElementById("btn-export-download");
  el.btnExportPublish = document.getElementById("btn-export-publish");
  el.btnExportApply = document.getElementById("btn-export-apply");
  el.btnExportDiscard = document.getElementById("btn-export-discard");
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
  el.userLevelInput = document.getElementById("user-new-level");
  el.userMsg = document.getElementById("user-msg");
  el.btnUserCreate = document.getElementById("btn-user-create");
  el.btnUserCancel = document.getElementById("btn-user-cancel");
  // beta：多 AI 群聊
  el.threads = document.getElementById("threads");
  el.newchatDialog = document.getElementById("newchat-dialog");
  el.newchatPick = document.getElementById("newchat-pick");
  el.newchatTitle = document.getElementById("newchat-title");
  el.newchatMsg = document.getElementById("newchat-msg");
  el.newchatBack = document.getElementById("newchat-back");
  el.newchatSub = document.getElementById("newchat-sub");
  el.newchatTray = document.getElementById("newchat-tray");
  el.btnNewchatCancel = document.getElementById("btn-newchat-cancel");
  el.btnNewchatCreate = document.getElementById("btn-newchat-create");
  el.btnNewchatPlain = document.getElementById("btn-newchat-plain");
  el.confirmDialog = document.getElementById("confirm-dialog");
  el.confirmTitle = document.getElementById("confirm-title");
  el.confirmText = document.getElementById("confirm-text");
  el.btnConfirmCancel = document.getElementById("btn-confirm-cancel");
  el.btnConfirmOk = document.getElementById("btn-confirm-ok");
  el.cardDetailDialog = document.getElementById("card-detail-dialog");
  el.cardDetailBody = document.getElementById("card-detail-body");
  el.groupDialog = document.getElementById("group-dialog");
  el.groupPick = document.getElementById("group-pick");
  el.groupMsg = document.getElementById("group-msg");
  el.btnGroupCancel = document.getElementById("btn-group-cancel");
  el.btnGroupCreate = document.getElementById("btn-group-create");
  el.memDialog = document.getElementById("mem-dialog");
  el.memTitle = document.getElementById("mem-title");
  el.memBody = document.getElementById("mem-body");
  el.btnMemClose = document.getElementById("btn-mem-close");
  // 全屏图片查看器
  el.imgViewer = document.getElementById("img-viewer");
  el.ivStage = document.getElementById("iv-stage");
  el.ivImg = document.getElementById("iv-img");
  el.ivPanel = document.getElementById("iv-panel");
  el.ivInfo = document.getElementById("iv-info");
  el.ivDownload = document.getElementById("iv-download");
  el.ivClose = document.getElementById("iv-close");
}

function bindEvents() {
  el.btnSend.addEventListener("click", onSend);
  el.btnStop.addEventListener("click", onStop);
  // 点用户气泡以外的任意区域：收起亮出的 ✏️
  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest(".msg.user")) deactivateEditEntries();
  });
  // 点击消息内任意图片：打开全屏查看器（用户上传图 / 生成图）
  el.messages.addEventListener("click", function (e) {
    var img = (e.target && e.target.closest)
      ? e.target.closest("img.gen-img, img.chat-img")
      : null;
    if (img) {
      e.preventDefault();
      openImageViewer(img.currentSrc || img.src, img._imgMeta || null);
    }
  });
  el.ivClose.addEventListener("click", closeImageViewer);
  el.ivInfo.addEventListener("click", function (e) {
    e.stopPropagation();
    toggleImageInfo();
  });
  el.ivDownload.addEventListener("click", function (e) {
    e.stopPropagation();
    downloadViewerImage();
  });
  // 点图片外的黑色区域关闭
  el.ivStage.addEventListener("click", function (e) {
    if (e.target === el.ivStage) closeImageViewer();
  });
  // Esc：先关信息面板，再关查看器
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" || el.imgViewer.classList.contains("hidden")) return;
    e.preventDefault();
    if (el.ivPanel.classList.contains("open")) toggleImageInfo();
    else closeImageViewer();
  });
  el.btnAttach.addEventListener("click", function () { el.imageFile.click(); });
  el.imageFile.addEventListener("change", function () {
    addImageFiles(el.imageFile.files);
    el.imageFile.value = "";  // 允许再次选择同一个文件
  });
  // 剪贴板粘贴图片
  document.addEventListener("paste", function (e) {
    var items = (e.clipboardData || {}).items;
    if (!items) return;
    var files = [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image/") === 0) {
        var f = items[i].getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) addImageFiles(files);
  });
  // 拖拽：整页拦截防止浏览器直接打开文件；仅视觉模型处理
  var dragDepth = 0;
  document.addEventListener("dragenter", function (e) {
    if (!visionEnabled()) return;
    var types = e.dataTransfer && e.dataTransfer.types;
    if (types && Array.prototype.indexOf.call(types, "Files") >= 0) {
      dragDepth++;
      el.messages.classList.add("drag-over");
    }
  });
  document.addEventListener("dragleave", function () {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) el.messages.classList.remove("drag-over");
  });
  document.addEventListener("dragover", function (e) { e.preventDefault(); });
  document.addEventListener("drop", function (e) {
    e.preventDefault();
    dragDepth = 0;
    el.messages.classList.remove("drag-over");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      addImageFiles(e.dataTransfer.files);
    }
  });
  if (el.sessionSearch) el.sessionSearch.addEventListener("input", function () {
    state.sessionQuery = el.sessionSearch.value;
    if (!state.batchMode && !state.roomView) renderSessions();
    else if (state.batchMode) renderBatchSessions();
  });
  el.btnNew.addEventListener("click", openNewChatDialog);
  el.btnSettings.addEventListener("click", openSettings);
  el.btnLore.addEventListener("click", openLoreDialog);
  el.btnBatchDelete.addEventListener("click", openBatchMode);
  el.btnTheme.addEventListener("click", toggleTheme);
  el.btnAddProvider.addEventListener("click", addProviderRow);
  if (el.btnAddImageModel) el.btnAddImageModel.addEventListener("click", addImageModelRow);
  if (el.btnSd) el.btnSd.addEventListener("click", toggleSdEngine);
  if (el.btnTopbarToggle) el.btnTopbarToggle.addEventListener("click", toggleTopbar);
  if (el.btnAddMyProvider) el.btnAddMyProvider.addEventListener("click", addMyProvider);
  if (el.btnAddMyImage) el.btnAddMyImage.addEventListener("click", addMyImage);
  if (el.btnUserSettingsClose) el.btnUserSettingsClose.addEventListener("click", function () {
    el.dialog.close();
    renderProviderBar();
  });
  el.btnConfigSave.addEventListener("click", saveConfig);
  el.btnConfigCancel.addEventListener("click", function () { if (guardDiscard()) { clearDirty(); el.dialog.close(); } });
  el.input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onSend(); }
    if (e.key === "Escape") { el.input.blur(); }
  });
  el.input.addEventListener("input", function () { scheduleDraftSave(); autosizeInput(); });
  if (el.btnNav) el.btnNav.addEventListener("click", toggleNav);
  if (el.overlay) el.overlay.addEventListener("click", closeNav);
  if (el.btnLogout) el.btnLogout.addEventListener("click", doLogout);
  if (el.btnUsers) el.btnUsers.addEventListener("click", openUserDialog);
  if (el.btnJump) el.btnJump.addEventListener("click", jumpBottom);
  if (el.messages) el.messages.addEventListener("scroll", onMessagesScroll);
  if (el.btnUserCreate) el.btnUserCreate.addEventListener("click", createUser);
  if (el.btnUserCancel) el.btnUserCancel.addEventListener("click", function () { el.userDialog.close(); });
  // beta：多 AI 群聊
  if (el.btnNewchatCancel) el.btnNewchatCancel.addEventListener("click", function () { if (el.newchatDialog) el.newchatDialog.close(); });
  if (el.btnNewchatCreate) el.btnNewchatCreate.addEventListener("click", confirmNewChat);
  if (el.newchatBack) el.newchatBack.addEventListener("click", function () { if (state.newChat) renderPickRoots(); });
  if (el.btnNewchatPlain) el.btnNewchatPlain.addEventListener("click", plainNewChat);
  if (el.btnConfirmCancel) el.btnConfirmCancel.addEventListener("click", function () { if (el.confirmDialog) el.confirmDialog.close(); });
  if (el.btnConfirmOk) el.btnConfirmOk.addEventListener("click", function () {
    var fn = confirmState; confirmState = null;
    if (el.confirmDialog) el.confirmDialog.close();
    if (fn) fn();
  });
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
  if (el.btnExportApply) el.btnExportApply.addEventListener("click", applyCompressProposal);
  if (el.btnExportDiscard) el.btnExportDiscard.addEventListener("click", discardCompressProposal);
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
  [el.attrDialog, el.attrgenDialog, el.exportDialog, el.newchatDialog, el.groupDialog, el.memDialog, el.confirmDialog, el.cardDetailDialog].forEach(function (d) {
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
    updateSdEngineButton();
    applyTopbarState();
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
    .catch(function (e) { showToast("导出对话失败: " + e.message, "err"); });
}
var compressReview = null; // {sid, room, pid}

function _hideBtn(b) { if (b) b.classList.add("hidden"); }
function _showBtn(b) { if (b) b.classList.remove("hidden"); }

function openCompressDialog() {
  var s = currentSession(); if (!s) return;
  compressReview = null;
  el.exportSub.textContent = "AI 正在归纳对话并按标题合并进角色卡与世界观…";
  el.exportBody.innerHTML = '<div class="sub">压缩中，请稍候…</div>';
  el.exportMsg.textContent = "";
  el.exportMsg.className = "msg-bar";
  [el.btnExportApply, el.btnExportDiscard, el.btnExportClose, el.btnExportDownload, el.btnExportPublish].forEach(_hideBtn);
  safeCloseDialogs();
  if (el.exportDialog.showModal) el.exportDialog.showModal();
  fetchJSON("/api/sessions/" + s.id + "/compress", { method: "POST" }).then(function (res) {
    compressReview = { sid: s.id, room: !!res.room, pid: res.proposal_id };
    el.exportSub.textContent = "压缩提案 · 确认后写入当前会话副本（库主卡不受影响，漏列的条目会原样保留）";
    el.exportBody.innerHTML = res.room ? renderRoomCompressDiff(res) : renderSingleCompressDiff(res);
    _showBtn(el.btnExportApply);
    _showBtn(el.btnExportDiscard);
  }).catch(function (e) {
    el.exportSub.textContent = "压缩失败";
    el.exportBody.innerHTML = '<div class="msg-bar err">' + escapeHtml(e.message) + "</div>";
    _showBtn(el.btnExportClose);
  });
}

function discardCompressProposal() {
  // 整体放弃：提案 30 分钟后自动过期，无需通知后端
  compressReview = null;
  if (el.exportDialog) el.exportDialog.close();
}

function applyCompressProposal() {
  if (!compressReview) return;
  var review = compressReview;
  el.exportSub.textContent = "正在写入…";
  el.exportBody.innerHTML = '<div class="sub">写入会话副本中…</div>';
  el.exportMsg.textContent = "";
  el.exportMsg.className = "msg-bar";
  [el.btnExportApply, el.btnExportDiscard].forEach(_hideBtn);
  fetchJSON("/api/sessions/" + review.sid + "/compress/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proposal_id: review.pid }),
  }).then(function (out) {
    compressReview = null;
    el.exportSub.textContent = "压缩完成，已写入当前会话副本（库主卡不受影响）。";
    if (review.room) {
      el.exportBody.innerHTML = renderRoomCompressSummary(out.members || {}, out.world);
    } else {
      el.exportBody.innerHTML = renderCompressSummary(out.character, out.world);
      if (out.character && out.character.name) _showBtn(el.btnExportPublish);
    }
    _showBtn(el.btnExportDownload);
    _showBtn(el.btnExportClose);
    // 写入后才刷新：房间成员副本生效，后续对话即可感知压缩设定
    selectSession(review.sid);
  }).catch(function (e) {
    el.exportSub.textContent = "写入失败";
    el.exportMsg.className = "msg-bar err";
    el.exportMsg.textContent = e.message;
    el.exportBody.innerHTML = '<div class="sub">提案未被使用（通常是提案生成后设定又被改过），请关闭后重新点「☁ 压缩」。</div>';
    compressReview = null;
    _showBtn(el.btnExportClose);
    selectSession(review.sid);
  });
}

/* ==== 提案 diff 渲染 ==== */

function renderSingleCompressDiff(res) {
  var h = '<div class="cs-block"><b>📇 角色卡变更</b>' + renderCharacterDiffBody(res.diff.character) + "</div>";
  h += '<div class="cs-block"><b>🌍 世界观变更</b>' + renderWorldDiffBody(res.diff.world) + "</div>";
  return h;
}

function renderRoomCompressDiff(res) {
  var h = '<div class="cs-block"><b>👥 各参与者变更</b>';
  var ids = Object.keys(res.diff.members || {});
  if (!ids.length) h += "<div class='sub'>（无）</div>";
  ids.forEach(function (mid) {
    var d = res.diff.members[mid];
    var seat = (state.roomMembers || []).find(function (m) { return m.id === mid; });
    var afterC = res.after.members[mid] || {};
    var name = (seat && memberDisplayName(seat)) || cardDisplayName(afterC) || mid;
    h += '<details class="member-diff"' + (_charDiffEmpty(d) ? "" : " open") + ">"
      + "<summary><b>" + escapeHtml(name) + "</b><span class='sub'>" + escapeHtml(_charDiffCounts(d)) + "</span></summary>"
      + renderCharacterDiffBody(d) + "</details>";
  });
  h += "</div>";
  h += '<div class="cs-block"><b>🌍 世界观变更</b>' + renderWorldDiffBody(res.diff.world) + "</div>";
  return h;
}

function _charDiffEmpty(d) {
  if (!d) return true;
  return d.intro.before === d.intro.after
    && d.background.before === d.background.after
    && !(d.table.added || []).length
    && !(d.table.changed || []).length;
}

function _charDiffCounts(d) {
  if (_charDiffEmpty(d)) return "无变更";
  var parts = [];
  if (d.intro.before !== d.intro.after) parts.push("简介更新");
  if (d.background.before !== d.background.after) parts.push("经历更新");
  var addN = (d.table.added || []).length;
  var chgN = (d.table.changed || []).length;
  if (addN) parts.push("新增条目 " + addN);
  if (chgN) parts.push("更新条目 " + chgN);
  return parts.join(" · ");
}

function renderCharacterDiffBody(d) {
  if (!d) return "<div class='sub'>（无）</div>";
  var h = "";
  if (d.intro.before !== d.intro.after) {
    h += "<div class='diff-field'><span class='diff-k'>简介</span>" + renderTextDiff(d.intro.before, d.intro.after) + "</div>";
  }
  if (d.background.before !== d.background.after) {
    h += "<div class='diff-field'><span class='diff-k'>经历</span>" + renderTextDiff(d.background.before, d.background.after) + "</div>";
  }
  h += renderTableDiff(d.table);
  if (!h) h = "<div class='sub'>无变更</div>";
  return h;
}

function renderWorldDiffBody(d) {
  if (!d) return "<div class='sub'>（无）</div>";
  var h = "";
  if (d.content.before !== d.content.after) {
    h += "<div class='diff-field'><span class='diff-k'>世界观</span>" + renderTextDiff(d.content.before, d.content.after) + "</div>";
  }
  h += renderTableDiff(d.table);
  if (!h) h = "<div class='sub'>无变更</div>";
  return h;
}

function renderTableDiff(td) {
  if (!td) return "";
  var h = "";
  (td.added || []).forEach(function (e) {
    h += '<div class="diff-run diff-add"><span class="diff-sign">＋</span><b>' + escapeHtml(e.title || "") + "</b>："
      + escapeHtml(e.content || "") + (e.pinned ? pinBadge() : "") + "</div>";
  });
  (td.changed || []).forEach(function (e) {
    h += '<div class="diff-field table-changed"><span class="diff-sign">≣</span><b>' + escapeHtml(e.title || "") + "</b>"
      + (e.pinned ? pinBadge() : "") + renderTextDiff(e.before, e.after) + "</div>";
  });
  var unchanged = td.unchanged || [];
  if (unchanged.length) {
    var pinN = unchanged.filter(function (e) { return e.pinned; }).length;
    var label = unchanged.length + " 条未变" + (pinN ? "（含常驻 " + pinN + " 条）" : "");
    h += '<details class="diff-unchanged"><summary>' + label + "</summary>"
      + unchanged.map(function (e) {
        return '<div class="diff-eq"><b>' + escapeHtml(e.title || "") + "</b>："
          + escapeHtml(e.content || "") + (e.pinned ? pinBadge() : "") + "</div>";
      }).join("") + "</details>";
  }
  if ((td.removed || []).length) {
    h += '<div class="msg-bar err">异常：有 ' + td.removed.length + " 条条目在合并中丢失，请放弃并重试。</div>";
  }
  return h;
}

function pinBadge() {
  return '<span class="pin-badge" title="常驻：无条件注入系统提示词">常驻</span>';
}

/* 行级 LCS：>1000 行时返回 null，调用方退化为前后整块渲染 */
function lineLCS(a, b) {
  if (a.length > 1000 || b.length > 1000) return null;
  var n = a.length, m = b.length;
  var dp = new Array(n + 1);
  for (var i = 0; i <= n; i++) {
    dp[i] = new Array(m + 1).fill(0);
  }
  for (i = n - 1; i >= 0; i--) {
    for (var j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  var ops = [];
  i = 0; j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ t: "eq", x: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ t: "del", x: a[i] }); i++; }
    else { ops.push({ t: "add", x: b[j] }); j++; }
  }
  while (i < n) ops.push({ t: "del", x: a[i++] });
  while (j < m) ops.push({ t: "add", x: b[j++] });
  return ops;
}

function renderTextDiff(before, after) {
  before = before || "";
  after = after || "";
  if (!before && !after) return "";
  var ops = lineLCS(before.split("\n"), after.split("\n"));
  if (ops === null) {
    return '<div class="diff-block"><div class="diff-run diff-del">' + escapeHtml(before) + "</div>"
      + '<div class="diff-run diff-add">' + escapeHtml(after) + "</div></div>";
  }
  var h = '<div class="diff-block">';
  var i = 0;
  while (i < ops.length) {
    var t = ops[i].t;
    var lines = [];
    while (i < ops.length && ops[i].t === t) { lines.push(ops[i].x); i++; }
    h += renderDiffRun(t, lines);
  }
  return h + "</div>";
}

function renderDiffRun(t, lines) {
  if (t === "eq") {
    if (lines.length <= 3) {
      return '<div class="diff-run diff-eq">' + lines.map(_eqLine).join("") + "</div>";
    }
    return '<details class="diff-eq details-eq"><summary>' + lines.length + " 行未变（点击展开）</summary>"
      + lines.map(_eqLine).join("") + "</details>";
  }
  var cls = t === "del" ? "diff-del" : "diff-add";
  var sign = t === "del" ? "−" : "＋";
  var body = lines.map(function (ln) {
    return '<div><span class="diff-sign">' + sign + "</span> " + escapeHtml(ln) + "</div>";
  }).join("");
  return '<div class="diff-run ' + cls + '">' + body + "</div>";
}

function _eqLine(ln) {
  return '<div><span class="diff-sign dim">·</span> ' + escapeHtml(ln) + "</div>";
}
function renderRoomCompressSummary(members, world) {
  var h = '<div class="cs-block"><b>👥 各参与者压缩结果</b>';
  var ids = Object.keys(members);
  if (!ids.length) h += "<div class='sub'>（无）</div>";
  ids.forEach(function (mid) {
    var c = members[mid];
    var seat = (state.roomMembers || []).find(function (m) { return m.id === mid; });
    var name = (seat && memberDisplayName(seat)) || (c && cardDisplayName(c)) || mid;
    h += '<div class="cs-member"><div>' + escapeHtml(name) + "</div>";
    if (c && c.intro) h += "<div class='sub'>简介：" + escapeHtml(c.intro) + "</div>";
    if (c && c.background) h += "<div class='sub'>经历：" + escapeHtml(c.background) + "</div>";
    if (c && c.table && c.table.length) {
      h += "<ul class='cs-list'>" + c.table.map(function (t) {
        return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
      }).join("") + "</ul>";
    }
    h += "</div>";
  });
  h += "</div><div class='cs-block'><b>🌍 压缩后的世界观</b>";
  if (world) {
    if (world.content) h += "<div class='sub'>" + escapeHtml(world.content) + "</div>";
    if (world.table && world.table.length) {
      h += "<ul class='cs-list'>" + world.table.map(function (t) {
        return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
      }).join("") + "</ul>";
    }
  } else h += "<div class='sub'>（无）</div>";
  h += "</div>";
  return h;
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
    showToast("已保存为角色卡：" + (res.character.name || ""), "ok");
  }).catch(function (e) { showToast(e.message, "err"); });
}
function forkSessionChar() {
  var s = currentSession(); if (!s) return;
  fetchJSON("/api/sessions/" + s.id + "/character/fork", { method: "POST" }).then(function (res) {
    state.scopeCache[s.id] = { char: res.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: true };
    renderSessionMeta();
  }).catch(function (e) { showToast(e.message, "err"); });
}
function restoreSessionChar() {
  var s = currentSession(); if (!s) return;
  if (!confirm("删除会话副本并还原为库主卡？")) return;
  fetchJSON("/api/sessions/" + s.id + "/character/override", { method: "DELETE" }).then(function (res) {
    state.scopeCache[s.id] = { char: res.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: false };
    renderSessionMeta();
  }).catch(function (e) { showToast(e.message, "err"); });
}
function exportSessionChar() {
  var s = currentSession(); if (!s) return;
  fetch("/api/sessions/" + s.id + "/character/export", { headers: authHeaders() })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (obj) { downloadJSON(obj, ((obj.data && obj.data.name) || "character") + ".json"); })
    .catch(function (e) { showToast("导出失败: " + e.message, "err"); });
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
  var char = effectiveChar(); if (!char) { showToast("请先绑定角色再生成属性"); return; }
  var sc = state.scopeCache[s.id]; var scoped = sc ? sc.scoped : false;
  var payload = { name: char.name || "", intro: char.intro || "", background: char.background || "", existing: char.attributes || [] };
  fetchJSON("/api/gen/attributes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }).then(function (res) {
    openAttrgenDialog(res.attributes || [], function (attrs) {
      var merged = (char.attributes || []).concat(attrs);
      if (scoped) {
        var upd = JSON.parse(JSON.stringify(char)); upd.attributes = merged;
        fetchJSON("/api/sessions/" + s.id + "/character", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ character: upd }) }).then(function (rr) {
          state.scopeCache[s.id] = { char: rr.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: true };
          renderSessionMeta(); showToast("已添加属性到会话副本", "ok");
        }).catch(function (e) { showToast(e.message, "err"); });
      } else {
        var lib = JSON.parse(JSON.stringify(char)); lib.attributes = merged;
        fetchJSON("/api/characters/" + char.id, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(lib) }).then(function () {
          if (state.scopeCache[s.id]) state.scopeCache[s.id].char.attributes = merged;
          return refreshLore();
        }).then(function () { renderSessionMeta(); showToast("已添加属性到角色卡", "ok"); }).catch(function (e) { showToast(e.message, "err"); });
      }
    });
  }).catch(function (e) { showToast("生成失败: " + e.message, "err"); });
}

/* ==== 角色扮演内容渲染（分段 + 样式区分）==== */
function rpEnabled() {
  var rp = state.config && state.config.roleplay;
  return !!(rp && rp.format_enabled);
}
// 把动作() / 内心 _…_ / 对白「」 分别包成带样式的 span
function renderRp(text) {
  // 旧语法 ____…____（多字节、跨行）+ 对白/动作仍走正则；单下划线内心用扫描器
  // 按下标做边界检查（避免 lookbehind 的旧浏览器兼容问题，也能接在其他 token 之后）。
  var tokenRe = /____[\s\S]*?____|「[^」\n]*」|“[^”\n]*”|"[^"\n]*"|『[^』\n]*』|（[^（）\n]*）|\([^()\n]*\)/;
  var innerRe = /^[^\s_](?:[^_\n]*?[^\s_])?_/;
  var html = "", last = 0, i = 0;

  function emitPlainUntil(end) {
    if (end > last) html += escapeHtml(text.slice(last, end));
  }
  // 单下划线内心：同 CommonMark「词内不生效」防 my_var 误染；不跨行，首尾不贴空白。
  // 返回 token 长度，不匹配返回 0。
  function thoughtLen(pos) {
    if (pos > 0 && /[A-Za-z0-9]/.test(text.charAt(pos - 1))) return 0;
    var inner = innerRe.exec(text.slice(pos + 1));
    if (!inner) return 0;
    var afterClose = pos + 1 + inner[0].length;
    if (afterClose < text.length && /[A-Za-z0-9]/.test(text.charAt(afterClose))) return 0;
    return afterClose - pos;
  }

  while (i < text.length) {
    var tlen = 0;
    if (text.charAt(i) === "_") {
      // 四下划线旧语法优先；否则试新单下划线
      tokenRe.lastIndex = i;
      var lm = tokenRe.exec(text);
      if (lm && lm.index === i) tlen = lm[0].length;
      else tlen = thoughtLen(i);
      if (tlen) {
        emitPlainUntil(i);
        html += '<span class="dlg-thought">' + escapeHtml(text.slice(i, i + tlen)) + "</span>";
      }
    } else {
      tokenRe.lastIndex = i;
      var m = tokenRe.exec(text);
      if (m && m.index === i) {
        tlen = m[0].length;
        emitPlainUntil(i);
        var tok = m[0];
        var c0 = tok.charAt(0);
        var cls = (c0 === "「" || c0 === "“" || c0 === '"' || c0 === "『") ? "dlg-speech" : "dlg-action";
        html += '<span class="' + cls + '">' + escapeHtml(tok) + "</span>";
      }
    }
    if (tlen) {
      i += tlen;
      last = i;
    } else {
      i += 1;
    }
  }
  emitPlainUntil(text.length);
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
function renderSessions() {
  el.sessionList.innerHTML = "";
  if (el.searchWrap) el.searchWrap.classList.remove("hidden");
  var shown = filteredSessions();
  if (!shown.length) {
    var empty = document.createElement("div");
    empty.className = "list-empty";
    empty.textContent = state.sessionQuery.trim() ? "没有匹配的会话" : "还没有会话，点 ＋ 新建";
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
    // 私聊窗口按成员角色卡实时解析显示名（别名优先）；群聊窗口用建群时的标题
    var winLabel = t.title || "窗口";
    if (t.kind !== "group" && t.member_ids && t.member_ids.length) {
      var seat0 = (state.roomMembers || []).find(function (m) {
        return m.id === t.member_ids[0] && !m.is_user;
      });
      if (seat0) winLabel = memberDisplayName(seat0);
    }
    item.textContent = (t.kind === "group" ? "👥 " : "💬 ") + winLabel;
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

// 手机端顶栏折叠：折叠后只留 ☰ + 会话标题 + 展开键（仅窄屏生效）
function toggleTopbar() {
  state.topbarCollapsed = !state.topbarCollapsed;
  localStorage.setItem("flyai-topbar-collapsed", state.topbarCollapsed ? "1" : "0");
  applyTopbarState();
}
function applyTopbarState() {
  if (!el.topbar) return;
  el.topbar.classList.toggle("collapsed", !!state.topbarCollapsed);
  if (el.btnTopbarToggle) {
    el.btnTopbarToggle.textContent = state.topbarCollapsed ? "⌄" : "⌃";
    el.btnTopbarToggle.title = state.topbarCollapsed ? "展开顶栏" : "折叠顶栏";
  }
}

function renderSessionMeta() {
  var s = currentSession();
  if (!s) {
    el.sessionMeta.innerHTML = '<span class="chip">新建会话以开始</span>';
    el.topbarTitle.textContent = "新会话";
    return;
  }
  el.topbarTitle.textContent = s.title || "新会话";

  if (roomMode()) {
    // 房间：不显示「绑定单个角色」的选择器，仅展示成员与世界观
    var names = (state.roomMembers || []).filter(function (m) { return !m.is_user; }).map(memberDisplayName);
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
    el.sessionMeta.appendChild(makeTokenChip());
    // R8：用户扮演的自设（persona）选择器
    var seat = roomUserSeat();
    var personaChip = document.createElement("span");
    personaChip.className = "chip";
    personaChip.innerHTML = "🎭 扮演 " + charSelectOptions("persona", (seat && seat.character_id) || "", "本人");
    el.sessionMeta.appendChild(personaChip);
    el.sessionMeta.appendChild(makeMetaActions(null, false));
    var selW = el.sessionMeta.querySelector('select[data-world]');
    if (selW) selW.addEventListener("change", function () { updateSessionBinding("world_id", selW.value); });
    var selP = el.sessionMeta.querySelector('select[data-persona]');
    if (selP) selP.addEventListener("change", function () { setPersona(seat, selP.value); });
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
  el.sessionMeta.appendChild(makeTokenChip());
  el.sessionMeta.appendChild(makeMetaActions(char, scoped));

  var selChar = el.sessionMeta.querySelector('select[data-char]');
  var selWorld = el.sessionMeta.querySelector('select[data-world]');
  if (selChar) selChar.addEventListener("change", function () { updateSessionBinding("character_id", selChar.value); });
  if (selWorld) selWorld.addEventListener("change", function () { updateSessionBinding("world_id", selWorld.value); });
}

function makeTokenChip() {
  var chip = document.createElement("span");
  chip.className = "chip token-chip";
  var total = state.currentSessionId ? intOr(state.tokenTotals[state.currentSessionId], 0) : 0;
  chip.textContent = "🪙 " + fmtCompact(total) + " tokens";
  var d = state.currentSessionId ? state.tokenDetails[state.currentSessionId] : null;
  if (d) {
    chip.title = "会话累计 token（持久保存）：共 " + fmtNum(total) +
      "\n输入 " + fmtNum(intOr(d.p, 0)) + " · 输出 " + fmtNum(intOr(d.c, 0));
  } else {
    chip.title = "会话累计 token（持久保存）";
  }
  return chip;
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
    opts.push('<option value="' + c.id + '"' + (c.id === selected ? " selected" : "") + '>' + escapeHtml(cardDisplayName(c)) + '</option>');
  });
  return '<select data-' + kind + '>' + opts.join("") + '</select>';
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

/* ==== 角色卡归属/层级小工具 ==== */
function isOwnCard(c) {
  if (!c) return false;
  if (c.author === state.userId) return true;
  return state.role === "admin" && !c.author;  // admin 的旧卡 author 为空
}
function rootCards() {
  return (state.characters || []).filter(function (c) { return !c.parent_id; });
}
function variantCards(rootId) {
  return (state.characters || []).filter(function (c) { return c.parent_id === rootId; });
}
function cardDisplayName(c) { return (c && (c.alias || c.name)) || "未命名"; }
// 房间成员的显示名：其角色卡（含副本）有别名时优先别名，否则回退座位名
function memberDisplayName(m) {
  if (!m) return "";
  var ch = m.character_id ? characterOf(m.character_id) : null;
  return ch ? cardDisplayName(ch) : (m.name || "");
}

/* ==== 通用二次确认弹窗 ==== */
var confirmState = null;
function askConfirm(opts) {
  el.confirmTitle.textContent = opts.title || "请确认";
  el.confirmText.textContent = opts.text || "";
  el.btnConfirmOk.textContent = opts.okLabel || "确认";
  el.btnConfirmOk.className = "btn " + (opts.danger === false ? "primary" : "danger");
  confirmState = opts.onOk || null;
  if (el.confirmDialog.open) el.confirmDialog.close();
  el.confirmDialog.showModal();
}

/* ==== 角色卡详情弹窗 ==== */
function cardDetailHtml(c) {
  var h = '<div class="cd-head">' + makeAvatarHtml(c, false) +
    '<div class="cd-head-info"><h3>' + escapeHtml(c.name || "未命名") + "</h3>" +
    '<div class="sub">' + (c.alias ? "别名：" + escapeHtml(c.alias) + " · " : "") +
    "标识：" + escapeHtml((c.author || "?") + ":" + c.id) + "</div></div></div>";
  if (c.intro) h += '<h4>简介</h4><div class="cd-sec">' + escapeHtml(c.intro) + "</div>";
  if (c.background) h += '<h4>经历</h4><div class="cd-sec">' + escapeHtml(c.background) + "</div>";
  if (c.system_prompt) h += '<h4>系统级提示词</h4><div class="cd-sec">' + escapeHtml(c.system_prompt) + "</div>";
  if (c.attributes && c.attributes.length) {
    h += '<h4>属性</h4><ul class="cd-list">' + c.attributes.map(function (a) {
      return "<li>" + escapeHtml(a.label || a.key) + "（" + escapeHtml(a.key) + "）· 初值 " +
        escapeHtml(a.initial !== undefined ? a.initial : 0) + "</li>";
    }).join("") + "</ul>";
  }
  if (c.table && c.table.length) {
    h += '<h4>内容表</h4><ul class="cd-list">' + c.table.map(function (t) {
      return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
    }).join("") + "</ul>";
  }
  if (c.prompt_format) h += '<h4>对白格式要求</h4><div class="cd-sec">' + escapeHtml(c.prompt_format) + "</div>";
  if (c.prompt_tools) h += '<h4>工具使用要求</h4><div class="cd-sec">' + escapeHtml(c.prompt_tools) + "</div>";
  return h;
}
function openCardDetail(c, actions) {
  el.cardDetailBody.innerHTML = cardDetailHtml(c) +
    '<div class="dialog-actions" id="card-detail-actions"></div>';
  var acts = document.getElementById("card-detail-actions");
  (actions || []).forEach(function (a) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "btn " + (a.cls || "");
    b.textContent = a.label;
    b.addEventListener("click", function () { if (a.onClick) a.onClick(); });
    acts.appendChild(b);
  });
  var close = document.createElement("button");
  close.type = "button"; close.className = "btn primary"; close.textContent = "关 闭";
  close.addEventListener("click", function () { el.cardDetailDialog.close(); });
  acts.appendChild(close);
  if (el.cardDetailDialog.open) el.cardDetailDialog.close();
  el.cardDetailDialog.showModal();
}
function forkToMine(id, after) {
  fetchJSON("/api/characters/" + id + "/fork", { method: "POST" })
    .then(function (c) {
      return refreshLore().then(function () {
        el.cardDetailDialog.close();
        if (after) after(c);
      });
    })
    .catch(function (e) { showToast("复制失败: " + e.message, "err"); });
}

/* ==== 新建对话：根角色卡 → 副本选择 ==== */
function openNewChatDialog() {
  if (state.busy) return;
  state.newChat = { selected: {}, rootId: "" };
  el.newchatTitle.value = "";
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  safeCloseDialogs();
  el.newchatDialog.showModal();
  renderPickRoots();
}
function renderPickRoots() {
  state.newChat.rootId = "";
  el.newchatBack.classList.add("hidden");
  el.newchatSub.textContent = "选择一个根角色卡，再选择主卡或其副本；可跨根卡多选，多个角色即多 AI 群聊房间。";
  el.newchatPick.innerHTML = "";
  var roots = rootCards();
  if (!roots.length) {
    el.newchatPick.innerHTML = '<div class="sub">角色库还没有角色卡片，请先在「🧩 内容」里创建。</div>';
  } else {
    var grid = document.createElement("div");
    grid.className = "root-grid";
    roots.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "root-card";
      var n = variantCards(c.id).length;
      b.innerHTML = makeAvatarHtml(c, true) +
        '<span class="rc-name">' + escapeHtml(c.name || "未命名") + "</span>" +
        '<span class="rc-meta">' + (isOwnCard(c) ? "" : "🌐 ") + n + " 个副本</span>";
      b.addEventListener("click", function () { renderPickVariants(c.id); });
      grid.appendChild(b);
    });
    el.newchatPick.appendChild(grid);
  }
  renderPickTray();
}
function renderPickVariants(rootId) {
  state.newChat.rootId = rootId;
  el.newchatBack.classList.remove("hidden");
  var root = characterOf(rootId);
  el.newchatSub.textContent = "选择「" + cardDisplayName(root) + "」的主卡或副本，可在详情中查看卡片数据。";
  el.newchatPick.innerHTML = "";
  var list = document.createElement("div");
  list.className = "variant-list";
  list.appendChild(variantRow(root, true));
  variantCards(rootId).forEach(function (v) { list.appendChild(variantRow(v, false)); });
  el.newchatPick.appendChild(list);
  renderPickTray();
}
function variantRow(c, isRoot) {
  var row = document.createElement("div");
  row.className = "variant-row";
  var own = isOwnCard(c);
  var selected = !!state.newChat.selected[c.id];
  var pick = document.createElement("button");
  pick.type = "button"; pick.className = "vr-pick" + (selected ? " selected" : "");
  pick.innerHTML =
    '<span class="vr-tag' + (isRoot ? "" : " variant") + '">' + (isRoot ? "主卡" : "副本") + "</span>" +
    makeAvatarHtml(c, true) +
    '<span class="vr-name">' + escapeHtml(cardDisplayName(c)) + "</span>" +
    (selected ? '<span class="vr-check">✓</span>' : "");
  pick.addEventListener("click", function () { togglePick(c.id); });
  row.appendChild(pick);

  var more = document.createElement("button");
  more.type = "button"; more.className = "flat-btn"; more.textContent = "...";
  more.title = "查看卡片数据";
  more.addEventListener("click", function () { openCardDetail(c, pickActions(c)); });
  row.appendChild(more);

  var del = document.createElement("button");
  del.type = "button"; del.className = "flat-btn danger" + (own ? "" : " hidden");
  del.textContent = "删除"; del.title = "删除";
  del.addEventListener("click", function () { confirmDeleteCard(c, isRoot); });
  row.appendChild(del);
  return row;
}
function pickActions(c) {
  var picked = !!state.newChat.selected[c.id];
  var acts = [{
    label: picked ? "取消选择" : "选择此卡", cls: "primary",
    onClick: function () { togglePick(c.id); el.cardDetailDialog.close(); },
  }];
  if (!isOwnCard(c)) {
    acts.push({
      label: "复制为我的角色卡", cls: "",
      onClick: function () {
        forkToMine(c.id, function (nc) { openCardDetail(nc, pickActions(nc)); });
      },
    });
  }
  return acts;
}
function togglePick(id) {
  var sel = state.newChat.selected;
  if (sel[id]) delete sel[id];
  else { var c = characterOf(id); if (c) sel[id] = c; }
  if (state.newChat.rootId) renderPickVariants(state.newChat.rootId);
  else renderPickRoots();
}
function renderPickTray() {
  var ids = Object.keys(state.newChat.selected);
  el.newchatTray.classList.toggle("hidden", !ids.length);
  el.newchatTray.innerHTML = '<span class="tray-label">已选：</span>';
  ids.forEach(function (id) {
    var c = state.newChat.selected[id];
    var chip = document.createElement("span");
    chip.className = "pick-chip";
    chip.textContent = cardDisplayName(c);
    var x = document.createElement("button");
    x.type = "button"; x.className = "pick-chip-x"; x.textContent = "✕";
    x.title = "移除";
    x.addEventListener("click", function () { togglePick(id); });
    chip.appendChild(x);
    el.newchatTray.appendChild(chip);
  });
  el.btnNewchatCreate.disabled = !ids.length;
}
function confirmDeleteCard(c, isRoot) {
  askConfirm({
    title: "删除角色卡",
    text: isRoot
      ? "确定删除根角色卡「" + cardDisplayName(c) + "」？其全部副本也会一并删除，且无法恢复。"
      : "确定删除副本「" + cardDisplayName(c) + "」？此操作无法恢复。",
    okLabel: "删除",
    onOk: function () { doDeleteCard(c.id); },
  });
}
function doDeleteCard(id) {
  fetchJSON("/api/characters/" + id, { method: "DELETE" })
    .then(function () { return refreshLore(); })
    .then(function () {
      if (state.newChat.rootId === id) renderPickRoots();
      else if (state.newChat.rootId) renderPickVariants(state.newChat.rootId);
      else renderPickRoots();
      renderSessionMeta();
    })
    .catch(function (e) {
      el.newchatMsg.textContent = "删除失败: " + e.message;
      el.newchatMsg.className = "msg-bar err";
    });
}
function confirmNewChat() {
  var title = el.newchatTitle.value.trim();
  var ids = Object.keys(state.newChat.selected);
  if (!ids.length) return;
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  fetchJSON("/api/rooms", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title, character_ids: ids }),
  }).then(function (s) {
    state.currentSessionId = s.id;
    state.currentAttrs = null;
    el.newchatDialog.close();
    return refreshSessions();
  }).then(function () {
    selectSession(state.currentSessionId);
    el.input.focus();
  }).catch(function (e) {
    el.newchatMsg.textContent = "创建房间失败: " + e.message;
    el.newchatMsg.className = "msg-bar err";
  });
}
function plainNewChat() {
  var title = el.newchatTitle.value.trim();
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  fetchJSON("/api/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title }),
  }).then(function (s) {
    state.currentSessionId = s.id; state.currentAttrs = null;
    el.newchatDialog.close();
    return refreshSessions();
  }).then(function () {
    selectSession(state.currentSessionId); el.input.focus();
  }).catch(function (e) {
    el.newchatMsg.textContent = "创建失败: " + e.message;
    el.newchatMsg.className = "msg-bar err";
  });
}

function selectSession(id) {
  if (state.busy) return;
  state.batchMode = false;
  state.batchPicked = {};
  state.currentSessionId = id;
  renderSessionList();
  closeNav();
  fetchJSON("/api/sessions/" + id).then(function (s) {
    // 更新本地 session 中的角色/世界与标题
    var sess = currentSession();
    if (sess) { sess.character_id = s.character_id; sess.world_id = s.world_id; sess.title = s.title; }
    state.currentAttrs = s.attributes || null;
    // 累计 token：以服务端持久值为准（页面周期内再叠加新 done 事件）
    var tu = s.token_usage || {};
    state.tokenTotals[id] = intOr(tu.total_tokens, 0);
    state.tokenDetails[id] = { p: intOr(tu.prompt_tokens, 0), c: intOr(tu.completion_tokens, 0) };
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
      restoreDraft();
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
/* R8：用户扮演自设（persona）—— 存在用户座位的 character_id 上，复用库角色卡 */
function roomUserSeat() {
  return (state.roomMembers || []).find(function (m) { return m.is_user; });
}
function personaChar() {
  var seat = roomUserSeat();
  if (!seat || !seat.character_id) return null;
  return characterOf(seat.character_id) || null;
}
function setPersona(seat, characterId) {
  if (!seat || !state.currentSessionId) return;
  fetchJSON("/api/rooms/" + state.currentSessionId + "/members/" + seat.id, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ character_id: characterId }),
  }).then(function (m) {
    var idx = state.roomMembers.findIndex(function (x) { return x.id === seat.id; });
    if (idx >= 0) state.roomMembers[idx] = m;
    renderSessionMeta();
    // 刷新当前窗口，让已有用户气泡也按新扮演角色重标注
    if (state.currentThreadId) loadThread(state.currentThreadId);
  }).catch(function (e) { addErrorBar("设置自设失败: " + e.message); });
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
    renderHistory(t.messages || [], t.edits || {});
    restoreDraft();
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
    lbl.appendChild(document.createTextNode(" " + escapeHtml(memberDisplayName(m))));
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
    wrap.innerHTML = '<div class="mem-head">' + makeAvatarHtml(ch || m, true) + ' <b>' + escapeHtml(memberDisplayName(m)) + "</b></div>";
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

function clearMessages() {
  el.messages.innerHTML = "";
  state.currentAssistant = null;
  state.turnNodes = {};
}

/* ==== 内容管理 ==== */
function openLoreDialog() {
  clearDirty();
  state.loreNav = "";
  el.loreBody.innerHTML = "<p class='sub'>管理角色卡与世界观。会话左上角可选用的角色/世界观。</p>";
  renderLoreSections();
  el.loreDialog.showModal();
}
function renderLoreSections() {
  var worldItems = (state.worlds || []).map(function (w) {
    return loreRow("worlds", w.id, w.name || "未命名", w.avatar, (w.table || []).length + " 条内容");
  }).join("");

  el.loreBody.innerHTML =
    '<div id="lore-char-nav"></div>' +
    "<div class='spacer'></div>" +
    "<h4>世界观</h4>" +
    '<div class="lore-list">' + (worldItems || '<div class="lore-item"><span class="l-name">暂无世界观</span></div>') + "</div>" +
    '<div class="btn-row" style="margin-top:10px">' +
    '<button id="lore-new-world" class="btn small">＋ 新建世界观</button>' +
    '<button id="lore-import-world" class="btn small">⇪ 导入世界观</button>' +
    "</div>" +
    '<div class="dialog-actions"><button id="lore-close" class="btn primary">完成</button></div>';

  renderLoreCharNav();
  document.getElementById("lore-new-world").addEventListener("click", function () { newWorld(); });
  document.getElementById("lore-import-world").addEventListener("click", function () { importLore("worlds"); });
  document.getElementById("lore-close").addEventListener("click", function () { el.loreDialog.close(); });
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

/* 角色卡两级导航（同新建对话选卡） */
function renderLoreCharNav() {
  var box = document.getElementById("lore-char-nav");
  if (!box) return;
  box.innerHTML = "<h4>角色卡</h4><div id='lore-char-body'></div>";
  var body = document.getElementById("lore-char-body");
  if (state.loreNav) renderLoreCharVariants(body, state.loreNav);
  else renderLoreCharRoots(body);
}
function renderLoreCharRoots(body) {
  var roots = rootCards();
  var grid = document.createElement("div");
  grid.className = "root-grid";
  roots.forEach(function (c) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "root-card";
    var n = variantCards(c.id).length;
    b.innerHTML = makeAvatarHtml(c, true) +
      '<span class="rc-name">' + escapeHtml(c.name || "未命名") + "</span>" +
      '<span class="rc-meta">' + (isOwnCard(c) ? "" : "🌐 ") + n + " 个副本</span>";
    b.addEventListener("click", function () { state.loreNav = c.id; renderLoreCharNav(); });
    grid.appendChild(b);
  });
  var add = document.createElement("button");
  add.type = "button"; add.className = "root-card add-tile";
  add.innerHTML = '<span class="rc-add">＋</span><span class="rc-name">新建根角色卡</span>';
  add.addEventListener("click", function () { newCharacter(); });
  grid.appendChild(add);
  body.appendChild(grid);

  var row = document.createElement("div");
  row.className = "btn-row"; row.style.marginTop = "10px";
  var imp = document.createElement("button");
  imp.type = "button"; imp.className = "btn small"; imp.textContent = "⇪ 导入角色";
  imp.addEventListener("click", function () { importLore("characters"); });
  row.appendChild(imp);
  body.appendChild(row);
}
function renderLoreCharVariants(body, rootId) {
  var root = characterOf(rootId);
  if (!root) { state.loreNav = ""; renderLoreCharNav(); return; }
  var bar = document.createElement("div");
  bar.className = "dialog-actions";
  var back = document.createElement("button");
  back.type = "button"; back.className = "btn"; back.textContent = "← 返回根角色卡";
  back.addEventListener("click", function () { state.loreNav = ""; renderLoreCharNav(); });
  bar.appendChild(back);
  body.appendChild(bar);

  var sub = document.createElement("p");
  sub.className = "sub";
  sub.innerHTML = "「" + escapeHtml(root.name || "未命名") + "」的主卡与副本";
  body.appendChild(sub);

  var list = document.createElement("div");
  list.className = "variant-list";
  list.appendChild(loreVariantRow(root, true));
  variantCards(rootId).forEach(function (v) { list.appendChild(loreVariantRow(v, false)); });
  body.appendChild(list);

  var add = document.createElement("button");
  add.type = "button"; add.className = "btn small";
  add.style.marginTop = "10px";
  add.textContent = "＋ 新建副本";
  add.addEventListener("click", function () { newVariant(rootId); });
  body.appendChild(add);
}
function loreVariantRow(c, isRoot) {
  var row = document.createElement("div");
  row.className = "variant-row lore-variant-row";
  var info = document.createElement("div");
  info.className = "vr-info";
  info.innerHTML =
    '<span class="vr-tag' + (isRoot ? "" : " variant") + '">' + (isRoot ? "主卡" : "副本") + "</span>" +
    makeAvatarHtml(c, true) +
    '<span class="vr-name">' + escapeHtml(cardDisplayName(c)) + "</span>" +
    (c.public ? '<span class="vr-public" title="已共享">🌐</span>' : "");
  row.appendChild(info);

  var actions = document.createElement("div");
  actions.className = "l-actions";
  var edit = document.createElement("button");
  edit.type = "button"; edit.className = "btn small"; edit.textContent = "编辑";
  edit.addEventListener("click", function () {
    if (isOwnCard(c)) editCharacter(c.id);
    else editSharedCard(c);
  });
  actions.appendChild(edit);

  var exportBtn = document.createElement("button");
  exportBtn.type = "button"; exportBtn.className = "btn small";
  exportBtn.textContent = "导出";
  exportBtn.title = "导出为 JSON";
  exportBtn.addEventListener("click", function () {
    el.cardDetailDialog.close();
    exportLore("characters", c.id);
  });
  actions.appendChild(exportBtn);

  var more = document.createElement("button");
  more.type = "button"; more.className = "flat-btn"; more.textContent = "...";
  more.title = "查看卡片数据";
  more.addEventListener("click", function () {
    var acts = [];
    if (isOwnCard(c)) {
      acts.push({ label: "编辑", cls: "primary", onClick: function () { el.cardDetailDialog.close(); editCharacter(c.id); } });
    } else {
      acts.push({
        label: "复制为我的角色卡并编辑", cls: "primary",
        onClick: function () { forkToMine(c.id, function () { el.cardDetailDialog.close(); }); },
      });
    }
    acts.push({
      label: "导出 JSON",
      onClick: function () { exportLore("characters", c.id); },
    });
    openCardDetail(c, acts);
  });
  actions.appendChild(more);

  if (isOwnCard(c)) {
    var del = document.createElement("button");
    del.type = "button"; del.className = "flat-btn danger"; del.textContent = "删除";
    del.addEventListener("click", function () {
      askConfirm({
        title: "删除角色卡",
        text: isRoot
          ? "确定删除根角色卡「" + cardDisplayName(c) + "」？其全部副本也会一并删除，且无法恢复。"
          : "确定删除副本「" + cardDisplayName(c) + "」？此操作无法恢复。",
        okLabel: "删除",
        onOk: function () {
          fetchJSON("/api/characters/" + c.id, { method: "DELETE" })
            .then(function () { return refreshLore(); })
            .then(function () {
              if (isRoot) state.loreNav = "";
              renderLoreCharNav(); renderSessionMeta();
            })
            .catch(function (e) { showToast("删除失败: " + e.message, "err"); });
        },
      });
    });
    actions.appendChild(del);
  }
  row.appendChild(actions);
  return row;
}
function editSharedCard(c) {
  askConfirm({
    title: "共享角色卡",
    text: "共享卡不能直接修改。是否复制为我的角色卡并编辑？",
    okLabel: "复制并编辑", danger: false,
    onOk: function () {
      forkToMine(c.id, function (nc) {
        state.loreNav = "";
        editCharacter(nc.id);
      });
    },
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
    }).catch(function (e) { showToast("导出失败: " + e.message, "err"); });
}

function importLore(endpoint) {
  var input = document.createElement("input");
  input.type = "file"; input.accept = ".json,application/json";
  input.onchange = function () {
    var f = input.files[0]; if (!f) return;
    var reader = new FileReader();
    reader.onload = function () {
      var obj;
      try { obj = JSON.parse(reader.result); } catch (e) { showToast("JSON 解析失败", "err"); return; }
      fetchJSON("/api/" + endpoint + "/import", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(obj),
      }).then(function () { return refreshLore(); })
        .then(function () { state.loreNav = ""; renderLoreSections(); renderSessionMeta(); })
        .catch(function (e) { showToast("导入失败: " + e.message, "err"); });
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
  askConfirm({
    title: endpoint === "characters" ? "删除角色卡" : "删除世界观",
    text: msg, okLabel: "删除",
    onOk: function () {
      fetchJSON("/api/" + endpoint + "/" + id, { method: "DELETE" }).then(function () {
        return refreshLore();
      }).then(function () {
        renderLoreSections();
        renderSessionMeta();
      });
    },
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
function newVariant(rootId) {
  // 副本以主卡全部内容为初始值（id 置空、parent_id 指向根卡），
  // table/attributes 深拷贝，避免编辑时改到缓存中的主卡
  var root = characterOf(rootId);
  if (!root) return;
  var d = {};
  Object.keys(root).forEach(function (k) { d[k] = root[k]; });
  d.id = "";
  d.parent_id = rootId;
  d.alias = "";
  d.table = (root.table || []).map(function (e) {
    return { title: e.title, content: e.content };
  });
  d.attributes = (root.attributes || []).map(function (a) {
    var o = {}; Object.keys(a).forEach(function (k) { o[k] = a[k]; }); return o;
  });
  characterEditor(d, false, rootId);
}
function editCharacter(id) { characterEditor(state.characters.find(function (c) { return c.id === id; })); }

function characterEditor(c, scoped, parentId) {
  clearDirty();
  scoped = !!scoped;
  var id = c ? c.id : "";
  var parentForNew = parentId || "";
  el.loreBody.innerHTML = characterEditorHtml(c, scoped, parentForNew);
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
  }, { initial: c || null, scoped: scoped, parentId: parentForNew });
}
function characterEditorHtml(c, scoped, parentForNew) {
  var v = c || { name: "", avatar: "", intro: "", background: "", table: [] };
  var parentId = c ? (c.parent_id || "") : (parentForNew || "");
  // 预填的新建副本：c 存在但 id 为空——标题按 id 判定
  var title = scoped ? "编辑会话副本" : (v.id ? "编辑角色" : (parentId ? "新建副本" : "新建角色"));
  var canPublish = !scoped && state.role === "admin" && !!(c && isOwnCard(c));
  return (
    '<div class="dialog-actions"><button class="btn" id="ed-back">← 返回列表</button></div>' +
    "<h3>" + title + "</h3>" +
    (scoped ? '<p class="sub">此副本仅供当前会话使用，修改不影响角色库主卡。</p>' : "") +
    (parentId ? '<p class="sub">此卡是根角色卡的副本，根卡 id：' + escapeHtml(parentId) + "</p>" : "") +
    '<div style="display:flex;gap:16px;align-items:flex-start;margin:12px 0">' +
    '<div id="ed-avatar" class="avatar-placeholder" title="点击上传头像">' +
    (v.avatar ? '<img id="ed-avatar-img" src="' + v.avatar + '" alt="" />' : "<span>点击上传<br/>头像</span>") + "</div>" +
    '<div style="flex:1">' +
    '<label class="f-label">角色名称<input type="text" id="ed-name" value="' + escapeAttr(v.name) + '" /></label>' +
    (scoped ? "" : '<label class="f-label">别名（副本列表中显示，留空则用角色名称）<input type="text" id="ed-alias" value="' + escapeAttr(v.alias || "") + '" /></label>') +
    "</div></div>" +
    '<label class="f-label">角色简介<textarea id="ed-intro" rows="2">' + escapeHtml(v.intro) + "</textarea></label>" +
    '<label class="f-label">角色经历<textarea id="ed-bg" rows="4">' + escapeHtml(v.background) + "</textarea></label>" +
    "<div class='spacer'></div>" +
    "<h4>对话范例（few-shot，维持原创角色语气的关键）</h4>" +
    '<p class="sub">3~5 轮范例对白，轮与轮之间单独一行写 &lt;START&gt;；可用 {{char}} 代表本角色、{{user}} 代表用户。' +
    "范例剧情不会真实发生，仅供 AI 模仿语气、节奏与格式。</p>" +
    '<label class="f-label">对话范例 mes_example<textarea id="ed-mes-example" rows="7" placeholder="{{user}}：你今天怎么来了？&#10;&lt;START&gt;&#10;{{char}}：（挑眉）不然呢，等你请我？">'
    + escapeHtml(v.mes_example || "") + "</textarea></label>" +
    "<div class='spacer'></div>" +
    "<h4>系统级提示词</h4>" +
    '<label class="f-label">高优先级系统指令，直接约束模型行为（留空则不注入）<textarea id="ed-system" rows="3">' + escapeHtml(v.system_prompt || "") + "</textarea></label>" +
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
    '<div class="dialog-actions"><button class="btn" id="ed-cancel">取消</button>' +
    (canPublish ? '<button class="btn" id="ed-publish">' + (c.public ? "🌐 取消共享发布" : "🌐 发布共享") + "</button>" : "") +
    '<button class="btn primary" id="ed-save">保存角色</button></div>' +
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
  var v;
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
          .catch(function (e) { showToast("生成失败: " + e.message, "err"); })
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
      }).catch(function (e) { showToast("上传失败: " + e.message, "err"); });
    });
  }

  document.getElementById("ed-save").addEventListener("click", function () {
    var payload = { table: readEntries(tablesEl) };
    payload.prompt_format = document.getElementById("ed-format").value;
    payload.prompt_tools = document.getElementById("ed-tools").value;
    var sysEl = document.getElementById("ed-system");
    if (sysEl) payload.system_prompt = sysEl.value;
    if (kind === "character") {
      payload.name = document.getElementById("ed-name").value;
      payload.intro = document.getElementById("ed-intro").value;
      payload.background = document.getElementById("ed-bg").value;
      var mesEl = document.getElementById("ed-mes-example");
      if (mesEl) payload.mes_example = mesEl.value;
      payload.avatar = (avatarEl && avatarEl.dataset.avatar) || (v.avatar || "");
      if (attrsEl) payload.attributes = readAttrs(attrsEl);
      if (!opts.scoped) {
        var aliasEl = document.getElementById("ed-alias");
        payload.alias = aliasEl ? aliasEl.value : "";
        payload.parent_id = v.parent_id || opts.parentId || "";
      }
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
        return refreshLore().then(function () {
          // 保存后回到该卡所在层级（根卡→其副本列表；副本→所属根卡列表）
          if (res && res.id) state.loreNav = res.parent_id || res.id;
          renderLoreSections(); renderSessionMeta();
        });
      }
    }).catch(function (e) { msg.textContent = "保存失败: " + e.message; msg.className = "msg-bar err"; });
  });

  // 发布 / 取消发布共享（仅 admin 自己的卡）
  var pubBtn = document.getElementById("ed-publish");
  if (pubBtn) {
    pubBtn.addEventListener("click", function () {
      var unpublish = !!v.public;
      pubBtn.disabled = true;
      fetchJSON("/api/characters/" + id + "/" + (unpublish ? "unpublish" : "publish"), { method: "POST" })
        .then(function () { return refreshLore(); })
        .then(function () {
          state.loreNav = v.parent_id || v.id;
          renderLoreSections(); renderSessionMeta();
        })
        .catch(function (e) {
          var msg = document.getElementById("ed-msg");
          msg.textContent = "发布失败: " + e.message; msg.className = "msg-bar err";
          pubBtn.disabled = false;
        });
    });
  }
}

function entryRowHtml(title, content, pinned) {
  return (
    '<div class="entry-row">' +
    '<input type="text" class="ed-title" placeholder="标题" value="' + escapeAttr(title) + '" />' +
    '<textarea class="ed-content" rows="2" placeholder="内容">' + escapeHtml(content) + "</textarea>" +
    '<label class="ed-pin" title="常驻：无条件注入系统提示词，等同世界书 Constant"><input type="checkbox" class="ed-pinned"'
      + (pinned ? " checked" : "") + " />常驻</label>" +
    '<button class="btn small ed-del">✕</button></div>'
  );
}
function addEntryRow(container, entry) {
  var row = document.createElement("div");
  row.innerHTML = entryRowHtml(
    entry ? entry.title || "" : "",
    entry ? entry.content || "" : "",
    entry ? !!entry.pinned : false
  );
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
    var pinnedEl = row.querySelector(".ed-pinned");
    out.push({ title: title, content: content, pinned: pinnedEl ? pinnedEl.checked : false });
  });
  return out;
}

/* ==== 历史渲染（按「轮」组织：一条用户消息 + 它的回复组）==== */
function currentThread() {
  var list = state.roomThreads || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === state.currentThreadId) return list[i];
  }
  return null;
}
function currentThreadIsDM() {
  var t = currentThread();
  return !t || t.kind === "dm";  // 无窗口上下文时按 dm
}

function renderHistory(messages, edits) {
  clearMessages();
  var canEdit = currentThreadIsDM();
  // 搜索卡 → 后续精选结果的配对（按候选编号取原图 URL）
  var pixivReveal = buildPixivReveal(messages);
  var i = 0;
  // 开头游离消息（正常不会有）
  while (i < messages.length && messages[i].role !== "user") {
    renderFlatNonUser(messages[i], null);
    i++;
  }
  while (i < messages.length) {
    var user = messages[i];
    var anchor = user.id;
    var end = messages.length;
    for (var j = i + 1; j < messages.length; j++) {
      if (messages[j].role === "user") { end = j; break; }
    }
    var userEl = makeUserBubble(user.content || "", user.images, anchor);
    el.messages.appendChild(userEl);
    state.turnNodes[anchor] = { userEl: userEl, editing: false };

    var openToolRegion = null;
    for (var k = i + 1; k < end; k++) {
      var m = messages[k];
      // 群聊自评「无需发言」不展示；旧版 bug 曾误隐「带工具调用的空白中间消息」，
      // 那种消息必须渲染，否则工具结果（图片）无处挂载
      var hiddenToolMsg = m.role === "assistant" && m.tool_calls && m.tool_calls.length;
      if (m.hidden && !hiddenToolMsg) {
        if (m.role === "assistant") openToolRegion = null;
        continue;
      }
      if (m.role === "assistant") {
        openToolRegion = appendAssistantMsg(m, anchor).toolRegion;
      } else if (m.role === "tool") {
        if (openToolRegion) {
          appendToolResultCard(openToolRegion, {
            id: m.tool_call_id, name: m.name,
            ok: (m.ok !== undefined) ? m.ok : !String(m.content || "").startsWith("[工具错误]"),
            output: m.content,
            revealed: pixivReveal[m.tool_call_id] || null,
          });
        }
      }
    }
    if (canEdit) addTurnControls(anchor, (edits || {})[anchor]);
    i = end;
  }
  scrollToBottom();
}
// 渲染不属于任何轮的 assistant/tool 游离消息（兜底）
function renderFlatNonUser(m, anchor) {
  // appendAssistantMsg 内部已插入 DOM；tool 游离消息无挂载点，不展示
  if (m.role === "assistant") appendAssistantMsg(m, anchor);
}

// 一轮的编辑入口 / 分页器 / 重生成操作行（仅 dm）
function addTurnControls(anchor, entry) {
  var node = state.turnNodes[anchor];
  // ✏️：hover 用户气泡显现，打开统一内联编辑器
  var editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "turn-edit-btn";
  editBtn.title = "编辑这条消息";
  editBtn.textContent = "✏️";
  editBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    openUserEditor(anchor);
  });
  node.userEl.appendChild(editBtn);
  // 点击整条用户消息才亮出 ✏️（无 hover 的触屏入口）；点气泡外任意处收起
  node.userEl.addEventListener("click", function (e) {
    if (node.editing) return;
    if (e.target.closest &&
        e.target.closest(".turn-edit-btn, .chat-img, a, .inline-edit, .inline-edit-actions")) return;
    activateEditEntry(node.userEl);
  });

  if (entry) {
    // 编辑版本分页：放在用户气泡内末尾
    if (entry.edits.length > 1) {
      node.userEl.appendChild(
        makePager(entry.edit_active + 1, entry.edits.length,
          function () { switchTurn(anchor, entry.edit_active - 1, null); },
          function () { switchTurn(anchor, entry.edit_active + 1, null); })
      );
    }
    // 回复版本分页：放在该轮回复组之后
    var slot = entry.edits[entry.edit_active];
    if (slot.reply_variants.length > 1) {
      el.messages.appendChild(
        makePager(slot.reply_active + 1, slot.reply_variants.length,
          function () { switchTurn(anchor, null, slot.reply_active - 1); },
          function () { switchTurn(anchor, null, slot.reply_active + 1); })
      );
    }
  }

  // 🔄 回复组操作行：重新生成（打开同一编辑器，原文直接发送即重生成）
  var actions = document.createElement("div");
  actions.className = "turn-actions";
  var regen = document.createElement("button");
  regen.type = "button";
  regen.className = "btn small ghost";
  regen.textContent = "🔄 重新生成";
  regen.addEventListener("click", function () { openUserEditor(anchor); });
  actions.appendChild(regen);
  el.messages.appendChild(actions);
}

// 同时只允许一条用户消息亮出编辑键
function activateEditEntry(userEl) {
  var current = document.querySelector(".msg.user.edit-on");
  if (current && current !== userEl) current.classList.remove("edit-on");
  userEl.classList.add("edit-on");
}
function deactivateEditEntries() {
  var nodes = document.querySelectorAll(".msg.user.edit-on");
  Array.prototype.forEach.call(nodes, function (n) { n.classList.remove("edit-on"); });
}

function makePager(active, total, goPrev, goNext) {
  var pager = document.createElement("div");
  pager.className = "turn-pager";
  var prev = document.createElement("button");
  prev.type = "button";
  prev.className = "pager-btn";
  prev.textContent = "‹";
  prev.disabled = active <= 1;
  prev.addEventListener("click", goPrev);
  var label = document.createElement("span");
  label.className = "pager-label";
  label.textContent = active + " / " + total;
  var next = document.createElement("button");
  next.type = "button";
  next.className = "pager-btn";
  next.textContent = "›";
  next.disabled = active >= total;
  next.addEventListener("click", goNext);
  pager.appendChild(prev);
  pager.appendChild(label);
  pager.appendChild(next);
  return pager;
}

function makeUserBubble(text, images, anchor) {
  var wrap = document.createElement("div");
  wrap.className = "msg user";
  if (anchor) wrap.dataset.userId = anchor;
  // 用户扮演自设时，气泡上方标注扮演的角色头像与名字
  var p = personaChar();
  if (p) {
    var label = document.createElement("div");
    label.className = "us-label";
    label.innerHTML = makeAvatarHtml(p, true) + '<span class="us-name">' + escapeHtml(p.name || "你") + "</span>";
    wrap.appendChild(label);
  }
  var bubble = document.createElement("div");
  bubble.className = "bubble";
  if (text) {
    var textEl = document.createElement("div");
    textEl.className = "bubble-text";
    textEl.textContent = text;
    bubble.appendChild(textEl);
  }
  (images || []).forEach(function (img) {
    var im = document.createElement("img");
    im.className = "chat-img";
    im.src = imageDataUrl(img);
    bubble.appendChild(im);
  });
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
    a.head.classList.remove("hidden");
    var ch = member && member.character_id ? characterOf(member.character_id) : null;
    // 角色卡有别名时优先显示别名
    a.speakerName = ch ? cardDisplayName(ch) : (name || "AI");
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
  // 旧轮编辑/重生成：插在该轮与下一轮之间；普通发送：追加到底
  if (state.streamInsertBefore) {
    el.messages.insertBefore(a.container, state.streamInsertBefore);
  } else {
    el.messages.appendChild(a.container);
  }
  state.currentAssistant = a;
  if (roomMode() && !a.speakerName) a.head.classList.remove("hidden");
  scrollToBottom();
  return a;
}
function appendAssistantMsg(m, anchor) {
  var a = makeAssistantContainer();
  if (anchor) a.container.dataset.anchor = anchor;
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
  return a;
}

/* ==== 图片附件（视觉模型）==== */
var IMAGE_MAX_COUNT = 4;
var IMAGE_MAX_EDGE = 1568;   // 压缩后最长边
var IMAGE_QUALITY = 0.82;
var IMAGE_MAX_FILE = 8 * 1024 * 1024;

function imageDataUrl(img) {
  return "data:" + img.media_type + ";base64," + img.data;
}
function addImageFiles(fileList) {
  if (!visionEnabled()) {
    showToast("当前模型不支持图片输入，请在设置中为视觉模型勾选「视觉」");
    return;
  }
  var files = Array.prototype.filter.call(fileList || [], function (f) {
    return f.type.indexOf("image/") === 0;
  });
  if (!files.length) return;
  // 串行处理，处理完一张就刷新缩略图
  files.reduce(function (p, f) {
    return p.then(function () {
      if (state.pendingImages.length >= IMAGE_MAX_COUNT) {
        showToast("每条消息最多附带 " + IMAGE_MAX_COUNT + " 张图片");
        return;
      }
      return processImageFile(f).then(function (img) {
        state.pendingImages.push(img);
        renderPendingImages();
      }).catch(function (e) {
        showToast("图片处理失败（" + (f.name || "") + "）：" + e.message, "err");
      });
    });
  }, Promise.resolve());
}
function processImageFile(file) {
  if (file.size > IMAGE_MAX_FILE) return Promise.reject(new Error("文件超过 8MB"));
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onerror = function () { reject(new Error("读取失败")); };
    reader.onload = function () {
      var pic = new Image();
      pic.onerror = function () { reject(new Error("无法解析为图片")); };
      pic.onload = function () {
        var scale = Math.min(1, IMAGE_MAX_EDGE / Math.max(pic.width, pic.height));
        var cw = Math.max(1, Math.round(pic.width * scale));
        var ch = Math.max(1, Math.round(pic.height * scale));
        var canvas = document.createElement("canvas");
        canvas.width = cw;
        canvas.height = ch;
        var ctx2d = canvas.getContext("2d");
        ctx2d.fillStyle = "#ffffff";  // 透明 PNG 转 JPEG 先铺白底
        ctx2d.fillRect(0, 0, cw, ch);
        ctx2d.drawImage(pic, 0, 0, cw, ch);
        canvas.toBlob(function (blob) {
          if (!blob) { reject(new Error("压缩失败")); return; }
          var fr = new FileReader();
          fr.onload = function () {
            var comma = fr.result.indexOf(",");
            resolve({ media_type: "image/jpeg", data: fr.result.slice(comma + 1) });
          };
          fr.onerror = function () { reject(new Error("读取失败")); };
          fr.readAsDataURL(blob);
        }, "image/jpeg", IMAGE_QUALITY);
      };
      pic.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}
function renderPendingImages() {
  el.attachPreview.innerHTML = "";
  el.attachPreview.classList.toggle("hidden", !state.pendingImages.length);
  state.pendingImages.forEach(function (img, i) {
    var cell = document.createElement("div");
    cell.className = "attach-cell";
    var pic = document.createElement("img");
    pic.src = imageDataUrl(img);
    cell.appendChild(pic);
    var x = document.createElement("button");
    x.type = "button";
    x.className = "attach-x";
    x.title = "移除";
    x.textContent = "×";
    x.addEventListener("click", function () {
      state.pendingImages.splice(i, 1);
      renderPendingImages();
    });
    cell.appendChild(x);
    el.attachPreview.appendChild(cell);
  });
}

/* ==== 流式聊天 ==== */
function onSend() {
  if (state.busy) return;
  var text = el.input.value.trim();
  var images = state.pendingImages.slice();
  if (!text && !images.length) return;
  var doSend = function () {
    state.busy = true;
    el.btnSend.disabled = true;
    el.btnStop.classList.remove("hidden");
    el.input.disabled = true;
    el.messages.appendChild(makeUserBubble(text, images));
    el.input.value = "";
    clearDraft();
    autosizeInput();
    // 已发图片清空待发区
    state.pendingImages = [];
    renderPendingImages();
    // 失败恢复：暂存本次内容；成功结束后由 done 清除
    state.pendingSend = { text: text, images: images };
    // 发送即视为贴底跟随
    state.stickBottom = true;
    // 每个 AI 各自一条卡片：流式期间按 speaker 事件新建，不预建占位
    state.currentAssistant = null;
    state.currentSpeaker = null;
    scrollToBottom();
    var tid = state.currentThreadId ||
      ((state.roomThreads && state.roomThreads.length) ? state.roomThreads[0].id : undefined);
    var payload = {
      message: text,
      session_id: state.currentSessionId,
      thread_id: tid,
      provider_id: chosenProviderId(),
      tools_enabled: true,
      images: images,
      image_model_id: state.imageModelId || "",
      image_engine: state.sdEngine ? "sd" : "",
    };
    postChatSSE(payload);
  };
  if (!state.currentSessionId) {
    fetchJSON("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })
      .then(function (s) { state.currentSessionId = s.id; return refreshSessions(); })
      .then(doSend);
  } else { doSend(); }
}
function onStop() { if (state.abortController) state.abortController.abort(); }

// SSE 通道共用（普通发送 / 历史编辑 / 重新生成）
function postChatSSE(payload) {
  state.abortController = new AbortController();
  return fetch("/api/chat", {
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
    if (e.name === "AbortError") { finalizeTurn(); return; }
    // 网络层失败（请求未成功发出）：文字图片回填，不丢字
    restoreFailedSend("请求失败: " + e.message);
  });
}

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
  a.contentEl.classList.add("streaming-cursor");
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
// 从工具输出中提取图片标记 [图片已生成]/[图片] <url>
function extractGeneratedImage(text) {
  var m = /\[(?:图片已生成|图片)\]\s*(\S+)/.exec(text || "");
  return m ? m[1] : "";
}
// 从工具输出中提取 [图片参数] 单行 JSON（看图器按生图方式展示）
function extractImageMeta(text) {
  var m = /^\[图片参数\]\s*(\{.*\})\s*$/m.exec(text || "");
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch (e) { return null; }
}
// 图片标记输出：卡片只渲染图片，不显示任何文本
function isImageMarkerText(text) {
  return /^\[(?:图片已生成|图片)\]/.test(text || "");
}
function makeGeneratedImage(url, meta) {
  var img = document.createElement("img");
  img.className = "gen-img";
  img.src = url;
  img.alt = "AI 生成图片";
  img._imgMeta = meta || null;
  return img;
}

/* ==== P 站搜图：候选墙 + 毛玻璃 → 原图揭示 ==== */
function isPixivCandidatesText(text) {
  return /^\[P站候选\]/.test(text || "");
}
function isPixivPicksText(text) {
  return /^\[P站精选\]/.test(text || "");
}
// [候] <编号> <pid> <url>
function parsePixivCands(text) {
  var out = [];
  var re = /\[候\]\s+(\d+)\s+(\d+)\s+(\S+)/g, m;
  while ((m = re.exec(text || ""))) {
    out.push({ index: parseInt(m[1], 10), pid: m[2], url: m[3] });
  }
  return out;
}
// [选] <编号> <pid> <url>
function parsePixivPicks(text) {
  var out = [];
  var re = /\[选\]\s+(\d+)\s+(\d+)\s+(\S+)/g, m;
  while ((m = re.exec(text || ""))) {
    out.push({ index: parseInt(m[1], 10), pid: m[2], url: m[3] });
  }
  return out;
}
// 从全部消息中建立 搜索工具消息id -> {候选编号: 原图URL}
function buildPixivReveal(messages) {
  var map = {};
  var cur = null;
  (messages || []).forEach(function (m) {
    if (m.role !== "tool") return;
    if (m.name === "search_pixiv") {
      cur = m.tool_call_id;
      map[cur] = {};
    } else if (m.name === "pick_pixiv" && cur) {
      parsePixivPicks(m.content).forEach(function (p) {
        map[cur][p.index] = p.url;
      });
    }
  });
  return map;
}
function makePixivGrid(cands, revealed, live) {
  // live=true：未精选项渲染模糊缩略图；false：未精选项为无网络请求的毛玻璃占位
  var grid = document.createElement("div");
  grid.className = "pixiv-grid";
  cands.forEach(function (c) {
    var item = document.createElement("figure");
    item.className = "px-item";
    item.dataset.index = String(c.index);
    var frame = document.createElement("div");
    frame.className = "px-frame";
    var finalUrl = revealed ? revealed[c.index] : null;
    if (finalUrl) {
      frame.appendChild(makePxImg(finalUrl, false, c.pid));
      frame.classList.add("revealed");
      item.classList.add("picked");
    } else if (live) {
      frame.appendChild(makePxImg(c.url, true, c.pid));
    } else {
      var frost = document.createElement("div");
      frost.className = "px-img px-frost";
      frame.appendChild(frost);
    }
    var link = document.createElement("a");
    link.className = "px-link";
    link.href = "https://www.pixiv.net/artworks/" + c.pid;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "P站 ↗";
    frame.appendChild(link);
    item.appendChild(frame);
    grid.appendChild(item);
  });
  return grid;
}
function makePxImg(src, blur, pid) {
  var img = document.createElement("img");
  img.className = "px-img " + (blur ? "px-blur" : "px-revealed");
  img.src = src;
  img.alt = "Pixiv 插画 " + pid;
  img.addEventListener("click", function () {
    if (!img.classList.contains("px-blur")) window.open(img.src, "_blank");
  });
  return img;
}
// 精选原图到达：在最近的候选墙对应位置替换为原图，加载完成后去除模糊
function revealPixivPicks(picks) {
  var grids = el.messages.querySelectorAll(".pixiv-grid");
  var grid = grids.length ? grids[grids.length - 1] : null;
  if (!grid) return;
  picks.forEach(function (p) {
    var item = grid.querySelector('.px-item[data-index="' + p.index + '"]');
    if (!item || item.classList.contains("picked")) return;
    var frame = item.querySelector(".px-frame");
    var old = frame.querySelector(".px-img");
    var img = makePxImg(p.url, true, p.pid);  // 原图加载完成前保持模糊
    function clear() {
      img.classList.remove("px-blur");
      img.classList.add("px-revealed");
      frame.classList.add("revealed");
      item.classList.add("picked");
    }
    if (img.complete && img.naturalWidth) clear();
    else img.addEventListener("load", clear);
    frame.insertBefore(img, old);
    if (old) old.remove();
  });
}
function onToolEnd(data) {
  var card = findCard(data.id); if (!card) return;
  card.classList.add(data.ok ? "ok" : "err");
  card.querySelector(".tstatus").textContent = data.ok ? "✓ 完成" : "✗ 失败";
  if (isPixivCandidatesText(data.preview)) {
    // 候选缩略图：全部毛玻璃遮盖，等待 AI 筛选后取原图
    var cands = parsePixivCands(data.preview);
    if (cands.length) {
      card.querySelector(".tstatus").textContent =
        "✓ 找到 " + cands.length + " 张候选，AI 筛选中…";
      card.appendChild(makePixivGrid(cands, null, true));
    }
  } else if (isPixivPicksText(data.preview)) {
    // 精选原图到达：去掉对应候选位置的模糊展示原图（本卡仅留状态）
    revealPixivPicks(parsePixivPicks(data.preview));
  } else {
    var hasMarker = isImageMarkerText(data.preview);
    if (hasMarker) {
      // 图片标记输出：只渲染图片，指令句与路径均不展示
      var imgUrl0 = extractGeneratedImage(data.preview);
      if (imgUrl0) {
        card.appendChild(
          makeGeneratedImage(imgUrl0, extractImageMeta(data.preview)));
      }
    } else if (data.preview) {
      var pre = document.createElement("div");
      pre.className = "tpreview";
      pre.textContent = data.preview;
      card.appendChild(pre);
    }
  }
  scrollToBottom();  // 不强制：用户上翻时停留在原位
}
function appendToolResultCard(region, data) {
  var card = document.createElement("div");
  card.className = "tool-card " + (data.ok ? "ok" : "err");
  var tname = document.createElement("div"); tname.className = "tname"; tname.textContent = "🔧 " + (data.name || data.id || "tool");
  var tstatus = document.createElement("div"); tstatus.className = "tstatus"; tstatus.textContent = data.ok ? "✓ 完成" : "✗ 失败";
  card.appendChild(tname); card.appendChild(tstatus);
  var output = data.output ? String(data.output) : "";
  if (isPixivCandidatesText(output)) {
    // 候选墙：已精选位置展示原图，其余以毛玻璃占位（候选文件已清理，不再请求）
    var cands = parsePixivCands(output);
    if (cands.length) card.appendChild(makePixivGrid(cands, data.revealed || {}, false));
  } else if (isPixivPicksText(output)) {
    // 精选原图已并入搜索卡网格；本卡仅显示数量
    var n = parsePixivPicks(output).length;
    if (n) card.querySelector(".tstatus").textContent = "✓ 已精选 " + n + " 张";
  } else {
    var hasMarker = isImageMarkerText(output);
    if (hasMarker) {
      // 图片标记输出：只渲染图片，指令句与路径均不展示
      var imgUrl = extractGeneratedImage(output);
      if (imgUrl) {
        card.appendChild(makeGeneratedImage(imgUrl, extractImageMeta(output)));
      }
    } else if (output) {
      var pre = document.createElement("div");
      pre.className = "tpreview";
      pre.textContent = output.slice(0, 300);
      card.appendChild(pre);
    }
  }
  region.appendChild(card);
}
function findCard(id) {
  if (!state.currentAssistant) return null;
  return state.currentAssistant.toolRegion.querySelector('.tool-card[data-id="' + id + '"]');
}

/* ==== 全屏图片查看器：放大 / 下载 / 按生图方式查看提示词 ==== */
function openImageViewer(src, meta) {
  el.ivImg.src = src;
  el.ivPanel.innerHTML = "";
  el.ivPanel.classList.remove("open");
  el.ivInfo.classList.remove("active");
  var hasMeta = !!(meta && (meta.prompt || meta.negative_prompt ||
      meta.model || meta.sampler));
  el.ivInfo.classList.toggle("hidden", !hasMeta);
  if (hasMeta) el.ivPanel.appendChild(buildImageInfoPanel(meta));
  el.imgViewer.classList.remove("hidden");
}
function closeImageViewer() {
  el.imgViewer.classList.add("hidden");
  el.ivPanel.classList.remove("open");
  el.ivInfo.classList.remove("active");
  el.ivImg.src = "";
}
function toggleImageInfo() {
  var open = el.ivPanel.classList.toggle("open");
  el.ivInfo.classList.toggle("active", open);
}
function downloadViewerImage() {
  var src = el.ivImg.src;
  if (!src) return;
  var a = document.createElement("a");
  a.href = src;
  a.download = suggestImageName(src);
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function suggestImageName(src) {
  var dm = /^data:image\/([\w.+-]+)/i.exec(src);
  if (dm) {
    var ext = dm[1].toLowerCase().replace("jpeg", "jpg");
    return "image_" + viewerStamp() + "." + ext;
  }
  try {
    var base = String(new URL(src, location.href).pathname).split("/").pop();
    if (base && /\.[A-Za-z0-9]{2,5}$/.test(base)) return base;
  } catch (e) {}
  return "image.jpg";
}
function viewerStamp() {
  var d = new Date();
  function p(n) { return (n < 10 ? "0" : "") + n; }
  return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + "_" +
    p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
}

// 信息面板：SD 与云端两种生图方式结构不同
function buildImageInfoPanel(meta) {
  var box = document.createElement("div");
  box.className = "iv-info";
  if (meta.engine === "sd") {
    box.appendChild(makeInfoHeader("🖥", "Stable Diffusion",
      meta.engine_label || "Anything V5 · 文生图"));
    if (meta.prompt) box.appendChild(makeInfoBlock("正向提示词", meta.prompt, "iv-mono"));
    if (meta.negative_prompt) {
      box.appendChild(makeInfoBlock("负向提示词", meta.negative_prompt, "iv-mono"));
    }
    var seedText = (meta.seed === -1 || meta.seed === "-1") ? "随机" : meta.seed;
    box.appendChild(makeInfoGrid([
      ["尺寸", (meta.width || "?") + " × " + (meta.height || "?")],
      ["采样步数", meta.steps],
      ["CFG Scale", meta.cfg_scale],
      ["采样器", meta.sampler],
      ["调度器", meta.schedule],
      ["种子", seedText],
    ]));
  } else if (meta.engine === "cloud") {
    var isEdit = meta.mode === "img2img";
    box.appendChild(makeInfoHeader("☁️", isEdit ? "云端图生图" : "云端生图",
      meta.model ? "模型 " + meta.model : ""));
    var chips = [];
    if (meta.model) chips.push(meta.model);
    if (meta.size) chips.push(meta.size);
    if (chips.length) box.appendChild(makeInfoChips(chips));
    if (isEdit && meta.refs && meta.refs.length) {
      box.appendChild(makeInfoRefs(meta.refs));
    }
    if (meta.prompt) box.appendChild(makeInfoBlock("提示词", meta.prompt, null));
  } else {
    box.appendChild(makeInfoHeader("🖼", "图片信息", ""));
    if (meta.prompt) box.appendChild(makeInfoBlock("提示词", meta.prompt, null));
  }
  return box;
}
function makeInfoHeader(icon, title, sub) {
  var h = document.createElement("div");
  h.className = "iv-info-head";
  h.innerHTML =
    '<span class="iv-info-icon">' + icon + "</span><div>" +
    '<div class="iv-info-title">' + escapeHtml(title) + "</div>" +
    (sub ? '<div class="iv-info-sub">' + escapeHtml(sub) + "</div>" : "") +
    "</div>";
  return h;
}
function makeInfoRefs(refs) {
  var wrap = document.createElement("div");
  wrap.className = "iv-block";
  var bar = document.createElement("div");
  bar.className = "iv-block-bar";
  var lab = document.createElement("span");
  lab.className = "iv-block-label";
  lab.textContent = "参考图片（" + refs.length + "）· 点击放大";
  bar.appendChild(lab);
  var body = document.createElement("div");
  body.className = "iv-refs";
  refs.forEach(function (url, i) {
    var fig = document.createElement("button");
    fig.type = "button";
    fig.className = "iv-ref";
    var img = document.createElement("img");
    img.loading = "lazy";
    img.src = url;
    img.alt = "图" + (i + 1);
    var cap = document.createElement("span");
    cap.className = "iv-ref-cap";
    cap.textContent = "图" + (i + 1);
    fig.appendChild(img);
    fig.appendChild(cap);
    fig.addEventListener("click", function () { openImageViewer(url); });
    body.appendChild(fig);
  });
  wrap.appendChild(bar);
  wrap.appendChild(body);
  return wrap;
}
function makeInfoBlock(label, text, extraClass) {
  var wrap = document.createElement("div");
  wrap.className = "iv-block";
  var bar = document.createElement("div");
  bar.className = "iv-block-bar";
  var lab = document.createElement("span");
  lab.className = "iv-block-label";
  lab.textContent = label;
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "iv-copy";
  btn.textContent = "复制";
  btn.addEventListener("click", function () {
    copyText(text).then(function (ok) {
      showToast(ok ? "已复制到剪贴板" : "复制失败，请手动选择", ok ? "ok" : "err");
    });
  });
  bar.appendChild(lab);
  bar.appendChild(btn);
  var body = document.createElement("div");
  body.className = "iv-block-body" + (extraClass ? " " + extraClass : "");
  body.textContent = text;
  wrap.appendChild(bar);
  wrap.appendChild(body);
  return wrap;
}
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(
      function () { return true; },
      function () { return fallbackCopy(text); });
  }
  return Promise.resolve(fallbackCopy(text));
}
function fallbackCopy(text) {
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  var ok = false;
  try { ok = document.execCommand("copy"); } catch (e) {}
  ta.remove();
  return ok;
}
function makeInfoGrid(rows) {
  var grid = document.createElement("div");
  grid.className = "iv-param-grid";
  rows.forEach(function (r) {
    if (r[1] === undefined || r[1] === null || r[1] === "") return;
    var item = document.createElement("div");
    item.className = "iv-param";
    var k = document.createElement("span");
    k.className = "iv-param-k";
    k.textContent = r[0];
    var v = document.createElement("span");
    v.className = "iv-param-v";
    v.textContent = String(r[1]);
    item.appendChild(k);
    item.appendChild(v);
    grid.appendChild(item);
  });
  return grid;
}
function makeInfoChips(chips) {
  var wrap = document.createElement("div");
  wrap.className = "iv-chips";
  chips.forEach(function (c) {
    var s = document.createElement("span");
    s.className = "iv-chip";
    s.textContent = c;
    wrap.appendChild(s);
  });
  return wrap;
}
function onStreamError(data) {
  // fatal:false（如达到工具轮数上限）后还会跟一个 done 事件，不提前收尾
  showToast(data.message || "发生错误", "err", 5000);
  if (data.fatal !== false) finalizeTurn();
}
function onDone(data) {
  recordUsage(data && data.usage);
  finalizeTurn();
}
/* Token 用量：本轮 toast + 会话内累计（服务端持久，页面内实时叠加）*/
function recordUsage(usage) {
  if (!usage) return;
  var p = intOr(usage.prompt_tokens, 0), c = intOr(usage.completion_tokens, 0);
  if (p || c) showToast("本轮 token：输入 " + fmtNum(p) + " · 输出 " + fmtNum(c), "info", 4200);
  if (!state.currentSessionId) return;
  var sid = state.currentSessionId;
  state.tokenTotals[sid] = intOr(state.tokenTotals[sid], 0) + intOr(usage.total_tokens, p + c);
  var d = state.tokenDetails[sid] || { p: 0, c: 0 };
  state.tokenDetails[sid] = { p: intOr(d.p, 0) + p, c: intOr(d.c, 0) + c };
  renderSessionMeta();
}
function intOr(v, d) {
  v = parseInt(v, 10);
  return isNaN(v) ? d : v;
}
function fmtNum(n) {
  return String(intOr(n, 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
/* 紧凑数字：999 以下原样；千级 10.5k（整千去小数 12k）；百万级 7M / 1.2M */
function fmtCompact(n) {
  n = intOr(n, 0);
  if (n >= 1000000) return trim1(n / 1000000) + "M";
  if (n >= 1000) {
    var k = trim1(n / 1000);
    return k === "1000" ? "1M" : k + "k";
  }
  return String(n);
}
function trim1(x) {
  var s = x.toFixed(1);
  return s.slice(-2) === ".0" ? s.slice(0, -2) : s;
}
function finalizeTurn() {
  var a = state.currentAssistant;
  if (a) {
    a.thinkingEl.open = false;
    a.setThinking(false);
    a.contentEl.classList.remove("streaming-cursor");
    if (a.speakerName) a.setOpen(false);  // 群聊发言结束后折叠，点击头部展开
  }
  state.pendingSend = null;
  var editingAnchor = state.turnEditAnchor;
  state.currentAssistant = null;
  state.currentSpeaker = null;
  state.streamInsertBefore = null;
  state.turnEditAnchor = null;
  state.busy = false;
  el.btnSend.disabled = false;
  el.btnStop.classList.add("hidden");
  el.input.disabled = false;
  el.input.focus();
  refreshSessions();
  // 从磁盘重画：流式直插的气泡没有操作入口（✏️/🔄/分页器），编辑/重生成也需
  // 规范化 DOM。群聊窗口 canEdit=false 不挂控制
  if (state.currentThreadId) loadThread(state.currentThreadId);
}

/* ==== 历史消息内联编辑 / 重新生成（共用同一编辑器）==== */
function openUserEditor(anchor) {
  if (state.busy) return;
  var node = state.turnNodes[anchor];
  if (!node || node.editing) return;
  node.userEl.classList.remove("edit-on");
  var bubble = node.userEl.querySelector(".bubble");
  if (!bubble) return;
  var textEl = bubble.querySelector(".bubble-text");
  var currentText = textEl ? textEl.textContent : "";
  if (textEl) textEl.remove();

  node.editing = true;
  var ta = document.createElement("textarea");
  ta.className = "inline-edit";
  ta.rows = Math.max(2, Math.min(8, currentText.split("\n").length + 1));
  ta.value = currentText;
  var firstImg = bubble.querySelector(".chat-img");
  if (firstImg) bubble.insertBefore(ta, firstImg); else bubble.appendChild(ta);

  var actions = document.createElement("div");
  actions.className = "inline-edit-actions";
  var hint = document.createElement("span");
  hint.className = "inline-edit-hint";
  hint.textContent = "不改内容直接发送 = 重新生成";
  var sendBtn = document.createElement("button");
  sendBtn.type = "button"; sendBtn.className = "btn small"; sendBtn.textContent = "发送";
  var cancelBtn = document.createElement("button");
  cancelBtn.type = "button"; cancelBtn.className = "btn small ghost"; cancelBtn.textContent = "取消";
  actions.appendChild(hint); actions.appendChild(sendBtn); actions.appendChild(cancelBtn);
  bubble.appendChild(actions);
  ta.focus();

  cancelBtn.addEventListener("click", function () {
    ta.remove(); actions.remove();
    node.editing = false;
    if (textEl) {
      if (firstImg) bubble.insertBefore(textEl, firstImg); else bubble.appendChild(textEl);
    }
  });
  sendBtn.addEventListener("click", function () {
    var newText = ta.value;
    ta.remove(); actions.remove();
    node.editing = false;
    if (newText) {
      var nt = document.createElement("div");
      nt.className = "bubble-text";
      nt.textContent = newText;
      if (firstImg) bubble.insertBefore(nt, firstImg); else bubble.appendChild(nt);
    }
    submitTurnEdit(anchor, newText, currentText);
  });
}

function submitTurnEdit(anchor, newText, originalText) {
  if (state.busy) return;
  var changed = newText !== originalText;
  var node = state.turnNodes[anchor];
  // 移除该轮回复区全部内容（旧回复 / 分页器 / 操作行）；
  // 记录下一轮起点作为流式插入位置，最近一轮 afterTurn=null → 追加到底
  var afterTurn = null;
  var cur = node.userEl.nextSibling;
  while (cur) {
    if (cur.nodeType === 1 && cur.classList &&
        cur.classList.contains("msg") && cur.classList.contains("user")) {
      afterTurn = cur;
      break;
    }
    cur = cur.nextSibling;
  }
  cur = node.userEl.nextSibling;
  while (cur && cur !== afterTurn) {
    var nx = cur.nextSibling;
    cur.remove();
    cur = nx;
  }
  state.streamInsertBefore = afterTurn;
  state.turnEditAnchor = anchor;
  state.currentAssistant = null;
  state.busy = true;
  el.btnSend.disabled = true;
  el.btnStop.classList.remove("hidden");
  scrollToBottom();

  var payload = {
    message: newText,
    session_id: state.currentSessionId,
    thread_id: state.currentThreadId,
    provider_id: chosenProviderId(),
    tools_enabled: true,
    image_model_id: state.imageModelId || "",
    image_engine: state.sdEngine ? "sd" : "",
  };
  if (changed) payload.edit_message_id = anchor;
  else payload.regenerate_message_id = anchor;
  postChatSSE(payload);
}

function switchTurn(anchor, editIndex, replyIndex) {
  if (state.busy) return;
  var body = {};
  if (editIndex !== null && editIndex !== undefined) body.edit_index = editIndex;
  if (replyIndex !== null && replyIndex !== undefined) body.reply_index = replyIndex;
  fetchJSON(
    "/api/rooms/" + state.currentSessionId + "/threads/" + state.currentThreadId + "/turns/" + anchor,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
  ).then(function (t) {
    var idx = -1;
    state.roomThreads.forEach(function (x, i) { if (x.id === t.id) idx = i; });
    if (idx >= 0) state.roomThreads[idx] = t; else state.roomThreads.push(t);
    renderHistory(t.messages || [], t.edits || {});
  }).catch(function (e) { addErrorBar("切换版本失败: " + e.message); });
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
  var isAdmin = state.role === "admin";
  el.settingsAdmin.hidden = !isAdmin;
  el.settingsUser.hidden = isAdmin;
  el.settingsSub.textContent = isAdmin
    ? "提供商与工具配置（管理员）"
    : "管理你自建的模型，或选用管理员共享的模型（你的等级 Lv" + state.userLevel + "）";
  if (isAdmin) {
    el.configMsg.textContent = ""; el.configMsg.className = "msg-bar";
    fillAdminForm();
    renderProviderRows();
    renderImageModelRows();
    renderPluginChecks();
  } else {
    el.userConfigMsg.textContent = ""; el.userConfigMsg.className = "msg-bar";
    renderUserModelCards();
  }
  el.dialog.showModal();
}

function _fillNum(id, val) {
  var elx = document.getElementById(id);
  if (elx) elx.value = val != null && val !== "" ? val : 0;
}

function _readNum(id) {
  var elx = document.getElementById(id);
  var n = elx ? parseInt(elx.value, 10) : 0;
  return isNaN(n) || n < 0 ? 0 : n;
}

function fillAdminForm() {
  el.setMaxIters.value = state.config.tools ? state.config.tools.max_iterations : 8;
  el.setTimeout.value = state.config.tools ? state.config.tools.timeout_seconds : 60;
  el.setReadDirs.value = (state.config.tools.allowed_read_dirs || []).join("\n");
  var rp = state.config.roleplay || {};
  el.setRpFormat.checked = !!(rp.format_enabled);
  el.setAttrEnabled.checked = !!(rp.attributes && rp.attributes.enabled);
  el.setAttrAi.checked = !!(rp.attributes && rp.attributes.ai_provided);
  _fillNum("set-budget-intro", rp.intro_chars);
  _fillNum("set-budget-bg", rp.background_chars);
  _fillNum("set-budget-world", rp.world_chars);
  _fillNum("set-budget-mes", rp.mes_example_chars);
  _fillNum("set-budget-pinned", rp.pinned_chars);
  var emb = state.config.embeddings || {};
  el.setEmbBase.value = emb.base_url || "";
  el.setEmbKey.value = emb.api_key || "";
  el.setEmbModel.value = emb.model || "";
  el.setEmbTopk.value = emb.top_k || 4;
  _fillNum("set-emb-threshold", emb.score_threshold != null ? emb.score_threshold : 0.55);
  _fillNum("set-emb-keylimit", emb.keyword_limit || 8);
  var img = state.config.image_generation || {};
  el.setImgEnabled.checked = img.enabled !== false;
  el.setImgSize.value = img.default_size || "2K";
  var sd = state.config.stable_diffusion || {};
  el.setSdEnabled.checked = sd.enabled === true;
  el.setSdBase.value = sd.base_url || "http://127.0.0.1:7860";
  el.setSdFnindex.value = sd.fn_index != null ? sd.fn_index : 99;
  el.setSdWidth.value = sd.width || 512;
  el.setSdHeight.value = sd.height || 768;
  el.setSdSteps.value = sd.steps || 28;
  el.setSdCfg.value = sd.cfg_scale != null ? sd.cfg_scale : 7.0;
  el.setSdSampler.value = sd.sampler || "DPM++ 2M";
  el.setSdSchedule.value = sd.schedule || "Karras";
  el.setSdNegative.value = sd.negative_prompt || "";
  var px = state.config.pixiv || {};
  el.setPxEnabled.checked = px.enabled !== false;
  el.setPxProxy.value = px.proxy || "";
  el.setPxR18.checked = !!px.r18_allowed;
  el.setPxNum.value = px.candidate_num || 15;
  var ws = state.config.web_search || {};
  el.setWsEnabled.checked = ws.enabled !== false;
  el.setWsProxy.value = ws.proxy || "";
  el.setWsNum.value = ws.results_num || 8;
  el.setWsPagechars.value = ws.max_page_chars || 6000;
  var br = state.config.browser || {};
  el.setBrowserEnabled.checked = !!br.enabled;
  el.setBrowserHeadless.checked = br.headless !== false;
  el.setBrowserProxy.value = br.proxy || "";
  el.setBrowserSteps.value = br.max_steps || 15;
  el.setBrowserIdle.value = br.idle_seconds || 180;
}
function renderProviderRows() {
  el.providerList.innerHTML = "";
  (state.config.providers || []).forEach(function (p) {
    el.providerList.appendChild(providerRow(p));
  });
  var radios = document.querySelectorAll('input[name="active-provider"]');
  if (radios.length && Array.prototype.every.call(radios, function (r) { return !r.checked; })) {
    radios[0].checked = true;
  }
}
function providerRow(p) {
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
  row.appendChild(fieldRow("vision", "视觉（支持图片输入）", p.vision, "checkbox"));
  row.appendChild(fieldRow("owner", "归属（uid）", p.owner));
  row.appendChild(fieldRow("shared", "共享", p.shared, "checkbox"));
  row.appendChild(fieldRow("min_level", "共享最低等级", p.min_level, "number"));
  var del = document.createElement("button"); del.className = "btn small danger"; del.textContent = "删除";
  del.style.gridColumn = "span 2";
  del.addEventListener("click", function () { row.remove(); });
  row.appendChild(del);
  return row;
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
  } else if (kind === "checkbox") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!value;
  } else {
    input = document.createElement("input");
    input.type = kind || "text"; input.value = value || "";
  }
  input.dataset.key = key;
  if (kind === "checkbox") {
    // 复选框与说明横排：框在左、文字在右
    wrap.style.flexDirection = "row";
    wrap.style.alignItems = "center";
    span.textContent = " " + label;
    wrap.appendChild(input);
    wrap.appendChild(span);
  } else {
    wrap.appendChild(span);
    wrap.appendChild(input);
  }
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
  row.appendChild(fieldRow("vision", "视觉（支持图片输入）", false, "checkbox"));
  row.appendChild(fieldRow("owner", "归属（uid）", "admin"));
  row.appendChild(fieldRow("shared", "共享", false, "checkbox"));
  row.appendChild(fieldRow("min_level", "共享最低等级", 0, "number"));
  var del = document.createElement("button"); del.className = "btn small danger"; del.textContent = "删除";
  del.style.gridColumn = "span 2";
  del.addEventListener("click", function () { row.remove(); });
  row.appendChild(del);
  el.providerList.appendChild(row);
}

/* 管理员：生图模型行 */
function renderImageModelRows() {
  el.imageModelList.innerHTML = "";
  (state.config.image_models || []).forEach(function (m) {
    el.imageModelList.appendChild(imageModelRow(m));
  });
}
function imageModelRow(m) {
  var isNew = !m;
  m = m || { id: "", name: "", base_url: "", api_key: "", model: "",
             owner: "admin", shared: false, min_level: 0 };
  var row = document.createElement("div");
  row.className = "provider-row grid image-row";
  if (m.id) row.dataset.id = m.id;
  var label = document.createElement("div");
  label.className = "provider-row p-label";
  label.innerHTML = "<span style='" + (isNew ? "color:var(--accent)" : "") + "'>"
    + escapeHtml(m.name || m.id || "新生图模型") + "</span>";
  row.appendChild(label);
  row.appendChild(fieldRow("id", "ID", m.id));
  row.appendChild(fieldRow("name", "名称", m.name));
  row.appendChild(fieldRow("base_url", "Base URL", m.base_url));
  row.appendChild(fieldRow("api_key", "API Key", m.api_key, "password"));
  row.appendChild(fieldRow("model", "模型", m.model));
  row.appendChild(fieldRow("owner", "归属（uid）", m.owner));
  row.appendChild(fieldRow("shared", "共享", m.shared, "checkbox"));
  row.appendChild(fieldRow("min_level", "共享最低等级", m.min_level, "number"));
  var del = document.createElement("button"); del.className = "btn small danger"; del.textContent = "删除";
  del.style.gridColumn = "span 2";
  del.addEventListener("click", function () { row.remove(); });
  row.appendChild(del);
  return row;
}
function addImageModelRow() {
  el.imageModelList.appendChild(imageModelRow(null));
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
    var visionInput = row.querySelector('input[data-key="vision"]');
    var ownerInput = row.querySelector('input[data-key="owner"]');
    var sharedInput = row.querySelector('input[data-key="shared"]');
    var levelInput = row.querySelector('input[data-key="min_level"]');
    var pval = {
      id: idInput ? idInput.value.trim() : "",
      type: typeSel ? typeSel.value : "openai",
      base_url: urlInput ? urlInput.value.trim() : "",
      api_key: keyInput ? keyInput.value : "",
      model: modelInput ? modelInput.value.trim() : "",
      vision: visionInput ? !!visionInput.checked : false,
      owner: ownerInput && ownerInput.value.trim() ? ownerInput.value.trim() : "admin",
      shared: sharedInput ? !!sharedInput.checked : false,
      min_level: levelInput ? (parseInt(levelInput.value, 10) || 0) : 0,
    };
    if (pval.id) providers.push(pval);
  });
  var active = radioChecked ? radioChecked.value : (providers[0] ? providers[0].id : "");

  // 生图模型：完全空白行跳过；填了内容但无 ID 则自动生成
  var imageModels = [];
  Array.prototype.forEach.call(el.imageModelList.querySelectorAll(".image-row"), function (row) {
    var val = {
      id: row.querySelector('input[data-key="id"]').value.trim(),
      name: row.querySelector('input[data-key="name"]').value.trim(),
      base_url: row.querySelector('input[data-key="base_url"]').value.trim(),
      api_key: row.querySelector('input[data-key="api_key"]').value,
      model: row.querySelector('input[data-key="model"]').value.trim(),
      owner: row.querySelector('input[data-key="owner"]').value.trim() || "admin",
      shared: row.querySelector('input[data-key="shared"]').checked,
      min_level: parseInt(row.querySelector('input[data-key="min_level"]').value, 10) || 0,
    };
    if (!val.id && !val.name && !val.base_url && !val.model) return;
    if (!val.id) {
      val.id = "img" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    }
    imageModels.push(val);
  });
  var readDirs = el.setReadDirs.value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
  var tools = { enabled: true, max_iterations: parseInt(el.setMaxIters.value, 10) || 8, timeout_seconds: parseInt(el.setTimeout.value, 10) || 60, allowed_read_dirs: readDirs };
  var roleplay = {
    format_enabled: !!el.setRpFormat.checked,
    attributes: { enabled: !!el.setAttrEnabled.checked, ai_provided: !!el.setAttrAi.checked },
    intro_chars: _readNum("set-budget-intro"),
    background_chars: _readNum("set-budget-bg"),
    world_chars: _readNum("set-budget-world"),
    mes_example_chars: _readNum("set-budget-mes"),
    pinned_chars: _readNum("set-budget-pinned"),
  };
  var curEmb = state.config.embeddings || {};
  var thresholdEl = document.getElementById("set-emb-threshold");
  var threshold = parseFloat(thresholdEl.value);
  if (isNaN(threshold)) threshold = 0.55;
  threshold = Math.min(1, Math.max(0, threshold));
  var embeddings = {
    base_url: el.setEmbBase.value.trim(),
    api_key: el.setEmbKey.value.trim(),
    model: el.setEmbModel.value.trim(),
    top_k: parseInt(el.setEmbTopk.value, 10) || 4,
    score_threshold: threshold,
    keyword_limit: _readNum("set-emb-keylimit") || 8,
    cache_file: curEmb.cache_file || "",
  };
  var imageGeneration = {
    enabled: !!el.setImgEnabled.checked,
    default_size: el.setImgSize.value || "2K",
  };
  var stableDiffusion = {
    enabled: !!el.setSdEnabled.checked,
    base_url: el.setSdBase.value.trim() || "http://127.0.0.1:7860",
    fn_index: parseInt(el.setSdFnindex.value, 10) || 99,
    width: parseInt(el.setSdWidth.value, 10) || 512,
    height: parseInt(el.setSdHeight.value, 10) || 768,
    steps: parseInt(el.setSdSteps.value, 10) || 28,
    cfg_scale: parseFloat(el.setSdCfg.value) || 7.0,
    sampler: el.setSdSampler.value.trim() || "DPM++ 2M",
    schedule: el.setSdSchedule.value.trim() || "Karras",
    negative_prompt: el.setSdNegative.value,
  };
  var pixiv = {
    enabled: !!el.setPxEnabled.checked,
    proxy: el.setPxProxy.value.trim(),
    r18_allowed: !!el.setPxR18.checked,
    candidate_num: parseInt(el.setPxNum.value, 10) || 15,
  };
  var webSearch = {
    enabled: !!el.setWsEnabled.checked,
    proxy: el.setWsProxy.value.trim(),
    results_num: parseInt(el.setWsNum.value, 10) || 8,
    max_page_chars: parseInt(el.setWsPagechars.value, 10) || 6000,
  };
  // 合并已有 browser 配置：UI 只暴露部分字段，保留 executable_path 等
  var browser = Object.assign({}, state.config.browser || {}, {
    enabled: !!el.setBrowserEnabled.checked,
    headless: !!el.setBrowserHeadless.checked,
    proxy: el.setBrowserProxy.value.trim(),
    max_steps: parseInt(el.setBrowserSteps.value, 10) || 15,
    idle_seconds: parseInt(el.setBrowserIdle.value, 10) || 180,
  });
  fetchJSON("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      active_provider: active, providers: providers, tools: tools, roleplay: roleplay,
      embeddings: embeddings, image_generation: imageGeneration,
      image_models: imageModels, stable_diffusion: stableDiffusion,
      pixiv: pixiv, web_search: webSearch, browser: browser,
    }),
  }).then(function (cfg) {
    state.config = cfg;
    el.configMsg.textContent = "已保存"; el.configMsg.className = "msg-bar ok";
    el.dialog.close();
    renderProviderBar();
    return fetchJSON("/api/plugins");
  }).then(function (r) { state.plugins = r.plugins || []; })
    .catch(function (e) { el.configMsg.textContent = "保存失败: " + e.message; el.configMsg.className = "msg-bar err"; });
}

/* ==== 普通用户：我的模型 / 共享模型卡片 ==== */
var PROVIDER_FIELDS = [
  { key: "type", label: "类型", kind: "select", options: ["openai", "anthropic"] },
  { key: "base_url", label: "Base URL" },
  { key: "api_key", label: "API Key", kind: "password" },
  { key: "model", label: "模型" },
  { key: "vision", label: "视觉（图片输入）", kind: "checkbox" },
];
var IMAGE_FIELDS = [
  { key: "name", label: "名称" },
  { key: "base_url", label: "Base URL" },
  { key: "api_key", label: "API Key", kind: "password" },
  { key: "model", label: "模型" },
];

function renderUserModelCards() {
  el.myProviderList.innerHTML = "";
  el.sharedProviderList.innerHTML = "";
  el.myImageList.innerHTML = "";
  el.sharedImageList.innerHTML = "";
  var providers = (state.config && state.config.providers) || [];
  providers.forEach(function (p) {
    var mine = p.owner === state.userId;
    var card = buildModelCard("provider", p, { mine: mine });
    (mine ? el.myProviderList : el.sharedProviderList).appendChild(card);
  });
  var images = (state.config && state.config.image_models) || [];
  images.forEach(function (m) {
    var mine = m.owner === state.userId;
    var card = buildModelCard("image", m, { mine: mine });
    (mine ? el.myImageList : el.sharedImageList).appendChild(card);
  });
}

function buildModelCard(kind, data, opts) {
  data = data || {};
  var fields = kind === "provider" ? PROVIDER_FIELDS : IMAGE_FIELDS;
  var card = document.createElement("div");
  card.className = "model-card" + (opts.mine ? "" : " shared");
  if (data.id) card.dataset.id = data.id;

  // 头部：选用 radio + 标题 +（共享）等级徽标
  var head = document.createElement("div");
  head.className = "model-card-head";
  var radio = document.createElement("input");
  radio.type = "radio";
  radio.name = kind === "provider" ? "pick-provider" : "pick-image";
  radio.value = data.id || "";
  var picked = kind === "provider"
    ? chosenProviderId()
    : (state.imageModelId || "");
  radio.checked = !!data.id && data.id === picked;
  radio.addEventListener("change", function () {
    if (kind === "provider") {
      localStorage.setItem("flyai-provider", data.id);
    } else {
      state.imageModelId = data.id;
      localStorage.setItem("flyai-image-model", data.id);
    }
  });
  head.appendChild(radio);
  var title = document.createElement("strong");
  if (kind === "provider") {
    title.textContent = data.model ? (data.model + "（" + data.id + "）") : "新模型";
  } else {
    title.textContent = data.name || data.model || "新生图模型";
  }
  head.appendChild(title);
  if (!opts.mine) {
    var badge = document.createElement("span");
    badge.className = "badge-level";
    badge.textContent = "需要等级 ≥ " + (data.min_level || 0);
    head.appendChild(badge);
  }
  card.appendChild(head);

  // 字段
  var grid = document.createElement("div");
  grid.className = "model-card-fields";
  fields.forEach(function (f) {
    grid.appendChild(cardField(f, data[f.key], !opts.mine));
  });
  card.appendChild(grid);

  // 操作按钮（仅归属自己）
  if (opts.mine) {
    var actions = document.createElement("div");
    actions.className = "model-card-actions";
    var save = document.createElement("button");
    save.className = "btn small primary";
    save.textContent = "保存";
    save.addEventListener("click", function () { saveMyCard(kind, card); });
    actions.appendChild(save);
    if (data.id) {
      var del = document.createElement("button");
      del.className = "btn small danger";
      del.textContent = "删除";
      del.addEventListener("click", function () { deleteMyCard(kind, card); });
      actions.appendChild(del);
    }
    card.appendChild(actions);
  }
  return card;
}

function cardField(f, value, disabled) {
  var label = document.createElement("label");
  label.className = "model-field";
  var span = document.createElement("span");
  span.textContent = f.label;
  var input;
  if (f.kind === "select") {
    input = document.createElement("select");
    f.options.forEach(function (o) {
      var opt = document.createElement("option");
      opt.value = o;
      opt.textContent = o;
      if (o === value) opt.selected = true;
      input.appendChild(opt);
    });
  } else {
    input = document.createElement("input");
    input.type = f.kind || "text";
    input.value = value == null ? "" : value;
  }
  input.dataset.k = f.key;
  if (disabled) input.disabled = true;
  label.appendChild(span);
  label.appendChild(input);
  return label;
}

function addMyProvider() {
  var card = buildModelCard("provider", null, { mine: true });
  card.dataset.new = "1";
  var radio = card.querySelector("input[type=radio]");
  if (radio) radio.disabled = true;
  el.myProviderList.appendChild(card);
}

function addMyImage() {
  var card = buildModelCard("image", null, { mine: true });
  card.dataset.new = "1";
  var radio = card.querySelector("input[type=radio]");
  if (radio) radio.disabled = true;
  el.myImageList.appendChild(card);
}

function saveMyCard(kind, card) {
  var fields = kind === "provider" ? PROVIDER_FIELDS : IMAGE_FIELDS;
  var values = {};
  fields.forEach(function (f) {
    var input = card.querySelector('[data-k="' + f.key + '"]');
    values[f.key] = f.kind === "checkbox" ? input.checked : input.value;
  });
  var isNew = card.dataset.new === "1";
  var base = kind === "provider" ? "/api/models/providers" : "/api/models/images";
  var url = isNew ? base : base + "/" + encodeURIComponent(card.dataset.id);
  userCardMsg("保存中…");
  fetchJSON(url, {
    method: isNew ? "POST" : "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  }).then(function (saved) {
    // 新建的模型自动选用，避免存完仍是别人的模型
    if (isNew && saved && saved.id) {
      if (kind === "provider") {
        localStorage.setItem("flyai-provider", saved.id);
      } else {
        state.imageModelId = saved.id;
        localStorage.setItem("flyai-image-model", saved.id);
      }
    }
    return refreshConfig();
  }).then(function () {
    renderUserModelCards();
    userCardMsg("已保存", "ok");
    renderProviderBar();
  }).catch(function (e) {
    userCardMsg("保存失败: " + e.message, "err");
  });
}

function deleteMyCard(kind, card) {
  var title = card.dataset.id;
  if (!confirm("确定删除模型「" + title + "」？")) return;
  var base = kind === "provider" ? "/api/models/providers" : "/api/models/images";
  fetchJSON(base + "/" + encodeURIComponent(title), { method: "DELETE" })
    .then(refreshConfig)
    .then(function () { renderUserModelCards(); })
    .catch(function (e) { userCardMsg("删除失败: " + e.message, "err"); });
}

function refreshConfig() {
  return fetchJSON("/api/config").then(function (cfg) {
    state.config = cfg;
    updateSdEngineButton();
  });
}

/* ==== Stable Diffusion 引擎开关 ==== */
function sdAvailable() {
  var sd = state.config && state.config.stable_diffusion;
  return !!(sd && sd.enabled === true);
}
function toggleSdEngine() {
  if (!sdAvailable()) return;
  state.sdEngine = !state.sdEngine;
  localStorage.setItem("flyai-sd-engine", state.sdEngine ? "sd" : "off");
  updateSdEngineButton();
  showToast(state.sdEngine
    ? "已切换为本地 Stable Diffusion 生图（Anything V5）"
    : "已切换为云端生图模型");
}
function updateSdEngineButton() {
  if (!el.btnSd) return;
  var available = sdAvailable();
  el.btnSd.classList.toggle("hidden", !available);
  el.btnSd.classList.toggle("active", available && state.sdEngine);
  // 管理员关闭 SD 后，不能再保留选中状态
  if (!available && state.sdEngine) {
    state.sdEngine = false;
    localStorage.setItem("flyai-sd-engine", "off");
  }
}

function userCardMsg(text, cls) {
  el.userConfigMsg.textContent = text;
  el.userConfigMsg.className = "msg-bar " + (cls || "");
}

/* ==== Toast 通知（替代原生 alert，不阻断操作）==== */
function showToast(msg, type, holdMs) {
  var box = document.getElementById("toast-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast-box";
    document.body.appendChild(box);
  }
  var t = document.createElement("div");
  t.className = "toast " + (type || "info");
  t.textContent = msg;
  box.appendChild(t);
  requestAnimationFrame(function () { t.classList.add("show"); });
  setTimeout(function () {
    t.classList.remove("show");
    setTimeout(function () { t.remove(); }, 300);
  }, holdMs || 3200);
}

/* ==== 输入草稿（按会话+窗口持久化，刷新/切换不丢字）==== */
function draftKey() {
  return "flyai-draft-" + (state.currentSessionId || "none") + "-" + (state.currentThreadId || "none");
}
function saveDraftNow() {
  var v = el.input.value;
  if (v) localStorage.setItem(draftKey(), v);
  else localStorage.removeItem(draftKey());
}
function scheduleDraftSave() {
  clearTimeout(state.draftTimer);
  state.draftTimer = setTimeout(saveDraftNow, 400);
}
function restoreDraft() {
  el.input.value = localStorage.getItem(draftKey()) || "";
  autosizeInput();
}
function clearDraft() { localStorage.removeItem(draftKey()); }

/* ==== 发送失败：文字与图片回填 ==== */
function restoreFailedSend(reason) {
  var ps = state.pendingSend;
  if (ps) {
    el.input.value = ps.text;
    state.pendingImages = ps.images.slice();
    renderPendingImages();
    saveDraftNow();
    autosizeInput();
  }
  showToast(reason, "err", 5000);
  finalizeTurn();
  el.input.focus();
}

/* ==== 输入框高度自适应 ==== */
function autosizeInput() {
  el.input.style.height = "auto";
  var h = el.input.scrollHeight;
  var maxH = 200;
  el.input.style.height = Math.min(h, maxH) + "px";
  el.input.style.overflowY = h > maxH ? "auto" : "hidden";
}

/* ==== 工具函数 ==== */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function escapeAttr(s) { return escapeHtml(s); }
function onMessagesScroll() {
  var m = el.messages;
  state.stickBottom = (m.scrollHeight - m.scrollTop - m.clientHeight) < 80;
  updateJumpBtn();
}
function scrollToBottom(force) {
  var m = el.messages;
  // 用户主动上翻（stick=false）时不跟随，只在贴底或强制时滚动
  if (force || state.stickBottom) m.scrollTop = m.scrollHeight;
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