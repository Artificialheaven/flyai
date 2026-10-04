/* FlyAI 前端 · 流式聊天 / SSE 解析 / 断线自动重连 / token 用量（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* ==== 流式聊天 ==== */
function onSend() {
  if (state.busy) return;
  var text = el.input.value.trim();
  var images = state.pendingImages.slice();
  if (!text && !images.length) return;
  var doSend = function () {
    closeMentionBox();
    state.busy = true;
    el.btnSend.disabled = true;
    el.btnStop.classList.remove("hidden");
    el.input.disabled = true;
    el.messages.appendChild(makeUserBubble(text, images));
    el.input.value = "";
    clearDraft();
    autosizeInput();
    // 已发图片清空待发区
    state.pendingImages = [];
    renderPendingImages();
    // 失败恢复：暂存本次内容；成功结束后由 done 清除
    state.pendingSend = { text: text, images: images };
    // 发送即视为贴底跟随
    state.stickBottom = true;
    // 每个 AI 各自一条卡片：流式期间按 speaker 事件新建，不预建占位
    state.currentAssistant = null;
    state.currentSpeaker = null;
    scrollToBottom();
    var tid = state.currentThreadId ||
      ((state.roomThreads && state.roomThreads.length) ? state.roomThreads[0].id : undefined);
    var payload = {
      message: text,
      session_id: state.currentSessionId,
      thread_id: tid,
      provider_id: chosenProviderId(),
      tools_enabled: true,
      images: images,
      image_model_id: state.imageModelId || "",
      image_engine: state.sdEngine ? "sd" : "",
      // @ 定向：被点名者优先、按 @ 顺序；随机顺序只影响其余成员
      target_member_ids: parseMentionTargets(text),
      shuffle_speakers: isShuffleOn(),
    };
    postChatSSE(payload);
  };
  if (!state.currentSessionId) {
    // 首页空状态直接发送：先建会话并正式进入（selectSession 会清掉 .empty-state、
    // 渲染空历史/meta），再发送；只 refreshSessions 的话空状态残留，气泡会把首页顶上去
    fetchJSON("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })
      .then(function (s) { return selectSession(s.id); })
      .then(doSend);
  } else { doSend(); }
}
/* ==== 记忆存档：让房间所有 AI 各自回顾并写进跨房间角色记忆簿 ==== */
function snapshotRoomMemory() {
  if (state.busy) return;
  if (!state.currentSessionId) {
    showToast("请先进入一个房间", "err");
    return;
  }
  askConfirm({
    title: "记录记忆",
    text: "让本房间的每个 AI 各自回顾本房间各个窗口的对话，把重要内容写进各自的跨房间记忆簿。\n\n之后在其他房间使用相同角色继续聊天时，她们会记得这些内容；同一张卡的记忆默认只读可见。",
    okLabel: "开始记录",
    danger: false,
    onOk: runMemorySnapshot,
  });
}
function runMemorySnapshot() {
  state.busy = true;
  el.btnSend.disabled = true;
  el.btnStop.classList.remove("hidden");
  el.input.disabled = true;
  state.currentAssistant = null;
  state.currentSpeaker = null;
  state.stickBottom = true;
  state.reconnectAttempts = 0;
  state.lastEventSeq = 0;
  showToast("各角色开始记录记忆…", "info", 3000);
  consumeRun(null, {
    post: true,
    url: "/api/rooms/" + state.currentSessionId + "/memory-snapshot",
    payload: { provider_id: chosenProviderId() },
  });
}

// @ 提及解析：只在群聊窗口生效，返回被点名成员 id（按 @ 出现顺序、去重）
function parseMentionTargets(text) {
  var thread = currentThread();
  if (!thread || thread.kind !== "group") return [];
  var members = [];
  (thread.member_ids || []).forEach(function (mid) {
    var m = (state.roomMembers || []).find(function (x) { return x.id === mid; });
    if (m && !m.is_user) members.push(m);
  });
  // 座位的候选名（显示名优先，含别名；再退回座位名）
  var nameOf = {};
  var names = [];
  members.forEach(function (m) {
    [memberDisplayName(m), m.name].forEach(function (n) {
      if (n) {
        if (names.indexOf(n) < 0) names.push(n);
        nameOf[n] = m.id;
      }
    });
  });
  names.sort(function (a, b) { return b.length - a.length; });  // 长名优先匹配
  var alt = names.map(function (n) {
    return n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }).join("|");
  if (!alt) return [];
  var re = new RegExp("@(" + alt + ")", "g");
  var order = [];
  var seen = {};
  var mm;
  while ((mm = re.exec(text))) {
    var mid = nameOf[mm[1]];
    if (mid && !seen[mid]) { order.push(mid); seen[mid] = true; }
  }
  return order;
}

/* ==== @ 候选浮层：输入 @ 后在输入框上方列出本群 AI，方向键/回车或鼠标选择 ==== */
var mentionState = null;
function closeMentionBox() {
  mentionState = null;
  if (el.mentionBox) el.mentionBox.classList.add("hidden");
}
function mentionCandidates() {
  var thread = currentThread();
  if (!thread || thread.kind !== "group") return [];
  var out = [];
  (thread.member_ids || []).forEach(function (mid) {
    var mem = (state.roomMembers || []).find(function (x) { return x.id === mid; });
    if (mem && !mem.is_user) out.push(mem);
  });
  return out;
}
// 光标前是否正在输入 @名字；返回 {start:@的位置, query} 或 null
function detectMention() {
  var caret = el.input.selectionStart;
  if (caret == null) return null;
  var before = el.input.value.slice(0, caret);
  // @ 前必须是行首/空白/标点；查询串不含空格和 @，最长 20 字
  var m = /(^|[\s,，。、.;；:：!！?？~～（(【\[「『])@([^\s@]{0,20})$/.exec(before);
  if (!m) return null;
  var q = m[2].toLowerCase();
  var items = mentionCandidates().filter(function (mem) {
    if (!q) return true;
    return memberDisplayName(mem).toLowerCase().indexOf(q) >= 0
      || (mem.name || "").toLowerCase().indexOf(q) >= 0;
  });
  if (!items.length) return null;
  return { start: caret - m[2].length - 1, items: items.slice(0, 8), index: 0 };
}
function updateMentionBox() {
  var found = detectMention();
  if (!found) { closeMentionBox(); return; }
  // 延续上次的高亮项（继续打字过滤时不跳回第一行）
  if (mentionState && mentionState.start === found.start) {
    var prevId = (mentionState.items[mentionState.index] || {}).id;
    var keep = found.items.findIndex(function (x) { return x.id === prevId; });
    if (keep >= 0) found.index = keep;
  }
  mentionState = found;
  renderMentionBox();
}
function renderMentionBox() {
  var box = el.mentionBox;
  if (!box) return;
  box.innerHTML = "";
  mentionState.items.forEach(function (mem, i) {
    var row = document.createElement("div");
    row.className = "mention-row" + (i === mentionState.index ? " active" : "");
    var ch = mem.character_id ? characterOf(mem.character_id) : null;
    if (ch && ch.avatar) {
      var img = document.createElement("img");
      img.className = "mention-avatar"; img.src = ch.avatar; img.alt = "";
      img.addEventListener("error", function () { img.remove(); });
      row.appendChild(img);
    }
    var nm = document.createElement("span");
    nm.className = "mention-name";
    nm.textContent = "@" + memberDisplayName(mem);
    row.appendChild(nm);
    var disp = memberDisplayName(mem);
    if (mem.name && mem.name !== disp) {
      var sub = document.createElement("span");
      sub.className = "mention-sub"; sub.textContent = mem.name;
      row.appendChild(sub);
    }
    // mousedown+preventDefault：选择时输入框不丢焦（click 之前会先 blur）
    row.addEventListener("mousedown", function (e) {
      e.preventDefault(); pickMention(mem);
    });
    box.appendChild(row);
  });
  box.classList.remove("hidden");
}
function pickMention(mem) {
  if (!mentionState) return;
  var v = el.input.value;
  var caret = el.input.selectionStart;
  var name = memberDisplayName(mem);
  var nv = v.slice(0, mentionState.start) + "@" + name + " " + v.slice(caret);
  el.input.value = nv;
  var pos = mentionState.start + name.length + 2;
  el.input.setSelectionRange(pos, pos);
  closeMentionBox();
  scheduleDraftSave(); autosizeInput();
  el.input.focus();
}
// 候选打开时拦截按键；返回 true 表示已处理、发送键逻辑不要再执行
function onMentionKeydown(e) {
  if (!mentionState) return false;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    var n = mentionState.items.length;
    mentionState.index = (mentionState.index + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
    renderMentionBox();
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    pickMention(mentionState.items[mentionState.index]);
    return true;
  }
  if (e.key === "Escape") { closeMentionBox(); return true; }
  return false;
}

function onStop() {
  var rid = state.currentRunId;
  if (rid) {
    // 主动停止：通知后台任务取消（已生成部分仍保存），再断开本连接
    fetch("/api/chat/runs/" + rid + "/cancel", {
      method: "POST", headers: authHeaders(),
    }).catch(function () {});
  }
  if (state.abortController) state.abortController.abort();
}

// ---- 运行标记（localStorage：刷新页面后找到未结束的运行）----
function runStorageKey() { return "flyai-run-" + (state.userId || "x"); }
function saveRunMarker() {
  if (!state.currentRunId) return;
  try {
    localStorage.setItem(runStorageKey(), JSON.stringify({
      run_id: state.currentRunId,
      sid: state.currentSessionId,
      tid: state.currentThreadId || "",
      seq: state.lastEventSeq,
      ts: Date.now(),
    }));
  } catch (e) {}
}
function clearRunMarker() {
  try { localStorage.removeItem(runStorageKey()); } catch (e) {}
}
// 事件帧很密（逐字增量），标记写入节流到 400ms
function scheduleRunMarkerSave() {
  if (state.runMarkerTimer) return;
  state.runMarkerTimer = setTimeout(function () {
    state.runMarkerTimer = null; saveRunMarker();
  }, 400);
}

// SSE 通道共用（普通发送 / 历史编辑 / 重新生成）
function postChatSSE(payload) {
  state.reconnectAttempts = 0;
  state.lastEventSeq = 0;
  return consumeRun(null, { post: true, payload: payload });
}

// 消费一个运行：post=true 走 POST /api/chat（新建运行）；否则 GET 续读
function consumeRun(runId, opts) {
  state.abortController = new AbortController();
  state.streamTerminal = false;
  var url, fetchOpts;
  if (opts.post) {
    url = opts.url || "/api/chat";
    fetchOpts = {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(opts.payload || {}),
    };
  } else {
    url = "/api/chat/runs/" + runId + "/events?after=" + state.lastEventSeq;
    fetchOpts = { headers: authHeaders() };
  }
  fetchOpts.signal = state.abortController.signal;
  var runEstablished = !!runId;
  return fetch(url, fetchOpts).then(function (resp) {
    if (!resp.ok) {
      var err = new Error("HTTP " + resp.status);
      err.status = resp.status;
      throw err;
    }
    var rid = resp.headers.get("X-Run-Id") || runId;
    if (rid) {
      state.currentRunId = rid;
      runEstablished = true;
      saveRunMarker();
    }
    return readSSE(resp.body, makeStreamHandlers());
  }).then(function () {
    // 连接结束：收到终态 → 正常结束；否则尝试自动重连
    if (state.streamTerminal) return;
    return autoReconnect(state.currentRunId);
  }).catch(function (e) {
    if (e.name === "AbortError") { finalizeTurn(); return; }
    // POST 且运行未建立（请求失败/4xx）：消息没进会话，回填不丢字
    if (opts.post && !runEstablished) {
      restoreFailedSend("请求失败: " + e.message);
      return;
    }
    return autoReconnect(state.currentRunId);
  });
}

function makeStreamHandlers() {
  return {
    message_start: onMsgStart,
    speaker: onSpeaker,
    speaker_skip: onSpeakerSkip,
    thinking_delta: onThinkingDelta,
    content_delta: onContentDelta,
    tool_calls: onToolCalls,
    tool_start: onToolStart,
    tool_end: onToolEnd,
    attributes: onAttributes,
    error: function (data) {
      onStreamError(data);
      if (data && data.fatal !== false) state.streamTerminal = true;
    },
    done: function (data) {
      recordUsage(data && data.usage);
      // 群聊单个 AI 的 done（terminal=false）：只记用量，不收尾
      if (data && data.terminal === false) { saveRunMarker(); return; }
      state.streamTerminal = true;
      finalizeTurn();
    },
  };
}

// 连接意外中断后的自动重连（指数退避，最多 8 次）
function autoReconnect(runId) {
  if (!runId) { finalizeTurn(); return Promise.resolve(); }
  state.reconnectAttempts += 1;
  if (state.reconnectAttempts > 8) {
    showToast("连接中断且多次重连失败，请手动刷新页面", "err", 6000);
    finalizeTurn();
    return Promise.resolve();
  }
  var delay = Math.min(8000, Math.round(500 * Math.pow(1.5, state.reconnectAttempts - 1)));
  showToast("连接中断，" + (delay / 1000) + " 秒后自动重连…", "info", delay + 1500);
  return new Promise(function (resolve) { setTimeout(resolve, delay); }).then(function () {
    // 先探活：服务重启/过期会 404
    return fetchJSON("/api/chat/runs/" + runId);
  }).then(function () {
    return consumeRun(runId, { post: false });
  }).catch(function (e) {
    if (String(e.message || "").indexOf("404") >= 0) {
      showToast("回答已中断（服务已重启或运行过期）", "err", 6000);
      finalizeTurn();
      return;
    }
    return autoReconnect(runId);
  });
}

function readSSE(body, handlers) {
  if (!body) return Promise.resolve();
  var reader = body.getReader();
  var decoder = new TextDecoder();
  var buffer = "";
  function pump() {
    return reader.read().then(function (res) {
      if (res.done) { if (buffer.trim()) processFrame(buffer); return; }
      buffer += decoder.decode(res.value, { stream: true });
      for (;;) {
        var idx = buffer.indexOf("\n\n");
        if (idx < 0) break;
        var frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        processFrame(frame);
      }
      return pump();
    });
  }
  function processFrame(frame) {
    var parsed = parseFrame(frame);
    if (!parsed) return;
    if (parsed.seq) {
      state.lastEventSeq = parsed.seq;
      scheduleRunMarkerSave();
    }
    if (handlers[parsed.event]) handlers[parsed.event](parsed.data);
  }
  return pump();
}
function parseFrame(frame) {
  var event = "message"; var data = ""; var seq = 0;
  frame.split("\n").forEach(function (line) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
    else if (line.startsWith("id:")) seq = parseInt(line.slice(3).trim(), 10) || 0;
  });
  if (!data) return null;
  try { return { event: event, data: JSON.parse(data), seq: seq }; } catch (e) { return null; }
}

/* 事件处理 */
function onMsgStart(data) {
  var sessionChanged = false;
  if (data.session_id && data.session_id !== state.currentSessionId) {
    state.currentSessionId = data.session_id;
    sessionChanged = true;
  }
  // 首页空状态直接发送：会话在服务端 bootstrap_room 升级，thread_id 此刻才诞生。
  // 采纳它，收尾 finalizeTurn 才会 loadThread 规范化重画（否则流式卡停在折叠态、
  // 回复被藏起，侧栏也没有选中窗口）
  var threadAdopted = false;
  if (data.thread_id && !state.currentThreadId) {
    state.currentThreadId = data.thread_id;
    state.roomView = true;
    var exists = (state.roomThreads || []).some(function (t) {
      return t.id === data.thread_id;
    });
    if (!exists) {
      state.roomThreads = state.roomThreads || [];
      state.roomThreads.push({ id: data.thread_id, kind: "dm", title: "" });
    }
    threadAdopted = true;
  }
  if (data.attributes && Object.keys(data.attributes).length) {
    state.currentAttrs = data.attributes;
  }
  if (sessionChanged) refreshSessions();
  else if (threadAdopted) renderSessionList();
}
function onSpeaker(data) {
  // 上一位发言者的卡片先收尾（收起），再为当前 AI 新建一张卡，避免多 AI 串进同一条消息
  var prev = state.currentAssistant;
  if (prev && prev.speakerName) { prev.setThinking(false); if (!prev.revealed) prev.setOpen(false); }
  state.currentSpeaker = data || null;
  state.currentAssistant = null;
  var a = ensureAssistant();
  a.setSpeaker((data && data.name) || "AI", memberOf(data && data.member_id));
  a.setOpen(true);
  a.setThinking(true);
}
function onSpeakerSkip(data) {
  // 该 AI 自评无需发言：把这块输出整卡移除（仍保存在历史中，reload 不展示）
  var a = state.currentAssistant;
  if (a) { a.setOpen(false); a.setThinking(false); a.container.remove(); }
  state.currentAssistant = null;
  state.currentSpeaker = null;
}
function onAttributes(data) {
  if (data && data.attributes) { state.currentAttrs = data.attributes; }
}
function onThinkingDelta(data) {
  var a = ensureAssistant(); if (!a) return;
  a.thinkingEl.classList.remove("hidden"); a.thinkingEl.open = true; a.thinkingDiv.textContent += data.text;
}
function onContentDelta(data) {
  var a = ensureAssistant(); if (!a) return;
  a.contentEl.classList.add("streaming-cursor");
  setAssistantContent(a, a.rawContent + (data.text || ""));
  scrollToBottom();  // 不强制：用户上翻时停留在原位
}
function onToolCalls(data) {
  var a = ensureAssistant(); if (!a) return;
  (data.calls || []).forEach(function (c) { appendToolCallCard(a.toolRegion, c, true); });
}
function appendToolCallCard(region, call, streaming) {
  var card = document.createElement("div");
  card.className = "tool-card";
  card.dataset.id = call.id;
  var tname = document.createElement("div"); tname.className = "tname"; tname.textContent = "🔧 " + call.name;
  var tstatus = document.createElement("div"); tstatus.className = "tstatus"; tstatus.textContent = streaming ? "等待执行…" : "已完成";
  card.appendChild(tname); card.appendChild(tstatus); region.appendChild(card);
}
function onToolStart(data) {
  var card = findCard(data.id); if (!card) return;
  card.classList.remove("err"); card.classList.remove("ok");
  var spin = document.createElement("span"); spin.className = "spinner";
  var tstatus = card.querySelector(".tstatus"); tstatus.innerHTML = "";
  tstatus.appendChild(spin); tstatus.appendChild(document.createTextNode(data.status_text || ("正在调用 " + data.name)));
}
// 从工具输出中提取图片标记 [图片已生成]/[图片] <url>
function onToolEnd(data) {
  var card = findCard(data.id); if (!card) return;
  card.classList.add(data.ok ? "ok" : "err");
  card.querySelector(".tstatus").textContent = data.ok ? "✓ 完成" : "✗ 失败";
  if (isPixivCandidatesText(data.preview)) {
    // 候选缩略图：全部毛玻璃遮盖，等待 AI 筛选后取原图
    var cands = parsePixivCands(data.preview);
    if (cands.length) {
      card.querySelector(".tstatus").textContent =
        "✓ 找到 " + cands.length + " 张候选，AI 筛选中…";
      card.appendChild(makePixivGrid(cands, null, true));
    }
  } else if (isPixivPicksText(data.preview)) {
    // 精选原图到达：去掉对应候选位置的模糊展示原图（本卡仅留状态）
    revealPixivPicks(parsePixivPicks(data.preview));
  } else if (isAudioMarkerText(data.preview)) {
    // 语音标记输出：只渲染语音条，指令句与路径均不展示
    var auUrl0 = extractGeneratedAudio(data.preview);
    if (auUrl0) {
      card.appendChild(
        makeAudioCard(auUrl0, extractAudioMeta(data.preview)));
    }
  } else {
    var hasMarker = isImageMarkerText(data.preview);
    if (hasMarker) {
      // 图片标记输出：只渲染图片，指令句与路径均不展示
      var imgUrl0 = extractGeneratedImage(data.preview);
      if (imgUrl0) {
        card.appendChild(
          makeGeneratedImage(imgUrl0, extractImageMeta(data.preview)));
      }
    } else if (data.preview) {
      var pre = document.createElement("div");
      pre.className = "tpreview";
      pre.textContent = data.preview;
      card.appendChild(pre);
    }
  }
  scrollToBottom();  // 不强制：用户上翻时停留在原位
}
function appendToolResultCard(region, data) {
  var card = document.createElement("div");
  card.className = "tool-card " + (data.ok ? "ok" : "err");
  var tname = document.createElement("div"); tname.className = "tname"; tname.textContent = "🔧 " + (data.name || data.id || "tool");
  var tstatus = document.createElement("div"); tstatus.className = "tstatus"; tstatus.textContent = data.ok ? "✓ 完成" : "✗ 失败";
  card.appendChild(tname); card.appendChild(tstatus);
  var output = data.output ? String(data.output) : "";
  if (isPixivCandidatesText(output)) {
    // 候选墙：已精选位置展示原图，其余以毛玻璃占位（候选文件已清理，不再请求）
    var cands = parsePixivCands(output);
    if (cands.length) card.appendChild(makePixivGrid(cands, data.revealed || {}, false));
  } else if (isPixivPicksText(output)) {
    // 精选原图已并入搜索卡网格；本卡仅显示数量
    var n = parsePixivPicks(output).length;
    if (n) card.querySelector(".tstatus").textContent = "✓ 已精选 " + n + " 张";
  } else if (isAudioMarkerText(output)) {
    // 语音标记输出：只渲染语音条，指令句与路径均不展示
    var auUrl = extractGeneratedAudio(output);
    if (auUrl) {
      card.appendChild(makeAudioCard(auUrl, extractAudioMeta(output)));
    }
  } else {
    var hasMarker = isImageMarkerText(output);
    if (hasMarker) {
      // 图片标记输出：只渲染图片，指令句与路径均不展示
      var imgUrl = extractGeneratedImage(output);
      if (imgUrl) {
        card.appendChild(makeGeneratedImage(imgUrl, extractImageMeta(output)));
      }
    } else if (output) {
      var pre = document.createElement("div");
      pre.className = "tpreview";
      pre.textContent = output.slice(0, 300);
      card.appendChild(pre);
    }
  }
  region.appendChild(card);
}
function findCard(id) {
  if (!state.currentAssistant) return null;
  return state.currentAssistant.toolRegion.querySelector('.tool-card[data-id="' + id + '"]');
}

function onStreamError(data) {
  // fatal:false（如达到工具轮数上限）后还会跟一个 done 事件，不提前收尾
  showToast(data.message || "发生错误", "err", 5000);
  if (data.fatal !== false) finalizeTurn();
}
/* Token 用量：本轮 toast + 会话内累计（服务端持久，页面内实时叠加）*/
function recordUsage(usage) {
  if (!usage) return;
  var p = intOr(usage.prompt_tokens, 0), c = intOr(usage.completion_tokens, 0);
  if (p || c) showToast("本轮 token：输入 " + fmtNum(p) + " · 输出 " + fmtNum(c), "info", 4200);
  if (!state.currentSessionId) return;
  var sid = state.currentSessionId;
  state.tokenTotals[sid] = intOr(state.tokenTotals[sid], 0) + intOr(usage.total_tokens, p + c);
  var d = state.tokenDetails[sid] || { p: 0, c: 0 };
  state.tokenDetails[sid] = { p: intOr(d.p, 0) + p, c: intOr(d.c, 0) + c };
  renderSessionMeta();
}
function finalizeTurn() {
  var a = state.currentAssistant;
  if (a) {
    a.thinkingEl.open = false;
    a.setThinking(false);
    a.contentEl.classList.remove("streaming-cursor");
    if (a.speakerName) a.setOpen(false);  // 群聊发言结束后折叠，点击头部展开
  }
  state.pendingSend = null;
  var editingAnchor = state.turnEditAnchor;
  state.currentAssistant = null;
  state.currentSpeaker = null;
  state.streamInsertBefore = null;
  state.turnEditAnchor = null;
  // 清理运行状态与标记
  state.currentRunId = null;
  state.lastEventSeq = 0;
  state.streamTerminal = false;
  state.reconnectAttempts = 0;
  if (state.runMarkerTimer) { clearTimeout(state.runMarkerTimer); state.runMarkerTimer = null; }
  clearRunMarker();
  state.busy = false;
  el.btnSend.disabled = false;
  el.btnStop.classList.add("hidden");
  el.input.disabled = false;
  el.input.focus();
  refreshSessions();
  // 从磁盘重画：流式直插的气泡没有操作入口（✏️/🔄/分页器），编辑/重生成也需
  // 规范化 DOM。群聊窗口 canEdit=false 不挂控制
  if (state.currentThreadId) {
    // 成员记忆（含📝记录记忆）在服务端落盘，同步重拉成员，免得记忆弹窗显示旧数据
    refreshRoomMembers();
    loadThread(state.currentThreadId);
  }
}

