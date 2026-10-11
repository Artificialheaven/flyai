/* 市场（Market）：全屏视图，发布 / 浏览 / 安装角色卡、世界观、历史对话。
   经典脚本（无构建），函数挂全局供 ui.js / lore.js / rooms.js / native.js 调用。 */
"use strict";

var Market = (function () {
  var el = {};
  var bound = false;
  var tab = "";                 // "" | character | world | session | mine
  var entries = [];
  var detailId = null;
  var searchTimer = null;

  var KIND_LABEL = { character: "角色卡", world: "世界观", session: "对话" };

  function cache() {
    el.view = document.getElementById("market-view");
    el.list = document.getElementById("mk-list");
    el.detail = document.getElementById("mk-detail");
    el.detailBody = document.getElementById("mk-detail-body");
    el.search = document.getElementById("mk-search");
    el.pubDialog = document.getElementById("mk-publish-dialog");
    el.pubTitle = document.getElementById("mk-pub-title");
    el.pubBody = document.getElementById("mk-pub-body");
  }

  function bind() {
    if (bound) return;
    bound = true;
    document.getElementById("mk-back").addEventListener("click", exitMarket);
    document.getElementById("mk-detail-back").addEventListener("click", closeDetail);
    document.getElementById("mk-publish").addEventListener("click", function () {
      openPublishDialog(null, null);
    });
    el.search.addEventListener("input", function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(loadList, 300);
    });
    var tabs = document.querySelectorAll(".mk-tab");
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener("click", function () {
        var all = document.querySelectorAll(".mk-tab");
        for (var j = 0; j < all.length; j++) all[j].classList.remove("active");
        this.classList.add("active");
        tab = this.getAttribute("data-kind") || "";
        loadList();
      });
    }
  }

  /* ==== 进入 / 退出 ==== */

  function enterMarket() {
    cache();
    bind();
    var appEl = document.getElementById("app");
    if (appEl) appEl.classList.add("gv-app-hidden");
    el.view.classList.remove("hidden");
    closeDetail();
    loadList();
  }

  function exitMarket() {
    el.view.classList.add("hidden");
    var appEl = document.getElementById("app");
    if (appEl) appEl.classList.remove("gv-app-hidden");
    if (el.pubDialog.open) el.pubDialog.close();
  }

  /* ==== 列表 ==== */

  function listUrl() {
    var q = (el.search.value || "").trim();
    var params = [];
    if (tab === "mine") params.push("mine=1");
    else if (tab) params.push("kind=" + encodeURIComponent(tab));
    if (q) params.push("q=" + encodeURIComponent(q));
    return "/api/market/entries" + (params.length ? "?" + params.join("&") : "");
  }

  function loadList() {
    el.list.innerHTML = '<p class="mk-empty">加载中…</p>';
    fetchJSON(listUrl()).then(function (data) {
      entries = data.entries || [];
      renderList();
    }).catch(function (e) {
      el.list.innerHTML = "";
      showToast("加载失败：" + e.message, "err");
    });
  }

  function coverHtml(e) {
    if (e.cover) {
      return '<img class="mk-card-cover" src="' + escapeHtml(assetUrl(e.cover)) +
        '" alt="" loading="lazy" onerror="this.classList.add(\'mk-cover-broken\')"/>';
    }
    var glyph = { character: "🧙", world: "🌍", session: "💬" }[e.kind] || "📦";
    return '<div class="mk-card-cover mk-cover-fallback">' + glyph + "</div>";
  }

  function levelBadge(e) {
    if (!e.min_level) return "";
    return '<span class="mk-badge mk-badge-level" title="需要等级 ' + e.min_level +
      '">Lv.' + e.min_level + "</span>";
  }

  function renderList() {
    if (!entries.length) {
      el.list.innerHTML = '<p class="mk-empty">' +
        (tab === "mine" ? "你还没有发布过内容，点右上角「＋ 发布」试试" : "没有找到内容") +
        "</p>";
      return;
    }
    var mine = tab === "mine";
    var html = ['<div class="mk-grid">'];
    entries.forEach(function (e) {
      html.push('<div class="mk-card" data-id="' + e.id + '">');
      html.push(coverHtml(e));
      html.push('<div class="mk-card-body">');
      html.push('<div class="mk-card-top"><span class="mk-badge mk-badge-kind">' +
        escapeHtml(KIND_LABEL[e.kind] || e.kind) + "</span>" + levelBadge(e) + "</div>");
      html.push('<div class="mk-card-title">' + escapeHtml(e.title) + "</div>");
      html.push('<div class="mk-card-meta">' + escapeHtml(e.author_name || "匿名") +
        ' · ⬇ ' + e.installs + "</div>");
      if (e.intro) {
        html.push('<p class="mk-card-intro">' + escapeHtml(e.intro) + "</p>");
      }
      if (mine) {
        html.push('<div class="mk-card-actions">');
        html.push('<button type="button" class="btn small-btn mk-btn-edit" data-act="edit">编辑</button>');
        html.push('<button type="button" class="btn small-btn mk-btn-refresh" data-act="refresh">更新内容</button>');
        html.push('<button type="button" class="btn small-btn danger mk-btn-delete" data-act="delete">下架</button>');
        html.push("</div>");
      }
      html.push("</div></div>");
    });
    html.push("</div>");
    el.list.innerHTML = html.join("");

    var cards = el.list.querySelectorAll(".mk-card");
    for (var i = 0; i < cards.length; i++) {
      cards[i].addEventListener("click", function (ev) {
        var btn = ev.target.closest ? ev.target.closest("button[data-act]") : null;
        var id = this.getAttribute("data-id");
        if (btn) {
          ev.stopPropagation();
          onManage(id, btn.getAttribute("data-act"));
          return;
        }
        openDetail(id);
      });
    }
  }

  /* ==== 详情 ==== */

  function openDetail(mid) {
    fetchJSON("/api/market/entries/" + mid).then(function (e) {
      detailId = mid;
      renderDetail(e);
      el.detail.classList.remove("hidden");
    }).catch(function (err) {
      showToast("详情加载失败：" + err.message, "err");
    });
  }

  function closeDetail() {
    detailId = null;
    el.detail.classList.add("hidden");
  }

  function renderDetail(e) {
    var mine = e.author_uid === state.uid;
    var h = [];
    if (e.cover) {
      h.push('<div class="mk-detail-cover"><img src="' + escapeHtml(assetUrl(e.cover)) +
        '" alt="" onerror="this.classList.add(\'mk-cover-broken\')"/></div>');
    }
    h.push('<div class="mk-detail-info">');
    h.push('<div class="mk-card-top"><span class="mk-badge mk-badge-kind">' +
      escapeHtml(KIND_LABEL[e.kind] || e.kind) + "</span>" + levelBadge(e) + "</div>");
    h.push('<h2 class="mk-detail-title">' + escapeHtml(e.title) + "</h2>");
    h.push('<p class="mk-detail-author">发布者：' + escapeHtml(e.author_name || "匿名") + "</p>");
    h.push('<p class="mk-detail-stats">版本 r' + e.revision + ' · 安装 ' +
      e.installs + " 次 · 更新于 " + escapeHtml(e.updated_at || "") + "</p>");
    if (e.intro) h.push('<p class="mk-detail-intro">' + escapeHtml(e.intro).replace(/\n/g, "<br/>") + "</p>");
    h.push('<div class="mk-detail-actions">');
    var installLabel = e.kind === "session" ? "导入为新会话" : "复制到我的库";
    h.push('<button type="button" id="mk-install-btn" class="btn primary">' + installLabel + "</button>");
    if (mine) {
      h.push('<button type="button" class="btn" id="mk-detail-edit">编辑信息</button>');
      h.push('<button type="button" class="btn" id="mk-detail-refresh">更新内容</button>');
      h.push('<button type="button" class="btn danger" id="mk-detail-delete">下架</button>');
    }
    h.push("</div></div>");
    el.detailBody.innerHTML = h.join("");

    document.getElementById("mk-install-btn").addEventListener("click", function () {
      doInstall(e, this);
    });
    if (mine) {
      document.getElementById("mk-detail-edit").addEventListener("click", function () {
        openEditDialog(e);
      });
      document.getElementById("mk-detail-refresh").addEventListener("click", function () {
        doRefresh(e);
      });
      document.getElementById("mk-detail-delete").addEventListener("click", function () {
        doDelete(e);
      });
    }
  }

  function doInstall(e, btn) {
    btn.disabled = true;
    fetchJSON("/api/market/entries/" + e.id + "/install", { method: "POST" })
      .then(function () {
        showToast(e.kind === "session" ? "已导入为新会话" : "已复制到你的库", "ok");
        closeDetail();
        loadList();
      })
      .catch(function (err) {
        btn.disabled = false;
        showToast("安装失败：" + err.message, "err");
      });
  }

  /* ==== 发布者管理 ==== */

  function onManage(id, act) {
    var e = null;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i].id === id) { e = entries[i]; break; }
    }
    if (!e) return;
    if (act === "edit") openEditDialog(e);
    else if (act === "refresh") doRefresh(e);
    else if (act === "delete") doDelete(e);
  }

  function doRefresh(e) {
    askConfirm({
      title: "更新内容？",
      text: "将用你当前「" + e.title + "」的最新内容替换市场快照，旧版本不再可见（已安装的副本不受影响）。",
      okLabel: "更新",
      danger: false,
      onOk: function () {
        fetchJSON("/api/market/entries/" + e.id + "/refresh", { method: "POST" })
          .then(function (updated) {
            showToast("内容已更新到 r" + updated.revision, "ok");
            loadList();
          })
          .catch(function (err) { showToast("更新失败：" + err.message, "err"); });
      }
    });
  }

  function doDelete(e) {
    askConfirm({
      title: "下架该内容？",
      text: "下架后其他用户无法再看到或安装「" + e.title + "」，已安装的副本不受影响。",
      okLabel: "下架",
      onOk: function () {
        fetchJSON("/api/market/entries/" + e.id, { method: "DELETE" })
          .then(function () {
            showToast("已下架", "ok");
            if (detailId === e.id) closeDetail();
            loadList();
          })
          .catch(function (err) { showToast("下架失败：" + err.message, "err"); });
      }
    });
  }

  /* ==== 发布弹窗 ==== */

  function openPublishDialog(presetKind, presetSourceId) {
    el.pubTitle.textContent = "发布到市场";
    if (presetKind && presetSourceId) {
      // 来自卡 / 会话上下文菜单：直接跳到填表单那一步
      pickObject(presetKind, { id: presetSourceId });
      return;
    }
    renderKindStep();
    if (!el.pubDialog.open) el.pubDialog.showModal();
  }

  function renderKindStep() {
    var h = ['<p class="sub">选择要发布的类型</p><div class="mk-pub-kinds">'];
    [["character", "🧙 角色卡"], ["world", "🌍 世界观"], ["session", "💬 历史对话"]]
      .forEach(function (k) {
        h.push('<button type="button" class="btn mk-pub-kind" data-kind="' + k[0] + '">' + k[1] + "</button>");
      });
    h.push("</div>");
    el.pubBody.innerHTML = h.join("");
    var btns = el.pubBody.querySelectorAll(".mk-pub-kind");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        renderObjectStep(this.getAttribute("data-kind"));
      });
    }
  }

  function renderObjectStep(kind) {
    el.pubBody.innerHTML = '<p class="sub">加载中…</p>';
    var url = kind === "character" ? "/api/characters"
      : kind === "world" ? "/api/worlds" : "/api/sessions";
    fetchJSON(url).then(function (data) {
      var list = kind === "character" ? data.characters
        : kind === "world" ? data.worlds : data.sessions;
      list = list || [];
      if (!list.length) {
        el.pubBody.innerHTML = '<p class="sub">你还没有可发布的' + KIND_LABEL[kind] + "</p>";
        return;
      }
      var h = ['<p class="sub">选择要发布的' + KIND_LABEL[kind] + "</p>"];
      h.push('<div class="mk-pub-list">');
      list.forEach(function (o) {
        h.push('<button type="button" class="mk-pub-item" data-id="' + o.id + '">');
        var avatar = o.avatar || "";
        if (avatar) {
          h.push('<img src="' + escapeHtml(assetUrl(avatar)) + '" alt="" onerror="this.style.display=\'none\'"/>');
        } else {
          h.push('<span class="mk-pub-item-glyph">📦</span>');
        }
        h.push("<span>" + escapeHtml(o.name || o.title || "未命名") + "</span>");
        h.push("</button>");
      });
      h.push("</div>");
      el.pubBody.innerHTML = h.join("");
      var items = el.pubBody.querySelectorAll(".mk-pub-item");
      for (var i = 0; i < items.length; i++) {
        items[i].addEventListener("click", function () {
          pickObject(kind, { id: this.getAttribute("data-id") });
        });
      }
    }).catch(function (err) {
      el.pubBody.innerHTML = '<p class="sub">加载失败：' + escapeHtml(err.message) + "</p>";
    });
  }

  function findObject(kind, id, cb) {
    var url = kind === "character" ? "/api/characters"
      : kind === "world" ? "/api/worlds" : "/api/sessions";
    fetchJSON(url).then(function (data) {
      var list = kind === "character" ? data.characters
        : kind === "world" ? data.worlds : data.sessions;
      var found = null;
      for (var i = 0; i < (list || []).length; i++) {
        if (list[i].id === id) { found = list[i]; break; }
      }
      cb(found);
    }).catch(function () { cb(null); });
  }

  function pickObject(kind, ref) {
    findObject(kind, ref.id, function (o) {
      if (!o) {
        showToast("对象不存在，无法发布", "err");
        return;
      }
      if (!el.pubDialog.open) el.pubDialog.showModal();
      renderFormStep(kind, o, null);
    });
  }

  function formHtml(prefill) {
    var h = [];
    h.push('<label class="mk-field"><span>标题</span>');
    h.push('<input type="text" id="mk-f-title" maxlength="60" value="' +
      escapeHtml(prefill.title) + '"/></label>');
    h.push('<label class="mk-field"><span>简介</span>');
    h.push('<textarea id="mk-f-intro" rows="4" maxlength="500" placeholder="介绍一下它吧（可选）">' +
      escapeHtml(prefill.intro) + "</textarea></label>");
    h.push('<label class="mk-field"><span>封面图地址（可选）</span>');
    h.push('<input type="text" id="mk-f-cover" value="' + escapeHtml(prefill.cover) +
      '" placeholder="/uploads/... 或完整 URL"/></label>');
    h.push('<label class="mk-field"><span>最低等级（0 = 所有人可见）</span>');
    h.push('<input type="number" id="mk-f-level" min="0" max="256" step="1" value="' +
      prefill.min_level + '"/></label>');
    return h.join("");
  }

  function renderFormStep(kind, o, editing) {
    var prefill = editing || {
      title: o.name || o.title || "",
      intro: "",
      cover: o.avatar || "",
      min_level: 0
    };
    el.pubTitle.textContent = editing ? "编辑发布信息" : "发布「" + (o.name || o.title) + "」";
    var h = [formHtml(prefill)];
    h.push('<div class="dialog-actions">');
    h.push('<button type="button" class="btn" id="mk-f-cancel">' + (editing ? "取消" : "返回") + "</button>");
    h.push('<button type="button" class="btn primary" id="mk-f-submit">' +
      (editing ? "保存" : "发布") + "</button>");
    h.push("</div>");
    el.pubBody.innerHTML = h.join("");

    document.getElementById("mk-f-cancel").addEventListener("click", function () {
      if (editing) { el.pubDialog.close(); return; }
      renderObjectStep(kind);
    });
    document.getElementById("mk-f-submit").addEventListener("click", function () {
      var body = {
        title: document.getElementById("mk-f-title").value,
        intro: document.getElementById("mk-f-intro").value,
        cover: document.getElementById("mk-f-cover").value,
        min_level: document.getElementById("mk-f-level").value
      };
      if (editing) submitEdit(editing._id, body);
      else submitPublish(kind, o.id, body, this);
    });
  }

  function submitPublish(kind, sourceId, body, btn) {
    body.kind = kind;
    body.source_id = sourceId;
    btn.disabled = true;
    fetchJSON("/api/market/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () {
      showToast("已发布", "ok");
      el.pubDialog.close();
      // 跳到「我发布的」让用户看到结果
      var all = document.querySelectorAll(".mk-tab");
      for (var i = 0; i < all.length; i++) all[i].classList.remove("active");
      var mineTab = document.querySelector('.mk-tab[data-kind="mine"]');
      mineTab.classList.add("active");
      tab = "mine";
      loadList();
    }).catch(function (err) {
      btn.disabled = false;
      showToast("发布失败：" + err.message, "err");
    });
  }

  /* ==== 编辑弹窗 ==== */

  function openEditDialog(e) {
    el.pubTitle.textContent = "编辑发布信息";
    var prefill = {
      _id: e.id,
      title: e.title,
      intro: e.intro,
      cover: e.cover,
      min_level: e.min_level
    };
    var h = [formHtml(prefill)];
    h.push('<div class="dialog-actions">');
    h.push('<button type="button" class="btn" id="mk-f-cancel">取消</button>');
    h.push('<button type="button" class="btn primary" id="mk-f-submit">保存</button>');
    h.push("</div>");
    el.pubBody.innerHTML = h.join("");
    if (!el.pubDialog.open) el.pubDialog.showModal();

    document.getElementById("mk-f-cancel").addEventListener("click", function () {
      el.pubDialog.close();
    });
    document.getElementById("mk-f-submit").addEventListener("click", function () {
      submitEdit(e.id, {
        title: document.getElementById("mk-f-title").value,
        intro: document.getElementById("mk-f-intro").value,
        cover: document.getElementById("mk-f-cover").value,
        min_level: document.getElementById("mk-f-level").value
      }, this);
    });
  }

  function submitEdit(mid, body, btn) {
    btn.disabled = true;
    fetchJSON("/api/market/entries/" + mid, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () {
      showToast("已保存", "ok");
      el.pubDialog.close();
      loadList();
    }).catch(function (err) {
      btn.disabled = false;
      showToast("保存失败：" + err.message, "err");
    });
  }

  return {
    enter: enterMarket,
    exit: exitMarket,
    closeDetail: closeDetail,
    openPublish: openPublishDialog
  };
})();

function enterMarket() { Market.enter(); }
function exitMarket() { Market.exit(); }
function closeMkDetail() { Market.closeDetail(); }

/* 来自卡 / 世界观 / 会话的上下文菜单：关掉开着的弹窗，进入市场并打开发布弹窗 */
function publishToMarket(kind, sourceId) {
  ["lore-dialog", "card-detail-dialog"].forEach(function (id) {
    var d = document.getElementById(id);
    if (d && d.open) d.close();
  });
  var moreMenu = document.getElementById("more-menu");
  if (moreMenu) moreMenu.classList.add("hidden");
  Market.enter();
  Market.openPublish(kind, sourceId);
}
