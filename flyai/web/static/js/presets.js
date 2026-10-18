/* 模型预设管理：列表 / 启用切换 / 新建编辑（采样参数 + prompt 块）/
   酒馆预设导入 / 导出 / 复制 / 删除 / 发布到市场。
   经典脚本（无构建），挂 window.openPresetManager 供 ui.js / settings 调用。 */
"use strict";

(function () {
  var PARAM_NUM_KEYS = [
    "temperature", "top_p", "top_k", "top_a", "min_p",
    "frequency_penalty", "presence_penalty", "repetition_penalty", "seed",
  ];
  var POSITIONS = [
    ["main", "并入系统提示词"],
    ["depth", "历史深度 @depth"],
    ["tail", "历史后 system"],
  ];
  var ROLES = [["system", "system"], ["user", "user"], ["assistant", "assistant"]];

  var el = {};
  var bound = false;
  var presets = [];
  var activeId = "";
  var editingId = null;   // null=新建，"" 不成立；保存后刷新列表

  function cache() {
    el.dlg = document.getElementById("preset-mgr-dialog");
    el.listView = document.getElementById("ps-list-view");
    el.editView = document.getElementById("ps-edit-view");
    el.list = document.getElementById("ps-list");
    el.activeName = document.getElementById("ps-active-name");
    el.importFile = document.getElementById("ps-import-file");
    el.msg = document.getElementById("ps-msg");
  }

  function setMsg(text, kind) {
    if (!el.msg) return;
    el.msg.textContent = text || "";
    el.msg.className = "msg-bar" + (kind ? " " + kind : "");
  }

  function bind() {
    if (bound) return;
    cache();
    bound = true;
    document.getElementById("btn-open-preset-manager")
      .addEventListener("click", openPresetManager);
    document.getElementById("btn-ps-mgr-close")
      .addEventListener("click", function () { el.dlg.close(); });
    document.getElementById("btn-ps-new")
      .addEventListener("click", function () { openEditor(null); });
    document.getElementById("btn-ps-import")
      .addEventListener("click", function () { el.importFile.click(); });
    el.importFile.addEventListener("change", onImport);
    document.getElementById("btn-ps-add-block")
      .addEventListener("click", function () { addBlockRow(null); });
    document.getElementById("btn-ps-edit-cancel")
      .addEventListener("click", showList);
    document.getElementById("btn-ps-save")
      .addEventListener("click", onSave);
  }

  function openPresetManager() {
    cache();
    bind();
    showList();
    if (!el.dlg.open) el.dlg.showModal();
    load();
  }
  window.openPresetManager = openPresetManager;

  // bind() 不能只放在 openPresetManager 里——打开弹窗的入口按钮本身要靠 bind 挂监听
  if (document.getElementById("btn-open-preset-manager")) bind();
  else document.addEventListener("DOMContentLoaded", bind);

  /* ---- 列表 ---- */

  function load() {
    fetchJSON("/api/presets").then(function (data) {
      presets = data.presets || [];
      activeId = data.active_preset_id || "";
      renderList();
    }).catch(function (e) {
      el.list.innerHTML = "";
      setMsg("加载失败：" + e.message, "err");
    });
  }

  function summary(p) {
    var parts = [];
    var n = (p.params ? Object.keys(p.params).length : 0);
    if (n) parts.push(n + " 个采样参数");
    var blocks = p.blocks || [];
    var enabled = blocks.filter(function (b) { return b.enabled; }).length;
    if (blocks.length) parts.push(blocks.length + " 块（启用 " + enabled + "）");
    if (p.max_tokens) parts.push("上限 " + fmtNum(p.max_tokens));
    if (p.assistant_prefill) parts.push("assistant 前缀");
    var src = p.source === "tavern" ? "酒馆导入" : "自建";
    return src + (parts.length ? " · " + parts.join(" · ") : "");
  }

  function renderList() {
    var active = null;
    for (var i = 0; i < presets.length; i++) {
      if (presets[i].id === activeId) { active = presets[i]; break; }
    }
    el.activeName.textContent = active ? active.name : "无";
    if (!presets.length) {
      el.list.innerHTML = '<p class="sub">还没有预设。可以新建，或导入酒馆（SillyTavern）的 OpenAI 预设文件（.json/.txt）。</p>';
      return;
    }
    var h = [];
    presets.forEach(function (p) {
      var isActive = p.id === activeId;
      h.push('<div class="ps-row' + (isActive ? " active" : "") + '" data-id="' + p.id + '">');
      h.push('<div class="ps-row-main">');
      h.push('<div class="ps-row-title">' + (isActive ? "✅ " : "") + escapeHtml(p.name) + "</div>");
      h.push('<div class="ps-row-meta">' + escapeHtml(summary(p)) + "</div>");
      if (p.note) h.push('<div class="ps-row-note">' + escapeHtml(p.note) + "</div>");
      h.push("</div>");
      h.push('<div class="ps-row-actions">');
      h.push('<button type="button" class="btn small ps-act" data-act="toggle">' +
        (isActive ? "停用" : "启用") + "</button>");
      h.push('<button type="button" class="btn small ps-act" data-act="edit">编辑</button>');
      h.push('<button type="button" class="btn small ps-act" data-act="clone">复制</button>');
      h.push('<button type="button" class="btn small ps-act" data-act="export">导出</button>');
      h.push('<button type="button" class="btn small ps-act" data-act="publish">发布</button>');
      h.push('<button type="button" class="btn small danger ps-act" data-act="delete">删除</button>');
      h.push("</div></div>");
    });
    el.list.innerHTML = h.join("");
    var rows = el.list.querySelectorAll(".ps-row");
    for (var j = 0; j < rows.length; j++) {
      rows[j].addEventListener("click", function (ev) {
        var btn = ev.target.closest ? ev.target.closest("button.ps-act") : null;
        if (!btn) return;
        onRowAction(this.getAttribute("data-id"), btn.getAttribute("data-act"));
      });
    }
  }

  function findPreset(id) {
    for (var i = 0; i < presets.length; i++) {
      if (presets[i].id === id) return presets[i];
    }
    return null;
  }

  function onRowAction(id, act) {
    var p = findPreset(id);
    if (!p) return;
    if (act === "toggle") {
      var next = activeId === id ? "" : id;
      fetchJSON("/api/presets/active", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: next }),
      }).then(function () {
        activeId = next;
        renderList();
        showToast(next ? "已启用预设：" + p.name : "已停用预设", "ok");
      }).catch(function (e) { showToast("切换失败：" + e.message, "err"); });
    } else if (act === "edit") {
      openEditor(p);
    } else if (act === "clone") {
      fetchJSON("/api/presets/" + id + "/clone", { method: "POST" })
        .then(function () { showToast("已复制", "ok"); load(); })
        .catch(function (e) { showToast("复制失败：" + e.message, "err"); });
    } else if (act === "export") {
      exportPreset(p);
    } else if (act === "publish") {
      el.dlg.close();
      publishToMarket("preset", id);
    } else if (act === "delete") {
      askConfirm({
        title: "删除预设？",
        text: "确定删除「" + p.name + "」？此操作不可恢复。",
        okLabel: "删除",
        onOk: function () {
          fetchJSON("/api/presets/" + id, { method: "DELETE" })
            .then(function () {
              if (activeId === id) activeId = "";
              showToast("已删除", "ok");
              load();
            })
            .catch(function (e) { showToast("删除失败：" + e.message, "err"); });
        },
      });
    }
  }

  function exportPreset(p) {
    fetch(apiUrl("/api/presets/" + p.id + "/export"), { headers: authHeaders() })
      .then(function (r) {
        if (!r.ok) throw new Error(String(r.status));
        return r.blob();
      })
      .then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        var safe = (p.name || "preset").replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 40);
        a.download = safe + ".json";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
      })
      .catch(function (e) { showToast("导出失败：" + e.message, "err"); });
  }

  function onImport() {
    var f = el.importFile.files && el.importFile.files[0];
    el.importFile.value = "";
    if (!f) return;
    var fd = new FormData();
    fd.append("file", f);
    fetchJSON("/api/presets/import", { method: "POST", body: fd })
      .then(function (out) {
        var s = out.stats || {};
        showToast("导入成功：" + (s.blocks != null ? s.blocks + " 个块（启用 " + s.enabled + "）" : ""), "ok");
        load();
      })
      .catch(function (e) { showToast("导入失败：" + e.message, "err"); });
  }

  function showList() {
    el.editView.classList.add("hidden");
    el.listView.classList.remove("hidden");
    document.getElementById("ps-list-actions").classList.remove("hidden");
    setMsg("");
  }

  /* ---- 编辑器 ---- */

  function opt(list, val) {
    return list.map(function (kv) {
      return '<option value="' + kv[0] + '"' + (kv[0] === val ? " selected" : "") + ">" +
        kv[1] + "</option>";
    }).join("");
  }

  function blockRowHtml(b) {
    b = b || { name: "", content: "", enabled: true, role: "system",
               position: "main", depth: 4, insert_order: 100 };
    var h = [];
    h.push('<div class="ps-block">');
    h.push('<div class="ps-block-bar">');
    h.push('<label class="ps-b-en"><input type="checkbox" class="ps-b-enabled"' +
      (b.enabled ? " checked" : "") + " /> 启用</label>");
    h.push('<input type="text" class="ps-b-name" maxlength="80" placeholder="块名称" value="' +
      escapeHtml(b.name || "") + '"/>');
    h.push('<select class="ps-b-pos">' + opt(POSITIONS, b.position) + "</select>");
    h.push('<label class="ps-b-depth">depth <input type="number" class="ps-b-depth-input" min="0" max="50" step="1" value="' +
      intOr(b.depth, 4) + '"/></label>');
    h.push('<label class="ps-b-order">order <input type="number" class="ps-b-order-input" min="0" max="10000" step="1" value="' +
      intOr(b.insert_order, 100) + '"/></label>');
    h.push('<select class="ps-b-role">' + opt(ROLES, b.role || "system") + "</select>");
    h.push('<button type="button" class="btn small danger ps-b-del">删</button>');
    h.push("</div>");
    h.push('<textarea class="ps-b-content" rows="4" placeholder="Prompt 内容，支持 {{char}} {{user}} {{group}} 等宏">' +
      escapeHtml(b.content || "") + "</textarea>");
    h.push("</div>");
    return h.join("");
  }

  function addBlockRow(b) {
    var wrap = document.createElement("div");
    wrap.innerHTML = blockRowHtml(b);
    var node = wrap.firstChild;
    node.querySelector(".ps-b-del").addEventListener("click", function () {
      node.remove();
    });
    document.getElementById("ps-blocks").appendChild(node);
  }

  function openEditor(p) {
    editingId = p ? p.id : null;
    setMsg("");
    document.getElementById("ps-f-name").value = p ? p.name : "";
    document.getElementById("ps-f-note").value = p ? (p.note || "") : "";
    document.getElementById("ps-f-max-tokens").value = p ? intOr(p.max_tokens, 0) : 0;
    document.getElementById("ps-f-prefill").value = p ? (p.assistant_prefill || "") : "";
    PARAM_NUM_KEYS.forEach(function (k) {
      var inp = document.getElementById("ps-p-" + k);
      var v = p && p.params && p.params[k] != null ? p.params[k] : "";
      inp.value = v;
    });
    document.getElementById("ps-p-reasoning_effort").value =
      p && p.params && p.params.reasoning_effort ? p.params.reasoning_effort : "";
    var blocksEl = document.getElementById("ps-blocks");
    blocksEl.innerHTML = "";
    var blocks = p ? (p.blocks || []) : [];
    if (!blocks.length) {
      addBlockRow({ name: "主提示词", content: "", position: "main",
                    role: "system", depth: 4, insert_order: 100, enabled: true });
    } else {
      blocks.forEach(function (b) { addBlockRow(b); });
    }
    el.listView.classList.add("hidden");
    document.getElementById("ps-list-actions").classList.add("hidden");
    el.editView.classList.remove("hidden");
  }

  function collectPayload() {
    var name = document.getElementById("ps-f-name").value.trim();
    if (!name) {
      setMsg("请填写预设名称", "err");
      return null;
    }
    var params = {};
    PARAM_NUM_KEYS.forEach(function (k) {
      var raw = document.getElementById("ps-p-" + k).value.trim();
      if (raw === "") return;
      var n = Number(raw);
      if (!isNaN(n)) params[k] = n;
    });
    var effort = document.getElementById("ps-p-reasoning_effort").value;
    if (effort) params.reasoning_effort = effort;

    var blocks = [];
    var nodes = document.getElementById("ps-blocks").querySelectorAll(".ps-block");
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      blocks.push({
        name: n.querySelector(".ps-b-name").value,
        content: n.querySelector(".ps-b-content").value,
        enabled: n.querySelector(".ps-b-enabled").checked,
        position: n.querySelector(".ps-b-pos").value,
        depth: intOr(n.querySelector(".ps-b-depth-input").value, 4),
        insert_order: intOr(n.querySelector(".ps-b-order-input").value, 100),
        role: n.querySelector(".ps-b-role").value,
        identifier: "",
      });
    }
    return {
      name: name,
      note: document.getElementById("ps-f-note").value,
      params: params,
      max_tokens: Math.max(0, intOr(document.getElementById("ps-f-max-tokens").value, 0)),
      assistant_prefill: document.getElementById("ps-f-prefill").value,
      blocks: blocks,
    };
  }

  function onSave() {
    var payload = collectPayload();
    if (!payload) return;
    var btn = document.getElementById("btn-ps-save");
    btn.disabled = true;
    var req = editingId
      ? fetchJSON("/api/presets/" + editingId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : fetchJSON("/api/presets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
    req.then(function () {
      showToast("已保存", "ok");
      showList();
      load();
    }).catch(function (e) {
      setMsg("保存失败：" + e.message, "err");
    }).finally(function () {
      btn.disabled = false;
    });
  }
})();
