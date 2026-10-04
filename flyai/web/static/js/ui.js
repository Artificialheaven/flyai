/* FlyAI 前端 · 界面骨架：元素缓存/事件绑定/弹窗/移动端抽屉/主题/Toast/草稿（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

function safeCloseDialogs() {
  [el.dialog, el.loreDialog, el.userDialog].forEach(function (d) {
    if (d && d.open) d.close();
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
  el.btnMore = document.getElementById("btn-more");
  el.moreMenu = document.getElementById("more-menu");
  el.input = document.getElementById("input");
  el.mentionBox = document.getElementById("mention-box");
  el.btnSend = document.getElementById("btn-send");
  el.btnStop = document.getElementById("btn-stop");
  el.btnAttach = document.getElementById("btn-attach");
  el.imageFile = document.getElementById("image-file");
  el.attachPreview = document.getElementById("attach-preview");
  el.btnNew = document.getElementById("btn-new-session");
  el.btnImportSession = document.getElementById("btn-import-session");
  el.sessionImportFile = document.getElementById("session-import-file");
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
  el.setWsEngine = document.getElementById("set-ws-engine");
  el.setWsSource = document.getElementById("set-ws-source");
  el.setWsNum = document.getElementById("set-ws-num");
  el.setWsPagechars = document.getElementById("set-ws-pagechars");
  el.setWsIters = document.getElementById("set-ws-iters");
  el.setBrowserEnabled = document.getElementById("set-browser-enabled");
  el.setBrowserHeadless = document.getElementById("set-browser-headless");
  el.setBrowserProxy = document.getElementById("set-browser-proxy");
  el.setBrowserSteps = document.getElementById("set-browser-steps");
  el.setBrowserIdle = document.getElementById("set-browser-idle");
  // 语音合成设置
  el.setTtsEnabled = document.getElementById("set-tts-enabled");
  el.setTtsKey = document.getElementById("set-tts-key");
  el.setTtsResource = document.getElementById("set-tts-resource");
  el.setTtsVoiceList = document.getElementById("set-tts-voice-list");
  el.setTtsAddVoice = document.getElementById("set-tts-add-voice");
  el.setTtsSpeaker = document.getElementById("set-tts-speaker");
  el.setTtsMinlevel = document.getElementById("set-tts-minlevel");
  el.setTtsFormat = document.getElementById("set-tts-format");
  el.setTtsSamplerate = document.getElementById("set-tts-samplerate");
  el.setTtsSpeechrate = document.getElementById("set-tts-speechrate");
  el.setTtsLoudness = document.getElementById("set-tts-loudness");
  el.setTtsProxy = document.getElementById("set-tts-proxy");
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
  if (el.btnMore) el.btnMore.addEventListener("click", function (e) {
    e.stopPropagation();
    toggleMoreMenu();
  });
  // 点菜单以外的区域：收起「更多」菜单
  document.addEventListener("click", function (e) {
    if (!el.moreMenu || !el.moreMenu.classList || el.moreMenu.classList.contains("hidden")) return;
    if (!e.target.closest || (!e.target.closest("#more-menu") && !e.target.closest("#btn-more"))) {
      closeMoreMenu();
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && el.moreMenu && !el.moreMenu.classList.contains("hidden")) {
      closeMoreMenu();
    }
  });
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
  el.btnImportSession.addEventListener("click", function () {
    el.sessionImportFile.value = "";  // 同一文件选两次也要重新触发 change
    el.sessionImportFile.click();
  });
  el.sessionImportFile.addEventListener("change", importSessionFromFile);
  el.btnSettings.addEventListener("click", openSettings);
  el.btnLore.addEventListener("click", openLoreDialog);
  el.btnBatchDelete.addEventListener("click", openBatchMode);
  el.btnTheme.addEventListener("click", toggleTheme);
  el.btnAddProvider.addEventListener("click", addProviderRow);
  if (el.setTtsAddVoice) el.setTtsAddVoice.addEventListener("click", function () {
    el.setTtsVoiceList.appendChild(ttsVoiceRow("", ""));
  });
  if (el.btnAddImageModel) el.btnAddImageModel.addEventListener("click", addImageModelRow);
  if (el.btnSd) el.btnSd.addEventListener("click", toggleSdEngine);
  if (el.btnAddMyProvider) el.btnAddMyProvider.addEventListener("click", addMyProvider);
  if (el.btnAddMyImage) el.btnAddMyImage.addEventListener("click", addMyImage);
  if (el.btnUserSettingsClose) el.btnUserSettingsClose.addEventListener("click", function () {
    el.dialog.close();
    renderProviderBar();
  });
  el.btnConfigSave.addEventListener("click", saveConfig);
  el.btnConfigCancel.addEventListener("click", function () { if (guardDiscard()) { clearDirty(); el.dialog.close(); } });
  el.input.addEventListener("keydown", function (e) {
    // @ 候选浮层打开时：方向键/回车/Tab/Esc 先交给候选处理，回车不发送
    if (onMentionKeydown(e)) return;
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) { e.preventDefault(); onSend(); }
    if (e.key === "Escape") { el.input.blur(); }
  });
  el.input.addEventListener("input", function () { scheduleDraftSave(); autosizeInput(); updateMentionBox(); });
  el.input.addEventListener("blur", closeMentionBox);
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
  // 窗口重命名弹窗
  var dlgRename = document.getElementById("thread-rename-dialog");
  var renameInput = document.getElementById("thread-rename-input");
  if (dlgRename) {
    document.getElementById("btn-thread-rename-cancel").addEventListener("click", function () { dlgRename.close(); });
    document.getElementById("btn-thread-rename-ok").addEventListener("click", confirmThreadRename);
    renameInput.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); confirmThreadRename(); }
    });
  }
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

