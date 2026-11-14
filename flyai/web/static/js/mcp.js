/* FlyAI 前端 · MCP 生态 / 外部触发 / watcher / 提示词分区管理（P4）。
 *
 * 页面入口 data-goto="mcp"（管理员）。数据接口 /api/agentdb/*，全部 admin 门禁；
 * 令牌只写不回显（后端只下发 token_set），本页任何渲染都不接触令牌明文。
 * 风格对齐 proactor.js：IIFE + 事件委托 + fetchJSON，ES5 语法。
 */
(function () {
  "use strict";

  var API = "/api/agentdb";
  var STATUS_LABEL = { new: "未测试", ready: "就绪", failed: "失败" };

  var serverList = document.getElementById("mcp-server-list");
  var serverMsg = document.getElementById("mcp-msg");
  var promptList = document.getElementById("prompt-list");
  var promptMsg = document.getElementById("prompt-msg");
  var watcherList = document.getElementById("watcher-list");
  var watcherMsg = document.getElementById("watcher-msg");
  var dlg = document.getElementById("mcp-server-dialog");
  var editingId = null;
  var loaded = false;

  if (!serverList) return;  // 登录页等无设置弹窗的场景

  function setMsg(bar, text, kind) {
    if (!bar) return;
    bar.textContent = text || "";
    bar.className = "mcp-msg" + (kind ? " " + kind : "");
  }

  function get(path) { return fetchJSON(API + path); }
  function post(path, body) {
    return fetchJSON(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }
  function put(path, body) {
    return fetchJSON(API + path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  }
  function del(path) {
    return fetchJSON(API + path, { method: "DELETE" });
  }

  /* ---------------- MCP server 列表 ---------------- */

  function loadServers() {
    return get("/mcp/servers").then(function (r) {
      var rows = r.items || [];
      if (!rows.length) {
        serverList.innerHTML = '<div class="mem-empty">还没有登记 MCP server。</div>';
        return;
      }
      serverList.innerHTML = rows.map(function (s) {
        var enabled = s.enabled === true || s.enabled === 1;
        var status = STATUS_LABEL[s.status] || s.status;
        var statusCls = "mcp-badge " + (s.status === "ready" ? "ok"
          : s.status === "failed" ? "err" : "dim");
        var toolNames = (s.tools || []).map(function (t) { return t.name; });
        var toolsTip = toolNames.length
          ? escapeHtml(toolNames.join(", ")) : "尚无缓存，先点测试连接";
        var errLine = s.last_error
          ? '<div class="mcp-err" title="' + escapeAttr(s.last_error) + '">'
            + escapeHtml(s.last_error.slice(0, 120)) + "</div>" : "";
        return '<div class="mem-row mcp-row" data-id="' + escapeAttr(s.server_id) + '">'
          + '<div class="mem-row-head">'
          + "<strong>" + escapeHtml(s.server_id) + "</strong>"
          + '<span class="' + statusCls + '">' + escapeHtml(status) + "</span>"
          + "</div>"
          + '<div class="mem-row-meta">' + escapeHtml(s.command)
          + " · 参数 " + (s.args || []).length + " 项 · 环境 "
          + Object.keys(s.env || {}).length + " 项 · "
          + (enabled ? "已启用" : "已停用") + "</div>"
          + '<div class="mem-row-meta" title="' + toolsTip + '">缓存工具 '
          + toolNames.length + " 个" + (toolNames.length
            ? "：" + escapeHtml(toolNames.slice(0, 6).join(", "))
                  + (toolNames.length > 6 ? " …" : "")
            : "") + "</div>"
          + errLine
          + '<div class="mcp-actions">'
          + '<button type="button" class="btn small" data-act="mcp-test">测试连接</button>'
          + '<button type="button" class="btn small" data-act="mcp-edit">编辑</button>'
          + '<button type="button" class="btn small" data-act="mcp-toggle">'
          + (enabled ? "停用" : "启用") + "</button>"
          + '<button type="button" class="btn small danger" data-act="mcp-del">删除</button>'
          + "</div></div>";
      }).join("");
    }).catch(function (e) {
      serverList.innerHTML = '<div class="mem-empty">加载失败: '
        + escapeHtml(e.message) + "</div>";
    });
  }

  function openServerDialog(row) {
    editingId = row ? row.server_id : null;
    $("mcp-dlg-title").textContent = row ? "编辑 MCP server" : "新增 MCP server";
    var idInput = $("mcp-dlg-id");
    idInput.value = row ? row.server_id : "";
    idInput.disabled = !!row;  // id 是主键，编辑时不改
    $("mcp-dlg-command").value = row ? row.command : "";
    $("mcp-dlg-args").value = row ? JSON.stringify(row.args || [], null, 2) : "[]";
    $("mcp-dlg-env").value = row ? JSON.stringify(row.env || {}, null, 2) : "{}";
    $("mcp-dlg-enabled").checked = row ? (row.enabled === true || row.enabled === 1) : true;
    setMsg($("mcp-dlg-msg"), "");
    if (typeof dlg.showModal === "function") dlg.showModal();
    else dlg.setAttribute("open", "");
  }

  function closeServerDialog() {
    if (typeof dlg.close === "function") dlg.close();
    else dlg.removeAttribute("open");
  }

  function parseJsonField(id, expectType, label) {
    var raw = $(id).value.trim();
    if (!raw) return expectType === "array" ? [] : {};
    var obj;
    try { obj = JSON.parse(raw); }
    catch (e) { throw new Error(label + " 不是合法 JSON: " + e.message); }
    if (expectType === "array" && !Array.isArray(obj)) {
      throw new Error(label + " 必须是 JSON 数组");
    }
    if (expectType === "object" && (Array.isArray(obj) || obj === null
        || typeof obj !== "object")) {
      throw new Error(label + " 必须是 JSON 对象");
    }
    return obj;
  }

  function saveServerDialog() {
    var sid = $("mcp-dlg-id").value.trim();
    var command = $("mcp-dlg-command").value.trim();
    if (!sid) { setMsg($("mcp-dlg-msg"), "server_id 不能为空", "err"); return; }
    if (!/^[a-z][a-z0-9_]{0,31}$/.test(sid)) {
      setMsg($("mcp-dlg-msg"),
        "server_id 必须匹配 ^[a-z][a-z0-9_]{0,31}$", "err");
      return;
    }
    if (!command) { setMsg($("mcp-dlg-msg"), "启动命令不能为空", "err"); return; }
    var args, env;
    try {
      args = parseJsonField("mcp-dlg-args", "array", "参数");
      env = parseJsonField("mcp-dlg-env", "object", "环境变量");
    } catch (e) {
      setMsg($("mcp-dlg-msg"), e.message, "err");
      return;
    }
    var enabled = $("mcp-dlg-enabled").checked;
    var req = editingId
      ? put("/mcp/servers/" + encodeURIComponent(editingId),
            { command: command, args: args, env: env, enabled: enabled })
      : post("/mcp/servers", {
          server_id: sid, command: command, args: args,
          env: env, enabled: enabled,
        });
    req.then(function () {
      closeServerDialog();
      setMsg(serverMsg, editingId ? "已保存改动" : "server 已登记", "ok");
      loadServers();
    }).catch(function (e) {
      setMsg($("mcp-dlg-msg"), "保存失败: " + e.message, "err");
    });
  }

  function testServer(sid, btn) {
    var old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "测试中…";
    post("/mcp/servers/" + encodeURIComponent(sid) + "/test", {})
      .then(function (r) {
        setMsg(serverMsg, sid + " 连接成功，发现 " + (r.tools || []).length
          + " 个工具", "ok");
        loadServers();
      }).catch(function (e) {
        setMsg(serverMsg, sid + " 连接失败: " + e.message, "err");
        loadServers();
      }).then(function () {
        btn.disabled = false;
        btn.textContent = old;
      });
  }

  function toggleServer(rowEl) {
    var sid = rowEl.getAttribute("data-id");
    var row = null;
    // 从当前渲染反查启用态
    var meta = rowEl.querySelector(".mem-row-meta").textContent;
    var enableNow = meta.indexOf("已停用") >= 0;
    post("/mcp/servers/" + encodeURIComponent(sid) + "/enabled",
      { enabled: enableNow })
      .then(function () {
        setMsg(serverMsg, sid + (enableNow ? " 已启用" : " 已停用"), "ok");
        loadServers();
      }).catch(function (e) {
        setMsg(serverMsg, "操作失败: " + e.message, "err");
      });
  }

  function removeServer(sid) {
    if (!window.confirm("确认删除 MCP server「" + sid + "」？缓存工具清单将一并清除。")) {
      return;
    }
    del("/mcp/servers/" + encodeURIComponent(sid)).then(function () {
      setMsg(serverMsg, sid + " 已删除", "ok");
      loadServers();
    }).catch(function (e) {
      setMsg(serverMsg, "删除失败: " + e.message, "err");
    });
  }

  /* ---------------- 提示词分区 ---------------- */

  function loadPrompts() {
    return get("/prompts").then(function (r) {
      var items = r.items || [];
      promptList.innerHTML = items.map(function (p) {
        var chips = (p.placeholders || []).map(function (ph) {
          return '<span class="mcp-chip">{' + escapeHtml(ph) + "}</span>";
        }).join(" ");
        var current = p.override || p.default;
        return '<div class="mem-row prompt-card" data-key="'
          + escapeAttr(p.key) + '">'
          + '<div class="mem-row-head"><strong>' + escapeHtml(p.key)
          + (p.override ? ' <span class="mcp-badge ok">已覆盖</span>' : "")
          + "</strong></div>"
          + '<div class="mem-row-meta">' + escapeHtml(p.desc) + "</div>"
          + '<div class="mcp-chips">可用占位符：' + (chips || "无") + "</div>"
          + '<textarea class="prompt-text" rows="4">'
          + escapeHtml(current) + "</textarea>"
          + '<details class="prompt-default"><summary>查看内置默认</summary>'
          + '<div class="mem-row-meta">' + escapeHtml(p.default) + "</div>"
          + "</details>"
          + '<div class="mcp-actions">'
          + '<button type="button" class="btn small" data-act="prompt-save">保存覆盖</button>'
          + '<button type="button" class="btn small" data-act="prompt-reset">恢复默认</button>'
          + "</div></div>";
      }).join("");
    }).catch(function (e) {
      promptList.innerHTML = '<div class="mem-empty">加载失败: '
        + escapeHtml(e.message) + "</div>";
    });
  }

  function savePrompt(card) {
    var key = card.getAttribute("data-key");
    var text = card.querySelector(".prompt-text").value;
    if (!text.trim()) {
      setMsg(promptMsg, "覆盖文本不能为空；要恢复默认请点「恢复默认」", "err");
      return;
    }
    put("/prompts/" + encodeURIComponent(key), { text: text })
      .then(function () {
        setMsg(promptMsg, key + " 覆盖已保存并即时生效", "ok");
        loadPrompts();
      }).catch(function (e) {
        setMsg(promptMsg, "保存失败: " + e.message, "err");
      });
  }

  function resetPrompt(card) {
    var key = card.getAttribute("data-key");
    del("/prompts/" + encodeURIComponent(key)).then(function (r) {
      setMsg(promptMsg, r.reset ? key + " 已恢复内置默认" : key + " 本就没有覆盖",
        "ok");
      loadPrompts();
    }).catch(function (e) {
      setMsg(promptMsg, "恢复失败: " + e.message, "err");
    });
  }

  /* ---------------- watcher 进程 ---------------- */

  function loadWatchers() {
    return get("/watchers").then(function (r) {
      var rows = r.items || [];
      if (!rows.length) {
        watcherList.innerHTML = '<div class="mem-empty">当前没有 watcher 登记。</div>';
        return;
      }
      watcherList.innerHTML = rows.map(function (w) {
        var alive = w.status === "running" && w.alive;
        var badge = w.status !== "running"
          ? '<span class="mcp-badge dim">已停止</span>'
          : alive ? '<span class="mcp-badge ok">存活</span>'
          : '<span class="mcp-badge err">进程已死</span>';
        return '<div class="mem-row mcp-row" data-id="' + w.id + '">'
          + '<div class="mem-row-head"><strong>#' + w.id + " "
          + escapeHtml(w.source || "watcher") + "</strong>" + badge + "</div>"
          + '<div class="mem-row-meta">会话 '
          + escapeHtml(w.session_id) + " · pid=" + escapeHtml(String(w.pid))
          + "</div>"
          + '<div class="mem-row-meta" title="'
          + escapeAttr(w.script_path || "") + '">'
          + escapeHtml(w.script_path || "") + "</div>"
          + '<div class="mcp-actions">'
          + '<button type="button" class="btn small danger" data-act="w-stop"'
          + (w.status !== "running" ? " disabled" : "") + ">停止</button>"
          + "</div></div>";
      }).join("");
    }).catch(function (e) {
      watcherList.innerHTML = '<div class="mem-empty">加载失败: '
        + escapeHtml(e.message) + "</div>";
    });
  }

  function stopWatcher(id) {
    post("/watchers/stop", { id: parseInt(id, 10) })
      .then(function () {
        setMsg(watcherMsg, "watcher #" + id + " 已停止", "ok");
        loadWatchers();
      }).catch(function (e) {
        setMsg(watcherMsg, "停止失败: " + e.message, "err");
      });
  }

  function reconcileWatchers(btn) {
    var old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "探活中…";
    post("/watchers/reconcile", {})
      .then(function (r) {
        setMsg(watcherMsg, "探活完成，标记 " + r.stopped + " 个死亡进程为 stopped",
          "ok");
        loadWatchers();
      }).catch(function (e) {
        setMsg(watcherMsg, "探活失败: " + e.message, "err");
      }).then(function () {
        btn.disabled = false;
        btn.textContent = old;
      });
  }

  /* ---------------- 事件绑定 ---------------- */

  function $(id) { return document.getElementById(id); }

  $("btn-mcp-add").addEventListener("click", function () {
    openServerDialog(null);
  });
  $("btn-mcp-dlg-save").addEventListener("click", saveServerDialog);
  $("btn-mcp-dlg-cancel").addEventListener("click", closeServerDialog);

  serverList.addEventListener("click", function (e) {
    var actEl = e.target.closest("[data-act]");
    if (!actEl) return;
    var rowEl = e.target.closest(".mcp-row");
    if (!rowEl) return;
    var act = actEl.getAttribute("data-act");
    var sid = rowEl.getAttribute("data-id");
    if (act === "mcp-test") testServer(sid, actEl);
    else if (act === "mcp-edit") {
      get("/mcp/servers").then(function (r) {
        var row = (r.items || []).filter(function (x) {
          return x.server_id === sid;
        })[0];
        if (row) openServerDialog(row);
      });
    } else if (act === "mcp-toggle") toggleServer(rowEl);
    else if (act === "mcp-del") removeServer(sid);
  });

  promptList.addEventListener("click", function (e) {
    var actEl = e.target.closest("[data-act]");
    if (!actEl) return;
    var card = e.target.closest(".prompt-card");
    if (!card) return;
    if (actEl.getAttribute("data-act") === "prompt-save") savePrompt(card);
    else resetPrompt(card);
  });

  watcherList.addEventListener("click", function (e) {
    var actEl = e.target.closest('[data-act="w-stop"]');
    if (!actEl) return;
    stopWatcher(e.target.closest(".mcp-row").getAttribute("data-id"));
  });
  $("btn-watcher-refresh").addEventListener("click", loadWatchers);
  $("btn-watcher-reconcile").addEventListener("click", function () {
    reconcileWatchers(this);
  });

  function refreshAll() {
    loadServers();
    loadPrompts();
    loadWatchers();
  }
  window.refreshMcpEcosystem = function () {
    if (loaded) refreshAll();
  };

  // 进入该分页时拉取数据（与 ui.js 的 data-goto 委托并存，互不干扰）
  var settingsDialog = document.getElementById("settings-dialog");
  settingsDialog.addEventListener("click", function (e) {
    var gotoEl = e.target.closest('[data-goto="mcp"]');
    if (!gotoEl) return;
    loaded = true;
    setMsg(serverMsg, "");
    setMsg(promptMsg, "");
    setMsg(watcherMsg, "");
    refreshAll();
  });
})();
