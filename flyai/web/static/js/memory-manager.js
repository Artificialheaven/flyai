/* 记忆管理弹窗：结构化记忆条目浏览/删除、统计、召回日志（P6）。 */

(function () {
  var mm = {
    dlg: document.getElementById("memory-mgr-dialog"),
    stats: document.getElementById("memory-stats"),
    search: document.getElementById("memory-search"),
    showSuperseded: document.getElementById("memory-show-superseded"),
    btnRefresh: document.getElementById("btn-memory-refresh"),
    list: document.getElementById("memory-entry-list"),
    logsPane: document.getElementById("memory-logs"),
    entriesPane: document.getElementById("memory-entries-pane"),
    logsTabPane: document.getElementById("memory-logs-pane"),
    entriesTabPane: document.getElementById("memory-entries-pane"),
    btnClose: document.getElementById("btn-memory-mgr-close"),
    msg: document.getElementById("memory-mgr-msg"),
  };

  function setMsg(text, kind) {
    if (!mm.msg) return;
    mm.msg.textContent = text || "";
    mm.msg.className = "msg-bar" + (kind ? " " + kind : "");
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function sourceLabel(source) {
    if (source === "extract") return "整理";
    if (source === "snapshot") return "存档";
    return "记忆";
  }

  // ---- 统计 ----
  function loadStats() {
    if (!mm.stats) return;
    mm.stats.textContent = "加载中…";
    fetchJSON("/api/my/memory/stats").then(function (data) {
      var t = data.total || {};
      var html = '<span class="mem-stat">有效条目 <b>{0}</b></span>'
        .replace("{0}", t.active || 0)
        + '<span class="mem-stat">已失效 <b>{0}</b></span>'.replace("{0}", t.superseded || 0)
        + '<span class="mem-stat">台账条目 <b>{0}</b></span>'.replace("{0}", t.ledger || 0);
      if ((data.per_char || []).length > 1) {
        html += '<div class="mem-per-char">';
        (data.per_char || []).forEach(function (p) {
          html += '<span class="mem-chip" title="{0}">{1}: {2}</span>'
            .replace("{0}", esc(p.char_id))
            .replace("{1}", esc(p.char_id.length > 8 ? p.char_id.slice(0, 8) + "…" : p.char_id))
            .replace("{2}", p.active);
        });
        html += "</div>";
      }
      mm.stats.innerHTML = html;
    }).catch(function () { mm.stats.textContent = "统计加载失败"; });
  }

  // ---- 条目列表 ----
  var searchTimer = null;

  function loadEntries() {
    if (!mm.list) return;
    mm.list.textContent = "加载中…";
    var params = new URLSearchParams();
    if (mm.search && mm.search.value.trim()) params.set("q", mm.search.value.trim());
    if (mm.showSuperseded && mm.showSuperseded.checked) params.set("include_superseded", "true");
    var url = "/api/my/memory" + (params.toString() ? "?" + params.toString() : "");
    fetchJSON(url).then(function (data) {
      var entries = data.entries || [];
      if (!entries.length) {
        mm.list.innerHTML = '<div class="mem-empty">没有符合条件的记忆条目。</div>';
        return;
      }
      mm.list.innerHTML = entries.map(function (e) {
        var cls = "mem-row" + (e.superseded ? " superseded" : "");
        var meta = [];
        meta.push(sourceLabel(e.source));
        if (e.created_at) meta.push(e.created_at);
        if (e.entities && e.entities.length) {
          meta.push(e.entities.slice(0, 4).map(esc).join("/"));
        }
        return '<div class="{0}" data-id="{1}" data-char="{2}">'
          .replace("{0}", cls).replace("{1}", esc(e.id)).replace("{2}", esc(e.char_id))
          + '<div class="mem-row-head"><span class="mem-row-meta">'
          + esc(meta.join(" · ")) + "</span>"
          + '<button type="button" class="mem-btn-del btn small danger">删除</button></div>'
          + '<div class="mem-row-text">' + esc(e.text) + "</div>"
          + "</div>";
      }).join("");
    }).catch(function (e) {
      mm.list.innerHTML = '<div class="mem-empty">加载失败: ' + esc(e.message) + "</div>";
    });
  }

  function refreshAll() {
    loadStats();
    loadEntries();
  }

  mm.list && mm.list.addEventListener("click", function (e) {
    var btn = e.target.closest(".mem-btn-del");
    if (!btn) return;
    var row = e.target.closest(".mem-row");
    if (!row) return;
    var id = row.getAttribute("data-id");
    var charId = row.getAttribute("data-char");
    if (!window.confirm("确认删除这条记忆？")) return;
    var url = "/api/my/memory/" + encodeURIComponent(id)
      + "?char_id=" + encodeURIComponent(charId);
    fetchJSON(url, { method: "DELETE" }).then(function () {
      showToast("记忆已删除", "ok");
      refreshAll();
    }).catch(function (err) {
      setMsg("删除失败: " + err.message, "err");
    });
  });

  if (mm.search) {
    mm.search.addEventListener("input", function () {
      if (searchTimer) clearTimeout(searchTimer);
      searchTimer = setTimeout(loadEntries, 300);
    });
  }
  mm.showSuperseded && mm.showSuperseded.addEventListener("change", loadEntries);
  mm.btnRefresh && mm.btnRefresh.addEventListener("click", refreshAll);
  mm.btnClose && mm.btnClose.addEventListener("click", function () { mm.dlg.close(); });

  // ---- 召回日志 ----
  function fmtTime(ts) {
    var d = new Date(ts * 1000);
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate())
      + " " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function loadLogs() {
    if (!mm.logsPane) return;
    mm.logsPane.textContent = "加载中…";
    fetchJSON("/api/my/memory/recall-logs").then(function (data) {
      var logs = data.logs || [];
      if (!logs.length) {
        mm.logsPane.innerHTML = '<div class="mem-empty">暂无召回记录。</div>';
        return;
      }
      mm.logsPane.innerHTML = logs.map(function (l) {
        var kind = l.kind === "auto" ? "自动回忆" : "主动查询";
        return '<div class="mem-log-row"><span class="mem-log-ts">' + esc(fmtTime(l.ts))
          + '</span><span class="mem-log-kind">' + esc(kind) + "</span>"
          + '<span class="mem-log-topic">' + esc(l.topic || "") + "</span>"
          + '<span class="mem-log-hits">' + (l.hits || 0) + " 条</span></div>";
      }).join("");
    }).catch(function () { mm.logsPane.textContent = "日志加载失败"; });
  }

  // ---- 页签 ----
  function switchTab(name) {
    var showEntries = name === "entries";
    mm.entriesTabPane.classList.toggle("hidden", !showEntries);
    mm.logsTabPane.classList.toggle("hidden", showEntries);
    var tabs = mm.dlg.querySelectorAll(".mem-tab");
    tabs.forEach(function (t) {
      t.classList.toggle("active", t.getAttribute("data-mtab") === name);
    });
    if (showEntries) loadEntries(); else loadLogs();
  }

  mm.dlg.querySelectorAll(".mem-tab").forEach(function (t) {
    t.addEventListener("click", function () {
      switchTab(t.getAttribute("data-mtab"));
    });
  });

  window.openMemoryManager = function () {
    if (!mm.dlg) return;
    setMsg("");
    mm.dlg.showModal();
    switchTab("entries");
    loadStats();
  };
})();
