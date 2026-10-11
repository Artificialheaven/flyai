/* FlyAI 前端 · 角色卡/世界观编辑器与 AI 属性生成（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

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
  populateVoiceSelect(c && c.voice ? c.voice : "");
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
function populateVoiceSelect(currentId) {
  var sel = document.getElementById("ed-voice");
  if (!sel) return;
  var voices = ((state.config && state.config.tts) ? state.config.tts.voices : null) || [];
  voices.forEach(function (vc) {
    if (!vc.id) return;
    var o = document.createElement("option");
    o.value = vc.id;
    o.textContent = vc.name ? vc.name + "（" + vc.id + "）" : vc.id;
    sel.appendChild(o);
  });
  // 已绑定 ID 不在清单中（旧卡/手填）：追加同值选项避免丢失
  if (currentId && !Array.prototype.some.call(sel.options, function (o) {
    return o.value === currentId;
  })) {
    var extra = document.createElement("option");
    extra.value = currentId; extra.textContent = currentId;
    sel.appendChild(extra);
  }
  sel.value = currentId || "";
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
    (v.avatar ? '<img id="ed-avatar-img" src="' + assetUrl(v.avatar) + '" alt="" />' : "<span>点击上传<br/>头像</span>") + "</div>" +
    '<div style="flex:1">' +
    '<label class="f-label">角色名称<input type="text" id="ed-name" value="' + escapeAttr(v.name) + '" /></label>' +
    (scoped ? "" : '<label class="f-label">别名（副本列表中显示，留空则用角色名称）<input type="text" id="ed-alias" value="' + escapeAttr(v.alias || "") + '" /></label>') +
    "</div></div>" +
    '<label class="f-label">绑定音色（留空用默认音色；AI 仍可临时换音色）' +
    '<select id="ed-voice"><option value="">默认音色（不绑定）</option></select></label>' +
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
      fileEl.value = "";  // 同一文件可再次选择
      openAvatarCropper(f, function (blob, ext) {
        var fd = new FormData();
        fd.append("file", new File([blob], "avatar" + ext, { type: blob.type }));
        fetch(apiUrl("/api/upload"), { method: "POST", headers: authHeaders(), body: fd }).then(function (r) { return r.json(); }).then(function (res) {
          avatarEl.innerHTML = '<img id="ed-avatar-img" src="' + assetUrl(res.url) + '" alt="" />';
          avatarEl.dataset.avatar = res.url;
        }).catch(function (e) { showToast("上传失败: " + e.message, "err"); });
      });
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
      var voiceEl = document.getElementById("ed-voice");
      if (voiceEl) payload.voice = voiceEl.value;
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

