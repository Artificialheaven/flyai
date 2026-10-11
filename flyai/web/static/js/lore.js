/* FlyAI 前端 · 内容库管理（角色卡 / 世界观）（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

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
  document.querySelectorAll("[data-world-pub]").forEach(function (b) {
    b.addEventListener("click", function () { publishToMarket("world", b.getAttribute("data-world-pub")); });
  });
  document.querySelectorAll("[data-char-pub]").forEach(function (b) {
    b.addEventListener("click", function () { publishToMarket("character", b.getAttribute("data-char-pub")); });
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
    if (isOwnCard(c)) {
      acts.push({
        label: "发布到市场",
        onClick: function () { publishToMarket("character", c.id); },
      });
    }
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
  fetch(apiUrl("/api/" + endpoint + "/" + id + "/export"), { headers: authHeaders() })
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
  var img = avatar ? '<img class="thumb" src="' + assetUrl(avatar) + '" alt="" />' : '<div class="default-img">' + escapeHtml((name||"?").slice(0,1).toUpperCase()) + "</div>";
  var kind = endpoint === "characters" ? "char" : "world";
  return (
    '<div class="lore-item">' + img +
    '<div class="l-info"><div class="l-name">' + escapeHtml(name) + "</div><div class='l-tables'>" + tables + "</div></div>" +
    '<div class="l-actions">' +
    '<button class="btn small" data-' + kind + '-edit="' + id + '" title="编辑">编辑</button>' +
    '<button class="btn small" data-' + kind + '-pub="' + id + '" title="发布到市场">发布</button>' +
    '<button class="btn small" data-' + kind + '-export="' + id + '" title="导出为 JSON">导出</button>' +
    '<button class="btn small danger" data-' + kind + '-del="' + id + '">删除</button>' +
    "</div></div>"
  );
}

function makeAvatarHtml(item, small) {
  if (item && item.avatar) return '<img class="avatar" src="' + assetUrl(item.avatar) + '" alt="" />';
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

