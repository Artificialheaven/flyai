/* FlyAI 前端 · 历史渲染 / RP 分段 / 历史内联编辑重生成 / 滚动跟随（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* ==== 角色扮演内容渲染（分段 + 样式区分）==== */
function rpEnabled() {
  var rp = state.config && state.config.roleplay;
  return !!(rp && rp.format_enabled);
}
// 把动作() / 内心 _…_ / 对白「」 分别包成带样式的 span
function renderRp(text) {
  // tokenRe 必须带 g 标志：否则 exec 忽略 lastIndex，每次都从字符串头返回最左
  // 匹配，导致一条消息里只有第一个 token 着色、后面全部退回纯文本。
  // 对白/动作允许跨行（模型常把长对白与动作写成多行）；内心单下划线走扫描器。
  var tokenRe = /____[\s\S]*?____|「[^」]*」|“[^”]*”|"[^"]*"|『[^』]*』|（[^（）]*）|\([^()]*\)/g;
  var innerRe = /^[^\s_](?:[^_]*?[^\s_])?_/;
  var html = "", last = 0, i = 0;

  function emitPlainUntil(end) {
    if (end > last) html += escapeHtml(text.slice(last, end));
  }
  // 单下划线内心：同 CommonMark「词内不生效」防 my_var 误染；首尾不贴空白，允许跨行。
  // 返回 token 长度，不匹配返回 0。
  function thoughtLen(pos) {
    if (pos > 0 && /[A-Za-z0-9]/.test(text.charAt(pos - 1))) return 0;
    var inner = innerRe.exec(text.slice(pos + 1));
    if (!inner) return 0;
    var afterClose = pos + 1 + inner[0].length;
    if (afterClose < text.length && /[A-Za-z0-9]/.test(text.charAt(afterClose))) return 0;
    return afterClose - pos;
  }

  while (i < text.length) {
    var tlen = 0;
    if (text.charAt(i) === "_") {
      // 四下划线旧语法优先；否则试新单下划线
      tokenRe.lastIndex = i;
      var lm = tokenRe.exec(text);
      if (lm && lm.index === i) {
        tlen = lm[0].length;
        emitPlainUntil(i);
        // 只显内心内容，____ 标记不展示
        html += '<span class="dlg-thought">' + escapeHtml(lm[0].slice(4, -4)) + "</span>";
      } else {
        tlen = thoughtLen(i);
        if (tlen) {
          emitPlainUntil(i);
          // 只显内心内容，首尾 _ 标记不展示
          html += '<span class="dlg-thought">'
            + escapeHtml(text.slice(i + 1, i + tlen - 1)) + "</span>";
        }
      }
    } else {
      tokenRe.lastIndex = i;
      var m = tokenRe.exec(text);
      if (m && m.index === i) {
        tlen = m[0].length;
        emitPlainUntil(i);
        var tok = m[0];
        var c0 = tok.charAt(0);
        var cls = (c0 === "「" || c0 === "“" || c0 === '"' || c0 === "『") ? "dlg-speech" : "dlg-action";
        html += '<span class="' + cls + '">' + escapeHtml(tok) + "</span>";
      }
    }
    if (tlen) {
      i += tlen;
      last = i;
    } else {
      i += 1;
    }
  }
  emitPlainUntil(text.length);
  return html;
}
// 设置 assistant 内容区；启用格式则渲染段落样式，否则纯文本
function setAssistantContent(a, text) {
  a.rawContent = text || "";
  if (rpEnabled()) a.contentEl.innerHTML = renderRp(a.rawContent);
  else a.contentEl.textContent = a.rawContent;
}

// 内置默认要求（角色卡/世界观编辑框预填，可覆盖）
function defaultFormatReq() {
  return (state.config && state.config.roleplay && state.config.roleplay.format_instruction) || "";
}
function defaultToolReq() {
  return (state.config && state.config.roleplay && state.config.roleplay.tool_instruction) || "";
}

/* ==== 历史渲染（按「轮」组织：一条用户消息 + 它的回复组）==== */
function currentThread() {
  var list = state.roomThreads || [];
  for (var i = 0; i < list.length; i++) {
    if (list[i].id === state.currentThreadId) return list[i];
  }
  return null;
}
function currentThreadIsDM() {
  var t = currentThread();
  return !t || t.kind === "dm";  // 无窗口上下文时按 dm
}

function renderHistory(messages, edits) {
  clearMessages();
  var canEdit = currentThreadIsDM();
  // 搜索卡 → 后续精选结果的配对（按候选编号取原图 URL）
  var pixivReveal = buildPixivReveal(messages);
  var i = 0;
  // 开头游离消息（正常不会有）；撤回墓碑单独成行（如撤回首轮、前面还有开场白）
  while (i < messages.length && messages[i].role !== "user") {
    if (messages[i].tombstone) { appendTombstone(messages[i]); i++; continue; }
    renderFlatNonUser(messages[i], null);
    i++;
  }
  while (i < messages.length) {
    var user = messages[i];
    var end = messages.length;
    for (var j = i + 1; j < messages.length; j++) {
      // 墓碑也是轮边界：被撤回轮的回复槽位不渲染
      if (messages[j].role === "user" || messages[j].tombstone) { end = j; break; }
    }
    if (user.tombstone) { appendTombstone(user); i = end; continue; }
    var anchor = user.id;
    var userEl = makeUserBubble(user.content || "", user.images, anchor);
    el.messages.appendChild(userEl);
    state.turnNodes[anchor] = { userEl: userEl, editing: false };

    var k = i + 1;
    while (k < end) {
      var m0 = messages[k];
      // 群聊自评「无需发言」不展示；旧版 bug 曾误隐「带工具调用的空白中间消息」，
      // 那种消息必须渲染，否则工具结果（图片）无处挂载
      var hiddenTool0 = m0.role === "assistant" && m0.tool_calls && m0.tool_calls.length;
      if (m0.hidden && !hiddenTool0) { k++; continue; }
      if (m0.role !== "assistant") { k++; continue; }  // 游离 tool 消息无挂载点，不展示
      // 收集同一发言者经工具回灌链接的连续链：
      // assistant(tool_calls) → tool* → assistant(最终回复，可能继续带调用)
      // 合并为一个气泡渲染——与流式时的单气泡一致，避免看起来「又发了一轮消息」
      var chain = [m0];
      var linked = !!hiddenTool0;  // 链尾是否带工具调用：决定能否吞入后续 assistant
      var p = k + 1;
      while (p < end) {
        var mp = messages[p];
        if (mp.role === "tool") {
          chain.push(mp); p++; continue;
        }
        if (mp.role === "assistant" && linked
            && _assistantKey(m0) === _assistantKey(mp)) {
          chain.push(mp);
          linked = !!(mp.tool_calls && mp.tool_calls.length);
          p++;
          continue;
        }
        break;
      }
      appendMergedAssistantMsg(chain, anchor, pixivReveal);
      k = p;
    }
    if (canEdit) addTurnControls(anchor, (edits || {})[anchor]);
    else addGroupRecallControl(anchor, userEl);
    i = end;
  }
  scrollToBottom();
}
// 渲染不属于任何轮的 assistant/tool 游离消息（兜底）
function renderFlatNonUser(m, anchor) {
  // appendAssistantMsg 内部已插入 DOM；tool 游离消息无挂载点，不展示
  if (m.role === "assistant") appendAssistantMsg(m, anchor);
}

// 一轮的编辑入口 / 分页器 / 重生成操作行（仅 dm）
function addTurnControls(anchor, entry) {
  var node = state.turnNodes[anchor];
  // ✏️：hover 用户气泡显现，打开统一内联编辑器
  var editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "turn-edit-btn";
  editBtn.title = "编辑这条消息";
  editBtn.textContent = "✏️";
  editBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    openUserEditor(anchor);
  });
  node.userEl.appendChild(editBtn);
  // 点击整条用户消息才亮出 ✏️（无 hover 的触屏入口）；点气泡外任意处收起
  node.userEl.addEventListener("click", function (e) {
    if (node.editing) return;
    if (e.target.closest &&
        e.target.closest(".turn-edit-btn, .chat-img, a, .inline-edit, .inline-edit-actions")) return;
    activateEditEntry(node.userEl);
  });

  if (entry) {
    // 编辑版本分页：放在用户气泡内末尾
    if (entry.edits.length > 1) {
      node.userEl.appendChild(
        makePager(entry.edit_active + 1, entry.edits.length,
          function () { switchTurn(anchor, entry.edit_active - 1, null); },
          function () { switchTurn(anchor, entry.edit_active + 1, null); })
      );
    }
    // 回复版本分页：放在该轮回复组之后
    var slot = entry.edits[entry.edit_active];
    if (slot.reply_variants.length > 1) {
      el.messages.appendChild(
        makePager(slot.reply_active + 1, slot.reply_variants.length,
          function () { switchTurn(anchor, null, slot.reply_active - 1); },
          function () { switchTurn(anchor, null, slot.reply_active + 1); })
      );
    }
  }

  // 🔄 回复组操作行：重新生成（打开同一编辑器，原文直接发送即重生成）
  var actions = document.createElement("div");
  actions.className = "turn-actions";
  var regen = document.createElement("button");
  regen.type = "button";
  regen.className = "btn small ghost";
  regen.textContent = "🔄 重新生成";
  regen.addEventListener("click", function () { openUserEditor(anchor); });
  actions.appendChild(regen);
  var recallBtn = document.createElement("button");
  recallBtn.type = "button";
  recallBtn.className = "btn small ghost";
  recallBtn.textContent = "🧹 撤回";
  recallBtn.addEventListener("click", function () { requestRecall(anchor); });
  actions.appendChild(recallBtn);
  el.messages.appendChild(actions);
}

// 群聊用户气泡内的撤回按钮（桌面 hover / 触屏点气泡亮出，复用 .edit-on 模式）
function addGroupRecallControl(anchor, userEl) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "turn-recall-btn";
  btn.title = "撤回这一轮";
  btn.textContent = "🧹";
  btn.addEventListener("click", function (e) {
    e.stopPropagation();
    requestRecall(anchor);
  });
  userEl.appendChild(btn);
  userEl.addEventListener("click", function (e) {
    if (e.target.closest &&
        e.target.closest(".turn-recall-btn,.chat-img,a")) return;
    activateEditEntry(userEl);
  });
}

/* ==== 撤回整轮消息 ==== */
function requestRecall(anchor) {
  if (state.busy) return;
  askConfirm({
    title: "撤回这条消息？",
    text: "将删除这条消息及其之后的全部角色回复（群聊中多个角色的回复会一并删除）。\n\n撤回后原文不回输入框，可在原位置点「重新编辑」找回文字与图片。",
    okLabel: "撤回",
    onOk: function () { doRecall(anchor); },
  });
}

function doRecall(anchor) {
  fetchJSON("/api/sessions/" + state.currentSessionId
            + "/messages/" + encodeURIComponent(anchor),
            { method: "DELETE" })
    .then(function () {
      showToast("已撤回", "ok", 2000);
      if (state.currentThreadId) return loadThread(state.currentThreadId);
      // 理论不可达的旧形态兜底：重拉会话重画
      return fetchJSON("/api/sessions/" + state.currentSessionId).then(function (s) {
        state.roomThreads = s.threads || [];
        renderHistory(s.messages || []);
      });
    })
    .catch(function (e) { addErrorBar("撤回失败: " + e.message); });
}

// 撤回墓碑行："您撤回了一条消息，点击 重新编辑"
function appendTombstone(m) {
  var row = document.createElement("div");
  row.className = "recall-tombstone";
  if (m.id) row.dataset.anchor = m.id;
  var pre = document.createElement("span");
  pre.textContent = "您撤回了一条消息，点击 ";
  var link = document.createElement("a");
  link.className = "rt-redo";
  link.href = "javascript:void(0)";
  link.textContent = "重新编辑";
  link.addEventListener("click", function () { restoreRecalledDraft(m.tombstone); });
  row.appendChild(pre);
  row.appendChild(link);
  el.messages.appendChild(row);
}

// 点"重新编辑"：把撤回的原文与图片回填输入框（不自动重发）
function restoreRecalledDraft(t) {
  if (state.busy || !t) return;
  el.input.value = t.content || "";
  state.pendingImages = (t.images || []).slice();
  renderPendingImages();
  saveDraftNow();
  autosizeInput();
  el.input.focus();
  var n = el.input.value.length;
  try { el.input.setSelectionRange(n, n); } catch (e) { /* 老 WebView 忽略 */ }
}

// 同时只允许一条用户消息亮出编辑键
function activateEditEntry(userEl) {
  var current = document.querySelector(".msg.user.edit-on");
  if (current && current !== userEl) current.classList.remove("edit-on");
  userEl.classList.add("edit-on");
}
function deactivateEditEntries() {
  var nodes = document.querySelectorAll(".msg.user.edit-on");
  Array.prototype.forEach.call(nodes, function (n) { n.classList.remove("edit-on"); });
}

function makePager(active, total, goPrev, goNext) {
  var pager = document.createElement("div");
  pager.className = "turn-pager";
  var prev = document.createElement("button");
  prev.type = "button";
  prev.className = "pager-btn";
  prev.textContent = "‹";
  prev.disabled = active <= 1;
  prev.addEventListener("click", goPrev);
  var label = document.createElement("span");
  label.className = "pager-label";
  label.textContent = active + " / " + total;
  var next = document.createElement("button");
  next.type = "button";
  next.className = "pager-btn";
  next.textContent = "›";
  next.disabled = active >= total;
  next.addEventListener("click", goNext);
  pager.appendChild(prev);
  pager.appendChild(label);
  pager.appendChild(next);
  return pager;
}

function makeUserBubble(text, images, anchor) {
  var wrap = document.createElement("div");
  wrap.className = "msg user";
  if (anchor) wrap.dataset.userId = anchor;
  // 用户扮演自设时，气泡上方标注扮演的角色头像与名字
  var p = personaChar();
  if (p) {
    var label = document.createElement("div");
    label.className = "us-label";
    label.innerHTML = makeAvatarHtml(p, true) + '<span class="us-name">' + escapeHtml(p.name || "你") + "</span>";
    wrap.appendChild(label);
  }
  var bubble = document.createElement("div");
  bubble.className = "bubble";
  if (text) {
    var textEl = document.createElement("div");
    textEl.className = "bubble-text";
    textEl.textContent = text;
    bubble.appendChild(textEl);
  }
  (images || []).forEach(function (img) {
    var im = document.createElement("img");
    im.className = "chat-img";
    im.src = imageDataUrl(img);
    bubble.appendChild(im);
  });
  wrap.appendChild(bubble);
  return wrap;
}
/* ==== 思考用时计时 ====
 * thinkTimerId：流式期间的每秒跳动计时器（同一时刻只有当前发言者在思考）。
 * thinkTiming.pending：本轮已结束的思考秒数（按发言顺序），finalizeTurn
 *   loadThread 从磁盘重画后据消息 id 回填，避免时长被「已思考」覆盖。
 * 秒数另存 localStorage（按 会话id|消息id），重进会话/刷新仍显示。 */
var thinkTimerId = null;
var thinkTiming = { pending: [] };
var THINK_SECS_KEY = "flyai-think-secs";

function thinkSecsStore() {
  try { return JSON.parse(localStorage.getItem(THINK_SECS_KEY) || "{}") || {}; }
  catch (e) { return {}; }
}
function saveThinkingDuration(sid, msgId, secs) {
  if (!sid || !msgId) return;
  var store = thinkSecsStore();
  store[sid + "|" + msgId] = secs;
  try { localStorage.setItem(THINK_SECS_KEY, JSON.stringify(store)); } catch (e) {}
}
function getThinkingDuration(sid, msgId) {
  if (!sid || !msgId) return 0;
  return intOr(thinkSecsStore()[sid + "|" + msgId], 0);
}
/* loadThread 重画完成后：把本轮 pending 秒数按顺序回填到最后若干条带思考块的
 * assistant 消息（DM 下即刚结束的那条），并按消息 id 持久化 */
function applyPendingThinkingDurations() {
  var pend = thinkTiming.pending.splice(0);
  if (!pend.length) return;
  var sid = state.currentSessionId || "";
  var all = Array.prototype.slice.call(el.messages.querySelectorAll(".msg.assistant"));
  // 只看最后一轮（anchor = 该轮用户消息 id），避免把秒数错贴上一轮
  var lastAnchor = all.length ? all[all.length - 1].dataset.anchor || "" : "";
  var withThink = all.filter(function (c) {
    var t = c.querySelector(".thinking");
    return (c.dataset.anchor || "") === lastAnchor &&
      t && !t.classList.contains("hidden");
  });
  var targets = withThink.slice(-pend.length);
  for (var i = 0; i < targets.length; i++) {
    var sumEl = targets[i].querySelector(".thinking summary");
    if (sumEl) sumEl.textContent = "已思考 " + pend[i] + " 秒";
    var msgId = targets[i].dataset.msgId || "";
    if (msgId) saveThinkingDuration(sid, msgId, pend[i]);
  }
}

/* ==== 召回记忆折叠组 ====
 * 召回条目随本轮 assistant 消息持久化（Message.recall，服务端不回灌给模型）：
 * 流式期间由 recall_notice 即时填充，流式结束 loadThread 重画后从 m.recall
 * 重建——刷新页面/断线重连也不会归零。 */
// 构造召回明细行（流式气泡与历史渲染共用）
function buildRecallRows(recallBody, items) {
  recallBody.innerHTML = "";
  (items || []).forEach(function (it) {
    var row = document.createElement("div");
    row.className = "recall-item";
    var src = document.createElement("span");
    src.className = "ri-src";
    src.textContent = "（" + (it.source || "记忆") + "）";
    var txt = document.createElement("span");
    txt.className = "ri-text";
    txt.textContent = it.text || "";
    row.appendChild(src); row.appendChild(txt);
    recallBody.appendChild(row);
  });
}
// 直接填充一个 assistant 气泡对象（流式期间使用）
function fillRecallGroup(a, items) {
  if (!a || !items || !items.length) return;
  a.rgCount.textContent = String(items.length);
  buildRecallRows(a.recallBody, items);
  a.recallGroup.classList.remove("hidden");
  a.recallGroup.open = false;  // 默认折叠，点击头部查看
}
// 从合并链的 assistant 消息收集持久化的召回条目
function recallItemsFromChain(chain) {
  var out = [];
  (chain || []).forEach(function (m) {
    if (m.role !== "assistant" || !m.recall) return;
    m.recall.forEach(function (it) { out.push(it); });
  });
  return out;
}

function makeAssistantContainer() {
  var wrap = document.createElement("div");
  wrap.className = "msg assistant";
  var inner = document.createElement("div");
  inner.className = "assistant-wrap";
  wrap.appendChild(inner);

  // 可折叠卡片头：显示 AI 头像/名字 + 思考中状态；点它展开/收起正文
  var head = document.createElement("button");
  head.type = "button";
  head.className = "as-header hidden";
  var idEl = document.createElement("span"); idEl.className = "as-id";
  var statusEl = document.createElement("span"); statusEl.className = "as-status";
  var chev = document.createElement("span"); chev.className = "as-chev"; chev.textContent = "▾";
  head.appendChild(idEl); head.appendChild(statusEl); head.appendChild(chev);
  inner.appendChild(head);

  var body = document.createElement("div");
  body.className = "as-body";
  var thinkingEl = document.createElement("details");
  thinkingEl.className = "thinking hidden";
  var summary = document.createElement("summary");
  summary.textContent = "已思考";
  var thinkingDiv = document.createElement("div");
  thinkingEl.appendChild(summary); thinkingEl.appendChild(thinkingDiv);
  body.appendChild(thinkingEl);

  // 自动召回折叠组：「🧠 已召回 N 段记忆」，展开看条目明细。
  // 无召回时整组隐藏（由 fillRecallDom/回填逻辑解除）
  var recallGroup = document.createElement("details");
  recallGroup.className = "recall-group hidden";
  var recallSummary = document.createElement("summary");
  var rgTitle = document.createElement("span"); rgTitle.className = "rg-title";
  rgTitle.textContent = "🧠 已召回 ";
  var rgCount = document.createElement("span"); rgCount.className = "rg-count";
  rgCount.textContent = "0";
  var rgTail = document.createElement("span"); rgTail.textContent = " 段记忆";
  recallSummary.appendChild(rgTitle); recallSummary.appendChild(rgCount);
  recallSummary.appendChild(rgTail);
  var recallBody = document.createElement("div");
  recallBody.className = "recall-items";
  recallGroup.appendChild(recallSummary); recallGroup.appendChild(recallBody);
  body.appendChild(recallGroup);

  // 工具折叠组：「🔧 已调用 N 个工具」，展开后是逐工具卡片（之前的样式）。
  // 未调用任何工具时整组隐藏（卡片挂入后由 syncToolGroup/历史渲染解除）
  var toolGroup = document.createElement("details");
  toolGroup.className = "tool-group hidden";
  var groupSummary = document.createElement("summary");
  var tgTitle = document.createElement("span"); tgTitle.className = "tg-title";
  tgTitle.textContent = "🔧 已调用 ";
  var tgCount = document.createElement("span"); tgCount.className = "tg-count";
  tgCount.textContent = "0";
  var tgTail = document.createElement("span"); tgTail.textContent = " 个工具";
  var tgStatus = document.createElement("span"); tgStatus.className = "tg-status";
  groupSummary.appendChild(tgTitle); groupSummary.appendChild(tgCount);
  groupSummary.appendChild(tgTail); groupSummary.appendChild(tgStatus);
  var toolRegion = document.createElement("div");
  toolRegion.className = "tool-cards";
  toolGroup.appendChild(groupSummary); toolGroup.appendChild(toolRegion);
  body.appendChild(toolGroup);

  var contentEl = document.createElement("div");
  contentEl.className = "assistant-content";
  body.appendChild(contentEl);
  inner.appendChild(body);

  var a = {
    container: wrap, head: head, idEl: idEl, statusEl: statusEl, chev: chev, bodyEl: body,
    thinkingEl: thinkingEl, thinkingSummary: summary, thinkingDiv: thinkingDiv,
    recallGroup: recallGroup, rgCount: rgCount, recallBody: recallBody,
    toolGroup: toolGroup, tgCount: tgCount, tgStatus: tgStatus,
    toolRegion: toolRegion, contentEl: contentEl,
    rawContent: "", speakerName: "", open: true,
    thinkingStartedAt: 0, thinkingSeconds: 0, thinkingTimed: false,
  };
  // 首个 thinking delta 时开始计时：摘要每秒跳动「已思考 N 秒」
  a.markThinkingStart = function () {
    if (!a.thinkingStartedAt) {
      a.thinkingStartedAt = Date.now();
      a.thinkingSummary.textContent = "已思考 1 秒";
      if (thinkTimerId) clearInterval(thinkTimerId);
      thinkTimerId = setInterval(function () {
        var n = Math.max(1, Math.round((Date.now() - a.thinkingStartedAt) / 1000));
        a.thinkingSummary.textContent = "已思考 " + n + " 秒";
      }, 500);
    }
  };
  // 思考结束：定格「已思考 N 秒」，并登记待重画后按消息 id 回填；无计时则仅「已思考」
  a.finishThinking = function () {
    if (a.thinkingTimed) return;
    if (thinkTimerId) { clearInterval(thinkTimerId); thinkTimerId = null; }
    if (a.thinkingStartedAt) {
      var secs = Math.max(1, Math.round((Date.now() - a.thinkingStartedAt) / 1000));
      a.thinkingSeconds = secs;
      a.thinkingSummary.textContent = "已思考 " + secs + " 秒";
      a.thinkingTimed = true;
      thinkTiming.pending.push(secs);
    } else {
      a.thinkingSummary.textContent = "已思考";
    }
  };
  a.setOpen = function (open) {
    a.open = !!open;
    a.bodyEl.classList.toggle("hidden", !a.open);
    a.chev.textContent = a.open ? "▾" : "▸";
  };
  a.setSpeaker = function (name, member) {
    a.head.classList.remove("hidden");
    var ch = member && member.character_id ? characterOf(member.character_id) : null;
    // 角色卡有别名时优先显示别名
    a.speakerName = ch ? cardDisplayName(ch) : (name || "AI");
    a.idEl.innerHTML =
      makeAvatarHtml(ch || member || null, true) +
      '<span class="spk-name">' + escapeHtml(a.speakerName) + "</span>";
  };
  a.setThinking = function (on) {
    a.statusEl.textContent = on ? "AI 思考中…" : "";
    a.container.classList.toggle("thinking", !!on);
  };
  head.addEventListener("click", function () { a.setOpen(!a.open); });
  return a;
}
function ensureAssistant() {
  if (state.currentAssistant) return state.currentAssistant;
  var a = makeAssistantContainer();
  // 旧轮编辑/重生成：插在该轮与下一轮之间；普通发送：追加到底
  if (state.streamInsertBefore) {
    el.messages.insertBefore(a.container, state.streamInsertBefore);
  } else {
    el.messages.appendChild(a.container);
  }
  state.currentAssistant = a;
  if (roomMode() && !a.speakerName) a.head.classList.remove("hidden");
  scrollToBottom();
  return a;
}
// assistant 消息的发言者归属：群聊按 actor_id，其次按 name；DM 均为空串
function _assistantKey(m) {
  return (m && (m.actor_id || m.name)) || "";
}

// 渲染工具链合并后的单个助理气泡（磁盘上仍是多条消息，仅展示合并）
function appendMergedAssistantMsg(chain, anchor, pixivReveal) {
  var first = chain[0];
  var last = chain[chain.length - 1];
  var a = makeAssistantContainer();
  if (anchor) a.container.dataset.anchor = anchor;
  a.container.dataset.msgId = last.id || first.id || "";
  el.messages.appendChild(a.container);

  var named = null;
  for (var i = 0; i < chain.length; i++) {
    if (chain[i].name) { named = chain[i]; break; }
  }
  if (named) {
    a.setSpeaker(named.name, memberOf(named.name));
    a.setThinking(false);
    a.setOpen(true);  // 历史记录默认展开可直接阅读
  }

  // 思考：各轮思考合并进同一块（流式时它们也在同一个气泡里；只取 assistant）
  var thinks = [];
  chain.forEach(function (m) {
    if (m.role !== "assistant") return;
    var t = (m.thinking || "").trim();
    if (t) thinks.push(t);
  });
  if (thinks.length) {
    a.thinkingEl.classList.remove("hidden");
    a.thinkingEl.open = false;
    a.thinkingDiv.textContent = thinks.join("\n\n");
    // 此前已计时：显示「已思考 N 秒」而非裸「已思考」
    var saved = getThinkingDuration(state.currentSessionId || "",
                                    a.container.dataset.msgId);
    if (saved) a.thinkingSummary.textContent = "已思考 " + saved + " 秒";
  }

  // 工具调用与结果：按链顺序交错挂入同一个折叠组（结果按 id 并入调用卡）
  chain.forEach(function (m) {
    if (m.role === "assistant" && m.tool_calls) {
      m.tool_calls.forEach(function (c) {
        appendToolCallCard(a.toolRegion, c, false);
      });
    } else if (m.role === "tool") {
      appendToolResultCard(a.toolRegion, {
        id: m.tool_call_id, name: m.name,
        ok: (m.ok !== undefined) ? m.ok
           : !String(m.content || "").startsWith("[工具错误]"),
        output: m.content,
        revealed: (pixivReveal || {})[m.tool_call_id] || null,
      });
    }
  });
  // 有工具卡片才显示工具折叠组（初始为 hidden）
  if (a.toolRegion.querySelectorAll(".tool-card").length) {
    a.toolGroup.classList.remove("hidden");
    a.toolGroup.open = false;
  }
  // 召回记忆：从持久化的 m.recall 重建（流式结束重画/刷新后不丢）
  fillRecallGroup(a, recallItemsFromChain(chain));

  // 正文：只合并链上 assistant 的文本（tool 消息是工具结果，已渲染成工具
  // 卡片；早期版本未过滤角色，导致工具返回内容被拼进 assistant-content）
  var textParts = [];
  chain.forEach(function (m) {
    if (m.role !== "assistant" || m.hidden) return;
    var t = m.content || "";
    if (!t) return;
    if (textParts.length && textParts[textParts.length - 1].slice(-1) !== "\n") {
      t = "\n\n" + t;
    }
    textParts.push(t);
  });
  var mergedText = textParts.join("");
  if (mergedText) setAssistantContent(a, mergedText);
  scrollToBottom();
  return a;
}

function appendAssistantMsg(m, anchor) {
  var a = makeAssistantContainer();
  if (anchor) a.container.dataset.anchor = anchor;
  a.container.dataset.msgId = m.id || "";
  el.messages.appendChild(a.container);
  if (m.name) {
    a.setSpeaker(m.name, memberOf(m.name));
    a.setThinking(false);
    a.setOpen(true);  // 历史记录默认展开可直接阅读
  }
  if (m.thinking) {
    a.thinkingEl.classList.remove("hidden"); a.thinkingEl.open = false;
    a.thinkingDiv.textContent = m.thinking;
    // 此前已计时（本轮回填前或历史重进）：显示「已思考 N 秒」而非裸「已思考」
    var saved = getThinkingDuration(state.currentSessionId || "", m.id);
    if (saved) a.thinkingSummary.textContent = "已思考 " + saved + " 秒";
  }
  if (m.content) setAssistantContent(a, m.content);
  if (m.recall) fillRecallGroup(a, m.recall);
  if (m.tool_calls) m.tool_calls.forEach(function (c) { appendToolCallCard(a.toolRegion, c, false); });
  if (a.toolRegion.querySelectorAll(".tool-card").length) {
    a.toolGroup.classList.remove("hidden");
    a.toolGroup.open = false;
  }
  scrollToBottom();
  return a;
}

/* ==== 历史消息内联编辑 / 重新生成（共用同一编辑器）==== */
function openUserEditor(anchor) {
  if (state.busy) return;
  var node = state.turnNodes[anchor];
  if (!node || node.editing) return;
  node.userEl.classList.remove("edit-on");
  var bubble = node.userEl.querySelector(".bubble");
  if (!bubble) return;
  var textEl = bubble.querySelector(".bubble-text");
  var currentText = textEl ? textEl.textContent : "";
  if (textEl) textEl.remove();

  node.editing = true;
  var ta = document.createElement("textarea");
  ta.className = "inline-edit";
  ta.rows = Math.max(2, Math.min(8, currentText.split("\n").length + 1));
  ta.value = currentText;
  var firstImg = bubble.querySelector(".chat-img");
  if (firstImg) bubble.insertBefore(ta, firstImg); else bubble.appendChild(ta);

  var actions = document.createElement("div");
  actions.className = "inline-edit-actions";
  var hint = document.createElement("span");
  hint.className = "inline-edit-hint";
  hint.textContent = "不改内容直接发送 = 重新生成";
  var sendBtn = document.createElement("button");
  sendBtn.type = "button"; sendBtn.className = "btn small"; sendBtn.textContent = "发送";
  var cancelBtn = document.createElement("button");
  cancelBtn.type = "button"; cancelBtn.className = "btn small ghost"; cancelBtn.textContent = "取消";
  actions.appendChild(hint); actions.appendChild(sendBtn); actions.appendChild(cancelBtn);
  bubble.appendChild(actions);
  ta.focus();

  cancelBtn.addEventListener("click", function () {
    ta.remove(); actions.remove();
    node.editing = false;
    if (textEl) {
      if (firstImg) bubble.insertBefore(textEl, firstImg); else bubble.appendChild(textEl);
    }
  });
  sendBtn.addEventListener("click", function () {
    var newText = ta.value;
    ta.remove(); actions.remove();
    node.editing = false;
    if (newText) {
      var nt = document.createElement("div");
      nt.className = "bubble-text";
      nt.textContent = newText;
      if (firstImg) bubble.insertBefore(nt, firstImg); else bubble.appendChild(nt);
    }
    submitTurnEdit(anchor, newText, currentText);
  });
}

function submitTurnEdit(anchor, newText, originalText) {
  if (state.busy) return;
  var changed = newText !== originalText;
  var node = state.turnNodes[anchor];
  // 移除该轮回复区全部内容（旧回复 / 分页器 / 操作行）；
  // 记录下一轮起点作为流式插入位置，最近一轮 afterTurn=null → 追加到底
  var afterTurn = null;
  var cur = node.userEl.nextSibling;
  while (cur) {
    if (cur.nodeType === 1 && cur.classList &&
        cur.classList.contains("msg") && cur.classList.contains("user")) {
      afterTurn = cur;
      break;
    }
    cur = cur.nextSibling;
  }
  cur = node.userEl.nextSibling;
  while (cur && cur !== afterTurn) {
    var nx = cur.nextSibling;
    cur.remove();
    cur = nx;
  }
  state.streamInsertBefore = afterTurn;
  state.turnEditAnchor = anchor;
  state.currentAssistant = null;
  state.busy = true;
  el.btnSend.disabled = true;
  el.btnStop.classList.remove("hidden");
  scrollToBottom();

  var payload = {
    message: newText,
    session_id: state.currentSessionId,
    thread_id: state.currentThreadId,
    provider_id: chosenProviderId(),
    tools_enabled: true,
    image_model_id: state.imageModelId || "",
    image_engine: state.sdEngine ? "sd" : "",
    // 编辑/重生成沿用输入框当前的深度思考与工具开关
    deep_thinking: !!state.deepThinking,
    disabled_tools: disabledToolNames(),
  };
  if (changed) payload.edit_message_id = anchor;
  else payload.regenerate_message_id = anchor;
  postChatSSE(payload);
}

function switchTurn(anchor, editIndex, replyIndex) {
  if (state.busy) return;
  var body = {};
  if (editIndex !== null && editIndex !== undefined) body.edit_index = editIndex;
  if (replyIndex !== null && replyIndex !== undefined) body.reply_index = replyIndex;
  fetchJSON(
    "/api/rooms/" + state.currentSessionId + "/threads/" + state.currentThreadId + "/turns/" + anchor,
    { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
  ).then(function (t) {
    var idx = -1;
    state.roomThreads.forEach(function (x, i) { if (x.id === t.id) idx = i; });
    if (idx >= 0) state.roomThreads[idx] = t; else state.roomThreads.push(t);
    renderHistory(t.messages || [], t.edits || {});
  }).catch(function (e) { addErrorBar("切换版本失败: " + e.message); });
}
function addErrorBar(msg) {
  var bar = document.createElement("div");
  bar.className = "error-bar"; bar.textContent = msg;
  el.messages.appendChild(bar); scrollToBottom();
  setTimeout(function () { bar.remove(); }, 10000);
}

function onMessagesScroll() {
  var m = el.messages;
  state.stickBottom = (m.scrollHeight - m.scrollTop - m.clientHeight) < 80;
  updateJumpBtn();
}
function scrollToBottom(force) {
  var m = el.messages;
  // 用户主动上翻（stick=false）时不跟随，只在贴底或强制时滚动
  if (force || state.stickBottom) m.scrollTop = m.scrollHeight;
  updateJumpBtn();
}
/* 距底部多远时显示「跳到底部」按钮 */
function updateJumpBtn() {
  if (!el.btnJump) return;
  var m = el.messages;
  var near = (m.scrollHeight - m.scrollTop - m.clientHeight) < 80;
  el.btnJump.classList.toggle("hidden", near);
}
function jumpBottom() {
  scrollToBottom(true);
  updateJumpBtn();
}