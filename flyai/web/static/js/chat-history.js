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
  // 开头游离消息（正常不会有）
  while (i < messages.length && messages[i].role !== "user") {
    renderFlatNonUser(messages[i], null);
    i++;
  }
  while (i < messages.length) {
    var user = messages[i];
    var anchor = user.id;
    var end = messages.length;
    for (var j = i + 1; j < messages.length; j++) {
      if (messages[j].role === "user") { end = j; break; }
    }
    var userEl = makeUserBubble(user.content || "", user.images, anchor);
    el.messages.appendChild(userEl);
    state.turnNodes[anchor] = { userEl: userEl, editing: false };

    var openToolRegion = null;
    for (var k = i + 1; k < end; k++) {
      var m = messages[k];
      // 群聊自评「无需发言」不展示；旧版 bug 曾误隐「带工具调用的空白中间消息」，
      // 那种消息必须渲染，否则工具结果（图片）无处挂载
      var hiddenToolMsg = m.role === "assistant" && m.tool_calls && m.tool_calls.length;
      if (m.hidden && !hiddenToolMsg) {
        if (m.role === "assistant") openToolRegion = null;
        continue;
      }
      if (m.role === "assistant") {
        openToolRegion = appendAssistantMsg(m, anchor).toolRegion;
      } else if (m.role === "tool") {
        if (openToolRegion) {
          appendToolResultCard(openToolRegion, {
            id: m.tool_call_id, name: m.name,
            ok: (m.ok !== undefined) ? m.ok : !String(m.content || "").startsWith("[工具错误]"),
            output: m.content,
            revealed: pixivReveal[m.tool_call_id] || null,
          });
        }
      }
    }
    if (canEdit) addTurnControls(anchor, (edits || {})[anchor]);
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
  el.messages.appendChild(actions);
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
  summary.textContent = "思考过程";
  var thinkingDiv = document.createElement("div");
  thinkingEl.appendChild(summary); thinkingEl.appendChild(thinkingDiv);
  body.appendChild(thinkingEl);
  var toolRegion = document.createElement("div");
  toolRegion.className = "tool-cards";
  body.appendChild(toolRegion);
  var contentEl = document.createElement("div");
  contentEl.className = "assistant-content";
  body.appendChild(contentEl);
  inner.appendChild(body);

  var a = {
    container: wrap, head: head, idEl: idEl, statusEl: statusEl, chev: chev, bodyEl: body,
    thinkingEl: thinkingEl, thinkingDiv: thinkingDiv, toolRegion: toolRegion, contentEl: contentEl,
    rawContent: "", speakerName: "", open: true,
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
function appendAssistantMsg(m, anchor) {
  var a = makeAssistantContainer();
  if (anchor) a.container.dataset.anchor = anchor;
  el.messages.appendChild(a.container);
  if (m.name) {
    a.setSpeaker(m.name, memberOf(m.name));
    a.setThinking(false);
    a.setOpen(true);  // 历史记录默认展开可直接阅读
  }
  if (m.thinking) { a.thinkingEl.classList.remove("hidden"); a.thinkingEl.open = false; a.thinkingDiv.textContent = m.thinking; }
  if (m.content) setAssistantContent(a, m.content);
  if (m.tool_calls) m.tool_calls.forEach(function (c) { appendToolCallCard(a.toolRegion, c, false); });
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