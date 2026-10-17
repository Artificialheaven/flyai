/* 记忆管理弹窗：按角色卡·房间两级浏览结构化记忆、删除、统计、整理/召回记录。 */

(function () {
  var mm = {
    dlg: document.getElementById("memory-mgr-dialog"),
    stats: document.getElementById("memory-stats"),
    search: document.getElementById("memory-search"),
    showSuperseded: document.getElementById("memory-show-superseded"),
    btnRefresh: document.getElementById("btn-memory-refresh"),
    list: document.getElementById("memory-entry-list"),
    sourceList: document.getElementById("memory-source-list"),
    sourceView: document.getElementById("memory-source-view"),
    drillView: document.getElementById("memory-drill-view"),
    btnBack: document.getElementById("btn-memory-back"),
    drillTitle: document.getElementById("memory-drill-title"),
    logsPane: document.getElementById("memory-recall-logs"),
    logsTabPane: document.getElementById("memory-logs-pane"),
    entriesTabPane: document.getElementById("memory-entries-pane"),
    btnClose: document.getElementById("btn-memory-mgr-close"),
    msg: document.getElementById("memory-mgr-msg"),
    drillChar: null,
    drillName: "",
    sources: [],
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

  function shortId(cid) {
    return cid.length > 10 ? cid.slice(0, 10) + "…" : cid;
  }

  // ---- 统计（chips 可点击钻取）----
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
          html += '<span class="mem-chip mem-chip-link" data-char="{0}" title="点击查看该卡的记忆">{1}: {2}</span>'
            .replace("{0}", esc(p.char_id))
            .replace("{1}", esc(shortId(p.char_id)))
            .replace("{2}", p.active);
        });
        html += "</div>";
      }
      mm.stats.innerHTML = html;
    }).catch(function () { mm.stats.textContent = "统计加载失败"; });
  }

  mm.stats && mm.stats.addEventListener("click", function (e) {
    var chip = e.target.closest(".mem-chip-link");
    if (!chip) return;
    var cid = chip.getAttribute("data-char");
    var known = (mm.sources || []).filter(function (s) { return s.char_id === cid; });
    var name = known.length ? known[0].char_name : "";
    enterDrill(cid, name || shortId(cid));
  });

  // ---- 来源两级列表 ----
  function sourceName(s) {
    if (s.orphan || !s.char_name) return "未挂载的记忆簿（" + shortId(s.char_id) + "）";
    return s.char_name;
  }

  function loadSources() {
    if (!mm.sourceList) return;
    mm.sourceList.textContent = "加载中…";
    fetchJSON("/api/my/memory/sources").then(function (data) {
      mm.sources = data.sources || [];
      if (!mm.sources.length) {
        mm.sourceList.innerHTML = '<div class="mem-empty">还没有任何记忆条目。多聊几轮后，自动整理会把重要事实写进这里。</div>';
        return;
      }
      mm.sourceList.innerHTML = mm.sources.map(function (s) {
        var room = s.orphan ? "当前没有房间引用这张卡" : (s.session_title || "未命名会话");
        var meta = [];
        meta.push("有效 " + (s.active || 0));
        if (s.superseded) meta.push("失效 " + s.superseded);
        if (s.ledger) meta.push("台账 " + s.ledger);
        return '<div class="mem-row mem-source" data-id="{0}" data-name="{1}">'
          .replace("{0}", esc(s.char_id)).replace("{1}", esc(sourceName(s)))
          + '<div class="mem-row-head"><span class="mem-row-meta">'
          + esc(sourceName(s) + " · " + room) + "</span>"
          + '<span class="mem-row-meta">' + esc(meta.join(" · ")) + "</span></div>"
          + "</div>";
      }).join("");
    }).catch(function (e) {
      mm.sourceList.innerHTML = '<div class="mem-empty">加载失败: ' + esc(e.message) + "</div>";
    });
  }

  mm.sourceList && mm.sourceList.addEventListener("click", function (e) {
    var row = e.target.closest(".mem-source");
    if (!row) return;
    enterDrill(row.getAttribute("data-id"), row.getAttribute("data-name"));
  });

  function showSources() {
    mm.drillChar = null;
    mm.drillName = "";
    if (mm.sourceView) mm.sourceView.classList.remove("hidden");
    if (mm.drillView) mm.drillView.classList.add("hidden");
    loadStats();
    loadSources();
  }

  function enterDrill(charId, name) {
    mm.drillChar = charId;
    mm.drillName = name || shortId(charId);
    if (mm.sourceView) mm.sourceView.classList.add("hidden");
    if (mm.drillView) mm.drillView.classList.remove("hidden");
    if (mm.drillTitle) mm.drillTitle.textContent = mm.drillName;
    if (mm.search) mm.search.value = "";
    if (mm.showSuperseded) mm.showSuperseded.checked = false;
    loadEntries();
  }

  mm.btnBack && mm.btnBack.addEventListener("click", showSources);

  // ---- 某张卡的条目列表 ----
  var searchTimer = null;

  function loadEntries() {
    if (!mm.list || !mm.drillChar) return;
    mm.list.textContent = "加载中…";
    var params = new URLSearchParams();
    params.set("char_id", mm.drillChar);
    if (mm.search && mm.search.value.trim()) params.set("q", mm.search.value.trim());
    if (mm.showSuperseded && mm.showSuperseded.checked) params.set("include_superseded", "true");
    fetchJSON("/api/my/memory?" + params.toString()).then(function (data) {
      var entries = data.entries || [];
      if (!entries.length) {
        mm.list.innerHTML = '<div class="mem-empty">这张卡还没有符合条件的记忆条目。</div>';
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

  function refreshCurrent() {
    loadStats();
    if (mm.drillChar) loadEntries(); else loadSources();
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
      refreshCurrent();
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
  mm.btnRefresh && mm.btnRefresh.addEventListener("click", refreshCurrent);
  mm.btnClose && mm.btnClose.addEventListener("click", function () { mm.dlg.close(); });

  // ---- 整理 / 召回记录 ----
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
        mm.logsPane.innerHTML = '<div class="mem-empty">暂无记录。</div>';
        return;
      }
      mm.logsPane.innerHTML = logs.map(function (l) {
        var kind, topic;
        if (l.kind === "extract") {
          kind = "自动整理";
          topic = "新增 " + (l.added || 0) + " · 更新 " + (l.updated || 0)
            + (l.ledger ? " · 台账 " + l.ledger : "");
        } else if (l.kind === "auto") {
          kind = "自动回忆";
          topic = (l.topic || "") + "（" + (l.hits || 0) + " 条）";
        } else {
          kind = "主动查询";
          topic = (l.topic || "(最近)") + "（" + (l.hits || 0) + " 条）";
        }
        return '<div class="mem-log-row"><span class="mem-log-ts">' + esc(fmtTime(l.ts))
          + '</span><span class="mem-log-kind">' + esc(kind) + "</span>"
          + '<span class="mem-log-topic">' + esc(topic) + "</span></div>";
      }).join("");
    }).catch(function () { mm.logsPane.textContent = "记录加载失败"; });
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
    if (showEntries) refreshCurrent(); else loadLogs();
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
    showSources();
  };
})();
