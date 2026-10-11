/* FlyAI 前端核心：全局 state/el、初始化入口与通用工具（原生 JS 经典脚本，无构建；其余功能见同目录 *.js）。 */

"use strict";

var state = {
  config: null,
  sessions: [],
  characters: [],
  worlds: [],
  currentSessionId: null,
  plugins: [],
  busy: false,
  abortController: null,
  currentAssistant: null,
  currentAttrs: null,
  authToken: null,  // 登录令牌（Bearer），唯一访问凭证
  userId: null,
  userName: "",
  userLevel: 0,     // 权限等级 0-255；admin 256
  imageModelId: "", // 普通用户选用的生图模型（localStorage 持久化）
  sdEngine: false,  // 是否使用本地 Stable Diffusion 生图（localStorage 持久化）
  // 输入框「深度思考」开关（localStorage 持久化；仅在发送时随请求生效）
  deepThinking: false,
  // /api/tools 清单 [{name,description,category,allowed}]，供「⋯ → 工具」开关渲染
  toolsMeta: [],
  scopeCache: {},   // sessionId -> {char, world, scoped}（有效角色/世界观，含副本）
  // beta：多 AI 群聊
  roomMembers: [],    // 当前房间参与者（session.members）
  roomThreads: [],    // 当前房间窗口（session.threads + 群聊）
  roomView: false,    // 左侧栏是否处于「房间窗口导航」状态
  currentThreadId: null,
  currentSpeaker: null, // {member_id,name,character_id} 流式当前发言者
  pendingImages: [],    // 待发送图片 [{media_type, data(base66)}]
  newChat: null,        // 选卡临时态 {selected: {id: card}, rootId: ""}
  loreNav: "",          // 内容菜单角色卡两级导航：""=根卡列表，否则为根卡 id
  // 历史编辑/重生成
  turnNodes: {},        // anchor -> {userEl, editing}
  streamInsertBefore: null,  // 旧轮流式：新回复插在该节点之前；null=追加到底
  turnEditAnchor: null,     // 处于编辑/重生成流式中的轮 anchor
  // 批量删除会话
  batchMode: false,
  batchPicked: {},          // 选中的会话 id -> true
  // 侧栏搜索 / 滚动跟随 / 失败恢复
  sessionQuery: "",
  stickBottom: true,        // 用户贴底时新内容才自动滚动
  pendingSend: null,        // 本次已发送 {text, images}，请求失败时回填
  // 断线重连
  pendingResume: null,      // 进页面时读到的未结束运行 {runId, sid, tid}；用户点进该会话才续读
  currentRunId: null,       // 本次聊天运行 id（刷新后凭此续读）
  lastEventSeq: 0,          // 已收到的最后事件序号
  streamTerminal: false,    // 本次连接是否已收到终态帧
  reconnectAttempts: 0,
  runMarkerTimer: null,
  draftTimer: null,
  tokenTotals: {},          // 会话 id -> 累计总 token（含服务端持久值 + 本页面新增）
  tokenDetails: {},         // 会话 id -> {p, c} 输入/输出明细，供 chip 悬停提示
  // App（Capacitor）模式：前端页面内置在 APK，接口/上传文件指向用户配置的服务器
  isApp: false,
  serverBase: "",           // 如 http://192.168.1.10:8000（浏览器模式为空=同源）
};

var el = {};

/* App（APK）模式服务器写死：前端页面内置在安装包里，接口/上传资源走该域名 */
var APP_SERVER = "https://fly.eastcloud.top";

document.addEventListener("DOMContentLoaded", function () {
  cacheElements();
  bindEvents();
  initTheme();
  initChatBackground();
  // Capacitor APK：运行在原生 WebView 中，接口指向写死的服务器
  if (window.Capacitor && typeof window.Capacitor.isNativePlatform === "function"
      && window.Capacitor.isNativePlatform()) {
    state.isApp = true;
    state.serverBase = APP_SERVER;
  }
  // 状态栏透明叠加、图标随深浅色（仅 APK 生效，浏览器为空操作）
  NativeBar.init();
  bootstrap();
});

/* ==== 初始化 ==== */
function init() {
  return Promise.all([
    fetchJSON("/api/config"),
    fetchJSON("/api/sessions"),
    fetchJSON("/api/plugins"),
    fetchJSON("/api/characters"),
    fetchJSON("/api/worlds"),
    fetchJSON("/api/tools"),
  ]).then(function (res) {
    state.config = res[0];
    state.sessions = res[1].sessions || [];
    state.plugins = res[2].plugins || [];
    state.characters = res[3].characters || [];
    state.worlds = res[4].worlds || [];
    state.toolsMeta = res[5].tools || [];
    renderProviderBar();
    renderSessionList();
    renderSessionMeta();
    updateSdEngineButton();
    updateDeepThinkButton();
    loadResumeMarker();
  }).catch(function (e) {
    addErrorBar("初始化失败: " + e.message);
  });
}

/* 刷新/重开页面：只读取上次未结束的运行标记，不自动进入会话、不续读；
   会话列表给出 ⏳ 提示，等用户主动点进该会话时由 maybeResumeRun 续读 */
function loadResumeMarker() {
  var marker = null;
  try { marker = JSON.parse(localStorage.getItem(runStorageKey()) || "null"); } catch (e) {}
  if (!marker || !marker.run_id || !marker.sid) return;
  // 服务端运行终态保留 10 分钟；标记过旧直接丢弃（探活同样会 404）
  if (Date.now() - (marker.ts || 0) > 30 * 60 * 1000) { clearRunMarker(); return; }
  state.pendingResume = { runId: marker.run_id, sid: marker.sid, tid: marker.tid || "" };
  renderSessionList();  // 列表在 init 中先于此渲染，补一次让 ⏳ 徽标出现
}

/* 用户进入会话后调用：若该会话有未结束的运行——本机刷新标记，或在另一台
   设备/标签页上发起的——探活成功后续读（全量回放） */
function maybeResumeRun(sid) {
  var pr = state.pendingResume;
  var knownId = pr && pr.sid === sid ? pr.runId : null;
  var knownTid = knownId ? (pr.tid || "") : "";
  if (knownId) state.pendingResume = null;

  function resumeWith(runId) {
    fetchJSON("/api/chat/runs/" + runId).then(function (r) {
      if (r.status !== "running") {
        if (knownId) { clearRunMarker(); renderSessionList(); }
        return;
      }
      // 群聊房间：切到运行所在窗口，然后全量回放事件
      var tid = r.thread_id || knownTid || "";
      var chain = (tid && tid !== state.currentThreadId) ? loadThread(tid) : Promise.resolve();
      chain.then(function () {
        // 进入忙碌态；回放的事件会自行创建回答卡片，不预建
        state.busy = true;
        el.btnSend.disabled = true;
        el.btnStop.classList.remove("hidden");
        el.input.disabled = true;
        state.reconnectAttempts = 0;
        state.lastEventSeq = 0;  // 页面内存已空，从第 0 帧全量回放
        consumeRun(r.id, { post: false });
        renderSessionList();
      });
    }).catch(function (e) {
      if (knownId && String(e.message || "").indexOf("404") >= 0) {
        clearRunMarker(); renderSessionList();
      }
    });
  }

  if (knownId) { resumeWith(knownId); return; }
  // 本设备无标记：运行可能在另一台设备上。查服务端发现活跃 run 并续读，
  // 避免停在旧历史上、再发消息撞 409
  fetchJSON("/api/chat/active-run?session_id=" + encodeURIComponent(sid))
    .then(function (d) { if (d.run_id) resumeWith(d.run_id); })
    .catch(function () {});
}

/* App 模式下把绝对路径接口地址指到用户配置的服务器；
 * 浏览器模式 / 已是完整 URL / data: blob: 时原样返回 */
function apiUrl(url) {
  if (!state.serverBase || !url || url.charAt(0) !== "/" || url.charAt(1) === "/") return url;
  return state.serverBase + url;
}
/* 服务端返回的资源路径（/uploads/.. 头像等）：App 模式加服务器前缀；
 * /static 本地内置资源与 data/blob/http 地址不动 */
function assetUrl(u) {
  if (!u) return u;
  if (u.charAt(0) !== "/" || u.charAt(1) === "/") return u;
  if (u.indexOf("/static/") === 0) return u;
  return state.serverBase ? state.serverBase + u : u;
}

function fetchJSON(url, opts) {
  opts = opts || {};
  var headers = authHeaders(opts.headers || {});
  return fetch(apiUrl(url), Object.assign({}, opts, { headers: headers })).then(function (r) {
    if (!r.ok) return r.json().then(function (j) { throw new Error(j.detail || r.status); });
    return r.json();
  });
}

function intOr(v, d) {
  v = parseInt(v, 10);
  return isNaN(v) ? d : v;
}
function fmtNum(n) {
  return String(intOr(n, 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
/* 紧凑数字：999 以下原样；千级 10.5k（整千去小数 12k）；百万级 7M / 1.2M */
function fmtCompact(n) {
  n = intOr(n, 0);
  if (n >= 1000000) return trim1(n / 1000000) + "M";
  if (n >= 1000) {
    var k = trim1(n / 1000);
    return k === "1000" ? "1M" : k + "k";
  }
  return String(n);
}
function trim1(x) {
  var s = x.toFixed(1);
  return s.slice(-2) === ".0" ? s.slice(0, -2) : s;
}
/* ==== 工具函数 ==== */
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function escapeAttr(s) { return escapeHtml(s); }
