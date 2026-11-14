/* FlyAI 前端 · 设置弹窗 / 提供商与模型管理 / SD 引擎开关（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* ==== 设置 ==== */
function openSettings() {
  if (!state.config) return;
  clearDirty();
  var isAdmin = state.role === "admin";
  el.dialog.classList.toggle("is-admin", isAdmin);
  el.settingsSub.textContent = isAdmin
    ? "提供商与工具配置（管理员）"
    : "管理你自建的模型，或选用管理员共享的模型（你的等级 Lv" + state.userLevel + "）";
  // 账户页：用户名 / 等级
  el.accInfoName.textContent = state.userName || "—";
  el.accInfoLevel.textContent = isAdmin ? "管理员 · Lv256" : "Lv" + state.userLevel;
  // 通用前端偏好（本地保存）
  if (el.setEnterSend) el.setEnterSend.checked = enterToSendEnabled();
  loadMySettings();
  if (isAdmin) {
    el.configMsg.textContent = ""; el.configMsg.className = "msg-bar";
    fillAdminForm();
    renderProviderRows();
    renderImageModelRows();
    renderPluginChecks();
  } else {
    el.userConfigMsg.textContent = ""; el.userConfigMsg.className = "msg-bar";
    renderUserModelCards();
    renderUserVoiceView();
    renderUserFeatures();  // 个人覆盖随后由 loadMySettings() 拉取后二次渲染
  }
  resetSettingsPages();
  syncOptionRows();
  el.dialog.showModal();
}

/* 用户个人设置（服务端 data/{uid}/settings.json，跨设备跟随账号） */
function _intInput(node, fallback) {
  var n = parseInt(node && node.value, 10);
  if (isNaN(n)) n = fallback;
  return n;
}

function loadMySettings() {
  if (!el.setKeepTurns) return;
  state.mySettings = null;
  el.setKeepTurns.value = 0;
  if (el.setAutoRecall) el.setAutoRecall.checked = false;
  if (el.setAutoRecallMax) el.setAutoRecallMax.value = 5;
  if (el.setExtractEvery) el.setExtractEvery.value = 10;
  if (el.setLoreAutoInject) el.setLoreAutoInject.checked = true;
  if (el.setMaxTokens) el.setMaxTokens.value = 0;
  return fetchJSON("/api/my/settings").then(function (s) {
    if (!s) return;
    state.mySettings = s;
    el.setKeepTurns.value = s.keep_turns || 0;
    if (el.setAutoRecall) el.setAutoRecall.checked = !!s.auto_recall;
    if (el.setAutoRecallMax) el.setAutoRecallMax.value = s.auto_recall_max || 5;
    if (el.setExtractEvery) el.setExtractEvery.value = s.extract_every_turns != null ? s.extract_every_turns : 10;
    if (el.setLoreAutoInject) el.setLoreAutoInject.checked = s.lore_auto_inject !== false;
    if (el.setMaxTokens) el.setMaxTokens.value = s.max_tokens || 0;
    if (state.role !== "admin") renderUserFeatures();
  }).catch(function () {});
}

function saveMySettings() {
  if (!el.setKeepTurns) return;
  var keep = Math.max(0, _intInput(el.setKeepTurns, 0));
  el.setKeepTurns.value = keep;
  var payload = {
    keep_turns: keep,
    auto_recall: el.setAutoRecall ? !!el.setAutoRecall.checked : false,
    auto_recall_max: el.setAutoRecallMax
      ? Math.max(1, Math.min(20, _intInput(el.setAutoRecallMax, 5)))
      : 5,
    extract_every_turns: el.setExtractEvery
      ? Math.max(0, _intInput(el.setExtractEvery, 10))
      : 10,
    lore_auto_inject: el.setLoreAutoInject ? !!el.setLoreAutoInject.checked : true,
    max_tokens: el.setMaxTokens
      ? Math.max(0, Math.min(100000, _intInput(el.setMaxTokens, 0)))
      : 0,
  };
  if (el.setAutoRecallMax) el.setAutoRecallMax.value = payload.auto_recall_max;
  if (el.setExtractEvery) el.setExtractEvery.value = payload.extract_every_turns;
  if (el.setMaxTokens) el.setMaxTokens.value = payload.max_tokens;
  fetchJSON("/api/my/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(function () {
    // 已落盘：不再是「未保存修改」（input 事件漏拦时也兜底）
    if (typeof clearDirty === "function") clearDirty();
    showToast("对话上下文设置已保存", "ok");
  }).catch(function (e) {
    showToast("保存失败: " + e.message, "err");
  });
}

/* 普通用户：朗读音色只读清单（按最低可见等级过滤） */
function renderUserVoiceView() {
  if (!el.userVoiceView) return;
  var tts = state.config && state.config.tts;
  var voices = (tts && tts.voices) || [];
  var level = state.userLevel || 0;
  var minLevel = tts && tts.min_level != null ? tts.min_level : 0;
  var usable = voices.filter(function (v) {
    return (v.min_level || 0) <= level && minLevel <= level;
  });
  if (!tts || !tts.enabled || minLevel > level) {
    el.userVoiceView.innerHTML = '<div class="uv-empty">语音朗读未开放。</div>';
    return;
  }
  if (!usable.length) {
    el.userVoiceView.innerHTML = '<div class="uv-empty">暂无可用音色。</div>';
    return;
  }
  el.userVoiceView.innerHTML = usable.map(function (v) {
    return '<div class="uv-row"><span class="uv-name">' + escapeHtml(v.name || v.id || "音色") +
      '</span><span class="uv-id">' + escapeHtml(v.id || "") + "</span></div>";
  }).join("");
}

/* ==== 普通用户：功能开关（全局默认 + 仅对自己生效的覆盖）==== */

/* 覆盖字段 key → 全局配置在 state.config 下的路径 */
var UF_GLOBAL_PATHS = {
  tools_enabled: ["tools", "enabled"],
  tool_max_iterations: ["tools", "max_iterations"],
  tool_timeout_seconds: ["tools", "timeout_seconds"],
  roleplay_format_enabled: ["roleplay", "format_enabled"],
  roleplay_attributes_enabled: ["roleplay", "attributes", "enabled"],
  intro_chars: ["roleplay", "intro_chars"],
  background_chars: ["roleplay", "background_chars"],
  world_chars: ["roleplay", "world_chars"],
  mes_example_chars: ["roleplay", "mes_example_chars"],
  pinned_chars: ["roleplay", "pinned_chars"],
  web_enabled: ["web_search", "enabled"],
  browser_enabled: ["browser", "enabled"],
  pixiv_enabled: ["pixiv", "enabled"],
  image_enabled: ["image_generation", "enabled"],
  galgame_enabled: ["galgame", "enabled"],
  galgame_max_loops: ["galgame", "max_loops"],
};
/* 旧版/精简配置缺段时的兜底默认（与后端各 Settings 默认一致） */
var UF_GLOBAL_FALLBACK = {
  tools_enabled: true, tool_max_iterations: 8, tool_timeout_seconds: 120,
  roleplay_format_enabled: true, roleplay_attributes_enabled: true,
  intro_chars: 0, background_chars: 0, world_chars: 0,
  mes_example_chars: 0, pinned_chars: 0,
  web_enabled: true, browser_enabled: false, pixiv_enabled: true,
  image_enabled: true, galgame_enabled: true, galgame_max_loops: 40,
};
var USER_FEATURE_BLOCKS = [
  {
    key: "tools", title: "工具与迭代",
    desc: "AI 可调用工具的总开关，以及一轮对话内的工具循环预算。",
    fields: [
      { key: "tools_enabled", label: "启用工具调用（内容表/记忆/联网/绘图等工具的总开关）", kind: "bool" },
      { key: "tool_max_iterations", label: "最大工具迭代轮数（0–40）", kind: "int", min: 0, max: 40 },
      { key: "tool_timeout_seconds", label: "单个工具超时秒数（5–600）", kind: "int", min: 5, max: 600 },
    ],
  },
  {
    key: "roleplay", title: "角色扮演",
    desc: "对话格式规范、属性跟踪与各段提示词的注入字数预算（0=不限）。",
    fields: [
      { key: "roleplay_format_enabled", label: "启用对话格式规范（动作/内心/对白 / 分段样式）", kind: "bool" },
      { key: "roleplay_attributes_enabled", label: "启用属性跟踪（好感度、金钱等，AI 每回合可修改）", kind: "bool" },
      { key: "intro_chars", label: "角色简介字数预算", kind: "int", min: 0, max: 50000 },
      { key: "background_chars", label: "角色经历字数预算", kind: "int", min: 0, max: 50000 },
      { key: "world_chars", label: "世界观字数预算", kind: "int", min: 0, max: 50000 },
      { key: "mes_example_chars", label: "对话范例字数预算", kind: "int", min: 0, max: 50000 },
      { key: "pinned_chars", label: "常驻条目字数预算", kind: "int", min: 0, max: 50000 },
    ],
  },
  {
    key: "web", title: "联网搜索",
    fields: [{ key: "web_enabled", label: "启用联网搜索（web_search / web_fetch 工具）", kind: "bool" }],
  },
  {
    key: "browser", title: "AI 浏览器",
    desc: "AI 像人一样逐级浏览网页；需服务器已安装 Playwright Chromium，未安装时开启也无法使用。",
    fields: [{ key: "browser_enabled", label: "启用 AI 浏览器（browser_* 工具组）", kind: "bool" }],
  },
  {
    key: "pixiv", title: "P 站搜图",
    fields: [{ key: "pixiv_enabled", label: "启用 P 站搜图（搜索候选 → 视觉筛选 → 原图）", kind: "bool" }],
  },
  {
    key: "galgame", title: "Galgame 模式",
    fields: [
      { key: "galgame_enabled", label: "允许我创建 Galgame 模式房间", kind: "bool" },
      { key: "galgame_max_loops", label: "Galgame 单轮最大工具循环数（1–100）", kind: "int", min: 1, max: 100 },
    ],
  },
  {
    key: "image", title: "AI 生图",
    fields: [{ key: "image_enabled", label: "启用 AI 生图（文生图 / 图生图工具）", kind: "bool" }],
  },
];

function ufGlobalValue(key) {
  var path = UF_GLOBAL_PATHS[key] || [];
  var node = state.config;
  for (var i = 0; i < path.length; i++) {
    if (node == null) return UF_GLOBAL_FALLBACK[key];
    node = node[path[i]];
  }
  return node == null ? UF_GLOBAL_FALLBACK[key] : node;
}

function ufBoolText(v) { return v ? "开" : "关"; }

function ufFieldEl(f, val, disabled) {
  var label = document.createElement("label");
  label.className = "uf-field" + (f.kind === "bool" ? " uf-toggle" : "");
  var span = document.createElement("span");
  span.className = "uf-field-label";
  span.textContent = f.label;
  var input = document.createElement("input");
  if (f.kind === "bool") {
    input.type = "checkbox";
    input.checked = !!val;
  } else {
    input.type = "number";
    if (f.min != null) input.min = f.min;
    if (f.max != null) input.max = f.max;
    input.value = val;
  }
  input.dataset.uf = f.key;
  input.disabled = !!disabled;
  var hint = document.createElement("small");
  hint.className = "uf-hint";
  var gv = ufGlobalValue(f.key);
  hint.textContent = "全局默认：" + (f.kind === "bool" ? ufBoolText(gv) : gv);
  if (f.kind === "bool") {
    label.appendChild(input);
    label.appendChild(span);
    label.appendChild(hint);
  } else {
    label.appendChild(span);
    label.appendChild(input);
    label.appendChild(hint);
  }
  return label;
}

function renderUserFeatures() {
  if (!el.userFeaturesList || !state.config) return;
  var mine = state.mySettings || {};
  el.userFeaturesList.innerHTML = "";
  USER_FEATURE_BLOCKS.forEach(function (block) {
    var followed = block.fields.every(function (f) {
      return mine[f.key] === null || mine[f.key] === undefined;
    });
    var card = document.createElement("div");
    card.className = "uf-block" + (followed ? " is-follow" : "");
    card.dataset.block = block.key;

    var head = document.createElement("div");
    head.className = "uf-head";
    var title = document.createElement("strong");
    title.textContent = block.title;
    var follow = document.createElement("label");
    follow.className = "uf-follow";
    var chk = document.createElement("input");
    chk.type = "checkbox";
    chk.className = "uf-follow-chk";
    chk.checked = followed;
    follow.appendChild(chk);
    follow.appendChild(document.createTextNode(" 跟随全局默认"));
    head.appendChild(title);
    head.appendChild(follow);
    card.appendChild(head);

    if (block.desc) {
      var d = document.createElement("p");
      d.className = "uf-desc";
      d.textContent = block.desc;
      card.appendChild(d);
    }

    var body = document.createElement("div");
    body.className = "uf-body";
    block.fields.forEach(function (f) {
      var ov = mine[f.key];
      var val = (ov === null || ov === undefined) ? ufGlobalValue(f.key) : ov;
      body.appendChild(ufFieldEl(f, val, followed));
    });
    card.appendChild(body);

    chk.addEventListener("change", function () {
      var followGlobal = chk.checked;
      card.classList.toggle("is-follow", followGlobal);
      block.fields.forEach(function (f) {
        var input = body.querySelector('[data-uf="' + f.key + '"]');
        if (!input) return;
        input.disabled = followGlobal;
        // 勾选跟随：控件回填全局当前值（只读展示）
        var gv = ufGlobalValue(f.key);
        if (f.kind === "bool") input.checked = !!gv;
        else input.value = gv;
      });
    });

    el.userFeaturesList.appendChild(card);
  });
  if (el.userFeatMsg) { el.userFeatMsg.textContent = ""; el.userFeatMsg.className = "msg-bar"; }
}

function saveFeaturePrefs() {
  if (!el.userFeaturesList) return;
  // 服务端 PUT 会整体重写个人设置，顺带带上对话上下文偏好以免被重置
  var keep = Math.max(0, _intInput(el.setKeepTurns, 0));
  var payload = {
    keep_turns: keep,
    auto_recall: el.setAutoRecall ? !!el.setAutoRecall.checked : false,
    auto_recall_max: el.setAutoRecallMax
      ? Math.max(1, Math.min(20, _intInput(el.setAutoRecallMax, 5))) : 5,
    extract_every_turns: el.setExtractEvery
      ? Math.max(0, _intInput(el.setExtractEvery, 10)) : 10,
    lore_auto_inject: el.setLoreAutoInject ? !!el.setLoreAutoInject.checked : true,
    max_tokens: el.setMaxTokens
      ? Math.max(0, Math.min(100000, _intInput(el.setMaxTokens, 0))) : 0,
  };
  // 默认全部跟随（null），取消跟随的区块逐字段收集
  USER_FEATURE_BLOCKS.forEach(function (block) {
    block.fields.forEach(function (f) { payload[f.key] = null; });
  });
  el.userFeaturesList.querySelectorAll(".uf-block").forEach(function (card) {
    if (card.querySelector(".uf-follow-chk").checked) return;
    var block = USER_FEATURE_BLOCKS.filter(function (b) {
      return b.key === card.getAttribute("data-block");
    })[0];
    if (!block) return;
    block.fields.forEach(function (f) {
      var input = card.querySelector('[data-uf="' + f.key + '"]');
      if (!input) return;
      if (f.kind === "bool") {
        payload[f.key] = !!input.checked;
      } else {
        var n = parseInt(input.value, 10);
        if (isNaN(n)) n = ufGlobalValue(f.key);  // 空/非法值回落全局默认
        n = Math.max(f.min, Math.min(f.max, n));
        input.value = n;
        payload[f.key] = n;
      }
    });
  });
  var btn = el.btnSaveFeatures;
  if (btn) { btn.disabled = true; }
  fetchJSON("/api/my/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then(function (saved) {
    state.mySettings = saved || payload;
    renderUserFeatures();
    if (el.setKeepTurns) el.setKeepTurns.value = keep;
    showToast("功能开关已保存（仅对你自己生效）", "ok");
  }).catch(function (e) {
    if (el.userFeatMsg) {
      el.userFeatMsg.textContent = "保存失败: " + e.message;
      el.userFeatMsg.className = "msg-bar err";
    }
    showToast("保存失败: " + e.message, "err");
  }).then(function () {
    if (btn) btn.disabled = false;
  });
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
  el.setWsEngine.value = ws.engine === "chromium" ? "chromium" : "http";
  el.setWsSource.value = ws.search_engine === "baidu" ? "baidu" : "bing";
  el.setWsNum.value = ws.results_num || 8;
  el.setWsPagechars.value = ws.max_page_chars || 6000;
  el.setWsIters.value = ws.max_iterations || 15;
  var br = state.config.browser || {};
  el.setBrowserEnabled.checked = !!br.enabled;
  el.setBrowserHeadless.checked = br.headless !== false;
  el.setBrowserProxy.value = br.proxy || "";
  el.setBrowserSteps.value = br.max_steps || 15;
  el.setBrowserIdle.value = br.idle_seconds || 180;
  var tts = state.config.tts || {};
  el.setTtsEnabled.checked = !!tts.enabled;
  el.setTtsKey.value = tts.api_key || "";
  el.setTtsResource.value = tts.resource_id || "seed-tts-2.0";
  renderTtsVoiceRows(tts.voices || []);
  el.setTtsSpeaker.value = tts.default_speaker || "";
  el.setTtsMinlevel.value = tts.min_level || 0;
  el.setTtsFormat.value = tts.format || "mp3";
  el.setTtsSamplerate.value = String(tts.sample_rate || 24000);
  el.setTtsSpeechrate.value = tts.speech_rate || 0;
  el.setTtsLoudness.value = tts.loudness_rate || 0;
  el.setTtsProxy.value = tts.proxy || "";
  var galgame = state.config.galgame || {};
  if (el.setGalgameEnabled) el.setGalgameEnabled.checked = galgame.enabled !== false;
  el.setGalgameLoops.value = galgame.max_loops != null ? galgame.max_loops : 40;
  // 沙箱与调度（旧配置/精简配置缺段时回退后端默认值）
  var sb = state.config.sandbox || {};
  if (el.setSandboxEnabled) el.setSandboxEnabled.checked = sb.enabled === true;
  if (el.setSandboxMaxFileKb) el.setSandboxMaxFileKb.value = sb.max_file_kb != null ? sb.max_file_kb : 512;
  if (el.setSandboxQuotaMb) el.setSandboxQuotaMb.value = sb.quota_mb != null ? sb.quota_mb : 10;
  if (el.setSandboxPublicMaxFileKb) el.setSandboxPublicMaxFileKb.value = sb.public_max_file_kb != null ? sb.public_max_file_kb : 4096;
  if (el.setSandboxPublicQuotaMb) el.setSandboxPublicQuotaMb.value = sb.public_quota_mb != null ? sb.public_quota_mb : 50;
  var sch = state.config.scheduler || {};
  if (el.setSchedulerEnabled) el.setSchedulerEnabled.checked = sch.enabled === true;
  if (el.setSchedulerTimezone) el.setSchedulerTimezone.value = sch.timezone || "Asia/Shanghai";
  if (el.setSchedulerTick) el.setSchedulerTick.value = sch.tick_seconds != null ? sch.tick_seconds : 30;
  if (el.setSchedulerReminder) el.setSchedulerReminder.value = sch.reminder_check_seconds != null ? sch.reminder_check_seconds : 60;
}

// 音色清单动态行：显示名 + 音色 ID + 删除（仿 editor.js 的 entry 行模式）
function ttsVoiceRow(name, id) {
  var row = document.createElement("span");
  row.className = "tts-voice-row";
  var nameInput = document.createElement("input");
  nameInput.type = "text"; nameInput.className = "tv-name";
  nameInput.placeholder = "显示名（如：鲸鱼娘）"; nameInput.value = name || "";
  var idInput = document.createElement("input");
  idInput.type = "text"; idInput.className = "tv-id";
  idInput.placeholder = "音色 ID（如 S_NkpD5org2）"; idInput.value = id || "";
  var del = document.createElement("button");
  del.type = "button"; del.className = "btn small ed-del"; del.textContent = "✕";
  del.addEventListener("click", function () { row.remove(); });
  row.appendChild(nameInput); row.appendChild(idInput); row.appendChild(del);
  return row;
}
function renderTtsVoiceRows(voices) {
  el.setTtsVoiceList.innerHTML = "";
  voices.forEach(function (v) {
    el.setTtsVoiceList.appendChild(ttsVoiceRow(v.name, v.id));
  });
}
function readTtsVoices() {
  var voices = [];
  Array.prototype.forEach.call(
    el.setTtsVoiceList.querySelectorAll(".tts-voice-row"),
    function (row) {
      var id = row.querySelector(".tv-id").value.trim();
      if (!id) return;  // 过滤音色 ID 为空的行
      voices.push({ name: row.querySelector(".tv-name").value.trim(), id: id });
    }
  );
  return voices;
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
  label.innerHTML = "<label style='display:flex;align-items:center;gap:6px;font-size:13px'><input type='radio' name='active-provider' value='" + escapeAttr(p.id) + "'" + (state.config.active_provider === p.id ? " checked" : "") + " /> 激活</label><span>" + escapeHtml(p.id) + "</span>";
  row.appendChild(label);
  row.appendChild(fieldRow("id", "ID", p.id));
  row.appendChild(fieldRow("type", "类型", p.type, "select"));
  row.appendChild(fieldRow("base_url", "Base URL", p.base_url));
  row.appendChild(fieldRow("api_key", "API Key", p.api_key, "password"));
  row.appendChild(fieldRow("model", "模型", p.model));
  row.appendChild(fieldRow("vision", "视觉（支持图片输入）", p.vision, "checkbox"));
  row.appendChild(fieldRow("thinking_params", "思考参数 JSON（开启深度思考时合并，可空）", p.thinking_params, "json"));
  row.appendChild(fieldRow("thinking_off_params", "关闭思考参数 JSON（模型默认思考时填，可空）", p.thinking_off_params, "json"));
  row.appendChild(fieldRow("owner", "归属（uid）", p.owner));
  row.appendChild(fieldRow("shared", "共享", p.shared, "checkbox"));
  row.appendChild(fieldRow("min_level", "共享最低等级", p.min_level, "number"));
  row.appendChild(fieldRow("rate", "计费倍率（每轮扣 1×倍率）", p.rate == null ? 1 : p.rate, "number"));
  var del = document.createElement("button"); del.className = "btn small danger"; del.textContent = "删除";
  del.style.gridColumn = "span 2";
  del.addEventListener("click", function () { row.remove(); });
  row.appendChild(del);

  // 手机端：点「激活」立即切换并保存（该页手机端没有保存键）；桌面端仍走保存按钮
  var radio = row.querySelector('input[name="active-provider"]');
  if (radio) {
    radio.addEventListener("change", function () {
      if (!isMobileView()) return;
      var pid = p.id;
      fetchJSON("/api/config/activate/" + encodeURIComponent(pid), { method: "POST" })
        .then(function () {
          state.config.active_provider = pid;
          renderProviderBar();
          showToast("已切换激活模型：" + (p.model || pid));
        })
        .catch(function (e) {
          showToast("切换失败: " + e.message, "err");
          renderProviderRows();  // 恢复 radio 选中态
        });
    });
  }
  return row;
}
/* 手机端逐字输入：关闭首字母大写/自动纠错/拼写检查；密码框标记 new-password 阻止凭据回填覆盖 Key */
function makeVerbatimInput(input, isPassword) {
  input.setAttribute("autocapitalize", "off");
  input.setAttribute("autocorrect", "off");
  input.spellcheck = false;
  input.autocomplete = isPassword ? "new-password" : "off";
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
  } else if (kind === "json") {
    input = document.createElement("textarea");
    input.rows = 2;
    input.placeholder = '可空，如 {"reasoning_effort":"high"}';
    input.value = value ? (typeof value === "string" ? value : JSON.stringify(value)) : "";
    makeVerbatimInput(input, false);
  } else {
    input = document.createElement("input");
    input.type = kind || "text"; input.value = value || "";
    // 手机端：URL/ID/Key 必须逐字输入（密码框另标记 new-password 避免回填登录凭据）
    if (kind !== "number") makeVerbatimInput(input, kind === "password");
  }
  input.dataset.key = key;
  if (kind === "json") wrap.style.gridColumn = "span 2";
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
  row.appendChild(fieldRow("thinking_params", "思考参数 JSON（开启深度思考时合并，可空）", "", "json"));
  row.appendChild(fieldRow("thinking_off_params", "关闭思考参数 JSON（模型默认思考时填，可空）", "", "json"));
  row.appendChild(fieldRow("owner", "归属（uid）", "admin"));
  row.appendChild(fieldRow("shared", "共享", false, "checkbox"));
  row.appendChild(fieldRow("min_level", "共享最低等级", 0, "number"));
  row.appendChild(fieldRow("rate", "计费倍率（每轮扣 1×倍率）", 1, "number"));
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
    var rateInput = row.querySelector('input[data-key="rate"]');
    var rateVal = rateInput ? parseFloat(rateInput.value) : 1.0;
    if (isNaN(rateVal)) rateVal = 1.0;
    rateVal = Math.max(0, rateVal);
    var thinkParams = {};
    var thinkOffParams = {};
    var jsonErr = (function () {
      var pairs = [
        ["thinking_params", "思考参数 JSON"],
        ["thinking_off_params", "关闭思考参数 JSON"],
      ];
      for (var pi = 0; pi < pairs.length; pi++) {
        var inp = row.querySelector('[data-key="' + pairs[pi][0] + '"]');
        if (inp && inp.value.trim()) {
          try {
            var parsed = JSON.parse(inp.value);
            if (pairs[pi][0] === "thinking_params") thinkParams = parsed;
            else thinkOffParams = parsed;
          } catch (e) {
            return pairs[pi][1] + " 解析失败（模型 " +
              (idInput && idInput.value || "?") + "）: " + e.message;
          }
        }
      }
      return null;
    })();
    if (jsonErr) {
      el.configMsg.textContent = jsonErr;
      el.configMsg.className = "msg-bar err";
      if (isMobileView()) showToast("思考参数 JSON 格式错误", "err");
      return;
    }
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
      rate: rateVal,
      thinking_params: thinkParams,
      thinking_off_params: thinkOffParams,
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
    engine: el.setWsEngine.value,
    search_engine: el.setWsSource.value,
    results_num: parseInt(el.setWsNum.value, 10) || 8,
    max_page_chars: parseInt(el.setWsPagechars.value, 10) || 6000,
    max_iterations: parseInt(el.setWsIters.value, 10) || 15,
  };
  // 合并已有 browser 配置：UI 只暴露部分字段，保留 executable_path 等
  var browser = Object.assign({}, state.config.browser || {}, {
    enabled: !!el.setBrowserEnabled.checked,
    headless: !!el.setBrowserHeadless.checked,
    proxy: el.setBrowserProxy.value.trim(),
    max_steps: parseInt(el.setBrowserSteps.value, 10) || 15,
    idle_seconds: parseInt(el.setBrowserIdle.value, 10) || 180,
  });
  // tts：UI 未暴露 timeout_seconds，合并保留
  var tts = Object.assign({}, state.config.tts || {}, {
    enabled: !!el.setTtsEnabled.checked,
    api_key: el.setTtsKey.value,
    resource_id: el.setTtsResource.value,
    voices: readTtsVoices(),
    default_speaker: el.setTtsSpeaker.value.trim(),
    min_level: parseInt(el.setTtsMinlevel.value, 10) || 0,
    format: el.setTtsFormat.value,
    sample_rate: parseInt(el.setTtsSamplerate.value, 10) || 24000,
    speech_rate: parseInt(el.setTtsSpeechrate.value, 10) || 0,
    loudness_rate: parseInt(el.setTtsLoudness.value, 10) || 0,
    proxy: el.setTtsProxy.value.trim(),
  });
  var galLoops = parseInt(el.setGalgameLoops.value, 10) || 40;
  galLoops = Math.max(1, Math.min(galLoops, 200));
  var galgame = Object.assign({}, state.config.galgame || {}, {
    enabled: el.setGalgameEnabled ? !!el.setGalgameEnabled.checked : true,
    max_loops: galLoops,
  });
  var sandbox = {
    enabled: el.setSandboxEnabled ? !!el.setSandboxEnabled.checked : false,
    max_file_kb: parseInt(el.setSandboxMaxFileKb && el.setSandboxMaxFileKb.value, 10) || 512,
    quota_mb: parseInt(el.setSandboxQuotaMb && el.setSandboxQuotaMb.value, 10) || 10,
    public_max_file_kb: parseInt(el.setSandboxPublicMaxFileKb && el.setSandboxPublicMaxFileKb.value, 10) || 4096,
    public_quota_mb: parseInt(el.setSandboxPublicQuotaMb && el.setSandboxPublicQuotaMb.value, 10) || 50,
  };
  var scheduler = {
    enabled: el.setSchedulerEnabled ? !!el.setSchedulerEnabled.checked : false,
    timezone: (el.setSchedulerTimezone && el.setSchedulerTimezone.value.trim()) || "Asia/Shanghai",
    tick_seconds: Math.max(5, parseInt(el.setSchedulerTick && el.setSchedulerTick.value, 10) || 30),
    reminder_check_seconds: Math.max(5, parseInt(el.setSchedulerReminder && el.setSchedulerReminder.value, 10) || 60),
  };
  fetchJSON("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      active_provider: active, providers: providers, tools: tools, roleplay: roleplay,
      embeddings: embeddings, image_generation: imageGeneration,
      image_models: imageModels, stable_diffusion: stableDiffusion,
      pixiv: pixiv, web_search: webSearch, browser: browser, tts: tts,
      galgame: galgame, sandbox: sandbox, scheduler: scheduler,
    }),
  }).then(function (cfg) {
    state.config = cfg;
    el.configMsg.textContent = "已保存并即时生效"; el.configMsg.className = "msg-bar ok";
    el.dialog.close();
    renderProviderBar();
    return fetchJSON("/api/plugins");
  }).then(function (r) { state.plugins = r.plugins || []; })
    .catch(function (e) {
      el.configMsg.textContent = "保存失败: " + e.message; el.configMsg.className = "msg-bar err";
      // 手机端 configMsg 在隐藏的 #admin-save-main 里，用 toast 兜底提示
      if (isMobileView()) showToast("保存失败: " + e.message, "err");
    });
}

/* ==== 普通用户：我的模型 / 共享模型卡片 ==== */
var PROVIDER_FIELDS = [
  { key: "type", label: "类型", kind: "select", options: ["openai", "anthropic"] },
  { key: "base_url", label: "Base URL" },
  { key: "api_key", label: "API Key", kind: "password" },
  { key: "model", label: "模型" },
  { key: "vision", label: "视觉（图片输入）", kind: "checkbox" },
  { key: "thinking_params", label: "深度思考参数（JSON，可空；开启深度思考时合并）", kind: "json" },
  { key: "thinking_off_params", label: "关闭思考参数（JSON，可空；模型默认思考时用它关闭）", kind: "json" },
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
      renderProviderBar();  // 顶栏立即同步，无需等保存/重开
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

  // 字段：共享卡片对服务端未下发的敏感字段显示静态占位（== 暂不显示 ==），
  // 不渲染空输入框；本人卡片字段齐全，照常可编辑
  var grid = document.createElement("div");
  grid.className = "model-card-fields";
  fields.forEach(function (f) {
    // 他人共享模型：思考参数不下发也不展示，直接跳过（不留「暂不显示」占位）
    if (!opts.mine && (f.key === "thinking_params" || f.key === "thinking_off_params")) return;
    if (!opts.mine && !(f.key in data)) {
      grid.appendChild(maskedField(f));
    } else {
      grid.appendChild(cardField(f, data[f.key], !opts.mine));
    }
  });
  // 共享 LLM：只读计费倍率（精简视图里仍下发 rate）
  if (!opts.mine && kind === "provider") {
    var rate = data.rate == null ? 1 : data.rate;
    grid.appendChild(staticField("计费倍率（每轮扣 1×倍率）", rate));
  }
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
  } else if (f.kind === "json") {
    input = document.createElement("textarea");
    input.rows = 2;
    input.placeholder = '可空，如 {"reasoning_effort":"high"}';
    input.value = value
      ? (typeof value === "string" ? value : JSON.stringify(value))
      : "";
    makeVerbatimInput(input, false);
  } else {
    input = document.createElement("input");
    input.type = f.kind || "text";
    input.value = value == null ? "" : value;
    makeVerbatimInput(input, f.kind === "password");
  }
  input.dataset.k = f.key;
  if (disabled) input.disabled = true;
  if (f.kind === "json") label.style.gridColumn = "1 / -1";
  label.appendChild(span);
  label.appendChild(input);
  return label;
}

/* 共享模型：敏感字段服务端根本不下发，显示静态占位而非空输入框 */
function maskedField(f) {
  var label = document.createElement("label");
  label.className = "model-field is-masked";
  var span = document.createElement("span");
  span.textContent = f.label;
  var box = document.createElement("span");
  box.className = "masked-box";
  box.textContent = "== 暂不显示 ==";
  label.appendChild(span);
  label.appendChild(box);
  return label;
}

/* 共享模型：只读信息行（如计费倍率） */
function staticField(labelText, value) {
  var label = document.createElement("label");
  label.className = "model-field is-static";
  var span = document.createElement("span");
  span.textContent = labelText;
  var box = document.createElement("span");
  box.className = "static-box";
  box.textContent = value;
  label.appendChild(span);
  label.appendChild(box);
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
  var parseError = null;
  fields.forEach(function (f) {
    var input = card.querySelector('[data-k="' + f.key + '"]');
    if (f.kind === "checkbox") {
      values[f.key] = input.checked;
    } else if (f.kind === "json") {
      var raw = (input.value || "").trim();
      if (raw) {
        try { values[f.key] = JSON.parse(raw); }
        catch (e) { parseError = f.label + " 不是合法 JSON"; }
      } else {
        values[f.key] = {};
      }
    } else {
      values[f.key] = input.value;
    }
  });
  if (parseError) { userCardMsg(parseError, "err"); return; }
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

