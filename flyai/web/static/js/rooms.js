/* FlyAI 前端 · 房间/窗口/群聊/选卡/persona/会话 meta（由 app.js 拆出的经典脚本；函数挂全局作用域）。 */

"use strict";

/* 房间内：左侧栏展示该房间的各窗口（每 AI 私聊 + 群聊 + 建群/记忆），顶部返回键 */
function renderRoomNav() {
  el.sessionList.innerHTML = "";
  var back = document.createElement("div");
  back.className = "session-item back";
  back.textContent = "← 返回会话";
  back.addEventListener("click", backToSessions);
  el.sessionList.appendChild(back);
  // 分区标题：纯标签不是可点项（原先这里显示常为「新会话」的房间标题，像残留按钮）
  var sec = document.createElement("div");
  sec.className = "room-section";
  sec.textContent = "窗口";
  el.sessionList.appendChild(sec);
  state.roomThreads.forEach(function (t) {
    var item = document.createElement("div");
    item.className = "session-item thread-item" + (t.id === state.currentThreadId ? " active" : "");
    // 私聊窗口按成员角色卡实时解析显示名（别名优先）；群聊窗口用建群时的标题
    var winLabel = t.title || "窗口";
    if (t.kind !== "group" && t.member_ids && t.member_ids.length) {
      var seat0 = (state.roomMembers || []).find(function (m) {
        return m.id === t.member_ids[0] && !m.is_user;
      });
      if (seat0) winLabel = memberDisplayName(seat0);
    }
    var label = document.createElement("span");
    label.className = "thread-label";
    label.textContent = (t.kind === "group" ? "👥 " : "💬 ") + winLabel;
    // 移动端窗口列表在抽屉里：点窗口要收抽屉，否则遮罩挡住整个页面
    label.addEventListener("click", function () { closeNav(); loadThread(t.id); });
    item.appendChild(label);

    // 窗口操作：重命名 / 删除（点按钮不触发进窗口）
    var actions = document.createElement("span");
    actions.className = "thread-actions";
    var rn = document.createElement("button");
    rn.type = "button"; rn.className = "thread-act"; rn.textContent = "✎";
    rn.title = "重命名窗口";
    rn.addEventListener("click", function (ev) {
      ev.stopPropagation(); openThreadRename(t, winLabel);
    });
    var dl = document.createElement("button");
    dl.type = "button"; dl.className = "thread-act danger"; dl.textContent = "✕";
    dl.title = "删除窗口";
    dl.addEventListener("click", function (ev) {
      ev.stopPropagation(); confirmDeleteThread(t, winLabel);
    });
    actions.appendChild(rn); actions.appendChild(dl);
    item.appendChild(actions);

    el.sessionList.appendChild(item);
  });
  var aiCount = (state.roomMembers || []).filter(function (m) { return !m.is_user; }).length;
  if (aiCount >= 2) {
    var add = document.createElement("div");
    add.className = "session-item add";
    add.textContent = "＋ 建群聊";
    add.addEventListener("click", openGroupDialog);
    el.sessionList.appendChild(add);
  }
  // 🧠 记忆不是对话窗口：已移到顶栏「⋯ 更多」菜单，窗口列表只列真正的窗口
}
function backToSessions() {
  state.roomView = false;
  clearMessages();
  if (el.threads) el.threads.classList.add("hidden");
  renderSessionList();
  renderSessionMeta();
}

function effectiveChar() {
  var s = currentSession();
  if (!s) return null;
  var sc = state.scopeCache[s.id];
  if (sc && sc.char) return sc.char;
  return characterOf(s.character_id) || null;
}
function effectiveWorld() {
  var s = currentSession();
  if (!s) return null;
  var sc = state.scopeCache[s.id];
  if (sc && sc.world) return sc.world;
  return worldOf(s.world_id) || null;
}
function isScoped() {
  var s = currentSession();
  if (!s) return false;
  var sc = state.scopeCache[s.id];
  return sc ? !!sc.scoped : false;
}

function renderSessionMeta() {
  var s = currentSession();
  if (!s) {
    el.sessionMeta.innerHTML = "";
    el.topbarTitle.textContent = "新会话";
    updateEmptyState();
    return;
  }
  el.topbarTitle.textContent = s.title || "新会话";

  if (roomMode()) {
    // 房间顶栏只留「成员 · 世界观」；扮演/随机顺序/导出/压缩/记录记忆/记忆
    // 全部收进「⋯ 更多」菜单（见 buildMoreMenu）
    var names = (state.roomMembers || []).filter(function (m) { return !m.is_user; }).map(memberDisplayName);
    var world = effectiveWorld();
    var worldChip = document.createElement("span");
    worldChip.className = "chip";
    worldChip.innerHTML = "🌍 世界观 " + worldSelectOptions("world", world ? world.id : "", "无世界观");
    el.sessionMeta.innerHTML = "";
    var roomChip = document.createElement("span");
    roomChip.className = "chip";
    roomChip.textContent = "👥 " + (names.length ? names.join("、") : "多AI房间");
    roomChip.title = names.join("、");
    el.sessionMeta.appendChild(roomChip);
    el.sessionMeta.appendChild(worldChip);
    var selW = el.sessionMeta.querySelector('select[data-world]');
    if (selW) selW.addEventListener("change", function () { updateSessionBinding("world_id", selW.value); });
    return;
  }

  var char = effectiveChar();
  var world = effectiveWorld();
  var scoped = isScoped();

  /* 可点击的 AI 头像 → 查看属性数值 */
  var avatarBtn = document.createElement("button");
  avatarBtn.className = "ai-avatar" + (scoped ? " scoped" : "");
  avatarBtn.title = "查看属性数值" + (scoped ? "（会话副本）" : "");
  avatarBtn.innerHTML = makeAvatarHtml(char, false);
  avatarBtn.addEventListener("click", openAttrViewer);

  var charChip = document.createElement("span");
  charChip.className = "chip";
  charChip.innerHTML = "👤 角色 " + (scoped ? "<span class='scoped-badge'>副本</span>" : "") + makeAvatarHtml(char, true) + " " + charSelectOptions("char", char ? char.id : "", "无角色");
  var worldChip = document.createElement("span");
  worldChip.className = "chip";
  worldChip.innerHTML = "🌍 世界观 " + worldSelectOptions("world", world ? world.id : "", "无世界观");
  el.sessionMeta.innerHTML = "";
  el.sessionMeta.appendChild(avatarBtn);
  el.sessionMeta.appendChild(charChip);
  el.sessionMeta.appendChild(worldChip);
  // token / 派生副本 / AI属性 等操作同样收进「⋯ 更多」菜单

  var selChar = el.sessionMeta.querySelector('select[data-char]');
  var selWorld = el.sessionMeta.querySelector('select[data-world]');
  if (selChar) selChar.addEventListener("change", function () { updateSessionBinding("character_id", selChar.value); });
  if (selWorld) selWorld.addEventListener("change", function () { updateSessionBinding("world_id", selWorld.value); });
}

/* ==== 「⋯ 更多」菜单：顶栏只留 标题/成员/世界观/模型，其余动作全在这里 ==== */

function openMoreMenu() {
  buildMoreMenu();
  el.moreMenu.classList.remove("hidden");
}
function closeMoreMenu() {
  el.moreMenu.classList.add("hidden");
}
function toggleMoreMenu() {
  if (el.moreMenu.classList.contains("hidden")) openMoreMenu();
  else closeMoreMenu();
}
function mmItem(text, fn) {
  var b = document.createElement("button");
  b.type = "button";
  b.className = "mm-item";
  b.textContent = text;
  b.addEventListener("click", function () {
    closeMoreMenu();
    if (fn) fn();
  });
  return b;
}
function mmSep() {
  var d = document.createElement("div");
  d.className = "mm-sep";
  return d;
}

/* ==== 「🧰 提供给 AI 的工具」快捷开关（仅本会话生效，localStorage 持久化） ==== */
var TOOL_GROUPS = [
  ["base", "基础"],
  ["web", "联网"],
  ["browser", "AI 浏览器"],
  ["pixiv", "P 站搜图"],
  ["image", "AI 生图"],
  ["galgame", "Galgame"],
  ["tts", "语音"],
];
var TOOL_LABELS = {
  query_lore: "内容表查询",
  query_chat_memory: "回忆旧对话",
  update_chat_memory: "更新记忆",
  update_attributes: "更新属性",
  read_image: "查看图片",
  web_search: "联网搜索",
  web_fetch: "读取网页",
  browser_open: "浏览器·打开",
  browser_snapshot: "浏览器·查看元素",
  browser_click: "浏览器·点击",
  browser_type: "浏览器·输入",
  browser_back: "浏览器·后退",
  browser_get_text: "浏览器·读取文本",
  browser_close: "浏览器·关闭",
  search_pixiv: "P 站·搜索",
  pick_pixiv: "P 站·取原图",
  generate_image: "AI 生图",
  edit_image: "以图生图",
  galgame_draw_background: "Galgame·背景绘制",
  galgame_draw_sprite: "Galgame·立绘绘制",
  galgame_compose_music: "Galgame·作曲",
  galgame_list_assets: "Galgame·资产清单",
  galgame_set_scene: "Galgame·切换场景",
  galgame_choice: "Galgame·选项",
  text_to_speech: "语音朗读",
};
function disabledToolsStorageKey() {
  return "flyai-disabled-tools-" + (state.currentSessionId || "none");
}
/* 当前会话被手动关闭的工具名（过滤掉服务端已不可用的名字，防脏数据） */
function getDisabledTools() {
  var names = [];
  try {
    names = JSON.parse(localStorage.getItem(disabledToolsStorageKey()) || "[]") || [];
  } catch (e) { names = []; }
  if (!Array.isArray(names)) return [];
  var valid = {};
  (state.toolsMeta || []).forEach(function (t) {
    if (t.allowed) valid[t.name] = true;
  });
  return names.filter(function (n) { return typeof n === "string" && valid[n]; });
}
/* 发送 payload 用 */
function disabledToolNames() { return getDisabledTools(); }
function setToolEnabled(name, on) {
  var names = getDisabledTools();
  var i = names.indexOf(name);
  if (on && i >= 0) names.splice(i, 1);
  if (!on && i < 0) names.push(name);
  localStorage.setItem(disabledToolsStorageKey(), JSON.stringify(names));
}
function mmToolRow(t, disabled) {
  var lbl = document.createElement("label");
  lbl.className = "mm-tool";
  lbl.title = t.description || "";
  var chk = document.createElement("input");
  chk.type = "checkbox";
  chk.checked = disabled.indexOf(t.name) < 0;
  chk.addEventListener("click", function (e) { e.stopPropagation(); });
  chk.addEventListener("change", function () { setToolEnabled(t.name, chk.checked); });
  var name = document.createElement("span");
  name.className = "mm-tool-name";
  var html = escapeHtml(TOOL_LABELS[t.name] || t.name);
  if (t.description) html += '<span class="mm-tool-desc">' + escapeHtml(t.description) + "</span>";
  name.innerHTML = html;
  lbl.appendChild(chk);
  lbl.appendChild(name);
  return lbl;
}
function buildToolsMenuSection() {
  var allowed = (state.toolsMeta || []).filter(function (t) { return t.allowed; });
  if (!allowed.length) return null;
  var disabled = getDisabledTools();
  var frag = document.createDocumentFragment();
  var head = document.createElement("div");
  head.className = "mm-tool-group";
  head.textContent = "🧰 提供给 AI 的工具（仅本对话）";
  frag.appendChild(head);
  TOOL_GROUPS.forEach(function (g) {
    var items = allowed.filter(function (t) { return (t.category || "base") === g[0]; });
    if (!items.length) return;
    var sub = document.createElement("div");
    sub.className = "mm-tool-group";
    sub.textContent = g[1];
    frag.appendChild(sub);
    items.forEach(function (t) { frag.appendChild(mmToolRow(t, disabled)); });
  });
  return frag;
}
function buildMoreMenu() {
  var menu = el.moreMenu;
  menu.innerHTML = "";

  // 信息头：当前模型 + 会话累计 token（完整数字；顶栏不再常驻计费信息）
  var info = document.createElement("div");
  info.className = "mm-info";
  var txt = "◈ " + activeProviderName();
  if (state.currentSessionId) {
    var total = intOr(state.tokenTotals[state.currentSessionId], 0);
    txt += "\n🪙 累计 " + fmtNum(total) + " tokens";
  }
  info.textContent = txt;
  menu.appendChild(info);

  var isRoom = roomMode();

  // 角色 / 世界观挂载：手机端（≤860px）顶栏 chip 被 CSS 隐藏，只能在这里挂载；
  // 桌面端顶栏仍有 chip，这里多一个等价入口
  menu.appendChild(mmSep());
  if (!isRoom) {
    var cr = document.createElement("div");
    cr.className = "mm-row-item";
    var ck = document.createElement("span");
    ck.className = "mm-k";
    ck.textContent = "👤 角色";
    var csel = document.createElement("select");
    csel.innerHTML = charOptionTags(effectiveChar() ? effectiveChar().id : "", "无角色");
    csel.addEventListener("change", function () { updateSessionBinding("character_id", csel.value); });
    cr.appendChild(ck);
    cr.appendChild(csel);
    menu.appendChild(cr);
  }
  (function () {
    var wr = document.createElement("div");
    wr.className = "mm-row-item";
    var wk = document.createElement("span");
    wk.className = "mm-k";
    wk.textContent = "🌍 世界观";
    var wsel = document.createElement("select");
    var world = effectiveWorld();
    var wopts = ['<option value="">无世界观</option>'];
    state.worlds.forEach(function (w) {
      wopts.push('<option value="' + escapeAttr(w.id) + '"' + (world && w.id === world.id ? " selected" : "") + ">" + escapeHtml(w.name || "未命名") + "</option>");
    });
    wsel.innerHTML = wopts.join("");
    wsel.addEventListener("change", function () { updateSessionBinding("world_id", wsel.value); });
    wr.appendChild(wk);
    wr.appendChild(wsel);
    menu.appendChild(wr);
  })();

  if (isRoom) {
    menu.appendChild(mmSep());
    // 🎭 扮演（persona）：行内下拉
    var seat = roomUserSeat();
    if (seat) {
      var pr = document.createElement("div");
      pr.className = "mm-row-item";
      var k = document.createElement("span");
      k.className = "mm-k";
      k.textContent = "🎭 扮演";
      var sel = document.createElement("select");
      sel.innerHTML = charOptionTags(seat.character_id || "", "本人");
      sel.addEventListener("change", function () { setPersona(seat, sel.value); });
      pr.appendChild(k);
      pr.appendChild(sel);
      menu.appendChild(pr);
    }
    // 🔀 随机顺序：右侧显示开/关
    var sh = mmItem("🔀 随机顺序", function () { toggleShuffle(); openMoreMenu(); });
    var st = document.createElement("span");
    st.className = "mm-state";
    if (isShuffleOn()) {
      sh.classList.add("on");
      st.classList.add("on");
      st.textContent = "开";
    } else {
      st.textContent = "关";
    }
    sh.appendChild(st);
    menu.appendChild(sh);
  }

  menu.appendChild(mmSep());
  var toolSection = buildToolsMenuSection();
  if (toolSection) {
    menu.appendChild(toolSection);
    menu.appendChild(mmSep());
  }
  if (isRoom) {
    menu.appendChild(mmItem("🧠 记忆与属性（座位 / 挂载 / 移除成员）", openMemDialog));
  }
  menu.appendChild(mmItem("⬇ 导出对话", downloadConversation));
  if (state.currentSessionId) {
    menu.appendChild(mmItem("🛒 发布到市场", function () {
      publishToMarket("session", state.currentSessionId);
    }));
  }
  menu.appendChild(mmItem("☁ 压缩对话", openCompressDialog));
  if (isRoom) {
    menu.appendChild(mmItem("📝 记录记忆", snapshotRoomMemory));
  }

  // 单 AI 会话才有的角色卡操作
  if (!isRoom) {
    var char = effectiveChar();
    var scoped = isScoped();
    if (char) {
      menu.appendChild(mmSep());
      if (scoped) {
        menu.appendChild(mmItem("✎ 编辑副本", editSessionChar));
        menu.appendChild(mmItem("⬇ 副本导出", exportSessionChar));
        menu.appendChild(mmItem("💾 保存入库", publishConversationCharacter));
        menu.appendChild(mmItem("♻ 还原为主卡", restoreSessionChar));
      } else {
        menu.appendChild(mmItem("📄 派生副本", forkSessionChar));
      }
      menu.appendChild(mmItem("✨ AI 建议属性", sessionAttrGen));
    }
  }
}

/* ==== 空状态：无会话时主区引导 ==== */
function updateEmptyState() {
  var ex = el.messages.querySelector(".empty-state");
  if (state.currentSessionId || state.busy) {
    if (ex) ex.remove();
    return;
  }
  el.messages.innerHTML = "";
  var wrap = document.createElement("div");
  wrap.className = "empty-state";
  var illu = document.createElement("div");
  illu.className = "empty-illu";
  illu.textContent = "💬";
  wrap.appendChild(illu);
  var h2 = document.createElement("h2");
  h2.textContent = "开始第一个对话";
  wrap.appendChild(h2);
  var p = document.createElement("p");
  p.textContent = "选一张角色卡，或直接使用通用助手";
  wrap.appendChild(p);
  var btn = document.createElement("button");
  btn.className = "btn primary";
  btn.textContent = "＋ 新建对话";
  btn.addEventListener("click", openNewChatDialog);
  wrap.appendChild(btn);
  el.messages.appendChild(wrap);
}

/* 只返回 <option> 标签：供已有的 <select> 用 innerHTML 填充
   （charSelectOptions 返回整段 <select>，塞进 select 会嵌套，选项被浏览器丢弃） */
function charOptionTags(selected, emptyLabel) {
  var opts = ['<option value="">' + emptyLabel + '</option>'];
  state.characters.forEach(function (c) {
    opts.push('<option value="' + escapeAttr(c.id) + '"' + (c.id === selected ? " selected" : "") + '>' + escapeHtml(cardDisplayName(c)) + '</option>');
  });
  return opts.join("");
}
function charSelectOptions(kind, selected, emptyLabel) {
  return '<select data-' + kind + '>' + charOptionTags(selected, emptyLabel) + '</select>';
}
function worldSelectOptions(kind, selected, emptyLabel) {
  var opts = ['<option value="">' + emptyLabel + '</option>'];
  state.worlds.forEach(function (w) {
    opts.push('<option value="' + escapeAttr(w.id) + '"' + (w.id === selected ? " selected" : "") + '>' + escapeHtml(w.name || "未命名") + '</option>');
  });
  return '<select data-world>' + opts.join("") + '</select>';
}

function currentSession() {
  return state.sessions.find(function (s) { return s.id === state.currentSessionId; });
}
function characterOf(id) { return state.characters.find(function (c) { return c.id === id; }); }
function worldOf(id) { return state.worlds.find(function (w) { return w.id === id; }); }

function refreshSessions() {
  return fetchJSON("/api/sessions").then(function (r) {
    state.sessions = r.sessions || [];
    renderSessionList();
    renderSessionMeta();
  });
}

function updateSessionBinding(field, value) {
  if (!state.currentSessionId) return;
  var body = {};
  body[field] = value;
  fetchJSON("/api/sessions/" + state.currentSessionId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(function (s) {
    var sess = currentSession();
    if (sess) { sess[field] = s[field]; }
    delete state.scopeCache[state.currentSessionId];  // 重新命中库卡
    renderSessionMeta();
  }).catch(function (e) { addErrorBar("设置失败: " + e.message); });
}

/* ==== 角色卡归属/层级小工具 ==== */
function isOwnCard(c) {
  if (!c) return false;
  if (c.author === state.userId) return true;
  return state.role === "admin" && !c.author;  // admin 的旧卡 author 为空
}
function rootCards() {
  return (state.characters || []).filter(function (c) { return !c.parent_id; });
}
function variantCards(rootId) {
  return (state.characters || []).filter(function (c) { return c.parent_id === rootId; });
}
function cardDisplayName(c) { return (c && (c.alias || c.name)) || "未命名"; }
// 房间成员的显示名：其角色卡（含副本）有别名时优先别名，否则回退座位名
function memberDisplayName(m) {
  if (!m) return "";
  var ch = m.character_id ? characterOf(m.character_id) : null;
  return ch ? cardDisplayName(ch) : (m.name || "");
}

/* ==== 通用二次确认弹窗 ==== */
var confirmState = null;
function askConfirm(opts) {
  el.confirmTitle.textContent = opts.title || "请确认";
  el.confirmText.textContent = opts.text || "";
  el.btnConfirmOk.textContent = opts.okLabel || "确认";
  el.btnConfirmOk.className = "btn " + (opts.danger === false ? "primary" : "danger");
  confirmState = opts.onOk || null;
  if (el.confirmDialog.open) el.confirmDialog.close();
  el.confirmDialog.showModal();
}

/* ==== 角色卡详情弹窗 ==== */
function cardDetailHtml(c) {
  var h = '<div class="cd-head">' + makeAvatarHtml(c, false) +
    '<div class="cd-head-info"><h3>' + escapeHtml(c.name || "未命名") + "</h3>" +
    '<div class="sub">' + (c.alias ? "别名：" + escapeHtml(c.alias) + " · " : "") +
    "标识：" + escapeHtml((c.author || "?") + ":" + c.id) + "</div></div></div>";
  if (c.intro) h += '<h4>简介</h4><div class="cd-sec">' + escapeHtml(c.intro) + "</div>";
  if (c.background) h += '<h4>经历</h4><div class="cd-sec">' + escapeHtml(c.background) + "</div>";
  if (c.system_prompt) h += '<h4>系统级提示词</h4><div class="cd-sec">' + escapeHtml(c.system_prompt) + "</div>";
  if (c.attributes && c.attributes.length) {
    h += '<h4>属性</h4><ul class="cd-list">' + c.attributes.map(function (a) {
      return "<li>" + escapeHtml(a.label || a.key) + "（" + escapeHtml(a.key) + "）· 初值 " +
        escapeHtml(a.initial !== undefined ? a.initial : 0) + "</li>";
    }).join("") + "</ul>";
  }
  if (c.table && c.table.length) {
    h += '<h4>内容表</h4><ul class="cd-list">' + c.table.map(function (t) {
      return "<li><b>" + escapeHtml(t.title || "") + "</b>：" + escapeHtml(t.content || "") + "</li>";
    }).join("") + "</ul>";
  }
  if (c.prompt_format) h += '<h4>对白格式要求</h4><div class="cd-sec">' + escapeHtml(c.prompt_format) + "</div>";
  if (c.prompt_tools) h += '<h4>工具使用要求</h4><div class="cd-sec">' + escapeHtml(c.prompt_tools) + "</div>";
  return h;
}
function openCardDetail(c, actions) {
  el.cardDetailBody.innerHTML = cardDetailHtml(c) +
    '<div class="dialog-actions" id="card-detail-actions"></div>';
  var acts = document.getElementById("card-detail-actions");
  (actions || []).forEach(function (a) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "btn " + (a.cls || "");
    b.textContent = a.label;
    b.addEventListener("click", function () { if (a.onClick) a.onClick(); });
    acts.appendChild(b);
  });
  var close = document.createElement("button");
  close.type = "button"; close.className = "btn primary"; close.textContent = "关 闭";
  close.addEventListener("click", function () { el.cardDetailDialog.close(); });
  acts.appendChild(close);
  if (el.cardDetailDialog.open) el.cardDetailDialog.close();
  el.cardDetailDialog.showModal();
}
function forkToMine(id, after) {
  fetchJSON("/api/characters/" + id + "/fork", { method: "POST" })
    .then(function (c) {
      return refreshLore().then(function () {
        el.cardDetailDialog.close();
        if (after) after(c);
      });
    })
    .catch(function (e) { showToast("复制失败: " + e.message, "err"); });
}

/* ==== 新建对话：根角色卡 → 副本选择 ==== */
function openNewChatDialog() {
  if (state.busy) return;
  state.newChat = { selected: {}, rootId: "" };
  el.newchatTitle.value = "";
  el.newchatMode.checked = false;
  applyNewChatMode();
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  safeCloseDialogs();
  el.newchatDialog.showModal();
  renderPickRoots();
}
function newChatModeOn() {
  return !!(el.newchatMode && el.newchatMode.checked);
}
function applyNewChatMode() {
  var on = newChatModeOn();
  el.btnNewchatPlain.disabled = on;
  el.newchatSub.textContent = on
    ? "Galgame 模式：只能选择一个角色。开始后先写初始剧情，AI 会绘制背景与立绘并以视觉小说形式演出。"
    : "选择一个根角色卡，再选择主卡或其副本；可跨根卡多选，多个角色即多 AI 群聊房间。";
}
function onNewChatModeChange() {
  if (newChatModeOn()) {
    // 切到 Galgame：只保留一个已选角色
    var sel = state.newChat.selected;
    var keys = Object.keys(sel);
    if (keys.length > 1) {
      keys.slice(1).forEach(function (k) { delete sel[k]; });
    }
  }
  applyNewChatMode();
  if (state.newChat.rootId) renderPickVariants(state.newChat.rootId);
  else renderPickRoots();
}
function renderPickRoots() {
  state.newChat.rootId = "";
  el.newchatBack.classList.add("hidden");
  applyNewChatMode();
  el.newchatPick.innerHTML = "";
  var roots = rootCards();
  if (!roots.length) {
    el.newchatPick.innerHTML = '<div class="sub">角色库还没有角色卡片，请先在「🧩 内容」里创建。</div>';
  } else {
    var grid = document.createElement("div");
    grid.className = "root-grid";
    roots.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "root-card";
      var n = variantCards(c.id).length;
      b.innerHTML = makeAvatarHtml(c, true) +
        '<span class="rc-name">' + escapeHtml(c.name || "未命名") + "</span>" +
        '<span class="rc-meta">' + (isOwnCard(c) ? "" : "🌐 ") + n + " 个副本</span>";
      b.addEventListener("click", function () { renderPickVariants(c.id); });
      grid.appendChild(b);
    });
    el.newchatPick.appendChild(grid);
  }
  renderPickTray();
}
function renderPickVariants(rootId) {
  state.newChat.rootId = rootId;
  el.newchatBack.classList.remove("hidden");
  var root = characterOf(rootId);
  el.newchatSub.textContent = newChatModeOn()
    ? "Galgame 模式：选择「" + cardDisplayName(root) + "」（只能选择一个角色）。"
    : "选择「" + cardDisplayName(root) + "」的主卡或副本，可在详情中查看卡片数据。";
  el.newchatPick.innerHTML = "";
  var list = document.createElement("div");
  list.className = "variant-list";
  list.appendChild(variantRow(root, true));
  variantCards(rootId).forEach(function (v) { list.appendChild(variantRow(v, false)); });
  el.newchatPick.appendChild(list);
  renderPickTray();
}
function variantRow(c, isRoot) {
  var row = document.createElement("div");
  row.className = "variant-row";
  var own = isOwnCard(c);
  var selected = !!state.newChat.selected[c.id];
  var pick = document.createElement("button");
  pick.type = "button"; pick.className = "vr-pick" + (selected ? " selected" : "");
  pick.innerHTML =
    '<span class="vr-tag' + (isRoot ? "" : " variant") + '">' + (isRoot ? "主卡" : "副本") + "</span>" +
    makeAvatarHtml(c, true) +
    '<span class="vr-name">' + escapeHtml(cardDisplayName(c)) + "</span>" +
    (selected ? '<span class="vr-check">✓</span>' : "");
  pick.addEventListener("click", function () { togglePick(c.id); });
  row.appendChild(pick);

  var more = document.createElement("button");
  more.type = "button"; more.className = "flat-btn"; more.textContent = "...";
  more.title = "查看卡片数据";
  more.addEventListener("click", function () { openCardDetail(c, pickActions(c)); });
  row.appendChild(more);

  var del = document.createElement("button");
  del.type = "button"; del.className = "flat-btn danger" + (own ? "" : " hidden");
  del.textContent = "删除"; del.title = "删除";
  del.addEventListener("click", function () { confirmDeleteCard(c, isRoot); });
  row.appendChild(del);
  return row;
}
function pickActions(c) {
  var picked = !!state.newChat.selected[c.id];
  var acts = [{
    label: picked ? "取消选择" : "选择此卡", cls: "primary",
    onClick: function () { togglePick(c.id); el.cardDetailDialog.close(); },
  }];
  if (!isOwnCard(c)) {
    acts.push({
      label: "复制为我的角色卡", cls: "",
      onClick: function () {
        forkToMine(c.id, function (nc) { openCardDetail(nc, pickActions(nc)); });
      },
    });
  }
  return acts;
}
function togglePick(id) {
  var sel = state.newChat.selected;
  if (sel[id]) {
    delete sel[id];
  } else {
    var c = characterOf(id);
    if (!c) return;
    if (newChatModeOn()) {
      // Galgame：单角色，新选择替换已有选择
      Object.keys(sel).forEach(function (k) { delete sel[k]; });
    }
    sel[id] = c;
  }
  if (state.newChat.rootId) renderPickVariants(state.newChat.rootId);
  else renderPickRoots();
}
function renderPickTray() {
  var ids = Object.keys(state.newChat.selected);
  el.newchatTray.classList.toggle("hidden", !ids.length);
  el.newchatTray.innerHTML = '<span class="tray-label">已选：</span>';
  ids.forEach(function (id) {
    var c = state.newChat.selected[id];
    var chip = document.createElement("span");
    chip.className = "pick-chip";
    chip.textContent = cardDisplayName(c);
    var x = document.createElement("button");
    x.type = "button"; x.className = "pick-chip-x"; x.textContent = "✕";
    x.title = "移除";
    x.addEventListener("click", function () { togglePick(id); });
    chip.appendChild(x);
    el.newchatTray.appendChild(chip);
  });
  el.btnNewchatCreate.disabled = !ids.length;
}
function confirmDeleteCard(c, isRoot) {
  askConfirm({
    title: "删除角色卡",
    text: isRoot
      ? "确定删除根角色卡「" + cardDisplayName(c) + "」？其全部副本也会一并删除，且无法恢复。"
      : "确定删除副本「" + cardDisplayName(c) + "」？此操作无法恢复。",
    okLabel: "删除",
    onOk: function () { doDeleteCard(c.id); },
  });
}
function doDeleteCard(id) {
  fetchJSON("/api/characters/" + id, { method: "DELETE" })
    .then(function () { return refreshLore(); })
    .then(function () {
      if (state.newChat.rootId === id) renderPickRoots();
      else if (state.newChat.rootId) renderPickVariants(state.newChat.rootId);
      else renderPickRoots();
      renderSessionMeta();
    })
    .catch(function (e) {
      el.newchatMsg.textContent = "删除失败: " + e.message;
      el.newchatMsg.className = "msg-bar err";
    });
}
function confirmNewChat() {
  var title = el.newchatTitle.value.trim();
  var ids = Object.keys(state.newChat.selected);
  if (!ids.length) return;
  var galgame = newChatModeOn();
  if (galgame && ids.length !== 1) {
    el.newchatMsg.textContent = "Galgame 模式只能选择一个角色";
    el.newchatMsg.className = "msg-bar err";
    return;
  }
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  fetchJSON("/api/rooms", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: title, character_ids: ids,
      mode: galgame ? "galgame" : "normal",
    }),
  }).then(function (s) {
    state.currentSessionId = s.id;
    state.currentAttrs = null;
    el.newchatDialog.close();
    return refreshSessions();
  }).then(function () {
    selectSession(state.currentSessionId);
    if (!galgame) el.input.focus();
  }).catch(function (e) {
    el.newchatMsg.textContent = "创建房间失败: " + e.message;
    el.newchatMsg.className = "msg-bar err";
  });
}
function plainNewChat() {
  var title = el.newchatTitle.value.trim();
  el.newchatMsg.textContent = ""; el.newchatMsg.className = "msg-bar";
  fetchJSON("/api/sessions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title }),
  }).then(function (s) {
    state.currentSessionId = s.id; state.currentAttrs = null;
    el.newchatDialog.close();
    return refreshSessions();
  }).then(function () {
    selectSession(state.currentSessionId); el.input.focus();
  }).catch(function (e) {
    el.newchatMsg.textContent = "创建失败: " + e.message;
    el.newchatMsg.className = "msg-bar err";
  });
}

function selectSession(id) {
  if (state.busy) return;
  // Galgame 运行（含等待选项）期间不进入其它 Galgame；普通会话不受影响。
  // 先从 ⚙ 返回主界面
  if (state.galgame && state.galgame.active && state.galgame.sid !== id) {
    var target = state.sessions.find(function (x) { return x.id === id; });
    if (target && target.mode === "galgame") {
      showToast("当前 Galgame 故事正在演出，请先从 ⚙ 返回主界面", "err", 4000);
      return;
    }
  }
  state.batchMode = false;
  state.batchPicked = {};
  state.currentSessionId = id;
  renderSessionList();
  closeNav();
  return fetchJSON("/api/sessions/" + id).then(function (s) {
    // 更新本地 session 中的角色/世界与标题
    var sess = currentSession();
    if (sess) { sess.character_id = s.character_id; sess.world_id = s.world_id; sess.title = s.title; }
    state.currentAttrs = s.attributes || null;
    // 累计 token：以服务端持久值为准（页面周期内再叠加新 done 事件）
    var tu = s.token_usage || {};
    state.tokenTotals[id] = intOr(tu.total_tokens, 0);
    state.tokenDetails[id] = { p: intOr(tu.prompt_tokens, 0), c: intOr(tu.completion_tokens, 0) };
    state.scopeCache[id] = { char: s.character || null, world: s.world || null, scoped: !!s.scoped };
    // Galgame：全屏视觉小说视图，不渲染普通聊天界面
    if (s.mode === "galgame") {
      state.roomMembers = s.members || [];
      state.roomThreads = s.threads || [];
      state.currentSpeaker = null;
      state.currentThreadId = null;
      state.roomView = true;
      renderSessionList();
      enterGalgame(s);
      return;
    }
    // 从 Galgame 直接切到普通会话：隐藏全屏视图（运行仍在后台）
    var gvView0 = document.getElementById("galgame-view");
    if (gvView0) gvView0.classList.add("hidden");
    var appEl0 = document.getElementById("app");
    if (appEl0) appEl0.classList.remove("gv-app-hidden");
    if (state.galgame && state.galgame.play) pausePlayback();
    // beta：多 AI 房间；有窗口时左侧栏切换为该房间的窗口导航
    state.roomMembers = s.members || [];
    state.roomThreads = s.threads || [];
    state.currentSpeaker = null;
    state.currentThreadId = null;
    // 单 AI 房间抽屉不进二级列表（仍加载唯一窗口的内容）；多 AI 才进
    state.roomView = roomNavActive();
    renderSessionList();
    renderSessionMeta();
    var viewReady;
    if (roomMode()) {
      viewReady = loadThread(state.roomThreads[0].id);
    } else {
      renderHistory(s.messages || []);
      restoreDraft();
      viewReady = Promise.resolve();
    }
    // 默认视图渲染完后，若这是刷新前未结束的会话，再切到对应窗口并续读
    viewReady.then(function () { maybeResumeRun(id); });
  }).catch(function (e) { addErrorBar("加载会话失败: " + e.message); });
}

function memberOf(idOrName) {
  return (state.roomMembers || []).find(function (m) {
    return m.id === idOrName || m.name === idOrName || !idOrName;
  });
}
function roomMode() {
  return !!(state.roomThreads && state.roomThreads.length);
}
/* 抽屉是否进入房间窗口二级列表：≥2 个 AI 才需要；
 * 单 AI 房间停在第一层会话列表，点击后直接打开唯一窗口 */
function roomNavActive() {
  if (!state.roomThreads || !state.roomThreads.length) return false;
  var aiN = (state.roomMembers || []).filter(function (m) { return !m.is_user; }).length;
  return aiN >= 2;
}
/* R8：用户扮演自设（persona）—— 存在用户座位的 character_id 上，复用库角色卡 */
function roomUserSeat() {
  return (state.roomMembers || []).find(function (m) { return m.is_user; });
}
function personaChar() {
  var seat = roomUserSeat();
  if (!seat || !seat.character_id) return null;
  return characterOf(seat.character_id) || null;
}
function setPersona(seat, characterId) {
  if (!seat || !state.currentSessionId) return;
  fetchJSON("/api/rooms/" + state.currentSessionId + "/members/" + seat.id, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ character_id: characterId }),
  }).then(function (m) {
    var idx = state.roomMembers.findIndex(function (x) { return x.id === seat.id; });
    if (idx >= 0) state.roomMembers[idx] = m;
    renderSessionMeta();
    // 刷新当前窗口，让已有用户气泡也按新扮演角色重标注
    if (state.currentThreadId) loadThread(state.currentThreadId);
  }).catch(function (e) { addErrorBar("设置自设失败: " + e.message); });
}

function renderThreads() {
  renderSessionList();  // 房间窗口改由左侧栏渲染（renderRoomNav），此函数仅作统一入口
}

/* 运行结束后重拉房间成员：update_chat_memory / 📝记录记忆 在服务端改了
   member.memory 并落盘，不刷新的话 🧠 记忆弹窗要等重进房间才看得到 */
function refreshRoomMembers() {
  if (!state.currentSessionId || !roomMode()) return Promise.resolve();
  return fetchJSON("/api/sessions/" + state.currentSessionId).then(function (s) {
    state.roomMembers = s.members || state.roomMembers;
  }).catch(function () { /* 静默：下次打开弹窗可重试，不影响收尾 */ });
}

function loadThread(tid) {
  if (!state.currentSessionId) return;
  state.currentThreadId = tid;
  state.currentSpeaker = null;
  renderThreads();
  return fetchJSON("/api/rooms/" + state.currentSessionId + "/threads/" + tid).then(function (t) {
    var idx = -1;
    state.roomThreads.forEach(function (x, i) { if (x.id === tid) idx = i; });
    if (idx >= 0) state.roomThreads[idx] = t; else state.roomThreads.push(t);
    renderHistory(t.messages || [], t.edits || {});
    restoreDraft();
    // 从磁盘重画后，把本轮思考用时按消息 id 回填（否则被裸「已思考」覆盖）
    applyPendingThinkingDurations();
  }).catch(function (e) { addErrorBar("加载窗口失败: " + e.message); });
}

function currentThread() {
  return (state.roomThreads || []).find(function (t) {
    return t.id === state.currentThreadId;
  });
}

// 房间结构变更后重新拉取房间数据并续画窗口
function reloadRoomState(fallbackTid) {
  return fetchJSON("/api/sessions/" + state.currentSessionId).then(function (s) {
    state.roomMembers = s.members || [];
    state.roomThreads = s.threads || [];
    var tid = state.currentThreadId;
    if (!state.roomThreads.some(function (t) { return t.id === tid; })) {
      tid = (fallbackTid && state.roomThreads.some(function (t) {
        return t.id === fallbackTid;
      })) ? fallbackTid : state.roomThreads[0].id;
    }
    renderThreads();
    return loadThread(tid);
  });
}

function doRemoveMember(m) {
  fetchJSON("/api/rooms/" + state.currentSessionId + "/members/" + m.id, {
    method: "DELETE",
  }).then(function () {
    el.memDialog.close();
    // 当前窗口若含该成员则仍可保留（历史保留）；窗口被删时 reload 会自动换窗
    return reloadRoomState();
  }).catch(function (e) { showToast("移除失败: " + e.message, "err"); });
}

/* ---- 窗口重命名 ---- */

var renameTarget = null;
function openThreadRename(t, winLabel) {
  renameTarget = t;
  var input = document.getElementById("thread-rename-input");
  input.value = t.title || winLabel || "";
  safeCloseDialogs();
  document.getElementById("thread-rename-dialog").showModal();
  input.focus(); input.select();
}

function confirmThreadRename() {
  var t = renameTarget;
  var input = document.getElementById("thread-rename-input");
  var title = input.value.trim();
  if (!t) return;
  if (!title) { showToast("窗口名称不能为空", "err"); return; }
  fetchJSON("/api/rooms/" + state.currentSessionId + "/threads/" + t.id, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title }),
  }).then(function (nt) {
    var i = state.roomThreads.findIndex(function (x) { return x.id === t.id; });
    if (i >= 0) state.roomThreads[i] = nt;
    document.getElementById("thread-rename-dialog").close();
    renderThreads();
  }).catch(function (e) { showToast("重命名失败: " + e.message, "err"); });
}

/* ---- 删除窗口 ---- */

function confirmDeleteThread(t, winLabel) {
  askConfirm({
    title: "删除窗口",
    text: "确定删除窗口「" + winLabel + "」？窗口内全部消息将被删除，且无法恢复。",
    okLabel: "删 除",
    onOk: function () { doDeleteThread(t.id); },
  });
}

function doDeleteThread(tid) {
  fetchJSON("/api/rooms/" + state.currentSessionId + "/threads/" + tid, {
    method: "DELETE",
  }).then(function () {
    state.roomThreads = state.roomThreads.filter(function (t) {
      return t.id !== tid;
    });
    renderThreads();
    if (state.currentThreadId === tid) {
      loadThread(state.roomThreads[0].id);
    }
  }).catch(function (e) { showToast("删除失败: " + e.message, "err"); });
}

/* ---- 群聊发言顺序：随机开关（按会话持久） ---- */

function shuffleStorageKey(sid) { return "flyai-shuffle-" + sid; }
function isShuffleOn() {
  if (!state.currentSessionId) return false;
  return localStorage.getItem(shuffleStorageKey(state.currentSessionId)) === "1";
}
function toggleShuffle() {
  if (!state.currentSessionId) return;
  var on = !isShuffleOn();
  localStorage.setItem(shuffleStorageKey(state.currentSessionId), on ? "1" : "0");
  renderSessionMeta();
}

function openGroupDialog() {
  el.groupPick.innerHTML = "";
  el.groupMsg.textContent = ""; el.groupMsg.className = "msg-bar";
  var ais = (state.roomMembers || []).filter(function (m) { return !m.is_user; });
  ais.forEach(function (m) {
    var lbl = document.createElement("label");
    lbl.className = "f-label toggle check-line";
    var cb = document.createElement("input"); cb.type = "checkbox"; cb.value = m.id;
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(" " + escapeHtml(memberDisplayName(m))));
    el.groupPick.appendChild(lbl);
  });
  if (!state.currentSessionId || !ais.length) {
    el.groupMsg.textContent = "房间内还没有 AI 参与者。";
    el.groupMsg.className = "msg-bar err"; return;
  }
  safeCloseDialogs();
  el.groupDialog.showModal();
}

function confirmGroup() {
  var ids = [];
  el.groupPick.querySelectorAll("input:checked").forEach(function (cb) { ids.push(cb.value); });
  if (ids.length < 2) { el.groupMsg.textContent = "至少选择两个 AI 参与者"; el.groupMsg.className = "msg-bar err"; return; }
  fetchJSON("/api/rooms/" + state.currentSessionId + "/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ member_ids: ids, title: "" }),
  }).then(function (t) {
    state.roomThreads.push(t);
    el.groupDialog.close();
    renderThreads();
    loadThread(t.id);
  }).catch(function (e) { el.groupMsg.textContent = e.message; el.groupMsg.className = "msg-bar err"; });
}

function openMemDialog() {
  el.memBody.innerHTML = "";
  var ais = (state.roomMembers || []).filter(function (m) { return !m.is_user; });
  el.memTitle.textContent = "房间号 " + (state.currentSessionId || "").slice(0, 8) + " · 各参与者在本房间的记忆（各窗口共享）与属性";
  ais.forEach(function (m) {
    var ch = characterOf(m.character_id);
    var wrap = document.createElement("div");
    wrap.className = "mem-block";
    wrap.innerHTML = '<div class="mem-head">' + makeAvatarHtml(ch || m, true) + ' <b>' + escapeHtml(memberDisplayName(m)) + "</b></div>";

    // ---- 座位设置：专属提供商 / 记忆簿挂载 / 移除成员 ----
    var seatLine = document.createElement("div");
    seatLine.className = "mem-seat-line";

    var provSel = document.createElement("select");
    provSel.className = "mem-prov";
    provSel.title = "该座位专属 LLM；不选则用房间默认提供商";
    var po = document.createElement("option");
    po.value = ""; po.textContent = "房间默认提供商";
    provSel.appendChild(po);
    (state.config.providers || []).forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.id; o.textContent = p.name || p.id;
      if ((m.provider_id || "") === p.id) o.selected = true;
      provSel.appendChild(o);
    });
    seatLine.appendChild(provSel);

    var modeSel = document.createElement("select");
    modeSel.className = "mem-memmode";
    modeSel.title = "跨会话角色记忆簿的挂载方式";
    [["", "不挂记忆簿"], ["rw", "记忆簿·读写"], ["ro", "记忆簿·只读"]].forEach(function (pair) {
      var o = document.createElement("option");
      o.value = pair[0]; o.textContent = pair[1];
      if ((m.memory_mode || "") === pair[0]) o.selected = true;
      modeSel.appendChild(o);
    });
    seatLine.appendChild(modeSel);

    var ownerSel = document.createElement("select");
    ownerSel.className = "mem-memowner";
    ownerSel.title = "挂载/写入的记忆簿归属角色；自己的角色无独立记忆簿时可挂别的卡";
    var bo = document.createElement("option");
    bo.value = ""; bo.textContent = "本角色的记忆簿";
    ownerSel.appendChild(bo);
    (state.characters || []).forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = cardDisplayName(c) + "（" + c.id.slice(0, 6) + "）";
      if ((m.memory_char_id || "") === c.id) o.selected = true;
      ownerSel.appendChild(o);
    });
    seatLine.appendChild(ownerSel);

    var rmBtn = document.createElement("button");
    rmBtn.type = "button"; rmBtn.className = "btn small danger";
    rmBtn.textContent = "移除成员";
    rmBtn.addEventListener("click", function () {
      askConfirm({
        title: "移除房间成员",
        text: "确定把「" + memberDisplayName(m) + "」移出房间？其私聊窗口将删除，各群聊窗口不再有它（历史消息保留）。",
        okLabel: "移 除",
        onOk: function () { doRemoveMember(m); },
      });
    });
    seatLine.appendChild(rmBtn);

    wrap.appendChild(seatLine);

    var memBox = document.createElement("textarea");
    memBox.className = "mem-memory"; memBox.rows = 2;
    memBox.placeholder = "该 AI 在本房间的记忆（各窗口共享）"; memBox.value = m.memory || "";
    var attrBox = document.createElement("textarea");
    attrBox.className = "mem-attrs"; attrBox.rows = 2;
    attrBox.placeholder = '属性 JSON，如 {"魅力":80,"好感度":50}';
    try { attrBox.value = JSON.stringify(m.attributes || {}, null, 2); } catch (e) { attrBox.value = ""; }
    var save = document.createElement("button"); save.className = "btn small"; save.textContent = "保存";
    var msg = document.createElement("div"); msg.className = "msg-bar";
    var sp = document.createElement("div"); sp.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:6px";
    sp.appendChild(save); sp.appendChild(msg);
    wrap.appendChild(memBox); wrap.appendChild(attrBox); wrap.appendChild(sp);
    save.addEventListener("click", function () {
      var attrs = {};
      try { attrs = JSON.parse(attrBox.value || "{}"); } catch (e) {
        msg.textContent = "属性 JSON 解析失败"; msg.className = "msg-bar err"; return;
      }
      if (!Array.isArray(attrs)) {
        fetchJSON("/api/rooms/" + state.currentSessionId + "/members/" + m.id, {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            memory: memBox.value, attributes: attrs,
            provider_id: provSel.value,
            memory_mode: modeSel.value,
            memory_char_id: ownerSel.value,
          }),
        }).then(function (mm) {
          m.memory = mm.memory; m.attributes = mm.attributes;
          m.provider_id = mm.provider_id;
          m.memory_mode = mm.memory_mode;
          m.memory_char_id = mm.memory_char_id;
          msg.textContent = "已保存"; msg.className = "msg-bar ok";
        }).catch(function (e) { msg.textContent = e.message; msg.className = "msg-bar err"; });
      } else {
        msg.textContent = "属性请提交对象，而非数组"; msg.className = "msg-bar err";
      }
    });
    el.memBody.appendChild(wrap);
  });
  safeCloseDialogs();
  el.memDialog.showModal();
}

function onDeleteSession(id) {
  if (state.busy) return;
  if (!confirm("删除该会话？")) return;
  fetchJSON("/api/sessions/" + id, { method: "DELETE" }).then(function () {
    if (state.currentSessionId === id) {
      state.currentSessionId = null;
      clearMessages();
      updateEmptyState();
    }
    return refreshSessions();
  });
}

function clearMessages() {
  el.messages.innerHTML = "";
  state.currentAssistant = null;
  state.turnNodes = {};
}

