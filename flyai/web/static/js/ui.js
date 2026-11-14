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
  el.btnMarket = document.getElementById("btn-market");
  el.btnBatchDelete = document.getElementById("btn-batch-delete");
  el.btnTheme = document.getElementById("btn-theme");
  el.providerBar = document.getElementById("provider-bar");
  el.dialog = document.getElementById("settings-dialog");
  el.providerList = document.getElementById("provider-list");
  el.btnAddProvider = document.getElementById("btn-add-provider");
  el.setMaxIters = document.getElementById("set-max-iters");
  el.setTimeout = document.getElementById("set-timeout");
  el.setReadDirs = document.getElementById("set-read-dirs");
  el.setKeepTurns = document.getElementById("set-keep-turns");
  el.setAutoRecall = document.getElementById("set-auto-recall");
  el.setAutoRecallMax = document.getElementById("set-auto-recall-max");
  el.setExtractEvery = document.getElementById("set-extract-every");
  el.setLoreAutoInject = document.getElementById("set-lore-auto-inject");
  el.setMaxTokens = document.getElementById("set-max-tokens");
  el.btnOpenMemoryManager = document.getElementById("btn-open-memory-manager");
  el.pluginChecks = document.getElementById("plugin-checks");
  el.setEmbBase = document.getElementById("set-emb-base");
  el.setEmbKey = document.getElementById("set-emb-key");
  el.setEmbModel = document.getElementById("set-emb-model");
  el.setEmbTopk = document.getElementById("set-emb-topk");
  el.setImgEnabled = document.getElementById("set-img-enabled");
  el.setImgSize = document.getElementById("set-img-size");
  el.imageModelList = document.getElementById("image-model-list");
  el.btnAddImageModel = document.getElementById("btn-add-image-model");
  // 深度思考胶囊开关
  el.btnDeepThink = document.getElementById("btn-deep-think");
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
  el.setEnterSend = document.getElementById("set-enter-send");
  el.settingsUser = document.getElementById("settings-user");
  // 聊天背景（个性化页）
  el.bgLayer = document.getElementById("chat-bg-layer");
  el.bgFile = document.getElementById("bg-file");
  el.btnBgPick = document.getElementById("btn-bg-pick");
  el.btnBgRemove = document.getElementById("btn-bg-remove");
  el.bgPreview = document.getElementById("bg-preview");
  el.bgOpacity = document.getElementById("bg-opacity");
  el.bgOpacityVal = document.getElementById("bg-opacity-val");
  // 设置多级菜单（手机端）
  el.btnSettingsExit = document.getElementById("btn-settings-exit");
  el.mValAppearance = document.getElementById("m-val-appearance");
  el.mValFontsize = document.getElementById("m-val-fontsize");
  el.mRowLogout = document.getElementById("m-row-logout");
  el.accInfoName = document.getElementById("acc-info-name");
  el.accInfoLevel = document.getElementById("acc-info-level");
  el.btnAccountUsers = document.getElementById("btn-account-users");
  el.btnDataImport = document.getElementById("btn-data-import");
  el.btnDataBatch = document.getElementById("btn-data-batch");
  el.userVoiceView = document.getElementById("user-voice-view");
  // 顶栏手机端按钮 / 输入区 +
  el.btnMute = document.getElementById("btn-mute");
  el.btnNewChatTop = document.getElementById("btn-new-chat-top");
  el.btnPlus = document.getElementById("btn-plus");
  el.myProviderList = document.getElementById("my-provider-list");
  el.sharedProviderList = document.getElementById("shared-provider-list");
  el.myImageList = document.getElementById("my-image-list");
  el.sharedImageList = document.getElementById("shared-image-list");
  el.btnAddMyProvider = document.getElementById("btn-add-my-provider");
  el.btnAddMyImage = document.getElementById("btn-add-my-image");
  el.btnUserSettingsClose = document.getElementById("btn-user-settings-close");
  el.userConfigMsg = document.getElementById("user-config-msg");
  el.userFeaturesList = document.getElementById("user-features-list");
  el.btnSaveFeatures = document.getElementById("btn-save-features");
  el.userFeatMsg = document.getElementById("user-feat-msg");
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
  el.setGalgameLoops = document.getElementById("set-galgame-loops");
  el.setGalgameEnabled = document.getElementById("set-galgame-enabled");
  // 沙箱与调度设置
  el.setSandboxEnabled = document.getElementById("set-sandbox-enabled");
  el.setSandboxMaxFileKb = document.getElementById("set-sandbox-max-file-kb");
  el.setSandboxQuotaMb = document.getElementById("set-sandbox-quota-mb");
  el.setSandboxPublicMaxFileKb = document.getElementById("set-sandbox-public-max-file-kb");
  el.setSandboxPublicQuotaMb = document.getElementById("set-sandbox-public-quota-mb");
  el.setSchedulerEnabled = document.getElementById("set-scheduler-enabled");
  el.setSchedulerTimezone = document.getElementById("set-scheduler-timezone");
  el.setSchedulerTick = document.getElementById("set-scheduler-tick");
  el.setSchedulerReminder = document.getElementById("set-scheduler-reminder");
  el.setCustomToolsEnabled = document.getElementById("set-custom-tools-enabled");
  el.setCustomCodeMax = document.getElementById("set-custom-code-max");
  el.setCustomMaxScope = document.getElementById("set-custom-max-scope");
  el.setCustomValidateTimeout = document.getElementById("set-custom-validate-timeout");
  el.setCustomCallTimeout = document.getElementById("set-custom-call-timeout");
  el.setLoopEnabled = document.getElementById("set-loop-enabled");
  el.setLoopEveryRounds = document.getElementById("set-loop-every-rounds");
  el.setLoopIdleLimit = document.getElementById("set-loop-idle-limit");
  el.setLoopCharThreshold = document.getElementById("set-loop-char-threshold");
  el.setLoopTokenRatio = document.getElementById("set-loop-token-ratio");
  el.setLoopKeepRounds = document.getElementById("set-loop-keep-rounds");
  el.setLoopContextWindow = document.getElementById("set-loop-context-window");
  el.setLoopRequestLevel = document.getElementById("set-loop-request-level");
  // P3 心情 / 自我记忆 / 主动行为设置
  el.setMoodEnabled = document.getElementById("set-mood-enabled");
  el.setMoodBaseline = document.getElementById("set-mood-baseline");
  el.setMoodDeltaThreshold = document.getElementById("set-mood-delta-threshold");
  el.setMoodMinConfidence = document.getElementById("set-mood-min-confidence");
  el.setMoodDailyCap = document.getElementById("set-mood-daily-cap");
  el.setMoodTimelineKeep = document.getElementById("set-mood-timeline-keep");
  el.setMoodJudgeProvider = document.getElementById("set-mood-judge-provider");
  el.setMoodJudgeModel = document.getElementById("set-mood-judge-model");
  el.setSmEnabled = document.getElementById("set-sm-enabled");
  el.setSmQuota = document.getElementById("set-sm-quota");
  el.setSmTopk = document.getElementById("set-sm-topk");
  el.setSmBudget = document.getElementById("set-sm-budget");
  el.setProEnabled = document.getElementById("set-pro-enabled");
  el.setProWakeCheck = document.getElementById("set-pro-wake-check");
  el.setProWakeProb = document.getElementById("set-pro-wake-prob");
  el.setProWakeN = document.getElementById("set-pro-wake-n");
  el.setProActiveHours = document.getElementById("set-pro-active-hours");
  el.setProPeak = document.getElementById("set-pro-peak");
  el.setProValley = document.getElementById("set-pro-valley");
  el.setProCooldown = document.getElementById("set-pro-cooldown");
  el.setProQuiet = document.getElementById("set-pro-quiet");
  el.setProPollsMax = document.getElementById("set-pro-polls-max");
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
  el.btnExportSaveAs = document.getElementById("btn-export-saveas");
  el.btnNav = document.getElementById("btn-nav");
  el.overlay = document.getElementById("overlay");
  el.setRpFormat = document.getElementById("set-rp-format");
  el.setAttrEnabled = document.getElementById("set-attr-enabled");
  el.setAttrAi = document.getElementById("set-attr-ai");
  el.btnLogout = document.getElementById("btn-logout");
  el.btnJump = document.getElementById("btn-jump-bottom");
  el.userName = document.getElementById("user-name");
  el.btnUsers = document.getElementById("btn-users");
  el.btnLoop = document.getElementById("btn-loop");
  el.btnMood = document.getElementById("btn-mood");
  el.btnProactive = document.getElementById("btn-proactive");
  el.userDialog = document.getElementById("user-dialog");
  el.userList = document.getElementById("user-list");
  el.userNameInput = document.getElementById("user-new-name");
  el.userPasswordInput = document.getElementById("user-new-password");
  el.userLevelInput = document.getElementById("user-new-level");
  el.userQuotaInput = document.getElementById("user-new-quota");
  el.quotaChip = document.getElementById("quota-chip");
  el.quotaRemain = document.getElementById("quota-remain");
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
  el.newchatMode = document.getElementById("newchat-mode");
  el.galgameView = document.getElementById("galgame-view");
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
  applyFontScale(fontScale());
  syncMuteButton();
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
  // 偏好即时保存到本浏览器；阻止冒泡，避免被设置弹窗记为「未保存修改」
  if (el.setEnterSend) {
    el.setEnterSend.addEventListener("change", function (e) {
      e.stopPropagation();
      try {
        localStorage.setItem("flyai-enter-send",
                             el.setEnterSend.checked ? "1" : "0");
      } catch (err) {}
    });
  }
  // 对话上下文相关设置：即时保存到账号设置。
  // change 和 input 都要阻止冒泡——弹窗脏检查两个事件都监听，
  // 手机端操作数字框会持续触发 input，不拦就会被记为「未保存修改」
  [el.setKeepTurns, el.setAutoRecall, el.setAutoRecallMax, el.setExtractEvery,
   el.setLoreAutoInject, el.setMaxTokens]
    .forEach(function (node) {
      if (!node) return;
      node.addEventListener("change", function (e) {
        e.stopPropagation();
        saveMySettings();
      });
      node.addEventListener("input", function (e) {
        e.stopPropagation();
      });
    });
  if (el.btnOpenMemoryManager) {
    el.btnOpenMemoryManager.addEventListener("click", function () {
      if (typeof openMemoryManager === "function") openMemoryManager();
    });
  }
  // 聊天背景：即时生效，stopPropagation 避免设置弹窗记为「未保存修改」
  el.btnBgPick.addEventListener("click", function () { el.bgFile.click(); });
  el.bgFile.addEventListener("change", function (e) { e.stopPropagation(); onBgFilePicked(); });
  el.bgOpacity.addEventListener("input", function (e) { e.stopPropagation(); onBgOpacityChange(); });
  el.btnBgRemove.addEventListener("click", onBgRemove);
  el.btnLore.addEventListener("click", openLoreDialog);
  el.btnMarket.addEventListener("click", enterMarket);
  el.btnBatchDelete.addEventListener("click", openBatchMode);
  el.btnTheme.addEventListener("click", toggleTheme);
  el.btnAddProvider.addEventListener("click", addProviderRow);
  if (el.setTtsAddVoice) el.setTtsAddVoice.addEventListener("click", function () {
    el.setTtsVoiceList.appendChild(ttsVoiceRow("", ""));
  });
  if (el.btnAddImageModel) el.btnAddImageModel.addEventListener("click", addImageModelRow);
  if (el.btnSd) el.btnSd.addEventListener("click", toggleSdEngine);
  if (el.btnDeepThink) el.btnDeepThink.addEventListener("click", toggleDeepThink);
  if (el.btnAddMyProvider) el.btnAddMyProvider.addEventListener("click", addMyProvider);
  if (el.btnAddMyImage) el.btnAddMyImage.addEventListener("click", addMyImage);
  if (el.btnUserSettingsClose) el.btnUserSettingsClose.addEventListener("click", function () {
    el.dialog.close();
    renderProviderBar();
  });
  if (el.btnSaveFeatures) el.btnSaveFeatures.addEventListener("click", saveFeaturePrefs);
  var btnSaveFeaturesM = document.getElementById("btn-save-features-m");
  if (btnSaveFeaturesM) btnSaveFeaturesM.addEventListener("click", saveFeaturePrefs);
  el.btnConfigSave.addEventListener("click", saveConfig);
  el.btnConfigCancel.addEventListener("click", function () { if (guardDiscard()) { clearDirty(); el.dialog.close(); } });
  el.input.addEventListener("keydown", function (e) {
    // @ 候选浮层打开时：方向键/回车/Tab/Esc 先交给候选处理，回车不发送
    if (onMentionKeydown(e)) return;
    if (e.key === "Enter") {
      var plainEnter = !e.shiftKey && !e.ctrlKey && !e.metaKey;
      // 裸 Enter：按「Enter 发送」偏好决定；Ctrl/Cmd+Enter 始终发送
      if ((plainEnter && enterToSendEnabled())
          || ((e.ctrlKey || e.metaKey) && !e.shiftKey)) {
        e.preventDefault();
        onSend();
      }
      // 偏好关闭时裸 Enter 走 textarea 默认行为：换行
    }
    if (e.key === "Escape") { el.input.blur(); }
  });
  el.input.addEventListener("input", function () { scheduleDraftSave(); autosizeInput(); updateMentionBox(); });
  el.input.addEventListener("blur", closeMentionBox);
  if (el.btnNav) el.btnNav.addEventListener("click", toggleNav);
  if (el.overlay) el.overlay.addEventListener("click", closeNav);
  if (el.btnLogout) el.btnLogout.addEventListener("click", doLogout);
  if (el.btnUsers) el.btnUsers.addEventListener("click", openUserDialog);
  if (el.btnLoop) el.btnLoop.addEventListener("click", function () {
    if (window.openLoopManager) openLoopManager();
  });
  if (el.btnMood) el.btnMood.addEventListener("click", function () {
    if (window.openMoodManager) openMoodManager();
  });
  if (el.btnProactive) el.btnProactive.addEventListener("click", function () {
    if (window.openProactiveManager) openProactiveManager();
  });
  if (el.btnJump) el.btnJump.addEventListener("click", jumpBottom);
  if (el.messages) el.messages.addEventListener("scroll", onMessagesScroll);
  if (el.btnUserCreate) el.btnUserCreate.addEventListener("click", createUser);
  if (el.btnUserCancel) el.btnUserCancel.addEventListener("click", function () { el.userDialog.close(); });
  // beta：多 AI 群聊
  if (el.btnNewchatCancel) el.btnNewchatCancel.addEventListener("click", function () { if (el.newchatDialog) el.newchatDialog.close(); });
  if (el.btnNewchatCreate) el.btnNewchatCreate.addEventListener("click", confirmNewChat);
  if (el.newchatBack) el.newchatBack.addEventListener("click", function () { if (state.newChat) renderPickRoots(); });
  if (el.btnNewchatPlain) el.btnNewchatPlain.addEventListener("click", plainNewChat);
  if (el.newchatMode) el.newchatMode.addEventListener("change", onNewChatModeChange);
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
  if (el.btnExportSaveAs) el.btnExportSaveAs.addEventListener("click", saveCompressAsNewCopy);
  if (el.userPasswordInput) el.userPasswordInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); createUser(); }
  });
  bindMobileUI();
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

/* ==== Enter 发送偏好（手机端默认关闭：Enter 用于换行）==== */
function isMobileDevice() {
  var ua = "";
  try { ua = navigator.userAgent || ""; } catch (e) {}
  if (/Android|iPhone|iPad|iPod|Windows Phone|Mobile/i.test(ua)) return true;
  // iPadOS 13+ 伪装成桌面 UA：粗指针 + 触屏兜底
  try {
    if (window.matchMedia
        && window.matchMedia("(pointer: coarse)").matches
        && (navigator.maxTouchPoints || 0) > 0) return true;
  } catch (e) {}
  return false;
}
function enterToSendEnabled() {
  var saved = null;
  try { saved = localStorage.getItem("flyai-enter-send"); } catch (e) {}
  if (saved === "1") return true;
  if (saved === "0") return false;
  // 未设置过：桌面端默认开启，手机端默认关闭
  return !isMobileDevice();
}

/* ==== 主题 ==== */
function initTheme() {
  var saved = localStorage.getItem("flyai-theme") || "dark";
  applyTheme(saved);
}
function applyTheme(theme) {
  document.body.className = "theme-" + theme;
  localStorage.setItem("flyai-theme", theme);
  if (el.dialog && el.dialog.open) syncOptionRows();
  if (window.NativeBar) NativeBar.sync(theme);
}
function toggleTheme() {
  applyTheme(document.body.className.indexOf("light") >= 0 ? "dark" : "light");
}

/* ==== 聊天背景（与主题一样仅本地持久化；图片缩到最长边 1920 后存 dataURL）==== */
var BG_MAX_EDGE = 1920;
var BG_QUALITY = 0.85;

function initChatBackground() {
  var url = readBgImage();
  var op = readBgOpacity();
  applyChatBackground(url, op);
}
function readBgImage() {
  try { return localStorage.getItem("flyai-bg-image") || ""; } catch (e) { return ""; }
}
function readBgOpacity() {
  var v;
  try { v = parseFloat(localStorage.getItem("flyai-bg-opacity")); } catch (e) { v = NaN; }
  return isNaN(v) ? 1 : Math.min(1, Math.max(0, v));
}
function applyChatBackground(url, opacity) {
  if (url) {
    el.bgLayer.style.backgroundImage = 'url("' + url.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '")';
  } else {
    el.bgLayer.style.backgroundImage = "";
  }
  el.bgLayer.style.opacity = String(opacity);
  syncBgControls(url, opacity);
}
/* 同步个性化页的缩略图 / 滑块 / 移除按钮状态 */
function syncBgControls(url, opacity) {
  if (!el.bgPreview) return;
  var pct = Math.round(opacity * 100);
  el.bgOpacity.value = String(pct);
  el.bgOpacityVal.textContent = pct + "%";
  el.btnBgRemove.disabled = !url;
  if (url) {
    el.bgPreview.classList.remove("empty");
    el.bgPreview.textContent = "";
    el.bgPreview.style.backgroundImage = 'url("' + url.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '")';
  } else {
    el.bgPreview.classList.add("empty");
    el.bgPreview.textContent = "未设置背景";
    el.bgPreview.style.backgroundImage = "";
  }
}
/* 选图：读取 → 解码 → 超尺寸则等比缩小 → JPEG dataURL 持久化 */
function onBgFilePicked() {
  var f = el.bgFile.files && el.bgFile.files[0];
  el.bgFile.value = "";  // 允许再次选择同一文件
  if (!f) return;
  if (f.type.indexOf("image/") !== 0) { showToast("请选择图片文件", "err"); return; }
  var reader = new FileReader();
  reader.onerror = function () { showToast("读取图片失败", "err"); };
  reader.onload = function () {
    var pic = new Image();
    pic.onerror = function () { showToast("无法解析该图片", "err"); };
    pic.onload = function () {
      var scale = Math.min(1, BG_MAX_EDGE / Math.max(pic.width, pic.height));
      var w = Math.max(1, Math.round(pic.width * scale));
      var h = Math.max(1, Math.round(pic.height * scale));
      var canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      canvas.getContext("2d").drawImage(pic, 0, 0, w, h);
      var dataUrl;
      try {
        dataUrl = canvas.toDataURL("image/jpeg", BG_QUALITY);
      } catch (e) {
        showToast("图片处理失败", "err"); return;
      }
      var op = readBgOpacity();
      try {
        localStorage.setItem("flyai-bg-image", dataUrl);
      } catch (err) {
        showToast("存储空间不足，图片未能保存", "err"); return;
      }
      applyChatBackground(dataUrl, op);
      showToast("背景已设置");
    };
    pic.src = reader.result;
  };
  reader.readAsDataURL(f);
}
function onBgOpacityChange() {
  var op = parseInt(el.bgOpacity.value, 10) / 100;
  if (isNaN(op)) op = 1;
  op = Math.min(1, Math.max(0, op));
  try { localStorage.setItem("flyai-bg-opacity", String(op)); } catch (e) {}
  el.bgLayer.style.opacity = String(op);
  el.bgOpacityVal.textContent = Math.round(op * 100) + "%";
}
function onBgRemove() {
  try { localStorage.removeItem("flyai-bg-image"); } catch (e) {}
  applyChatBackground("", readBgOpacity());
  showToast("已移除背景");
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

/* ==================================================================== */
/* ==== 手机端现代化：设置分页 / bottom sheet / 静音 / 字体缩放     ==== */
/* ==================================================================== */

function isMobileView() {
  try { return window.matchMedia("(max-width: 860px)").matches; } catch (e) { return false; }
}

/* ==== 设置弹窗：分页栈导航 ==== */
var settingsStack = ["root"];
function settingsPageEl(name) {
  return el.dialog.querySelector('.m-page[data-page="' + name + '"]');
}
function resetSettingsPages() {
  settingsStack = ["root"];
  Array.prototype.forEach.call(el.dialog.querySelectorAll(".m-page"), function (p) {
    p.classList.remove("active", "animating");
    p.style.transform = "";
  });
  settingsPageEl("root").classList.add("active");
}
function showSettingsPageFlat(name) {
  Array.prototype.forEach.call(el.dialog.querySelectorAll(".m-page.active"), function (p) {
    p.classList.remove("active");
  });
  settingsPageEl(name).classList.add("active");
}
function pushSettingsPage(name) {
  var from = settingsPageEl(settingsStack[settingsStack.length - 1]);
  var to = settingsPageEl(name);
  if (!to || to === from) return;
  settingsStack.push(name);
  if (!isMobileView()) { showSettingsPageFlat(name); return; }
  to.classList.add("animating");
  to.style.transform = "translateX(100%)";
  void to.offsetWidth;
  to.style.transform = "translateX(0)";
  from.style.transform = "translateX(-28%)";
  setTimeout(function () {
    to.classList.remove("animating");
    to.classList.add("active");
    to.style.transform = "";
    from.classList.remove("active");
    from.style.transform = "";
  }, 270);
}
function popSettingsPage() {
  if (settingsStack.length <= 1) { closeSettingsRoot(); return; }
  var cur = settingsPageEl(settingsStack.pop());
  var back = settingsPageEl(settingsStack[settingsStack.length - 1]);
  if (!isMobileView()) {
    cur.classList.remove("active");
    back.classList.add("active");
    return;
  }
  back.classList.add("animating");
  back.style.transform = "translateX(-28%)";
  void back.offsetWidth;
  back.style.transform = "translateX(0)";
  cur.classList.add("animating");
  cur.style.transform = "translateX(100%)";
  setTimeout(function () {
    back.classList.remove("animating");
    back.classList.add("active");
    back.style.transform = "";
    cur.classList.remove("active", "animating");
    cur.style.transform = "";
  }, 270);
}
function closeSettingsRoot() {
  if (guardDiscard()) { clearDirty(); el.dialog.close(); }
}

/* 外观 / 字体大小选项行勾选状态同步 */
var SCALE_LABELS = { "1": "标准", "1.1": "大", "1.2": "特大" };
function syncOptionRows() {
  var theme = document.body.className.indexOf("light") >= 0 ? "light" : "dark";
  Array.prototype.forEach.call(
    el.dialog.querySelectorAll("[data-theme-option]"),
    function (b) {
      b.classList.toggle("selected", b.getAttribute("data-theme-option") === theme);
    }
  );
  var sc = fontScale();
  Array.prototype.forEach.call(
    el.dialog.querySelectorAll("[data-scale-option]"),
    function (b) {
      b.classList.toggle("selected", b.getAttribute("data-scale-option") === sc);
    }
  );
  if (el.mValAppearance) el.mValAppearance.textContent = theme === "light" ? "浅色" : "深色";
  if (el.mValFontsize) el.mValFontsize.textContent = SCALE_LABELS[sc];
}

/* ==== iOS 风格底部操作清单 ==== */
function openSheet(items, opts) {
  var mask = document.createElement("div");
  mask.className = "sheet-mask";
  var sheet = document.createElement("div");
  sheet.className = "sheet";
  function close() {
    mask.classList.remove("show");
    sheet.classList.remove("show");
    setTimeout(function () { mask.remove(); sheet.remove(); }, 290);
  }
  items.forEach(function (it) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "sheet-item" + (it.danger ? " danger" : "");
    b.textContent = it.label;
    b.addEventListener("click", function () {
      close();
      if (it.fn) setTimeout(it.fn, 150);
    });
    sheet.appendChild(b);
  });
  var cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "sheet-item sheet-cancel";
  cancel.textContent = (opts && opts.cancelLabel) || "取消";
  cancel.addEventListener("click", close);
  mask.addEventListener("click", close);
  sheet.appendChild(cancel);
  document.body.appendChild(mask);
  document.body.appendChild(sheet);
  requestAnimationFrame(function () {
    mask.classList.add("show");
    sheet.classList.add("show");
  });
  return { close: close };
}

/* ==== 静音偏好 ==== */
function isTtsMuted() {
  try { return localStorage.getItem("flyai-muted") === "1"; } catch (e) { return false; }
}
function syncMuteButton() {
  if (!el.btnMute) return;
  var muted = isTtsMuted();
  el.btnMute.classList.toggle("is-muted", muted);
  el.btnMute.querySelector("use").setAttribute("href", muted ? "#i-speaker-off" : "#i-speaker");
  el.btnMute.title = muted ? "取消静音" : "静音（关闭语音播放）";
}
function toggleMute() {
  try { localStorage.setItem("flyai-muted", isTtsMuted() ? "0" : "1"); } catch (e) {}
  syncMuteButton();
  showToast(isTtsMuted() ? "已静音，语音不会播放" : "已取消静音");
}

/* ==== 字体大小：body zoom 整体缩放（移动 Chromium / Safari 均支持）==== */
function fontScale() {
  var v = "1";
  try { v = localStorage.getItem("flyai-font-scale") || "1"; } catch (e) {}
  return SCALE_LABELS[v] ? v : "1";
}
function applyFontScale(v) {
  document.body.style.zoom = String(v);
}

/* ==== 手机端新交互绑定 ==== */
function bindMobileUI() {
  /* 分页：返回箭头 / data-goto 菜单行（事件委托） */
  el.dialog.addEventListener("click", function (e) {
    if (!e.target.closest) return;
    var back = e.target.closest(".m-back");
    if (back) {
      e.preventDefault();
      var bp = back.closest(".m-page");
      if (bp && bp.getAttribute("data-page") === "root") closeSettingsRoot();
      else popSettingsPage();
      return;
    }
    var row = e.target.closest("[data-goto]");
    if (row) {
      e.preventDefault();
      pushSettingsPage(row.getAttribute("data-goto"));
    }
  });

  /* 外观选项行 */
  el.dialog.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-theme-option]") : null;
    if (!b) return;
    applyTheme(b.getAttribute("data-theme-option"));
  });
  /* 字体大小选项行 */
  el.dialog.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-scale-option]") : null;
    if (!b) return;
    var v = b.getAttribute("data-scale-option");
    try { localStorage.setItem("flyai-font-scale", v); } catch (err) {}
    applyFontScale(v);
    syncOptionRows();
    showToast("字体大小：" + SCALE_LABELS[v]);
  });

  /* 各管理员子页：保存设置 */
  el.dialog.addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".btn-admin-save") : null;
    if (b) { e.preventDefault(); saveConfig(); }
  });

  /* 根页：内容（角色卡/素材管理弹窗） */
  var mRowMarket = document.getElementById("m-row-market");
  mRowMarket.addEventListener("click", function () {
    clearDirty();
    el.dialog.close();
    closeNav();              // 手机端：收起抽屉，市场为全屏视图
    enterMarket();
  });

  var mRowLore = document.getElementById("m-row-lore");
  mRowLore.addEventListener("click", function () {
    clearDirty();
    el.dialog.close();
    openLoreDialog();
  });

  /* 根页：退出登录 */
  el.mRowLogout.addEventListener("click", function () {
    if (confirm("确定退出登录？")) { clearDirty(); doLogout(); }
  });

  /* 顶栏：静音 / 新建对话 */
  el.btnMute.addEventListener("click", toggleMute);
  el.btnNewChatTop.addEventListener("click", openNewChatDialog);

  /* 账号管理：用户管理（admin） */
  el.btnAccountUsers.addEventListener("click", function () { openUserDialog(); });

  /* 数据管理 */
  el.btnDataImport.addEventListener("click", function () {
    el.dialog.close();
    el.sessionImportFile.value = "";
    el.sessionImportFile.click();
  });
  el.btnDataBatch.addEventListener("click", function () {
    el.dialog.close();
    closeNav();
    openBatchMode();
  });

  /* 输入区 + ：发图 / SD 引擎 */
  el.btnPlus.addEventListener("click", function () {
    var items = [{ label: "📷 发送图片", fn: function () { el.imageFile.click(); } }];
    if (sdAvailable()) {
      items.push({
        label: (state.sdEngine ? "✓ " : "") + "SD 本地生图引擎",
        fn: toggleSdEngine,
      });
    }
    openSheet(items);
  });

  /* 抽屉底部用户行：点击进入设置（仿 DeepSeek） */
  var userRow = document.querySelector(".user-row");
  userRow.addEventListener("click", function (e) {
    if (!isMobileView()) return;
    if (e.target.closest && e.target.closest("#btn-user-sheet")) return;
    closeNav();
    openSettings();
  });

  /* 抽屉底部用户 ⋯ */
  var btnUserSheet = document.getElementById("btn-user-sheet");
  btnUserSheet.addEventListener("click", function () {
    var items = [];
    if (state.role === "admin") {
      items.push({ label: "👥 用户管理", fn: openUserDialog });
    }
    items.push({
      label: "退出登录", danger: true, fn: function () {
        if (confirm("确定退出登录？")) doLogout();
      },
    });
    openSheet(items);
  });

  /* 断点跨越（移动→桌面）：弹窗分页复位 */
  try {
    var mq = window.matchMedia("(max-width: 860px)");
    var onBreakChange = function (ev) {
      if (!ev.matches && el.dialog.open) {
        resetSettingsPages();
      }
    };
    if (mq.addEventListener) mq.addEventListener("change", onBreakChange);
    else mq.addListener(onBreakChange);
  } catch (e) {}
}

/* ==== 输入框「深度思考」胶囊开关（localStorage 持久化，发送时随请求生效） ==== */
function toggleDeepThink() {
  state.deepThinking = !state.deepThinking;
  localStorage.setItem("flyai-deep-thinking", state.deepThinking ? "1" : "0");
  updateDeepThinkButton();
  showToast(state.deepThinking ? "已开启深度思考" : "已关闭深度思考");
}
function updateDeepThinkButton() {
  if (!el.btnDeepThink) return;
  el.btnDeepThink.classList.toggle("active", !!state.deepThinking);
  el.btnDeepThink.setAttribute("aria-pressed", state.deepThinking ? "true" : "false");
}

