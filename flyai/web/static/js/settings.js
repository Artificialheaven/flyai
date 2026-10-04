/* FlyAI 前端 · 设置弹窗 / 提供商与模型管理 / SD 引擎开关（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

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
    // 密码类字段多为 API Key/新密码，标记 new-password 避免浏览器回填登录凭据
    if (kind === "password") input.autocomplete = "new-password";
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
  fetchJSON("/api/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      active_provider: active, providers: providers, tools: tools, roleplay: roleplay,
      embeddings: embeddings, image_generation: imageGeneration,
      image_models: imageModels, stable_diffusion: stableDiffusion,
      pixiv: pixiv, web_search: webSearch, browser: browser, tts: tts,
    }),
  }).then(function (cfg) {
    state.config = cfg;
    el.configMsg.textContent = "已保存并即时生效"; el.configMsg.className = "msg-bar ok";
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

