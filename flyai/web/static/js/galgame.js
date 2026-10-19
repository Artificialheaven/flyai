/* Galgame 模式：全屏视觉小说视图（单角色）。
 *
 * 独立流式循环（不影响普通聊天）：复用 parseFrame，SSE 序号存在
 * state.galgame.lastSeq；断线指数退避重连。
 * 流式时按行切分，完整对白行立即请求 TTS；galgame_choice 工具开始时
 * 启动打字机回放，回放结束后解锁选项；无选项的终态则显示自由输入。
 */
"use strict";

var GV_VOICE_KEY = "flyai-galgame-voice";
var GV_MUSIC_KEY = "flyai-galgame-music";
var GV_AUTO_KEY = "flyai-galgame-auto";
var GV_BGM_VOL_KEY = "flyai-galgame-bgm-vol";
var GV_TTS_VOL_KEY = "flyai-galgame-tts-vol";

var gvEl = {};
var gvBound = false;

function cacheGvElements() {
  gvEl.view = document.getElementById("galgame-view");
  gvEl.bg = document.getElementById("gv-bg");
  gvEl.sprite = document.getElementById("gv-sprite");
  gvEl.settings = document.getElementById("gv-settings");
  gvEl.settingsDialog = document.getElementById("gv-settings-dialog");
  gvEl.gvReturn = document.getElementById("gv-return");
  gvEl.settingsClose = document.getElementById("gv-settings-close");
  gvEl.entry = document.getElementById("gv-entry");
  gvEl.entryText = document.getElementById("gv-entry-text");
  gvEl.entryStart = document.getElementById("gv-entry-start");
  gvEl.choices = document.getElementById("gvchoices");
  gvEl.box = document.getElementById("gv-box");
  gvEl.name = document.getElementById("gv-name");
  gvEl.text = document.getElementById("gv-text");
  gvEl.voice = document.getElementById("gv-voice");
  gvEl.music = document.getElementById("gv-music");
  gvEl.auto = document.getElementById("gv-auto");
  gvEl.backlogBtn = document.getElementById("gv-backlog-btn");
  gvEl.backlog = document.getElementById("gv-backlog");
  gvEl.backlogList = document.getElementById("gv-backlog-list");
  gvEl.bgmVol = document.getElementById("gv-bgm-vol");
  gvEl.bgmVolVal = document.getElementById("gv-bgm-vol-val");
  gvEl.ttsVol = document.getElementById("gv-tts-vol");
  gvEl.ttsVolVal = document.getElementById("gv-tts-vol-val");
  gvEl.spName = document.getElementById("gv-sp-name");
  gvEl.spSave = document.getElementById("gv-sp-save");
  gvEl.spList = document.getElementById("gv-sp-list");
  gvEl.rbDialog = document.getElementById("gv-rollback-dialog");
  gvEl.rbText = document.getElementById("gv-rb-text");
  gvEl.rbSave = document.getElementById("gv-rb-save");
  gvEl.rbSkip = document.getElementById("gv-rb-skip");
  gvEl.rbCancel = document.getElementById("gv-rb-cancel");
  gvEl.debug = document.getElementById("gv-debug");
  gvEl.debugHead = document.getElementById("gv-debug-head");
  gvEl.debugBody = document.getElementById("gv-debug-body");
  gvEl.debugToggle = document.getElementById("gv-debug-toggle");
  gvEl.replayTip = document.getElementById("gv-replay-tip");
  gvEl.replayMin = document.getElementById("gv-replay-min");
  gvEl.replayExit = document.getElementById("gv-replay-exit");
}

function enterGalgame(s) {
  cacheGvElements();
  var gv = state.galgame;
  // 重入同一会话且运行仍活跃：仅恢复视图，不重置流式状态
  if (gv && gv.sid === s.id && gv.active) {
    gv.session = s;
    showGalgameView();
    if (gv.play && gv.play.paused) resumePlayback();
    return;
  }

  var thread = (s.threads || []).length ? s.threads[0] : null;
  var aiMember = null;
  (s.members || []).forEach(function (m) {
    if (!m.is_user && aiMember === null) aiMember = m;
  });
  var charName = aiMember
    ? (typeof memberDisplayName === "function" ? memberDisplayName(aiMember) : aiMember.name)
    : "";

  state.galgame = {
    session: s,
    sid: s.id,
    tid: thread ? thread.id : "",
    charName: charName || "AI",
    // 场景
    scene: s.galgame || {},
    keyed: (gv && gv.sid === s.id && gv.keyed) || {},
    // 已合成 TTS 缓存：对白文本 -> url（刷新后直接复用，不再请求）
    ttsCache: s.galgame_tts || {},
    // 运行状态
    active: false,
    runId: null,
    abort: null,
    lastSeq: 0,
    terminal: false,
    reconnectAttempts: 0,
    suppressTts: false,
    // 内容累积（本次 LLM 回复）
    rawContent: "",
    lineBuffer: "",
    ttsLines: [],      // 与 rawContent 非空行一一对应：{spoken,url,promise}
    segStart: 0,       // 本段回复在 ttsLines 中的起点（每次选项后续读重置）
    segments: null,
    play: null,
    // 选项
    awaitingChoice: false,
    pendingOptions: null,
    // 重入时发现的「失去响应的 galgame_choice」：{callId, options}
    dangling: null,
    curTool: "",          // 当前正在执行的工具名（debug 用）
    speakerName: "",
    // debug 面板
    debugLog: [],
    debugTimer: null,
    debugCollapsed: false,
    // 语音
    voiceOn: true,
    voiceBlocked: false,
    voiceWarned: false,
    // 背景音乐
    musicOn: true,
    musicEnsured: false,
    // 自动播放（句末自动推进；遇选项停）
    autoOn: false,
    autoTimer: null,
    // 重放模式：{msgs, turns, pos, scene}；纯本地回放历史，不发起新演出
    replay: null,
    // 回放结束钩子（重放模式用；正常演出走 awaitingChoice/continue 分支）
    _onPlaybackDone: null,
    // 音量（0-100，默认满格=历史行为）
    bgmVolume: 100,
    ttsVolume: 100,
  };
  gv = state.galgame;
  initVoiceState();
  initMusicState();
  initAutoState();
  initVolumeSettings();

  bindGvControls();
  showGalgameView();
  applyScene(gv.scene, false);
  renderDebug();
  dbgEvent("进入 Galgame：" + gv.charName);

  // 可能存在刷新前未结束的运行：先探活，成功则由事件回放重建，不先重播
  var pr = state.pendingResume;
  if (pr && pr.sid === gv.sid) {
    state.pendingResume = null;
    probeAndResume(pr.runId, thread);
    return;
  }
  // 本设备无标记：运行可能在另一台设备/标签页上。先向服务端发现，
  // 有活跃 run 直接续读，否则才按磁盘历史重播（避免盲发 /chat 撞 409）
  discoverActiveRun(thread);
}

function discoverActiveRun(thread) {
  var gv = state.galgame;
  fetchJSON("/api/chat/active-run?session_id=" + encodeURIComponent(gv.sid))
    .then(function (d) {
      if (state.galgame !== gv) return;  // 等待期间已切换会话/回档
      if (d.run_id) {
        dbgEvent("发现其它设备上的活跃演出，续读…");
        probeAndResume(d.run_id, thread);
      } else {
        showFromThread(thread);
      }
    })
    .catch(function () {
      if (state.galgame !== gv) return;
      showFromThread(thread);
    });
}

function showFromThread(thread) {
  var messages = thread ? (thread.messages || []) : [];
  if (!messages.length || !replayLastAssistant(thread)) {
    // 无历史，或历史里没有带内容的助理消息（如仅工具调用/失败的首轮）
    showEntry("initial");
  }
}

function showGalgameView() {
  var app = document.getElementById("app");
  if (app) app.classList.add("gv-app-hidden");
  gvEl.view.classList.remove("hidden");
}

/* ==== 控件绑定（仅一次）==== */
function bindGvControls() {
  if (gvBound) return;
  gvBound = true;

  gvEl.view.addEventListener("click", onStageClick);
  // 这些区域的点击不推进剧情
  [gvEl.settings, gvEl.backlogBtn, gvEl.voice, gvEl.music, gvEl.auto,
   gvEl.entry, gvEl.choices, gvEl.backlog, gvEl.replayTip,
   gvEl.settingsDialog, gvEl.rbDialog]
    .forEach(function (node) {
      if (node) node.addEventListener("click", function (e) { e.stopPropagation(); });
    });

  gvEl.settings.addEventListener("click", function () {
    if (gvEl.settingsDialog.open) {
      gvEl.settingsDialog.close();
    } else {
      gvEl.settingsDialog.showModal();
      loadSavePoints();  // 每次打开刷新存档点清单
    }
  });
  gvEl.settingsClose.addEventListener("click", function () {
    gvEl.settingsDialog.close();
  });
  gvEl.gvReturn.addEventListener("click", function () {
    gvEl.settingsDialog.close();
    exitGalgame();
  });

  gvEl.entryStart.addEventListener("click", onEntryStart);
  gvEl.voice.addEventListener("click", toggleVoice);
  gvEl.music.addEventListener("click", toggleMusic);
  gvEl.auto.addEventListener("click", toggleAuto);
  gvEl.backlogBtn.addEventListener("click", openBacklog);
  if (gvEl.replayExit) {
    gvEl.replayExit.addEventListener("click", function () { endReplay(); });
  }
  // 提示条可最小化为左下角小圆标（不挡立绘）；最小化后点圆标恢复
  if (gvEl.replayTip) {
    gvEl.replayTip.addEventListener("click", function (e) {
      var tip = gvEl.replayTip;
      var minimized = tip.classList.contains("gv-replay-minimized");
      var isMinBtn = e.target.closest
        ? e.target.closest("#gv-replay-min") : (e.target === gvEl.replayMin);
      if (isMinBtn) {
        if (!minimized) {
          tip.classList.add("gv-replay-minimized");
          dbgEvent("重放提示条已最小化");
        }
      } else if (minimized) {
        tip.classList.remove("gv-replay-minimized");
        dbgEvent("重放提示条已展开");
      }
    });
  }

  // 对白回看：点列表外区域关闭；列表内点击不冒泡到舞台
  gvEl.backlog.addEventListener("click", function () { closeBacklog(); });
  gvEl.backlogList.addEventListener("click", function (e) {
    e.stopPropagation();
    // 「从头回放」：从第一个剧情节点开始本地重放
    var allBtn = e.target.closest ? e.target.closest(".gv-bl-replay-all") : null;
    if (allBtn) {
      startReplay(state.galgame._blMsgs || [], null);
      return;
    }
    // 「从此处重放」：进入本地重放模式
    var repBtn = e.target.closest ? e.target.closest(".gv-bl-replay") : null;
    if (repBtn) {
      var crow = repBtn.closest(".gv-bl-choice");
      if (crow) {
        startReplay(state.galgame._blMsgs || [], parseInt(crow.dataset.idx, 10));
      }
      return;
    }
    // 点台词行：若有已保存的语音则重听
    var row = e.target.closest ? e.target.closest(".gv-bl-speech") : null;
    if (row && row.dataset.url) replayBacklogAudio(row.dataset.url);
  });

  // 滚轮上滑打开回看（视觉小说惯例）
  gvEl.view.addEventListener("wheel", function (e) {
    if (e.deltaY >= -8) return;
    if (gvEl.backlog.classList.contains("hidden")
        && !gvEl.settingsDialog.open && !gvEl.rbDialog.open) {
      openBacklog();
    }
  }, { passive: true });

  // Esc 关闭回看（dialog 自带 Esc，覆盖层手动处理）
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && gvEl.backlog
        && !gvEl.backlog.classList.contains("hidden")) {
      closeBacklog();
    }
  });

  // 音量滑杆（设置对话框）：拖动即时生效并持久化
  if (gvEl.bgmVol) {
    gvEl.bgmVol.addEventListener("input", function () {
      var gv = state.galgame;
      var v = parseInt(gvEl.bgmVol.value, 10) || 0;
      gv.bgmVolume = v;
      gvEl.bgmVolVal.textContent = v;
      bgmEngine.setVolume(v);
      try { localStorage.setItem(GV_BGM_VOL_KEY, String(v)); } catch (e) {}
    });
  }
  if (gvEl.ttsVol) {
    gvEl.ttsVol.addEventListener("input", function () {
      var gv = state.galgame;
      var v = parseInt(gvEl.ttsVol.value, 10) || 0;
      gv.ttsVolume = v;
      gvEl.ttsVolVal.textContent = v;
      try { localStorage.setItem(GV_TTS_VOL_KEY, String(v)); } catch (e) {}
    });
  }

  // 存档点
  gvEl.spSave.addEventListener("click", gvSaveCurrent);
  gvEl.rbSave.addEventListener("click", function () { gvDoRollback(true); });
  gvEl.rbSkip.addEventListener("click", function () { gvDoRollback(false); });
  gvEl.rbCancel.addEventListener("click", function () { gvEl.rbDialog.close(); });

  // debug 面板：点击标题或右侧 −/+ 折叠；面板内点击不推进剧情
  gvEl.debug.addEventListener("click", function (e) { e.stopPropagation(); });
  gvEl.debugHead.addEventListener("click", function () {
    toggleDebugCollapsed();
  });
  gvEl.debugToggle.addEventListener("click", function (e) {
    e.stopPropagation();
    toggleDebugCollapsed();
  });
}

function toggleDebugCollapsed() {
  var gv = state.galgame;
  gv.debugCollapsed = !gv.debugCollapsed;
  renderDebug();
}

/* ==== Debug 面板（右上角，实时状态 + 最近事件）==== */
function dbgNow() {
  var d = new Date();
  var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
  return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}
function dbgEvent(text) {
  var gv = state.galgame;
  if (!gv) return;
  gv.debugLog.push({ t: dbgNow(), text: String(text) });
  if (gv.debugLog.length > 9) gv.debugLog.shift();
  renderDebug();
}
function scheduleRenderDebug() {
  var gv = state.galgame;
  if (!gv || gv.debugTimer) return;
  gv.debugTimer = setTimeout(function () {
    gv.debugTimer = null;
    renderDebug();
  }, 120);
}
function gvActivity() {
  var gv = state.galgame;
  if (gv.play) {
    var p = gv.play;
    var phase;
    if (p.phase === "preparing") phase = "整段语音准备中";
    else if (p.phase === "typing") phase = "打字中" + (p.audio ? "（语音播放）" : "");
    else phase = "等待点击" + (p.audio ? "（语音播放）" : "");
    return "回放 " + (p.index + 1) + "/" + gv.segments.length + " · " + phase;
  }
  if (gv.awaitingChoice) return "等待用户选择";
  if (gv.active) {
    if (gv.curTool) return "工具执行：" + gv.curTool;
    return "接收内容（" + gv.rawContent.length + " 字）";
  }
  return "空闲";
}
function renderDebug() {
  var gv = state.galgame;
  if (!gvEl.debugBody) return;
  var runShort = gv.runId ? gv.runId.slice(0, 8) : "—";
  var ttsPending = 0;
  gv.ttsLines.forEach(function (e) {
    if (e.promise && !e.url) ttsPending += 1;
  });
  var lines = [
    '<div class="gv-db-line"><b>状态</b> ' + escapeHtml(gvActivity()) + "</div>",
    '<div class="gv-db-line"><b>run</b> ' + runShort + " · seq " + gv.lastSeq + "</div>",
    '<div class="gv-db-line"><b>发言</b> '
      + escapeHtml(gv.speakerName || gv.charName || "—") + "</div>",
    '<div class="gv-db-line"><b>工具</b> '
      + escapeHtml(gv.curTool || "—") + ' · <b>TTS</b> ' + ttsPending + " 待回</div>",
  ];
  var bgmLabels = {idle: "空闲", loading: "加载音色中", ready: "就绪",
                   playing: "播放中", unavailable: "不可用"};
  lines.push('<div class="gv-db-line"><b>BGM</b> '
    + bgmLabels[bgmEngine.status()] + (gv.musicOn ? "" : "（已关）") + "</div>");
  if (gv.debugCollapsed) {
    // 折叠：只留标题条，正文整体隐藏；悬停标题可看当前状态
    gvEl.debug.classList.add("gv-debug-collapsed");
    gvEl.debugToggle.textContent = "+";
    gvEl.debugHead.title = gvActivity();
    gvEl.debugBody.innerHTML = "";
    return;
  }
  gvEl.debug.classList.remove("gv-debug-collapsed");
  gvEl.debugToggle.textContent = "−";
  gvEl.debugHead.title = "点击折叠";
  lines.push('<div class="gv-db-sep"></div>');
  var recent = gv.debugLog.slice(-5);
  for (var i = 0; i < recent.length; i++) {
    lines.push('<div class="gv-db-log">' + recent[i].t + " "
      + escapeHtml(recent[i].text) + "</div>");
  }
  gvEl.debugBody.innerHTML = lines.join("");
}

/* ==== 初始剧情 / 继续输入（复用 #gv-entry）==== */
function showEntry(mode) {
  var gv = state.galgame;
  gv.entryMode = mode;
  var title = gvEl.entry.querySelector("h3");
  var sub = gvEl.entry.querySelector(".sub");
  if (mode === "continue") {
    if (title) title.textContent = "接下来……";
    if (sub) sub.textContent = "自由输入动作或台词推进剧情；留空则无法继续。";
    gvEl.entryStart.textContent = "继续";
    gvEl.entryText.value = "";
  } else {
    if (title) title.textContent = "故事的开端";
    if (sub) {
      sub.textContent = "写下初始剧情（你是谁、身在何处、发生了什么），AI 将据此绘制背景与立绘并开始故事。";
    }
    gvEl.entryStart.textContent = "开始故事";
    gvEl.entryText.value = "";
  }
  gvEl.entry.classList.remove("hidden");
}
function hideEntry() {
  gvEl.entry.classList.add("hidden");
}
function onEntryStart() {
  var gv = state.galgame;
  var text = gvEl.entryText.value.trim();
  if (!text) {
    showToast("请先写下剧情内容", "err");
    return;
  }
  hideEntry();
  postGalgame(text);
}

/* ==== 自动播放（句末自动推进；遇选项停）==== */
function initAutoState() {
  var gv = state.galgame;
  var saved = null;
  try { saved = localStorage.getItem(GV_AUTO_KEY); } catch (e) {}
  gv.autoOn = saved === "1";
  gv.autoTimer = null;
  renderAuto();
}
function renderAuto() {
  var gv = state.galgame;
  if (!gvEl.auto) return;
  gvEl.auto.textContent = gv.autoOn ? "⏩" : "▶";
  gvEl.auto.classList.toggle("gv-auto-on", gv.autoOn);
  gvEl.auto.title = gv.autoOn
    ? "关闭自动播放"
    : "自动播放：句末自动推进，遇选项停";
}
function toggleAuto() {
  var gv = state.galgame;
  gv.autoOn = !gv.autoOn;
  try { localStorage.setItem(GV_AUTO_KEY, gv.autoOn ? "1" : "0"); } catch (e) {}
  renderAuto();
  if (!gv.autoOn) {
    clearAutoTimer();
  } else {
    var p = gv.play;
    // 当前句已显示完：直接安排；语音在播则等其 ended
    if (p && p.phase === "shown" && !p.audio) onSegmentShown(p, 900);
  }
}
function clearAutoTimer() {
  var gv = state.galgame;
  if (gv.autoTimer) { clearTimeout(gv.autoTimer); gv.autoTimer = null; }
}
// 一句已完整显示：auto 开 → 延迟后进入下一句；语音未播完则等 ended 再安排
function onSegmentShown(p, delay) {
  var gv = state.galgame;
  if (!gv.autoOn) return;
  clearAutoTimer();
  gv.autoTimer = setTimeout(function () {
    gv.autoTimer = null;
    if (gv.play !== p || p.phase !== "shown") return;
    if (p.audio) return;
    nextSegment(p);
  }, delay || 1100);
}

/* ==== 对白回看 Backlog ==== */
function openBacklog() {
  var gv = state.galgame;
  if (!gv || !gvEl.backlog.classList.contains("hidden")) return;
  gvEl.backlog.classList.remove("hidden");
  if (!gv.tid) {
    gvEl.backlogList.innerHTML = '<div class="gv-bl-empty">暂时没有对白</div>';
    return;
  }
  gvEl.backlogList.innerHTML = '<div class="gv-bl-empty">读取中…</div>';
  var url = "/api/rooms/" + encodeURIComponent(gv.sid)
    + "/threads/" + encodeURIComponent(gv.tid);
  fetchJSON(url).then(function (t) {
    if (!gvEl.backlog.classList.contains("hidden")) {
      renderBacklog(t.messages || []);
    }
  }).catch(function (e) {
    gvEl.backlogList.innerHTML = "";
    showToast("对白读取失败：" + e.message, "err");
  });
}
function closeBacklog() {
  gvEl.backlog.classList.add("hidden");
  gvEl.backlogList.innerHTML = "";
  gvEl.backlogList.scrollTop = 0;
}
function renderBacklog(messages) {
  var gv = state.galgame;
  var html = ['<button type="button" class="gv-bl-replay-all">🔁 从头开始回放整个故事</button>'];
  messages.forEach(function (m, mi) {
    if (m.tombstone) return;  // 撤回墓碑永不进 backlog
    if (m.role === "user") {
      var playerText = (m.content || "").trim();
      if (playerText) {
        html.push('<div class="gv-bl-row gv-bl-playerline">'
          + '<div class="gv-bl-choice-main"><span class="gv-bl-choice-tag">你的输入</span>'
          + '<span class="gv-bl-choice-text">' + escapeHtml(playerText)
          + "</span></div></div>");
      }
      return;
    }
    if (m.role !== "assistant") return;
    splitLines(m.content || "").forEach(function (line) {
      var seg = classifyLine(line);
      if (seg.type === "speech") {
        var url = gv.ttsCache[seg.spoken] || "";
        html.push('<div class="gv-bl-row gv-bl-speech'
          + (url ? " gv-bl-hasvoice" : "") + '"'
          + (url ? ' data-url="' + escapeHtml(url) + '"' : "") + ">"
          + '<div class="gv-bl-name">' + escapeHtml(gv.charName) + "</div>"
          + '<div class="gv-bl-text">' + escapeHtml(seg.display) + "</div></div>");
      } else {
        html.push('<div class="gv-bl-row gv-bl-' + seg.type + '">'
          + '<div class="gv-bl-text">' + escapeHtml(seg.display)
          + "</div></div>");
      }
    });
    // 玩家选项的真实结局：已作答 / 超时未选 / 出错 / 仍在等待
    var call = gvAssistantChoiceCall(m);
    if (!call) return;
    var oc = gvChoiceOutcome(messages, mi, call.id);
    if (oc.state === "answered") {
      html.push('<div class="gv-bl-row gv-bl-choice" data-idx="' + mi
        + '" data-call="' + escapeHtml(call.id) + '">'
        + '<div class="gv-bl-choice-main"><span class="gv-bl-choice-tag">你的选择</span>'
        + '<span class="gv-bl-choice-text">' + escapeHtml(oc.text) + "</span></div>"
        + '<button type="button" class="gv-bl-replay">🔁 从此处重放</button></div>');
    } else if (oc.state === "timeout") {
      html.push('<div class="gv-bl-row gv-bl-choice gv-bl-choice-timeout" data-idx="'
        + mi + '">'
        + '<div class="gv-bl-choice-main"><span class="gv-bl-choice-tag">未选择</span>'
        + '<span class="gv-bl-choice-text">⏱ 超时（600 秒未作答，剧情自行继续）</span>'
        + "</div>"
        + '<button type="button" class="gv-bl-replay">🔁 从此处重放</button></div>');
    } else if (oc.state === "error") {
      html.push('<div class="gv-bl-row gv-bl-choice gv-bl-choice-error">'
        + '<div class="gv-bl-choice-main"><span class="gv-bl-choice-tag">未选择</span>'
        + '<span class="gv-bl-choice-text">⚠ '
        + escapeHtml(oc.text || "选项工具出错") + "</span></div></div>");
    } else {
      html.push('<div class="gv-bl-row gv-bl-choice gv-bl-choice-pending">'
        + '<div class="gv-bl-choice-main"><span class="gv-bl-choice-tag">选项</span>'
        + '<span class="gv-bl-choice-text">等待选择…</span></div></div>');
    }
  });
  gv._blMsgs = messages;
  gvEl.backlogList.innerHTML = html.join("")
    || '<div class="gv-bl-empty">暂时没有对白</div>';
  // 打开时停在最新一条，向上滚动回看更早内容
  gvEl.backlogList.scrollTop = gvEl.backlogList.scrollHeight;
}
function replayBacklogAudio(url) {
  var gv = state.galgame;
  // 与演出音频一致：TTS 音量 + 压低 BGM
  var a = new Audio(url);
  a.volume = Math.max(0, Math.min(100, gv.ttsVolume)) / 100;
  bgmEngine.setDuck(true);
  a.addEventListener("ended", function () { bgmEngine.setDuck(false); });
  var pr = a.play();
  if (pr && pr.catch) pr.catch(function () { bgmEngine.setDuck(false); });
}

// gv 状态引用的线程对象（按 tid，找不到取第一个）
function gvThreadObj() {
  var gv = state.galgame;
  var threads = (gv.session && gv.session.threads) || [];
  for (var i = 0; i < threads.length; i++) {
    if (threads[i].id === gv.tid) return threads[i];
  }
  return threads[0] || null;
}

/* ==== 剧情重放（本地回放历史段落；选项只给当时选中的一个）==== */
// 取助理消息上的 galgame_choice 工具调用
function gvAssistantChoiceCall(m) {
  var calls = m.tool_calls || [];
  for (var i = 0; i < calls.length; i++) {
    if (calls[i].name === "galgame_choice") return calls[i];
  }
  return null;
}
// 选项的最终结局：answered=已选 / timeout=超时未选 / error=工具出错 /
// pending=仍在等待（只可能是最新一条）
function gvChoiceOutcome(msgs, assistantIdx, callId) {
  for (var k = assistantIdx + 1; k < msgs.length; k++) {
    if (msgs[k].role === "assistant" || msgs[k].role === "user" || msgs[k].tombstone) break;
    if (msgs[k].role === "tool" && msgs[k].tool_call_id === callId) {
      var content = msgs[k].content || "";
      var mm = /用户选择了：「([^」]*)」/.exec(content);
      if (mm) return {state: "answered", text: mm[1]};
      if (/^\[工具错误\][\s\S]*超时/.test(content)) {
        return {state: "timeout", text: ""};
      }
      if (/^\[工具错误\]/.test(content)) {
        return {state: "error",
                text: content.replace(/^\[工具错误\]\s*/, "").slice(0, 80)};
      }
      return {state: "error", text: "工具未返回选择"};
    }
  }
  return {state: "pending", text: ""};
}
// 从助理消息之后的工具结果里取该 choice 的选中文案（遇下一条消息节点为止）
function gvChoiceSelected(msgs, assistantIdx, callId) {
  var oc = gvChoiceOutcome(msgs, assistantIdx, callId);
  return oc.state === "answered" ? oc.text : "";
}
// 时间线节点：按顺序的 user / assistant 消息下标；afterIdx<0 表示从头
function gvTimeline(msgs, afterIdx) {
  var out = [];
  for (var k = 0; k < msgs.length; k++) {
    if (k <= afterIdx) continue;
    if ((msgs[k].role === "assistant" || msgs[k].role === "user") && !msgs[k].tombstone) {
      out.push(k);
    }
  }
  return out;
}
// 重放要播放的助理消息下标：起点之后的全部 assistant（user 节点由 gvTimeline 收）
function gvReplayTurns(msgs, startIdx) {
  return gvTimeline(msgs, startIdx).filter(function (i) {
    return msgs[i].role === "assistant";
  });
}
// 由历史消息构建演出段落（语音地址取已保存映射，缺失则播放时按需补合成）
function buildHistorySegments(m) {
  var gv = state.galgame;
  if (m.tombstone) return [];  // 撤回墓碑不参与演出
  if (m.role === "user") {
    // 玩家主动输入（开场剧情、时间跳跃）：作为玩家叙述行展示，不发语音
    var t = (m.content || "").trim();
    return t ? [{type: "playerline", display: t, spoken: "", tts: null}] : [];
  }
  return splitLines(m.content || "").map(function (line) {
    var seg = classifyLine(line);
    seg.tts = seg.spoken
      ? {spoken: seg.spoken, url: gv.ttsCache[seg.spoken] || null, promise: null}
      : null;
    return seg;
  });
}
// 解析工具结果中的场景标记，返回更新后的场景（[场景切换] 为整体替换）
function parseSceneMarker(content, scene) {
  scene = scene || {};
  var cm = /^\[场景切换\]\s*(\{.*\})\s*$/.exec(content || "");
  if (cm) {
    try { return JSON.parse(cm[1]); } catch (e) {}
  }
  var am = /^\[背景已生成\]\s*(\S+)/m.exec(content || "");
  if (am && !((scene.background || {}).url)) {
    scene.background = {id: "", url: am[1]};
  }
  var sm = /^\[立绘已生成\]\s*(\S+)/m.exec(content || "");
  if (sm && !((scene.sprite || {}).url)) {
    scene.sprite = {id: "", url: sm[1]};
  }
  var gm = /^\[音乐已生成\]\s*(\S+)/m.exec(content || "");
  if (gm && !((scene.music || {}).url)) {
    scene.music = {id: "", url: gm[1]};
  }
  return scene;
}
// 重建截至某条消息之前的场景（初始场景 + 此前全部工具标记）
function replaySceneUpTo(msgs, beforeIdx) {
  var scene = {};
  for (var k = 0; k < beforeIdx && k < msgs.length; k++) {
    if (msgs[k].role === "tool") {
      scene = parseSceneMarker(msgs[k].content || "", scene);
    }
  }
  return scene;
}
// 应用某个时间线节点之后工具结果中的场景标记（到下一个时间线节点为止）
function replayApplyTurnScene(msgs, idx, scene) {
  for (var k = idx + 1; k < msgs.length; k++) {
    if (msgs[k].role === "assistant" || msgs[k].role === "user" || msgs[k].tombstone) break;
    if (msgs[k].role === "tool") {
      scene = parseSceneMarker(msgs[k].content || "", scene);
    }
  }
  return scene;
}

// startIdx 为选项所在消息下标 → 从其后重放；为 null → 从头回放整条时间线
function startReplay(msgs, startIdx) {
  var gv = state.galgame;
  if (!gv || !msgs.length) return;
  // 正在流式接收（非等选项）时不允许重放，避免与新内容打架
  if (gv.active && !gv.awaitingChoice) {
    showToast("当前演出正在进行，结束后再来重放吧", "err");
    return;
  }
  var fromStart = startIdx == null;
  var turns = gvTimeline(msgs, fromStart ? -1 : startIdx);
  if (!turns.length) {
    showToast(fromStart ? "还没有剧情可回放" : "这个选择之后还没有剧情可重放", "err");
    return;
  }

  closeBacklog();
  clearAutoTimer();
  try { if (gv.play && gv.play.audio) gv.play.audio.pause(); } catch (e) {}
  hideChoices();
  hideEntry();

  var scene = replaySceneUpTo(msgs, turns[0]);
  gv.replay = {msgs: msgs, turns: turns, pos: 0, scene: scene};
  gvEl.replayTip.classList.remove("hidden");
  gvEl.replayTip.classList.remove("gv-replay-minimized");
  dbgEvent(fromStart
    ? ("从头回放：共 " + turns.length + " 个节点")
    : ("开始重放：从消息 " + startIdx + " 起，共 " + turns.length + " 个节点"));
  replayNextTurn();
}

function replayNextTurn() {
  var gv = state.galgame;
  var rp = gv.replay;
  if (!rp) return;
  if (rp.pos >= rp.turns.length) { endReplay(); return; }
  var idx = rp.turns[rp.pos];
  rp.scene = replayApplyTurnScene(rp.msgs, idx, rp.scene);
  var segs = buildHistorySegments(rp.msgs[idx]);
  // 空内容节点（纯工具调用轮）：瞬间穿过，其场景标记已并入累积场景
  if (!segs.length) {
    rp.pos += 1;
    replayNextTurn();
    return;
  }
  applyScene(rp.scene, true);
  gv._onPlaybackDone = replayTurnDone;
  startPlayback(segs);
}

// 一段历史演出结束：按该节点选项的真实结局决定下一步
function replayTurnDone() {
  var gv = state.galgame;
  var rp = gv.replay;
  if (!rp) return;
  var idx = rp.turns[rp.pos];
  var call = gvAssistantChoiceCall(rp.msgs[idx]);
  if (!call) {
    // 无选项节点：结尾节点 → 结束回放；中间节点（罕见）直接续下一段
    if (rp.pos >= rp.turns.length - 1) endReplay();
    else { rp.pos += 1; replayNextTurn(); }
    return;
  }
  var oc = gvChoiceOutcome(rp.msgs, idx, call.id);
  if (oc.state === "answered") {
    renderReplayChoice(oc.text, "choice");
  } else if (oc.state === "timeout") {
    renderReplayChoice("⏱ 当时超时未选择（600 秒未作答，点击继续）", "timeout");
  } else if (oc.state === "error") {
    renderReplayChoice("⚠ " + (oc.text || "选项工具出错") + "（点击继续）", "error");
  } else {
    // 选项仍在真实等待（只可能是最新节点）：重放不能作答，结束后重建接上
    endReplay();
  }
}

function renderReplayChoice(text, kind) {
  var gv = state.galgame;
  gvEl.choices.innerHTML = "";
  var b = document.createElement("button");
  b.type = "button";
  b.className = "gv-choice-btn gv-replay-choice"
    + (kind === "timeout" ? " gv-replay-timeout"
       : kind === "error" ? " gv-replay-error" : "");
  b.textContent = text;
  b.addEventListener("click", function () {
    var st = state.galgame;
    if (!st.replay) return;
    dbgEvent(kind === "choice"
      ? ("重放选择：" + text.slice(0, 20))
      : "重放：越过未选择节点");
    hideChoices();
    st.replay.pos += 1;
    replayNextTurn();
  });
  gvEl.choices.appendChild(b);
  gvEl.choices.classList.remove("hidden");
}

// 重放结束：丢弃重放视图，全量重建（与 gvAfterRollback 同路径：活跃 run
// 会被发现续读，未响应选项会重新弹出）
function endReplay() {
  var gv = state.galgame;
  var sid = gv.sid;
  if (!gv.replay) return;
  gv.replay = null;
  gv._onPlaybackDone = null;
  try { if (gv.abort) gv.abort.abort(); } catch (e) {}
  gv.awaitingChoice = false;
  gv.pendingOptions = null;
  try { hideChoices(); } catch (e) {}
  try { hideEntry(); } catch (e) {}
  bgmEngine.stop();
  bgmEngine.setDuck(false);
  try { clearRunMarker(); } catch (e) {}
  gvEl.replayTip.classList.add("hidden");
  gvEl.replayTip.classList.remove("gv-replay-minimized");
  showToast("重放结束", "ok");
  selectSession(sid);
}

/* ==== 存档点 ==== */
function gvSpTime(iso) {
  // "2026-10-04T19:20:00" → "10-04 19:20"
  return String(iso || "").slice(5, 16).replace("T", " ");
}

function loadSavePoints() {
  var gv = state.galgame;
  if (!gv || !gvEl.spList) return;
  gvEl.spList.innerHTML = '<div class="gv-sp-empty">读取中…</div>';
  fetchJSON("/api/galgame/savepoints?session_id=" + encodeURIComponent(gv.sid))
    .then(function (d) {
      gvRenderSavePoints(d.savepoints || []);
    })
    .catch(function (e) {
      gvEl.spList.innerHTML = "";
      showToast("存档点读取失败：" + e.message, "err");
    });
}

function gvRenderSavePoints(items) {
  gvEl.spList.innerHTML = "";
  if (!items.length) {
    gvEl.spList.innerHTML = '<div class="gv-sp-empty">还没有存档点</div>';
    return;
  }
  // 最新在前
  items.slice().reverse().forEach(function (sp) {
    var row = document.createElement("div");
    row.className = "gv-sp-item";
    if (sp.thumb) {
      var thumb = document.createElement("div");
      thumb.className = "gv-sp-thumb";
      var img = document.createElement("img");
      img.alt = "";
      img.src = assetUrl(sp.thumb);
      img.addEventListener("error", function () { thumb.remove(); });
      thumb.appendChild(img);
      row.appendChild(thumb);
    }
    var info = document.createElement("div");
    info.className = "gv-sp-info";
    info.title = sp.name;
    var b = document.createElement("b");
    b.textContent = sp.name;
    var meta = document.createElement("span");
    meta.textContent = gvSpTime(sp.created_at) + " · " + sp.messages + " 条消息";
    info.appendChild(b);
    info.appendChild(meta);
    var ops = document.createElement("div");
    ops.className = "gv-sp-ops";
    var back = document.createElement("button");
    back.type = "button";
    back.className = "btn primary gv-sp-back";
    back.textContent = "回到这里";
    back.addEventListener("click", function () { gvAskRollback(sp); });
    var del = document.createElement("button");
    del.type = "button";
    del.className = "btn gv-sp-del";
    del.textContent = "删除";
    del.addEventListener("click", function () { gvDeleteSavePoint(sp); });
    ops.appendChild(back);
    ops.appendChild(del);
    row.appendChild(info);
    row.appendChild(ops);
    gvEl.spList.appendChild(row);
  });
}

function gvSaveCurrent() {
  var gv = state.galgame;
  var name = gvEl.spName ? gvEl.spName.value.trim() : "";
  var btn = gvEl.spSave;
  btn.disabled = true;
  fetchJSON("/api/galgame/savepoints", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: gv.sid, name: name}),
  }).then(function () {
    showToast("已保存存档点", "ok");
    if (gvEl.spName) gvEl.spName.value = "";
    dbgEvent("保存存档点");
    loadSavePoints();
  }).catch(function (e) {
    showToast("保存失败：" + e.message, "err", 4000);
  }).finally(function () { btn.disabled = false; });
}

var gvPendingRollback = null;

function gvAskRollback(sp) {
  gvPendingRollback = sp;
  gvEl.rbText.textContent = "目标存档点：" + sp.name +
    "（" + gvSpTime(sp.created_at) + "，" + sp.messages + " 条消息）";
  gvEl.rbDialog.showModal();
}

function gvDoRollback(saveCurrent) {
  var gv = state.galgame;
  var sp = gvPendingRollback;
  if (!sp) return;
  var btns = [gvEl.rbSave, gvEl.rbSkip, gvEl.rbCancel];
  btns.forEach(function (b) { b.disabled = true; });
  fetchJSON("/api/galgame/savepoints/restore", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      session_id: gv.sid,
      savepoint_id: sp.id,
      save_current: saveCurrent,
      current_name: "",
    }),
  }).then(function () {
    gvEl.rbDialog.close();
    gvEl.settingsDialog.close();
    dbgEvent("回到存档点：" + sp.name);
    gvAfterRollback();
  }).catch(function (e) {
    showToast("回档失败：" + e.message, "err", 4000);
  }).finally(function () {
    btns.forEach(function (b) { b.disabled = false; });
  });
}

function gvDeleteSavePoint(sp) {
  var gv = state.galgame;
  fetchJSON("/api/galgame/savepoints/delete", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({session_id: gv.sid, savepoint_id: sp.id}),
  }).then(function () {
    dbgEvent("删除存档点：" + sp.name);
    loadSavePoints();
  }).catch(function (e) {
    showToast("删除失败：" + e.message, "err");
  });
}

// 回档成功后：断开当前流、停音乐，以新数据重建 Galgame（active=false
// 触发 enterGalgame 全量重建，同时保留同会话的 keyed 抠像缓存）
function gvAfterRollback() {
  var gv = state.galgame;
  var sid = gv.sid;
  try { if (gv.abort) gv.abort.abort(); } catch (e) {}
  gv.active = false;
  gv.awaitingChoice = false;
  gv.pendingOptions = null;
  try { hideChoices(); } catch (e) {}
  try { hideEntry(); } catch (e) {}
  bgmEngine.stop();
  bgmEngine.setDuck(false);
  try { clearRunMarker(); } catch (e) {}
  showToast("已回到存档点", "ok");
  selectSession(sid);
}

/* ==== 返回主界面（运行继续在后台进行，重入可续）==== */
function exitGalgame() {
  var gv = state.galgame;
  gvEl.view.classList.add("hidden");
  var app = document.getElementById("app");
  if (app) app.classList.remove("gv-app-hidden");
  pausePlayback();
  bgmEngine.stop();
  bgmEngine.setDuck(false);
  renderMusic();
  // 恢复普通房间 UI（私聊窗口）
  if (state.currentSessionId === gv.sid) {
    var t = (state.roomThreads || []).length ? state.roomThreads[0] : null;
    state.currentThreadId = t ? t.id : null;
    state.roomView = !!t;
    if (t) loadThread(t.id);
  }
}

/* ==== 场景：背景 + 立绘（立绘绿幕抠像）==== */
function applyScene(scene, autoplayMusic) {
  var gv = state.galgame;
  scene = scene || {};
  gv.scene = scene;
  gvApplyMusic(scene, autoplayMusic !== false);
  var bg = scene.background;
  if (bg && bg.url) {
    gvEl.bg.src = assetUrl(bg.url);
    gvEl.bg.classList.remove("hidden");
  } else {
    gvEl.bg.classList.add("hidden");
  }
  var sp = scene.sprite;
  if (sp && sp.url) {
    var currentUrl = sp.url;
    keySprite(currentUrl).then(function (out) {
      var st = state.galgame;
      if (!st || st.sid !== gv.sid) return;
      if (((st.scene || {}).sprite || {}).url === currentUrl) {
        gvEl.sprite.src = out;
        gvEl.sprite.classList.remove("hidden");
      }
    });
  } else {
    gvEl.sprite.classList.add("hidden");
  }
}

// 按场景态切换 BGM：无 music → 停；曲目不同且开关开 → 换；首入（无用户
// 手势、AudioContext 会被挂起）时不自动播，留给首次点击
function gvApplyMusic(scene, autoplay) {
  var gv = state.galgame;
  var mu = (scene || {}).music;
  var url = mu && mu.url ? mu.url : null;
  if (!url) {
    if (bgmEngine.status() === "playing") bgmEngine.stop();
    renderMusic();
    return;
  }
  if (autoplay && gv.musicOn && bgmEngine.currentUrl() !== url) {
    bgmEngine.lazyEnsure(function (msg) { if (msg) dbgEvent("BGM：" + msg); })
      .then(function () { bgmEngine.play(url); renderMusic(); })
      .catch(function () {});
  }
}

// 首次点击：初始化引擎并起播当前场景曲目（浏览器自动播放策略要求）
function gvFirstClickEnsure() {
  var gv = state.galgame;
  if (gv.musicEnsured || !gv.musicOn) return;
  gv.musicEnsured = true;
  bgmEngine.lazyEnsure(function (msg) {
    if (msg) dbgEvent("BGM：" + msg);
    renderMusic();
  }).then(function () {
    renderMusic();
    var url = ((gv.scene || {}).music || {}).url || null;
    if (url && gv.musicOn && bgmEngine.currentUrl() !== url) bgmEngine.play(url);
  }).catch(function () {
    dbgEvent("BGM 不可用（浏览器不支持或音色库下载失败）");
    renderMusic();
  });
}

function loadImage(url) {
  return new Promise(function (resolve, reject) {
    var img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = function () { resolve(img); };
    img.onerror = reject;
    img.src = assetUrl(url);
  });
}

// 绿幕抠像：g 显著大于 r/b → 透明；边缘按比例平滑；despill 去绿色溢出
function keySprite(url) {
  var gv = state.galgame;
  if (gv.keyed[url]) return Promise.resolve(gv.keyed[url]);
  return loadImage(url).then(function (img) {
    var w = img.naturalWidth, h = img.naturalHeight;
    var canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    var c = canvas.getContext("2d");
    c.drawImage(img, 0, 0);
    var id = c.getImageData(0, 0, w, h);
    var d = id.data;
    for (var i = 0; i < d.length; i += 4) {
      var r = d[i], g = d[i + 1], b = d[i + 2];
      var mx = Math.max(r, b);
      var dg = g - mx;
      if (g > 90 && dg > 26) {
        d[i + 3] = 0;
      } else if (g > 90 && dg > 10) {
        var factor = 1 - (dg - 10) / 16;
        d[i + 3] = Math.max(0, Math.round(d[i + 3] * factor));
      }
      if (g > mx) d[i + 1] = mx;
    }
    c.putImageData(id, 0, 0);
    var out = canvas.toDataURL("image/png");
    state.galgame.keyed[url] = out;
    return out;
  }).catch(function () {
    return url;  // 抠像失败：退回原图
  });
}

/* ==== 语音开关 ==== */
function initVoiceState() {
  var gv = state.galgame;
  var saved = null;
  try { saved = localStorage.getItem(GV_VOICE_KEY); } catch (e) {}
  gv.voiceOn = saved === null ? true : saved === "1";
  renderVoice();
}
function renderVoice() {
  var gv = state.galgame;
  gvEl.voice.textContent = gv.voiceOn ? "🔊" : "🔇";
  gvEl.voice.classList.toggle("gv-voice-off", !gv.voiceOn || gv.voiceBlocked);
  gvEl.voice.title = gv.voiceBlocked
    ? "语音不可用（权限/Key 不足）"
    : (gv.voiceOn ? "关闭语音" : "开启语音");
}
function toggleVoice() {
  var gv = state.galgame;
  if (gv.voiceOn) {
    gv.voiceOn = false;
  } else {
    gv.voiceOn = true;
    gv.voiceBlocked = false;
    gv.voiceWarned = false;
  }
  try { localStorage.setItem(GV_VOICE_KEY, gv.voiceOn ? "1" : "0"); } catch (e) {}
  renderVoice();
  if (!gv.voiceOn && gv.play && gv.play.audio) {
    gv.play.audio.pause();
    bgmEngine.setDuck(false);
  }
}

/* ==== 音量设置（BGM / TTS）==== */
function _loadVol(key, fallback) {
  var s = null;
  try { s = localStorage.getItem(key); } catch (e) {}
  var v = s === null ? fallback : parseInt(s, 10);
  if (isNaN(v)) v = fallback;
  return Math.max(0, Math.min(100, v));
}
function initVolumeSettings() {
  var gv = state.galgame;
  gv.bgmVolume = _loadVol(GV_BGM_VOL_KEY, 100);
  gv.ttsVolume = _loadVol(GV_TTS_VOL_KEY, 100);
  if (gvEl.bgmVol) {
    gvEl.bgmVol.value = gv.bgmVolume;
    gvEl.bgmVolVal.textContent = gv.bgmVolume;
  }
  if (gvEl.ttsVol) {
    gvEl.ttsVol.value = gv.ttsVolume;
    gvEl.ttsVolVal.textContent = gv.ttsVolume;
  }
  bgmEngine.setVolume(gv.bgmVolume);  // 引擎未初始化也会先记住基础音量
}

/* ==== 背景音乐开关 ==== */
function initMusicState() {
  var gv = state.galgame;
  var saved = null;
  try { saved = localStorage.getItem(GV_MUSIC_KEY); } catch (e) {}
  gv.musicOn = saved === null ? true : saved === "1";
  renderMusic();
}
function renderMusic() {
  var gv = state.galgame;
  if (!gvEl.music) return;
  gvEl.music.textContent = gv.musicOn ? "🎵" : "🔇";
  gvEl.music.classList.toggle("gv-music-off", !gv.musicOn);
  var st = bgmEngine.status();
  var tail = st === "loading" ? "（音色库加载中）"
    : st === "unavailable" ? "（当前环境不可用）" : "";
  gvEl.music.title = gv.musicOn ? "关闭背景音乐" + tail : "开启背景音乐";
}
function toggleMusic() {
  var gv = state.galgame;
  gv.musicOn = !gv.musicOn;
  try { localStorage.setItem(GV_MUSIC_KEY, gv.musicOn ? "1" : "0"); } catch (e) {}
  if (gv.musicOn) {
    var url = ((gv.scene || {}).music || {}).url || null;
    if (url) {
      bgmEngine.lazyEnsure(function (msg) { if (msg) dbgEvent("BGM：" + msg); })
        .then(function () { bgmEngine.play(url); renderMusic(); })
        .catch(function () {});
    }
  } else {
    bgmEngine.stop();
  }
  renderMusic();
}

function requestGvTts(text) {
  var gv = state.galgame;
  dbgEvent("TTS 请求：" + text.slice(0, 18));
  return fetchJSON("/api/galgame/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: gv.sid, text: text }),
  }).then(function (d) {
    dbgEvent("TTS 完成：" + text.slice(0, 18));
    gv.ttsCache[text] = d.url;
    return d.url;
  }, function (err) {
    var msg = String(err && err.message || err);
    if (msg.indexOf("权限") >= 0 || msg.indexOf("Key") >= 0) {
      gv.voiceBlocked = true;
      if (!gv.voiceWarned) { showToast(msg, "err", 5000); gv.voiceWarned = true; }
    } else if (!gv.voiceWarned) {
      showToast("语音合成失败：" + msg, "err", 4000);
      gv.voiceWarned = true;
    }
    renderVoice();
    return null;
  });
}

function ensureEntryAudio(entry) {
  if (entry.url) return Promise.resolve(entry.url);
  var gv = state.galgame;
  if (!entry.promise) {
    entry.promise = requestGvTts(entry.spoken).then(function (url) {
      entry.url = url;
      entry.promise = null;
      return url;
    });
  }
  return entry.promise;
}

/* ==== 流式连接 ==== */
// 把活跃 run 写入 localStorage（与普通聊天共用 runStorageKey）：刷新后
// app.js 的 loadResumeMarker 能读到，进入会话时走 probeAndResume 全量回放，
// 选项按钮才会回来；否则刷新只会重播磁盘段落、显示自由输入
function gvSaveMarker() {
  var gv = state.galgame;
  if (!gv.runId) return;
  try {
    localStorage.setItem(runStorageKey(), JSON.stringify({
      run_id: gv.runId,
      sid: gv.sid,
      tid: gv.tid || "",
      seq: gv.lastSeq || 0,
      ts: Date.now(),
    }));
  } catch (e) {}
}

function resetRunState() {
  var gv = state.galgame;
  gv.rawContent = "";
  gv.lineBuffer = "";
  gv.ttsLines = [];
  gv.segStart = 0;
  gv.segments = null;
  gv.play = null;
  gv.awaitingChoice = false;
  gv.pendingOptions = null;
  gv.dangling = null;
  gv.lastSeq = 0;
  gv.terminal = false;
  gv.reconnectAttempts = 0;
  gv.suppressTts = false;
  gv.curTool = "";
  hideChoices();
  hideEntry();
  gvEl.box.classList.add("hidden");
  renderDebug();
}

function postGalgame(text) {
  var gv = state.galgame;
  resetRunState();
  gv.active = true;
  var payload = {
    message: text,
    session_id: gv.sid,
    thread_id: gv.tid || undefined,
    provider_id: chosenProviderId(),
    tools_enabled: true,
    images: [],
    image_model_id: state.imageModelId || "",
    image_engine: state.sdEngine ? "sd" : "",
    target_member_ids: [],
    shuffle_speakers: false,
    disabled_tools: (typeof disabledToolNames === "function") ? disabledToolNames() : [],
  };
  dbgEvent("发送：" + text.slice(0, 24));
  gvConnect(null, { post: true, payload: payload });
}

function probeAndResume(rid, thread) {
  var gv = state.galgame;
  fetchJSON("/api/chat/runs/" + rid).then(function (r) {
    if (r.status !== "running") {
      try { clearRunMarker(); } catch (e) {}
      showFromThread(thread);  // 运行已结束：用磁盘历史重播
      return;
    }
    if (r.thread_id) gv.tid = r.thread_id;
    resetRunState();
    gv.active = true;
    gv.runId = rid;
    gv.suppressTts = true;  // 流式历史行不预请求 TTS，播放时按需补
    dbgEvent("续读运行 " + rid.slice(0, 8));
    gvConnect(rid, { post: false });
  }).catch(function (e) {
    if (String(e.message || "").indexOf("404") >= 0) {
      try { clearRunMarker(); } catch (e) {}
    }
    showFromThread(thread);
  });
}

function gvConnect(runId, opts) {
  var gv = state.galgame;
  gv.abort = new AbortController();
  var url, fetchOpts, established = !!runId;
  if (opts.post) {
    url = "/api/chat";
    fetchOpts = {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(opts.payload || {}),
    };
  } else {
    url = "/api/chat/runs/" + runId + "/events?after=" + gv.lastSeq;
    fetchOpts = { headers: authHeaders() };
  }
  fetchOpts.signal = gv.abort.signal;
  return fetch(apiUrl(url), fetchOpts).then(function (resp) {
    if (!resp.ok) {
      var err = new Error("HTTP " + resp.status);
      err.status = resp.status;
      throw err;
    }
    var rid = resp.headers.get("X-Run-Id") || runId;
    if (rid) {
      gv.runId = rid;
      established = true;
      gvSaveMarker();  // run 建立即留标记：中途刷新可续读
    }
    return readGvSSE(resp.body);
  }).then(function () {
    if (gv.terminal) return;
    return gvReconnect(gv.runId);
  }).catch(function (e) {
    if (e.name === "AbortError") return;
    // 409：会话上已有进行中的演出（可能在其它设备/标签页发起）。
    // 不报错、不弹输入框，自动发现活跃 run 并续读
    if (e.status === 409 && opts.post && !established) {
      dbgEvent("会话已有进行中的演出（409），自动续读…");
      discoverActiveRun(gvThreadObj());
      return;
    }
    if (opts.post && !established) {
      showToast("请求失败：" + e.message, "err", 5000);
      gv.active = false;
      showEntry(gv.rawContent ? "continue" : "initial");
      return;
    }
    return gvReconnect(gv.runId);
  });
}

function gvReconnect(runId) {
  var gv = state.galgame;
  if (!runId) { gv.active = false; return Promise.resolve(); }
  // 已被存档点回档等流程替换：旧循环到此为止，不得再碰新 gv
  if (state.galgame !== gv) return Promise.resolve();
  gv.reconnectAttempts += 1;
  if (gv.reconnectAttempts > 8) {
    showToast("连接中断且多次重连失败，请返回主界面后重试", "err", 6000);
    gv.active = false;
    return Promise.resolve();
  }
  var delay = Math.min(8000, Math.round(500 * Math.pow(1.5, gv.reconnectAttempts - 1)));
  showToast("连接中断，" + (delay / 1000) + " 秒后自动重连…", "info", delay + 1500);
  dbgEvent("连接中断，" + (delay / 1000) + "s 后重连 #" + gv.reconnectAttempts);
  return new Promise(function (resolve) { setTimeout(resolve, delay); }).then(function () {
    if (state.galgame !== gv) return null;  // 等待期间已被新 gv 替换
    return fetchJSON("/api/chat/runs/" + runId);
  }).then(function (info) {
    if (info === null || state.galgame !== gv) return;
    return gvConnect(runId, { post: false });
  }).catch(function (e) {
    if (state.galgame !== gv) return;
    if (String(e.message || "").indexOf("404") >= 0) {
      showToast("回答已中断（服务已重启或运行过期）", "err", 6000);
      gv.active = false;
      showEntry("continue");
      return;
    }
    return gvReconnect(runId);
  });
}

function readGvSSE(body) {
  if (!body) return Promise.resolve();
  var gv = state.galgame;
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
    if (parsed.seq) gv.lastSeq = parsed.seq;
    var h = gvHandlers[parsed.event];
    if (h) h(parsed.data);
  }
  return pump();
}

var gvHandlers = {
  message_start: function (data) {
    var gv = state.galgame;
    if (data && data.thread_id && !gv.tid) gv.tid = data.thread_id;
  },
  speaker: function (data) {
    var gv = state.galgame;
    gv.speakerName = (data && data.name) || gv.charName;
  },
  content_delta: function (data) {
    var gv = state.galgame;
    if (gv.replay) return;  // 重放期间到达的内容不写入视图（endReplay 全量重建）
    // 上一次选项已回应：新回复开始，重置段落起点
    if (gv.awaitingChoice) {
      gv.awaitingChoice = false;
      gv.segStart = gv.ttsLines.length;
      hideChoices();
      // 必须保证 rawContent 以换行收尾：上一轮末行若没有 \n，本轮首块
      // 内容会直接拼接，splitLines 把两行并成一行，之后全部行号错位、
      // 语音与文字配错（跨轮顺序错位的根因）
      if (gv.rawContent && gv.rawContent.slice(-1) !== "\n") {
        gv.rawContent += "\n";
      }
      dbgEvent("新段落开始（选项已回应）");
    }
    var chunk = (data && data.text) || "";
    gv.rawContent += chunk;
    gv.lineBuffer += chunk;
    scheduleRenderDebug();
    var idx;
    while ((idx = gv.lineBuffer.indexOf("\n")) >= 0) {
      var line = gv.lineBuffer.slice(0, idx).trim();
      gv.lineBuffer = gv.lineBuffer.slice(idx + 1);
      if (line) handleCompleteLine(line);
    }
  },
  tool_start: function (data) {
    var gv = state.galgame;
    if (gv.replay) return;
    if (data && data.name) {
      gv.curTool = data.name;
      dbgEvent("工具开始：" + data.name);
    }
    if (!data || data.name !== "galgame_choice") return;
    flushLineBuffer();
    // 兜底：正常路径 arguments 是已解析对象（provider 解析失败会给 {"_raw": ...}，
    // 个别网关可能透传 JSON 字符串）；options 也兼容单个字符串
    var args = data.arguments || {};
    if (typeof args === "string") {
      try { args = JSON.parse(args); } catch (e) { args = {}; }
    }
    var rawOpts = args.options || [];
    if (typeof rawOpts === "string") rawOpts = [rawOpts];
    var options = rawOpts.map(function (t) {
      return t == null ? "" : String(t).trim();
    }).filter(Boolean).slice(0, 4);
    gv.awaitingChoice = true;
    gv.pendingOptions = options;
    // 刷新运行标记的时间戳：选项等待最长 600s，保证刷新后能续读活跃 run
    gvSaveMarker();
    if (options.length < 2) {
      dbgEvent("警告：模型只给了 " + options.length + " 个选项");
    }
    if (gv.ttsLines.length > gv.segStart) {
      // 先整段演出：选项暂不显示，结束后在 finishPlayback 弹出
      startPlayback(buildSegments());
    } else {
      renderChoices(options, false);
    }
  },
  tool_end: function (data) {
    var gv = state.galgame;
    if (gv.replay) return;
    gv.curTool = "";
    dbgEvent("工具结束：" + (data && data.name || "?")
      + (data && data.ok ? " ✓" : " ✗"));
    // 选项刚在另一台设备/标签页上作答（本端没点过）：立即收起选项框，
    // 进入与点击设备相同的「等待新轮内容」状态，不必等到首个 content_delta
    if (data && data.name === "galgame_choice" && gv.awaitingChoice
        && data.ok !== false) {
      gv.awaitingChoice = false;
      gv.segStart = gv.ttsLines.length;
      hideChoices();
      if (gv.rawContent && gv.rawContent.slice(-1) !== "\n") {
        gv.rawContent += "\n";
      }
      dbgEvent("选项已在其它设备上作答");
    }
    var preview = (data && data.preview) || "";
    var m = /^\[场景切换\]\s*(\{.*\})\s*$/m.exec(preview);
    if (m) dbgEvent("场景切换");
    if (m) {
      var scene = null;
      try { scene = JSON.parse(m[1]); } catch (e) {}
      if (scene) applyScene(scene, true);
      return;
    }
    var am = /^\[背景已生成\]\s*(\S+)/m.exec(preview);
    if (am) {
      var im0 = new Image();
      im0.src = assetUrl(am[1]);  // 预加载
      return;
    }
    var sm = /^\[立绘已生成\]\s*(\S+)/m.exec(preview);
    if (sm) keySprite(sm[1]);  // 预抠像
    var gm = /^\[音乐已生成\]\s*(\S+)/m.exec(preview);
    if (gm) {
      dbgEvent("BGM：新曲目已生成");
      gv.scene = gv.scene || {};
      gv.scene.music = { url: gm[1] };
      var st = bgmEngine.status();
      // 引擎已就绪（用户此前点击过）→ 直接播；否则首次点击时从 scene 起播
      if (gv.musicOn && (st === "ready" || st === "playing")) {
        bgmEngine.play(gm[1]).then(renderMusic);
      }
      renderMusic();
    }
  },
  error: function (data) {
    var gv = state.galgame;
    if (gv.replay) return;
    dbgEvent("错误：" + ((data && data.message) || "?"));
    showToast((data && data.message) || "发生错误", "err", 5000);
    if (!data || data.fatal !== false) {
      gv.terminal = true;
      gv.active = false;
      try { clearRunMarker(); } catch (e) {}
      if (!gv.play) showEntry("continue");
    }
  },
  done: function (data) {
    var gv = state.galgame;
    if (gv.replay) return;
    flushLineBuffer();
    if (data && data.terminal === false) return;
    gv.terminal = true;
    gv.active = false;
    try { clearRunMarker(); } catch (e) {}
    dbgEvent("done（运行结束）");
    if (gv.ttsLines.length > gv.segStart && !gv.play) {
      startPlayback(buildSegments());
    } else if (!gv.awaitingChoice && !gv.play) {
      showEntry("continue");
    }
  },
};

function flushLineBuffer() {
  var gv = state.galgame;
  if (gv.lineBuffer) {
    var line = gv.lineBuffer.trim();
    gv.lineBuffer = "";
    if (line) handleCompleteLine(line);
  }
}

function handleCompleteLine(line) {
  var gv = state.galgame;
  var spoken = collectSpoken(line).join("");
  var entry = { spoken: spoken, url: gv.ttsCache[spoken] || null, promise: null };
  if (spoken && !entry.url && gv.voiceOn && !gv.suppressTts) {
    entry.promise = requestGvTts(spoken).then(function (url) {
      entry.url = url;
      entry.promise = null;
      return url;
    });
  }
  gv.ttsLines.push(entry);
}

/* ==== 行解析与分类 ==== */
// 收集一行内所有引号内容：「」、“”、『』、""
function collectSpoken(line) {
  var re = /「([^」]*)」|“([^”]*)”|『([^』]*)』|"([^"]*)"/g;
  var out = [];
  var m;
  while ((m = re.exec(line))) {
    out.push(m[1] != null ? m[1] : (m[2] != null ? m[2] : (m[3] != null ? m[3] : m[4])));
  }
  return out;
}

function classifyLine(line) {
  var spoken = collectSpoken(line).join("");
  if (spoken) {
    return { type: "speech", display: spoken, spoken: spoken };
  }
  var am = /^[（(]([\s\S]*)[)）]$/.exec(line);
  if (am) {
    return { type: "action", display: am[1].trim() || line, spoken: "" };
  }
  var tm = /^_([\s\S]+)_$/.exec(line);
  if (tm) {
    return { type: "thought", display: tm[1].trim() || line, spoken: "" };
  }
  var stm = /^\*([\s\S]*)\*$/.exec(line);
  if (stm) {
    return { type: "action", display: stm[1].trim() || line, spoken: "" };
  }
  return { type: "narration", display: line, spoken: "" };
}

function splitLines(text) {
  return text.split(/\r?\n/).map(function (s) {
    return s.trim();
  }).filter(function (s) {
    return !!s;
  });
}

function buildSegments() {
  var gv = state.galgame;
  var lines = splitLines(gv.rawContent);
  var segs = [];
  for (var i = gv.segStart; i < lines.length; i++) {
    var seg = classifyLine(lines[i]);
    var entry = gv.ttsLines[i] || null;
    // 防御：行号与流式记录的文本必须一致；不一致说明行对齐出了问题，
    // 不挂错语音（播放时按正确文本现合成）
    seg.tts = (entry && entry.spoken === seg.spoken) ? entry : null;
    segs.push(seg);
  }
  return segs;
}

/* ==== 打字机回放 + 点击推进 ==== */
function startPlayback(segs) {
  var gv = state.galgame;
  // 替换上一段未结束的回放（停掉等待动画/打字定时器/音频）
  var old = gv.play;
  if (old) {
    if (old.timer) clearTimeout(old.timer);
    if (old.waitTimer) clearInterval(old.waitTimer);
    if (old.audio) old.audio.pause();
  }
  gv.segments = segs;
  gvEl.box.classList.remove("hidden");

  // 无内容（纯工具调用节点）：直接结束、触发回放钩子，不碰 startSegment(0)
  if (!segs.length) {
    finishPlayback();
    return;
  }

  // 需要语音的对白记录（整段）
  var ttsRecs = [];
  if (gv.voiceOn && !gv.voiceBlocked) {
    segs.forEach(function (seg) {
      if (seg.type === "speech" && seg.tts
          && (seg.tts.spoken || seg.tts.url)) {
        ttsRecs.push(seg.tts);
      }
    });
  }
  if (!ttsRecs.length) {
    startSegment(0);  // 无语音：直接开始演出
    return;
  }

  // 整段 TTS 全部合成完成前：显示「角色思考中…」，不开始播放
  var prep = {
    phase: "preparing",
    index: -1,
    seg: null,
    timer: null,
    waitTimer: null,
    audio: null,
    dots: 0,
  };
  gv.play = prep;
  gvEl.name.textContent = gv.charName;
  gvEl.text.className = "gv-text gvt-preparing";
  gvEl.text.textContent = "角色思考中";
  prep.waitTimer = setInterval(function () {
    if (gv.play !== prep) return;
    prep.dots = (prep.dots + 1) % 4;
    gvEl.text.textContent = "角色思考中" + ".".repeat(prep.dots);
  }, 450);
  renderDebug();

  Promise.all(ttsRecs.map(function (rec) {
    return ensureEntryAudio(rec);
  })).then(function () {
    if (gv.play !== prep || prep.phase !== "preparing") return;
    clearPreparing(prep);
    startSegment(0);  // 整段语音就绪，开始逐句演出
  });
}

function clearPreparing(prep) {
  if (prep.waitTimer) { clearInterval(prep.waitTimer); prep.waitTimer = null; }
}

function startSegment(i) {
  var gv = state.galgame;
  var seg = gv.segments[i];
  var p = {
    index: i,
    seg: seg,
    // typing=打字机输出中；shown=已完整，等点击。音频与文字同时开始
    phase: "typing",
    timer: null,
    waitTimer: null,
    audio: null,
    paused: false,
  };
  gv.play = p;
  gvEl.name.textContent = seg.type === "speech" ? gv.charName : "";
  gvEl.text.className = "gv-text gvt-" + seg.type;
  gvEl.text.textContent = "";

  // 文字立即开始打字机
  typewriter(p, seg.display, function () {
    p.phase = "shown";
    // 一句输出完毕：停住等点击；auto 开则延迟自动推进
    onSegmentShown(p, 1100);
  });

  // 语音在同一句开始时并行播放（准备阶段通常已就绪；个别未就绪则稍后加入）
  var ttsRec = seg.tts;
  if (seg.type === "speech" && gv.voiceOn && !gv.voiceBlocked && ttsRec
      && (ttsRec.spoken || ttsRec.url)) {
    ensureEntryAudio(ttsRec).then(function (url) {
      if (gv.play === p && url && !p.paused) playSegmentAudio(p, url);
    });
  }
}

function playSegmentAudio(p, url) {
  var gv = state.galgame;
  var a = new Audio(assetUrl(url));
  a.volume = Math.max(0, Math.min(100, gv.ttsVolume)) / 100;
  p.audio = a;
  bgmEngine.setDuck(true);  // 台词期间压低 BGM
  a.addEventListener("ended", function () {
    if (gv.play !== p) return;
    p.audio = null;  // 文字与语音并行，播完不改变文字状态，仍等点击
    bgmEngine.setDuck(false);
    // 文字已显示完且 auto 在开：语音收尾后稍停即自动推进
    if (p.phase === "shown") onSegmentShown(p, 250);
  });
  var pr = a.play();
  if (pr && pr.catch) {
    pr.catch(function () {});  // 自动播放被拦：不影响打字机
  }
}

function typewriter(p, full, done) {
  var gvElText = gvEl.text;
  var step = 0;
  function tick() {
    p.timer = null;
    if (p.paused) return;
    step += 1;
    gvElText.textContent = full.slice(0, step);
    if (step >= full.length) {
      done();
      return;
    }
    p.timer = setTimeout(tick, 45);
  }
  p.timer = setTimeout(tick, 45);
}

function completeText(p) {
  if (p.timer) { clearTimeout(p.timer); p.timer = null; }
  p.phase = "shown";
  gvEl.text.textContent = p.seg.display;
}

function nextSegment(p) {
  var gv = state.galgame;
  clearAutoTimer();  // 手动推进 / 自动推进都清掉待触发定时器
  if (p.audio) { p.audio.pause(); p.audio = null; bgmEngine.setDuck(false); }
  var i = p.index + 1;
  if (i >= gv.segments.length) finishPlayback();
  else startSegment(i);
}

function finishPlayback() {
  var gv = state.galgame;
  clearAutoTimer();  // 遇选项/段落结束：auto 不再自动走，选择需手动
  gv.play = null;
  gvEl.box.classList.add("hidden");
  // 重放模式 / 自定义回放结束回调优先
  if (gv._onPlaybackDone) {
    var hook = gv._onPlaybackDone;
    gv._onPlaybackDone = null;
    hook();
    return;
  }
  if (gv.awaitingChoice) {
    // 整段对话演出结束，选项此刻才弹出
    dbgEvent("演出结束，弹出选项");
    renderChoices(gv.pendingOptions || [], false);
  } else if (!gv.active) {
    showEntry("continue");
  }
}

// 舞台点击：打字中→立即补全（音频继续）；已补全→跳下一句
function onStageClick() {
  var gv = state.galgame;
  gvFirstClickEnsure();
  var p = gv.play;
  if (!p) return;
  if (p.phase === "preparing") {
    // 不想等：跳过整段等待直接开始（各句语音未就绪时会在该句前补等）
    clearPreparing(p);
    startSegment(0);
  } else if (p.phase === "typing") {
    // 第一次点击：本句立即补全，仍停在本句
    completeText(p);
  } else {
    // 已完整：进入下一句
    nextSegment(p);
  }
}

function pausePlayback() {
  var gv = state.galgame;
  var p = gv.play;
  if (!p) return;
  clearAutoTimer();
  p.paused = true;
  if (p.timer) { clearTimeout(p.timer); p.timer = null; }
  if (p.waitTimer) { clearInterval(p.waitTimer); p.waitTimer = null; }
  if (p.audio) { p.audio.pause(); bgmEngine.setDuck(false); }
}
function resumePlayback() {
  var gv = state.galgame;
  var p = gv.play;
  if (!p) return;
  p.paused = false;
  if (p.phase === "preparing") {
    // 恢复等待动画；TTS 全部就绪的回调仍会触发演出
    if (!p.waitTimer) {
      p.waitTimer = setInterval(function () {
        if (gv.play !== p) return;
        p.dots = (p.dots + 1) % 4;
        gvEl.text.textContent = "角色思考中" + ".".repeat(p.dots);
      }, 450);
    }
    return;
  }
  startSegment(p.index);
}

/* ==== 选项 ==== */
function renderChoices(options, disabled) {
  var gv = state.galgame;
  gvEl.choices.innerHTML = "";
  options.forEach(function (text) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "gv-choice-btn";
    b.textContent = text;
    b.disabled = !!disabled;
    b.addEventListener("click", function () { gvChoose(text); });
    gvEl.choices.appendChild(b);
  });
  var free = document.createElement("button");
  free.type = "button";
  free.className = "gv-choice-btn gv-choice-free";
  free.textContent = "✎ 自己输入";
  free.disabled = !!disabled;
  free.addEventListener("click", function () { toggleFreeInput(free); });
  gvEl.choices.appendChild(free);
  gvEl.choices.classList.toggle("gv-choices-wait", !!disabled);
  gvEl.choices.classList.remove("hidden");
}

function hideChoices() {
  gvEl.choices.classList.add("hidden");
  gvEl.choices.innerHTML = "";
}

function toggleFreeInput(anchor) {
  var existing = gvEl.choices.querySelector(".gv-free-row");
  if (existing) { existing.remove(); return; }
  var row = document.createElement("div");
  row.className = "gv-free-row";
  var input = document.createElement("input");
  input.type = "text";
  input.maxLength = 200;
  input.placeholder = "输入自定义行动/台词…";
  var ok = document.createElement("button");
  ok.type = "button";
  ok.className = "btn primary gv-free-ok";
  ok.textContent = "确定";
  function submit() {
    var text = input.value.trim();
    if (text) gvChoose(text);
  }
  ok.addEventListener("click", submit);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
    e.stopPropagation();
  });
  row.appendChild(input);
  row.appendChild(ok);
  anchor.insertAdjacentElement("afterend", row);
  input.focus();
}

function gvChoose(text) {
  var gv = state.galgame;
  if (gv.dangling) { postGalgameResume(gv.dangling, text); return; }
  if (!gv.runId) return;
  fetchJSON("/api/galgame/choice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: gv.runId, text: text }),
  }).then(function () {
    gv.awaitingChoice = false;
    gv.pendingOptions = null;
    gv.segStart = gv.ttsLines.length;
    // 此时新轮内容尚未到达、rawContent 还是上一轮全文：必须保证它以换行
    // 收尾，否则下一轮首块会与上一轮末行拼成一行——splitLines 行号错位，
    // segStart 之后第一行被吞（丢第一句）、语音整体错一位。
    // 不能放到 content_delta 里做：这里已把 awaitingChoice 置 false。
    if (gv.rawContent && gv.rawContent.slice(-1) !== "\n") {
      gv.rawContent += "\n";
    }
    dbgEvent("已选择：" + text.slice(0, 20));
    hideChoices();
  }).catch(function (e) {
    showToast("选择失败：" + (e.message || e), "err", 4000);
  });
}

// 续接失去响应的 galgame_choice：不新增用户消息，服务端补上 tool 响应后续演
function postGalgameResume(dangling, text) {
  var gv = state.galgame;
  var callId = dangling.callId;
  resetRunState();
  gv.active = true;
  var payload = {
    message: "",
    session_id: gv.sid,
    thread_id: gv.tid || undefined,
    provider_id: chosenProviderId(),
    tools_enabled: true,
    images: [],
    image_model_id: state.imageModelId || "",
    image_engine: state.sdEngine ? "sd" : "",
    target_member_ids: [],
    shuffle_speakers: false,
    disabled_tools: (typeof disabledToolNames === "function") ? disabledToolNames() : [],
    galgame_resume: { tool_call_id: callId, text: text },
  };
  dbgEvent("续接选项：" + text.slice(0, 20));
  gvConnect(null, { post: true, payload: payload });
}

/* ==== 重入：重播最后一条助理消息 ==== */
function replayLastAssistant(thread) {
  var gv = state.galgame;
  var last = null;
  var lastIndex = -1;
  (thread.messages || []).forEach(function (m, i) {
    if (m.role === "assistant" && (m.content || "").trim()) {
      last = m;
      lastIndex = i;
    }
  });
  if (!last) return false;
  gv.rawContent = last.content;
  // 该 assistant 的 galgame_choice 是否已失去响应（服务重启等导致后续无 tool 消息）：
  // 是则演出结束后直接弹出当初保存的选项，选择走 galgame_resume 续接
  var msgs = thread.messages || [];
  (last.tool_calls || []).forEach(function (call) {
    if (call.name !== "galgame_choice" || gv.dangling) return;
    var answered = false;
    for (var k = lastIndex + 1; k < msgs.length; k++) {
      if (msgs[k].role === "tool" && msgs[k].tool_call_id === call.id) {
        answered = true;
        break;
      }
    }
    if (answered) return;
    var a = call.arguments || {};
    if (typeof a === "string") {
      try { a = JSON.parse(a); } catch (e) { a = {}; }
    }
    var rawOpts = a.options || [];
    if (typeof rawOpts === "string") rawOpts = [rawOpts];
    var options = rawOpts.map(function (t) {
      return t == null ? "" : String(t).trim();
    }).filter(Boolean).slice(0, 4);
    if (options.length >= 2) {
      gv.dangling = { callId: call.id, options: options };
      gv.awaitingChoice = true;
      gv.pendingOptions = options;
      dbgEvent("发现未响应的选项调用，将恢复 " + options.length + " 个选项");
    }
  });
  // 与 buildSegments 的行一一对应；url 从已保存清单取，刷新后不重新合成
  var ttsCache0 = gv.ttsCache;
  gv.ttsLines = splitLines(last.content).map(function (line) {
    var spoken = classifyLine(line).spoken || "";
    return { spoken: spoken, url: ttsCache0[spoken] || null, promise: null };
  });
  var segs = buildSegments();
  startPlayback(segs);
  return true;
}
