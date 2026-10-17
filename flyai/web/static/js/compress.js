/* FlyAI 前端 · 导出 / 压缩提案 diff / 副本发布（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* ==== R7：导出 / 压缩 / 会话副本 ==== */
/* 所有前端下载的统一入口：APK 里 a[download] + blob: 不生效（WebView 无 DownloadListener），
   原生平台走 FlySaver 插件存进系统下载目录；浏览器走传统锚点点击。 */
function downloadBlob(blob, filename) {
  if (window.NativeSaver && NativeSaver.isNative()) {
    NativeSaver.saveBlob(blob, filename).then(function (saved) {
      showToast("已保存到「下载」目录：" + saved, "ok", 3500);
    }).catch(function (e) {
      showToast("保存失败: " + e.message, "err", 4000);
    });
    return;
  }
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}
function downloadJSON(obj, filename) {
  var blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  downloadBlob(blob, filename);
}
function downloadConversation() {
  // 自包含 zip：对话 + 立绘 / 背景 / BGM / 已合成语音
  var s = currentSession(); if (!s) return;
  fetch(apiUrl("/api/sessions/" + s.id + "/export-zip"), { headers: authHeaders() })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.blob();
    })
    .then(function (blob) { downloadBlob(blob, "conversation-" + s.id.slice(0, 8) + ".zip"); })
    .catch(function (e) { showToast("导出对话失败: " + e.message, "err"); });
}
var compressReview = null; // {sid, room, pid, scoped}

function _hideBtn(b) { if (b) b.classList.add("hidden"); }
function _showBtn(b) { if (b) b.classList.remove("hidden"); }

function openCompressDialog() {
  var s = currentSession(); if (!s) return;
  compressReview = null;
  el.exportSub.textContent = "AI 正在归纳对话并按标题合并进角色卡与世界观…";
  el.exportBody.innerHTML = '<div class="sub">压缩中，请稍候…</div>';
  el.exportMsg.textContent = "";
  el.exportMsg.className = "msg-bar";
  [el.btnExportApply, el.btnExportDiscard, el.btnExportClose, el.btnExportDownload, el.btnExportPublish, el.btnExportSaveAs].forEach(_hideBtn);
  safeCloseDialogs();
  if (el.exportDialog.showModal) el.exportDialog.showModal();
  fetchJSON("/api/sessions/" + s.id + "/compress", { method: "POST" }).then(function (res) {
    var scoped = !!res.scoped;
    compressReview = { sid: s.id, room: !!res.room, pid: res.proposal_id, scoped: scoped };
    el.exportSub.textContent = scoped
      ? "压缩提案 · 可写入当前会话副本，或另存为角色库中的新副本（不动当前副本），也可直接下载压缩包。库主卡不受影响，漏列的条目会原样保留。"
      : "压缩提案 · 当前使用的是库主卡：确认后会先生成会话专属副本再写入，主卡不受影响；也可以另存为新副本到角色库，或直接下载压缩包。";
    el.exportBody.innerHTML = res.room ? renderRoomCompressDiff(res) : renderSingleCompressDiff(res);
    el.btnExportApply.textContent = scoped ? "✓ 应用到当前副本" : "✓ 应用（生成会话副本）";
    _showBtn(el.btnExportApply);
    _showBtn(el.btnExportSaveAs);
    _showBtn(el.btnExportDownload);
    _showBtn(el.btnExportDiscard);
  }).catch(function (e) {
    el.exportSub.textContent = "压缩失败";
    el.exportBody.innerHTML = '<div class="msg-bar err">' + escapeHtml(e.message) + "</div>";
    _showBtn(el.btnExportClose);
  });
}

function discardCompressProposal() {
  // 整体放弃：提案 30 分钟后自动过期，无需通知后端
  compressReview = null;
  if (el.exportDialog) el.exportDialog.close();
}

function saveCompressAsNewCopy() {
  // 另存为角色库中的新副本：不动当前会话/主卡，提案也不消费，可继续应用或下载
  if (!compressReview) return;
  var review = compressReview;
  el.btnExportSaveAs.disabled = true;
  fetchJSON("/api/sessions/" + review.sid + "/compress/save-as", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proposal_id: review.pid }),
  }).then(function (out) {
    var names = (out.characters || []).map(function (c) { return c.name; }).filter(Boolean);
    showToast("已另存为新副本：" + (names.join("、") || "角色卡")
      + (review.room ? "（世界观不入库，可下载压缩包留存）" : ""), "ok");
  }).catch(function (e) {
    showToast(e.message, "err");
  }).finally(function () {
    el.btnExportSaveAs.disabled = false;
  });
}

function applyCompressProposal() {
  if (!compressReview) return;
  var review = compressReview;
  el.exportSub.textContent = "正在写入…";
  el.exportBody.innerHTML = '<div class="sub">写入会话副本中…</div>';
  el.exportMsg.textContent = "";
  el.exportMsg.className = "msg-bar";
  [el.btnExportApply, el.btnExportDiscard, el.btnExportSaveAs, el.btnExportDownload].forEach(_hideBtn);
  fetchJSON("/api/sessions/" + review.sid + "/compress/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proposal_id: review.pid }),
  }).then(function (out) {
    compressReview = null;
    el.exportSub.textContent = "压缩完成，已写入当前会话副本（库主卡不受影响）。";
    if (review.room) {
      el.exportBody.innerHTML = renderRoomCompressSummary(out.members || {}, out.world);
    } else {
      el.exportBody.innerHTML = renderCompressSummary(out.character, out.world);
      if (out.character && out.character.name) _showBtn(el.btnExportPublish);
    }
    _showBtn(el.btnExportDownload);
    _showBtn(el.btnExportClose);
    // 写入后才刷新：房间成员副本生效，后续对话即可感知压缩设定
    selectSession(review.sid);
  }).catch(function (e) {
    el.exportSub.textContent = "写入失败";
    el.exportMsg.className = "msg-bar err";
    el.exportMsg.textContent = e.message;
    el.exportBody.innerHTML = '<div class="sub">提案未被使用（通常是提案生成后设定又被改过），请关闭后重新点「☁ 压缩」。</div>';
    compressReview = null;
    _showBtn(el.btnExportClose);
    selectSession(review.sid);
  });
}

/* ==== 提案 diff 渲染 ==== */

function renderSingleCompressDiff(res) {
  var h = '<div class="cs-block"><b>📇 角色卡变更</b>' + renderCharacterDiffBody(res.diff.character) + "</div>";
  h += '<div class="cs-block"><b>🌍 世界观变更</b>' + renderWorldDiffBody(res.diff.world) + "</div>";
  return h;
}

function renderRoomCompressDiff(res) {
  var h = '<div class="cs-block"><b>👥 各参与者变更</b>';
  var ids = Object.keys(res.diff.members || {});
  if (!ids.length) h += "<div class='sub'>（无）</div>";
  ids.forEach(function (mid) {
    var d = res.diff.members[mid];
    var seat = (state.roomMembers || []).find(function (m) { return m.id === mid; });
    var afterC = res.after.members[mid] || {};
    var name = (seat && memberDisplayName(seat)) || cardDisplayName(afterC) || mid;
    h += '<details class="member-diff"' + (_charDiffEmpty(d) ? "" : " open") + ">"
      + "<summary><b>" + escapeHtml(name) + "</b><span class='sub'>" + escapeHtml(_charDiffCounts(d)) + "</span></summary>"
      + renderCharacterDiffBody(d) + "</details>";
  });
  h += "</div>";
  h += '<div class="cs-block"><b>🌍 世界观变更</b>' + renderWorldDiffBody(res.diff.world) + "</div>";
  return h;
}

function _charDiffEmpty(d) {
  if (!d) return true;
  return d.intro.before === d.intro.after
    && d.background.before === d.background.after
    && !(d.table.added || []).length
    && !(d.table.changed || []).length;
}

function _charDiffCounts(d) {
  if (_charDiffEmpty(d)) return "无变更";
  var parts = [];
  if (d.intro.before !== d.intro.after) parts.push("简介更新");
  if (d.background.before !== d.background.after) parts.push("经历更新");
  var addN = (d.table.added || []).length;
  var chgN = (d.table.changed || []).length;
  if (addN) parts.push("新增条目 " + addN);
  if (chgN) parts.push("更新条目 " + chgN);
  return parts.join(" · ");
}

function renderCharacterDiffBody(d) {
  if (!d) return "<div class='sub'>（无）</div>";
  var h = "";
  if (d.intro.before !== d.intro.after) {
    h += "<div class='diff-field'><span class='diff-k'>简介</span>" + renderTextDiff(d.intro.before, d.intro.after) + "</div>";
  }
  if (d.background.before !== d.background.after) {
    h += "<div class='diff-field'><span class='diff-k'>经历</span>" + renderTextDiff(d.background.before, d.background.after) + "</div>";
  }
  h += renderTableDiff(d.table);
  if (!h) h = "<div class='sub'>无变更</div>";
  return h;
}

function renderWorldDiffBody(d) {
  if (!d) return "<div class='sub'>（无）</div>";
  var h = "";
  if (d.content.before !== d.content.after) {
    h += "<div class='diff-field'><span class='diff-k'>世界观</span>" + renderTextDiff(d.content.before, d.content.after) + "</div>";
  }
  h += renderTableDiff(d.table);
  if (!h) h = "<div class='sub'>无变更</div>";
  return h;
}

function renderTableDiff(td) {
  if (!td) return "";
  var h = "";
  (td.added || []).forEach(function (e) {
    h += '<div class="diff-run diff-add"><span class="diff-sign">＋</span><b>' + escapeHtml(e.title || "") + "</b>："
      + escapeHtml(e.content || "") + (e.pinned ? pinBadge() : "") + "</div>";
  });
  (td.changed || []).forEach(function (e) {
    h += '<div class="diff-field table-changed"><span class="diff-sign">≣</span><b>' + escapeHtml(e.title || "") + "</b>"
      + (e.pinned ? pinBadge() : "") + renderTextDiff(e.before, e.after) + "</div>";
  });
  var unchanged = td.unchanged || [];
  if (unchanged.length) {
    var pinN = unchanged.filter(function (e) { return e.pinned; }).length;
    var label = unchanged.length + " 条未变" + (pinN ? "（含常驻 " + pinN + " 条）" : "");
    h += '<details class="diff-unchanged"><summary>' + label + "</summary>"
      + unchanged.map(function (e) {
        return '<div class="diff-eq"><b>' + escapeHtml(e.title || "") + "</b>："
          + escapeHtml(e.content || "") + (e.pinned ? pinBadge() : "") + "</div>";
      }).join("") + "</details>";
  }
  if ((td.removed || []).length) {
    h += '<div class="msg-bar err">异常：有 ' + td.removed.length + " 条条目在合并中丢失，请放弃并重试。</div>";
  }
  return h;
}

function pinBadge() {
  return '<span class="pin-badge" title="常驻：无条件注入系统提示词">常驻</span>';
}

/* 行级 LCS：>1000 行时返回 null，调用方退化为前后整块渲染 */
function lineLCS(a, b) {
  if (a.length > 1000 || b.length > 1000) return null;
  var n = a.length, m = b.length;
  var dp = new Array(n + 1);
  for (var i = 0; i <= n; i++) {
    dp[i] = new Array(m + 1).fill(0);
  }
  for (i = n - 1; i >= 0; i--) {
    for (var j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  var ops = [];
  i = 0; j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ t: "eq", x: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ t: "del", x: a[i] }); i++; }
    else { ops.push({ t: "add", x: b[j] }); j++; }
  }
  while (i < n) ops.push({ t: "del", x: a[i++] });
  while (j < m) ops.push({ t: "add", x: b[j++] });
  return ops;
}

function renderTextDiff(before, after) {
  before = before || "";
  after = after || "";
  if (!before && !after) return "";
  var ops = lineLCS(before.split("\n"), after.split("\n"));
  if (ops === null) {
    return '<div class="diff-block"><div class="diff-run diff-del">' + escapeHtml(before) + "</div>"
      + '<div class="diff-run diff-add">' + escapeHtml(after) + "</div></div>";
  }
  var h = '<div class="diff-block">';
  var i = 0;
  while (i < ops.length) {
    var t = ops[i].t;
    var lines = [];
    while (i < ops.length && ops[i].t === t) { lines.push(ops[i].x); i++; }
    h += renderDiffRun(t, lines);
  }
  return h + "</div>";
}

function renderDiffRun(t, lines) {
  if (t === "eq") {
    if (lines.length <= 3) {
      return '<div class="diff-run diff-eq">' + lines.map(_eqLine).join("") + "</div>";
    }
    return '<details class="diff-eq details-eq"><summary>' + lines.length + " 行未变（点击展开）</summary>"
      + lines.map(_eqLine).join("") + "</details>";
  }
  var cls = t === "del" ? "diff-del" : "diff-add";
  var sign = t === "del" ? "−" : "＋";
  var body = lines.map(function (ln) {
    return '<div><span class="diff-sign">' + sign + "</span> " + escapeHtml(ln) + "</div>";
  }).join("");
  return '<div class="diff-run ' + cls + '">' + body + "</div>";
}

function _eqLine(ln) {
  return '<div><span class="diff-sign dim">·</span> ' + escapeHtml(ln) + "</div>";
}
function renderRoomCompressSummary(members, world) {
  var h = '<div class="cs-block"><b>👥 各参与者压缩结果</b>';
  var ids = Object.keys(members);
  if (!ids.length) h += "<div class='sub'>（无）</div>";
  ids.forEach(function (mid) {
    var c = members[mid];
    var seat = (state.roomMembers || []).find(function (m) { return m.id === mid; });
    var name = (seat && memberDisplayName(seat)) || (c && cardDisplayName(c)) || mid;
    h += '<div class="cs-member"><div>' + escapeHtml(name) + "</div>";
    if (c && c.intro) h += "<div class='sub'>简介：" + escapeHtml(c.intro) + "</div>";
    if (c && c.background) h += "<div class='sub'>经历：" + escapeHtml(c.background) + "</div>";
    if (c && c.table && c.table.length) {
      h += "<ul class='cs-list'>" + c.table.map(function (t) {
        return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
      }).join("") + "</ul>";
    }
    h += "</div>";
  });
  h += "</div><div class='cs-block'><b>🌍 压缩后的世界观</b>";
  if (world) {
    if (world.content) h += "<div class='sub'>" + escapeHtml(world.content) + "</div>";
    if (world.table && world.table.length) {
      h += "<ul class='cs-list'>" + world.table.map(function (t) {
        return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
      }).join("") + "</ul>";
    }
  } else h += "<div class='sub'>（无）</div>";
  h += "</div>";
  return h;
}
function renderCompressSummary(char, world) {
  var h = '<div class="cs-block"><b>📇 压缩后的角色卡</b>';
  if (char) {
    h += "<div>" + escapeHtml(char.name) + "</div>";
    if (char.intro) h += "<div class='sub'>简介：" + escapeHtml(char.intro) + "</div>";
    if (char.background) h += "<div class='sub'>经历：" + escapeHtml(char.background) + "</div>";
    if (char.table && char.table.length) h += listEl(char.table);
  } else h += "<div class='sub'>（无）</div>";
  h += "</div><div class='cs-block'><b>🌍 压缩后的世界观</b>";
  if (world) {
    if (world.content) h += "<div class='sub'>" + escapeHtml(world.content) + "</div>";
    if (world.table && world.table.length) h += listEl(world.table);
  } else h += "<div class='sub'>（无）</div>";
  h += "</div>";
  return h;
  function listEl(tbl) {
    return "<ul class='cs-list'>" + tbl.map(function (t) {
      return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
    }).join("") + "</ul>";
  }
}
function publishConversationCharacter() {
  var s = currentSession(); if (!s) return;
  fetchJSON("/api/sessions/" + s.id + "/character/publish", { method: "POST" }).then(function (res) {
    showToast("已保存为角色卡：" + (res.character.name || ""), "ok");
  }).catch(function (e) { showToast(e.message, "err"); });
}
function forkSessionChar() {
  var s = currentSession(); if (!s) return;
  fetchJSON("/api/sessions/" + s.id + "/character/fork", { method: "POST" }).then(function (res) {
    state.scopeCache[s.id] = { char: res.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: true };
    renderSessionMeta();
  }).catch(function (e) { showToast(e.message, "err"); });
}
function restoreSessionChar() {
  var s = currentSession(); if (!s) return;
  if (!confirm("删除会话副本并还原为库主卡？")) return;
  fetchJSON("/api/sessions/" + s.id + "/character/override", { method: "DELETE" }).then(function (res) {
    state.scopeCache[s.id] = { char: res.character, world: state.scopeCache[s.id] && state.scopeCache[s.id].world, scoped: false };
    renderSessionMeta();
  }).catch(function (e) { showToast(e.message, "err"); });
}
function exportSessionChar() {
  var s = currentSession(); if (!s) return;
  fetch(apiUrl("/api/sessions/" + s.id + "/character/export"), { headers: authHeaders() })
    .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function (obj) { downloadJSON(obj, ((obj.data && obj.data.name) || "character") + ".json"); })
    .catch(function (e) { showToast("导出失败: " + e.message, "err"); });
}
function editSessionChar() {
  var s = currentSession(); if (!s) return;
  var char = effectiveChar();
  if (!char) return;
  characterEditor(char, true);  // 副本态：保存写入会话 override
}

