/**
 * 酒馆助手（TavernHelper / JS-Slash-Runner）兼容运行时（P1：状态面板）。
 *
 * 卡内 ``extensions.tavern_helper.scripts`` 原本运行在酒馆助手的 about:srcdoc
 * iframe 里，依赖宿主 document、MVU 全局、事件总线与一组 TavernHelper API。
 * 这里把面板 HTML 与一份“消息镜像”放进同一个沙箱 iframe，再注入轻量兼容层，
 * 让作者脚本能在自己的文档里找到 ``#chat`` / ``.mes[mesid]`` / ``[data-yzdj]``
 * 并正常渲染。
 *
 * 服务端只下发脚本正文；执行完全在无同源权限的 iframe 内，读不到登录态。
 * 脚本对变量的写入通过 postMessage 交回宿主（P2 再接持久化）。
 */

import type { Message } from '../chat/types.js'
import type {
  CardWriteContext,
  StAuthorPayload,
  StAuthorScript,
} from '../flyai/types.js'
import {
  CARD_APP_PROTOCOL_VERSION,
  CARD_MSG_ERROR,
  CARD_MSG_READY,
  CARD_MSG_RESIZE,
  escapeForScript,
} from './cardApp.js'
import JQUERY_SOURCE from './vendor/jquery-3.6.0.min.js?raw'
import LODASH_SOURCE from './vendor/lodash-4.18.1.min.js?raw'
import ZOD_SOURCE from './vendor/zod-4.6.7.min.js?raw'

/** 宿主 → 卡：变量/消息快照更新 */
export const AUTHOR_MSG_VARS = 'flyai-author-vars'
/** 卡 → 宿主：MVU 变量写入意图 */
export const AUTHOR_MSG_WRITE = 'flyai-author-write'
/** 卡 → 宿主：作者脚本想填入输入框的文本（不直接发送） */
export const AUTHOR_MSG_COMPOSE = 'flyai-author-compose'
/** 卡 → 宿主：发送/继续/重生成等模型动作（宿主逐次确认） */
export const AUTHOR_MSG_ACTION = 'flyai-author-action'
/** 卡 → 宿主：世界书写回 */
export const AUTHOR_MSG_WRITE_WORLD = 'flyai-author-write-world'
/** 卡 → 宿主：角色副本字段写回 */
export const AUTHOR_MSG_WRITE_CHARACTER = 'flyai-author-write-character'
/** 宿主 → 卡：生成/聊天事件 */
export const AUTHOR_MSG_EVENT = 'flyai-author-event'

const MAX_MESSAGES = 200
const MAX_MESSAGE_CHARS = 8000

export type AuthorRuntimeMessage = {
  role: 'user' | 'assistant'
  text: string
  name?: string
  statData?: Record<string, unknown>
}

export type AuthorRuntimeOptions = {
  /** 当前要渲染的面板 HTML（展示侧正则替换后的结果） */
  html: string
  author: StAuthorPayload
  messages: AuthorRuntimeMessage[]
  /** 会话级 MVU stat_data */
  variables?: Record<string, unknown> | null
  character?: { name?: string; description?: string; avatar?: string | null }
  externalMedia?: boolean
  /** 世界书/角色副本读取快照（st.write_context） */
  writeContext?: CardWriteContext
}

/** 会话可见消息 → 作者运行时镜像；只保留文本与变量快照。 */
export const authorRuntimeMessages = (
  messages: readonly Message[]
): AuthorRuntimeMessage[] => {
  const out: AuthorRuntimeMessage[] = []
  for (const message of messages) {
    if (message.role === 'user') {
      out.push({
        role: 'user',
        text: message.content.slice(0, MAX_MESSAGE_CHARS),
        statData: message.statData,
      })
      continue
    }
    if (message.role !== 'assistant') {
      continue
    }
    const text = message.blocks
      .filter((block) => block.type === 'text')
      .map((block) => (block.type === 'text' ? block.text : ''))
      .join('')
    out.push({
      role: 'assistant',
      text: text.slice(0, MAX_MESSAGE_CHARS),
      name: message.speakerName,
      statData: message.statData,
    })
  }
  return out.slice(-MAX_MESSAGES)
}

/**
 * 作者脚本常把 `window.top` / `window.parent` 当成酒馆主窗口访问
 * （`window.top.addEventListener(...)`、`window.parent.SillyTavern...`）。
 * 沙箱 iframe 是 opaque origin，直接读这些属性会抛 SecurityError；Chrome 又
 * 不允许改写 `window.top`。这里在源码层面把可执行的顶层窗口访问改到
 * `window.__FLYAI_HOST__`（shim 提供的同文档代理），字符串/注释/模板字面量不动。
 */
export const adaptAuthorSource = (source: string): string => {
  // 方括号形式在字面量分割前先处理：`window["top"]` 的 key 是字符串，
  // 会在分词时被拆开。
  source = source.replace(
    /\b(window|self|globalThis)\s*(?:\?\.)?\[\s*(['"])(top|parent)\2\s*\]/g,
    'window.__FLYAI_HOST__'
  )
  const parts: { code: boolean; text: string }[] = []
  let code = ''
  let i = 0
  const flush = () => {
    if (code) {
      parts.push({ code: true, text: code })
      code = ''
    }
  }
  while (i < source.length) {
    const ch = source[i]
    const next = source[i + 1]
    if (ch === '/' && next === '/') {
      flush()
      let end = source.indexOf('\n', i)
      if (end < 0) end = source.length
      parts.push({ code: false, text: source.slice(i, end) })
      i = end
      continue
    }
    if (ch === '/' && next === '*') {
      flush()
      let end = source.indexOf('*/', i + 2)
      end = end < 0 ? source.length : end + 2
      parts.push({ code: false, text: source.slice(i, end) })
      i = end
      continue
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      flush()
      const quote = ch
      let j = i + 1
      while (j < source.length) {
        const c = source[j]
        if (c === '\\') {
          j += 2
          continue
        }
        if (c === quote) {
          j += 1
          break
        }
        // 模板字面量里的 ${...} 内部仍是代码；为保守起见整段不重写，
        // 作者脚本极少在模板表达式里访问 top/parent。
        j += 1
      }
      parts.push({ code: false, text: source.slice(i, j) })
      i = j
      continue
    }
    code += ch
    i += 1
  }
  flush()

  const rewrite = (text: string): string => {
    let out = text
    // window.top / window.parent / self.top / globalThis.parent（含可选链与空白）
    out = out.replace(
      /\b(window|self|globalThis)\s*(?:\?\.)?\.\s*(top|parent)\b/g,
      'window.__FLYAI_HOST__'
    )
    // 裸 top / parent：仅当脚本没有自己声明同名变量时改写。
    const declares = /\b(?:var|let|const|function)\s+(?:top|parent)\b/.test(text)
    if (!declares) {
      out = out.replace(
        /(^|[^\w$.])(?:top|parent)\s*(?=[.[\]]|$)/g,
        '$1window.__FLYAI_HOST__'
      )
    }
    return out
  }

  return parts
    .map((part) => (part.code ? rewrite(part.text) : part.text))
    .join('')
}

/** 展示侧正则里的整页 HTML 应用：只改写内联脚本中的 top/parent 访问。 */
export const adaptHtmlScripts = (html: string): string =>
  html.replace(
    /<script\b([^>]*)>([\s\S]*?)<\/script>/gi,
    (all: string, attrs: string, body: string) =>
      /<script\b[^>]*\bsrc\s*=/i.test(all)
        ? all
        : `<script${attrs}>${adaptAuthorSource(body)}</script>`
  )

const escapeHtml = (text: string): string =>
  text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')

const escapeScriptBody = (source: string): string =>
  source.replace(/<\/script/gi, '<\\/script')

/** 把卡内脚本里的 CDN import 映射到本地 shim，避免依赖外网。
 *
 * 很多卡（包括御主地界）不会写出完整 URL，而是先拼常量：
 *   const MVU_PATH = 'MagicalAstrogy/MagVarUpdate@' + MVU_SHA + '/artifact/bundle.js'
 *   'https://cdn.jsdelivr.net/gh/' + MVU_PATH
 * 所以除了字面量 URL，还要从 SHA 常量与 CDN 基础前缀还原出最终 URL。
 */
const buildImportMap = (source: string): string => {
  const imports: Record<string, string> = {}
  const mvu = `data:text/javascript,${encodeURIComponent(
    'export const Mvu = globalThis.Mvu; export default Mvu;'
  )}`
  const zod = `data:text/javascript,${encodeURIComponent(
    'export const registerMvuSchema = (...a) => globalThis.registerMvuSchema(...a);'
  )}`
  const add = (url: string, target: string) => {
    if (url && !imports[url]) {
      imports[url] = target
    }
  }
  // 1) 字面量完整 URL
  for (const match of source.matchAll(/["'](https?:\/\/[^\s"']+)["']/g)) {
    const url = match[1]
    if (/MagVarUpdate(?:@[^/]+)?\/artifact\/bundle|MVU-offline@[^/]+\/mvu_bundle_full/i.test(url)) {
      add(url, mvu)
    } else if (/\/mvu_zod\.js(?:[?#]|$)/i.test(url)) {
      add(url, zod)
    }
  }
  // 2) 拼接常量：CDN 基础前缀 + SHA + 固定路径
  const bases: string[] = []
  for (const match of source.matchAll(/["'](https?:\/\/[^"']+\/gh\/)["']/g)) {
    if (!bases.includes(match[1])) {
      bases.push(match[1])
    }
  }
  const shas: string[] = []
  for (const match of source.matchAll(
    /(?:const|let|var)\s+\w*(?:SHA|HASH|VERSION)\w*\s*=\s*["']([0-9a-f]{7,40})["']/gi
  )) {
    if (!shas.includes(match[1])) {
      shas.push(match[1])
    }
  }
  for (const base of bases) {
    for (const sha of shas) {
      add(`${base}MagicalAstrogy/MagVarUpdate@${sha}/artifact/bundle.js`, mvu)
      add(`${base}StageDog/tavern_resource@${sha}/dist/util/mvu_zod.js`, zod)
    }
  }
  if (Object.keys(imports).length === 0) {
    return ''
  }
  return `<script type="importmap">${JSON.stringify({ imports })}</script>`
}

const contentSecurityPolicy = (externalMedia: boolean): string =>
  [
    "default-src 'none'",
    "script-src 'unsafe-inline' 'unsafe-eval' data: blob: https://cdn.jsdelivr.net https://fastly.jsdelivr.net https://gcore.jsdelivr.net https://testingcf.jsdelivr.net https://cdn.statically.io https://raw.githack.com https://cdnjs.cloudflare.com https://unpkg.com https://code.jquery.com",
    "style-src 'unsafe-inline' https: data:",
    `img-src data: blob:${externalMedia ? ' https:' : ''}`,
    `media-src data: blob:${externalMedia ? ' https:' : ''}`,
    'font-src data: https:',
    "connect-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ')

const chatMarkup = (
  messages: readonly AuthorRuntimeMessage[],
  panelHtml: string
): string => {
  if (messages.length === 0) {
    return `<div id="chat"><div class="mes" mesid="0" data-is-user="false"><div class="mes_text">${panelHtml}</div></div></div>`
  }
  const parts = messages.map((message, index) => {
    const isLast = index === messages.length - 1
    // 消息正文只为作者脚本提供 DOM 文本，不直接展示（否则长对话会把整段
    // 聊天记录倒进状态面板）。用离屏 span 保留 textContent/innerText。
    const body = `<span class="flyai-mirror-text">${escapeHtml(message.text)}</span>${isLast ? panelHtml : ''}`
    return `<div class="mes" mesid="${index}" data-is-user="${
      message.role === 'user' ? 'true' : 'false'
    }"><div class="mes_text">${body}</div></div>`
  })
  return `<div id="chat">${parts.join('')}</div>`
}

type ShimOptions = {
  messages: readonly AuthorRuntimeMessage[]
  variables?: Record<string, unknown> | null
  character?: { name?: string; description?: string; avatar?: string | null }
  writeContext?: CardWriteContext
}

const buildShim = ({
  messages,
  variables,
  character,
  writeContext,
}: ShimOptions): string => {
  const messageData = JSON.stringify(messages)
  const chatVariables = JSON.stringify({
    stat_data:
      variables && typeof variables === 'object' ? variables : {},
  })
  const characterData = JSON.stringify({
    name: character?.name ?? '',
    description: character?.description ?? '',
    avatar: character?.avatar ?? '',
  })
  const worldbookData = JSON.stringify({
    name: writeContext?.worldbook?.name ?? '',
    entries: writeContext?.worldbook?.entries ?? [],
    charWorldName: writeContext?.characterWorldName ?? '',
    character: writeContext?.character ?? {},
  })
  return `(function(){
  var HOST = window.parent || window;
  try { Object.defineProperty(window, 'parent', { value: window, configurable: true }); } catch (e) {}
  try { Object.defineProperty(window, 'top', { value: window, configurable: true }); } catch (e) {}
  // 源码里对 window.top / window.parent 的访问会改写成 __FLYAI_HOST__；
  // 代理把 DOM/事件/酒馆对象转发到当前文档，postMessage 转到真实宿主。
  var hostProxy = window;
  try {
    hostProxy = new Proxy(window, {
      get: function(target, prop){
        if (prop === 'postMessage') {
          return function(data){ try { HOST.postMessage(data, '*'); } catch (e) {} };
        }
        if (prop === 'parent' || prop === 'top') return hostProxy;
        var value = target[prop];
        return typeof value === 'function' ? value.bind(target) : value;
      },
      set: function(target, prop, value){ target[prop] = value; return true; }
    });
  } catch (e) { hostProxy = window; }
  window.__FLYAI_HOST__ = hostProxy;
  var VERSION = ${CARD_APP_PROTOCOL_VERSION};
  var RESIZE = ${escapeForScript(CARD_MSG_RESIZE)};
  var READY = ${escapeForScript(CARD_MSG_READY)};
  var ERROR_EVENT = ${escapeForScript(CARD_MSG_ERROR)};
  var MSG_VARS = ${escapeForScript(AUTHOR_MSG_VARS)};
  var MSG_WRITE = ${escapeForScript(AUTHOR_MSG_WRITE)};
  var MSG_COMPOSE = ${escapeForScript(AUTHOR_MSG_COMPOSE)};
  var MSG_ACTION = ${escapeForScript(AUTHOR_MSG_ACTION)};
  var MSG_WRITE_WORLD = ${escapeForScript(AUTHOR_MSG_WRITE_WORLD)};
  var MSG_WRITE_CHARACTER = ${escapeForScript(AUTHOR_MSG_WRITE_CHARACTER)};
  var MSG_EVENT = ${escapeForScript(AUTHOR_MSG_EVENT)};
  var MESSAGES = JSON.parse(${escapeForScript(messageData)});
  var CHAT_VARIABLES = JSON.parse(${escapeForScript(chatVariables)});
  var CHARACTER = JSON.parse(${escapeForScript(characterData)});
  var WORLD = JSON.parse(${escapeForScript(worldbookData)});
  var WORLD_ENTRIES = Array.isArray(WORLD.entries) ? WORLD.entries : [];
  var CHARACTER_FIELDS = WORLD.character && typeof WORLD.character === 'object' ? WORLD.character : {};
  function clone(value){
    try { return JSON.parse(JSON.stringify(value)); } catch (e) { return {}; }
  }
  function storage(){
    var data = {};
    return {
      getItem: function(k){ k = String(k); return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
      setItem: function(k, v){ data[String(k)] = String(v); },
      removeItem: function(k){ delete data[String(k)]; },
      clear: function(){ data = {}; },
      key: function(i){ return Object.keys(data)[i] || null; },
      get length(){ return Object.keys(data).length; }
    };
  }
  try { Object.defineProperty(window, 'localStorage', { value: storage(), configurable: true }); } catch (e) {}
  try { Object.defineProperty(window, 'sessionStorage', { value: storage(), configurable: true }); } catch (e) {}

  var listeners = {};
  function on(event, handler, once){
    if (typeof handler !== 'function') return;
    var key = String(event);
    (listeners[key] = listeners[key] || []).push({ fn: handler, once: !!once });
  }
  function off(event, handler){
    var key = String(event);
    var list = listeners[key];
    if (!list) return;
    if (!handler) { delete listeners[key]; return; }
    listeners[key] = list.filter(function(item){ return item.fn !== handler; });
  }
  function emit(event){
    var key = String(event);
    var args = Array.prototype.slice.call(arguments, 1);
    var list = (listeners[key] || []).slice();
    for (var i = 0; i < list.length; i += 1) {
      var item = list[i];
      if (item.once) off(key, item.fn);
      try { item.fn.apply(null, args); } catch (e) { try { console.error(e); } catch (ignore) {} }
    }
  }
  var eventSource = {
    on: function(event, handler){ on(event, handler); },
    once: function(event, handler){ on(event, handler, true); },
    makeFirst: function(event, handler){ on(event, handler); },
    makeLast: function(event, handler){ on(event, handler); },
    off: function(event, handler){ off(event, handler); },
    removeListener: function(event, handler){ off(event, handler); },
    emit: emit
  };
  window.eventOn = function(event, handler){ on(event, handler); };
  window.eventOnce = function(event, handler){ on(event, handler, true); };
  window.eventOff = function(event, handler){ off(event, handler); };
  window.eventEmit = emit;
  window.eventRemoveListener = function(event, handler){ off(event, handler); };
  window.tavern_events = {
    MESSAGE_SENT: 'message_sent',
    MESSAGE_RECEIVED: 'message_received',
    MESSAGE_UPDATED: 'message_updated',
    MESSAGE_SWIPED: 'message_swiped',
    MESSAGE_DELETED: 'message_deleted',
    USER_MESSAGE_RENDERED: 'user_message_rendered',
    CHARACTER_MESSAGE_RENDERED: 'character_message_rendered',
    GENERATION_STARTED: 'generation_started',
    GENERATION_STOPPED: 'generation_stopped',
    GENERATION_ENDED: 'generation_ended',
    CHAT_CHANGED: 'chat_id_changed',
    CHAT_CREATED: 'chat_created',
    CHAT_DELETED: 'chat_deleted'
  };
  var MVU_EVENTS = {
    VARIABLE_INITIALIZED: 'mag_variable_initialized',
    VARIABLE_UPDATE_STARTED: 'mag_variable_update_started',
    COMMAND_PARSED: 'mag_command_parsed',
    VARIABLE_UPDATE_ENDED: 'mag_variable_update_ended',
    BEFORE_MESSAGE_UPDATE: 'mag_before_message_update',
    SINGLE_VARIABLE_UPDATED: 'mag_variable_updated'
  };

  function lastIndex(){ return MESSAGES.length ? MESSAGES.length - 1 : 0; }
  function resolveId(id){
    if (id === 'latest' || id === 'last' || id === undefined || id === null) return lastIndex();
    if (id === 'last_user') { for (var i = MESSAGES.length - 1; i >= 0; i -= 1) if (MESSAGES[i].role === 'user') return i; return lastIndex(); }
    if (id === 'last_char') { for (var j = MESSAGES.length - 1; j >= 0; j -= 1) if (MESSAGES[j].role === 'assistant') return j; return lastIndex(); }
    var n = Number(id);
    return isFinite(n) ? n : lastIndex();
  }
  function messageAt(index){
    var message = MESSAGES[index];
    if (!message) return null;
    var text = String(message.text || '');
    return {
      message_id: index,
      message: text,
      mes: text,
      name: message.name || (message.role === 'user' ? '' : CHARACTER.name),
      is_user: message.role === 'user',
      is_system: false,
      swipes: [text],
      extra: { variables: { stat_data: clone(message.statData || {}) } }
    };
  }
  window.getChatMessages = function(id){
    if (Array.isArray(id)) {
      var out = [];
      for (var i = 0; i < id.length && i < 50; i += 1) {
        var item = messageAt(resolveId(id[i]));
        if (item) out.push(item);
      }
      return out;
    }
    var one = messageAt(resolveId(id));
    return one ? [one] : [];
  };
  window.getCurrentMessageId = function(){ return lastIndex(); };
  window.getLastMessageId = function(){ return lastIndex(); };
  window.getLastMessage = function(){ return (MESSAGES[lastIndex()] || {}).text || ''; };
  function normalizeMvu(value){
    if (!value || typeof value !== 'object') return { stat_data: {} };
    return value.stat_data && typeof value.stat_data === 'object'
      ? clone(value)
      : { stat_data: clone(value) };
  }
  function messageVariables(index){
    var message = MESSAGES[index];
    if (message && message.statData && typeof message.statData === 'object') return message.statData;
    return null;
  }
  function chatStat(){
    return CHAT_VARIABLES && CHAT_VARIABLES.stat_data && typeof CHAT_VARIABLES.stat_data === 'object'
      ? CHAT_VARIABLES.stat_data
      : {};
  }
  window.Mvu = {
    events: MVU_EVENTS,
    getMvuData: function(option){
      var type = option && option.type ? String(option.type) : 'chat';
      if (type === 'message') {
        var index = resolveId(option.message_id);
        var stat = messageVariables(index);
        return { stat_data: clone(stat || chatStat()), display_data: {}, delta_data: {} };
      }
      return { stat_data: clone(chatStat()), display_data: {}, delta_data: {} };
    },
    replaceMvuData: function(value, option){
      var normalized = normalizeMvu(value);
      var type = option && option.type ? String(option.type) : 'chat';
      var index = type === 'message' ? resolveId(option.message_id) : null;
      if (index !== null) {
        if (MESSAGES[index]) MESSAGES[index].statData = clone(normalized.stat_data);
      } else {
        CHAT_VARIABLES = normalized;
      }
      try {
        HOST.postMessage({
          v: VERSION,
          type: MSG_WRITE,
          scope: type,
          messageId: index,
          stat_data: clone(normalized.stat_data)
        }, '*');
      } catch (e) {}
      emit(MVU_EVENTS.SINGLE_VARIABLE_UPDATED, null, null, null, clone(normalized.stat_data));
      emit(MVU_EVENTS.VARIABLE_UPDATE_ENDED, clone(normalized));
      return Promise.resolve();
    },
    getCurrentMvuData: function(){
      return { stat_data: clone(chatStat()), display_data: {}, delta_data: {} };
    },
    replaceCurrentMvuData: function(value){
      return window.Mvu.replaceMvuData(value, { type: 'chat' });
    }
  };
  window.registerMvuSchema = function(schema){
    window.__FLYAI_MVU_SCHEMA__ = schema;
    return true;
  };
  window.getAllVariables = function(){ return clone(CHAT_VARIABLES); };
  window.getVariables = function(option){
    var type = option && option.type ? String(option.type) : 'chat';
    if (type === 'message') {
      var index = resolveId(option.message_id);
      return { stat_data: clone(messageVariables(index) || chatStat()) };
    }
    return clone(CHAT_VARIABLES);
  };
  window.replaceVariables = function(value, option){ return window.Mvu.replaceMvuData(value, option); };
  window.updateVariablesWith = function(updater, option){
    var current = normalizeMvu(window.getVariables(option));
    var next = updater(clone(current));
    window.Mvu.replaceMvuData(next, option);
    return next;
  };
  window.insertOrAssignVariables = function(value, option){
    var current = normalizeMvu(window.getVariables(option));
    current.stat_data = Object.assign({}, current.stat_data, normalizeMvu(value).stat_data);
    window.Mvu.replaceMvuData(current, option);
    return current;
  };
  window.insertVariables = function(value, option){
    var current = normalizeMvu(window.getVariables(option));
    current.stat_data = Object.assign({}, normalizeMvu(value).stat_data, current.stat_data);
    window.Mvu.replaceMvuData(current, option);
    return current;
  };
  window.deleteVariable = function(key, option){
    var current = normalizeMvu(window.getVariables(option));
    delete current.stat_data[String(key)];
    window.Mvu.replaceMvuData(current, option);
    return current;
  };
  window.getChatVariable = function(key){ return chatStat()[String(key)]; };
  window.setChatVariable = function(key, value){ window.insertOrAssignVariables({ [String(key)]: value }, { type: 'chat' }); return true; };
  window.getGlobalVariables = function(){ return {}; };
  window.setGlobalVariable = function(){ return false; };

  window.waitGlobalInitialized = function(name){
    var key = String(name);
    return new Promise(function(resolve){
      if (typeof window[key] !== 'undefined') { resolve(window[key]); return; }
      var started = Date.now();
      var timer = setInterval(function(){
        if (typeof window[key] !== 'undefined') {
          clearInterval(timer); resolve(window[key]); return;
        }
        if (Date.now() - started >= 10000) { clearInterval(timer); resolve(undefined); }
      }, 50);
    });
  };
  function postWorld(entries){
    var list = Array.isArray(entries) ? clone(entries) : [];
    try { HOST.postMessage({ v: VERSION, type: MSG_WRITE_WORLD, entries: list }, '*'); } catch (e) {}
    WORLD_ENTRIES = list;
    return Promise.resolve(clone(list));
  }
  window.getWorldbookNames = function(){
    var names = [];
    if (WORLD.charWorldName) names.push(WORLD.charWorldName);
    if (WORLD.name && names.indexOf(WORLD.name) < 0) names.push(WORLD.name);
    return names;
  };
  window.getCharWorldbookNames = function(){
    return { primary: WORLD.charWorldName || WORLD.name || null, additional: [] };
  };
  window.getGlobalWorldbookNames = function(){ return []; };
  window.getWorldbook = function(){ return clone(WORLD_ENTRIES); };
  window.updateWorldbookWith = function(name, updater){
    var next = typeof updater === 'function' ? updater(clone(WORLD_ENTRIES)) : WORLD_ENTRIES;
    return postWorld(next);
  };
  window.updateWorldbook = window.updateWorldbookWith;
  window.replaceWorldbook = function(first, second){
    return postWorld(Array.isArray(first) ? first : second);
  };
  window.getCharName = function(){ return CHARACTER.name; };
  window.getCurrentCharName = window.getCharName;
  window.getCharAvatar = function(){ return CHARACTER.avatar; };

  window.SillyTavern = window.SillyTavern || {};
  window.SillyTavern.getContext = function(){
    return {
      characters: [{
        name: CHARACTER.name,
        avatar: CHARACTER.avatar,
        description: CHARACTER.description,
        data: { name: CHARACTER.name, avatar: CHARACTER.avatar, description: CHARACTER.description }
      }],
      characterId: 0,
      chat: MESSAGES.map(function(message){
        return {
          mes: String(message.text || ''),
          message: String(message.text || ''),
          name: message.name || (message.role === 'user' ? '' : CHARACTER.name),
          is_user: message.role === 'user',
          is_system: false,
          extra: {}
        };
      }),
      chatMetadata: {},
      name1: '',
      name2: CHARACTER.name,
      eventSource: eventSource,
      getCurrentMessageId: function(){ return lastIndex(); }
    };
  };
  window.toastr = {
    info: function(){}, success: function(){}, warning: function(){}, error: function(){}, clear: function(){}
  };
  try { window.alert = function(message){ try { HOST.postMessage({ v: VERSION, type: 'flyai-card-toast', level: 'info', text: String(message || '') }, '*'); } catch (e) {} }; } catch (e) {}
  try { window.confirm = function(){ return false; }; } catch (e) {}
  try { window.prompt = function(){ return null; }; } catch (e) {}
  window.isEditMode = false;
  // 脚本在 body 末尾执行，DOM 已就绪；$(fn) 直接同步执行，
  // 避免 jQuery 的异步 ready 把“注册变量结构”推迟到 MVU ready 事件之后。
  try {
    var jq = window.jQuery;
    if (typeof jq === 'function' && typeof Proxy !== 'undefined') {
      var jqProxy = new Proxy(jq, {
        apply: function(target, thisArg, args){
          if (args.length === 1 && typeof args[0] === 'function') {
            try { args[0](target); } catch (e) { setTimeout(function(){ throw e; }, 0); }
            return target;
          }
          return target.apply(thisArg, args);
        }
      });
      window.$ = jqProxy;
      window.jQuery = jqProxy;
    }
  } catch (e) {}

  function postAction(action, text){
    try { HOST.postMessage({ v: VERSION, type: MSG_ACTION, action: action, text: text ? String(text) : '' }, '*'); } catch (e) {}
  }
  function splitSlashChain(text){
    var parts = [], current = '';
    for (var i = 0; i < text.length; i += 1) {
      var ch = text.charAt(i);
      if (ch === '\\\\' && text.charAt(i + 1) === '|') { current += '|'; i += 1; }
      else if (ch === '|') { parts.push(current); current = ''; }
      else current += ch;
    }
    parts.push(current);
    return parts;
  }
  window.triggerSlash = function(command){
    var parts = splitSlashChain(String(command == null ? '' : command));
    for (var i = 0; i < parts.length; i += 1) {
      var segment = parts[i].replace(/^\\s+/, '');
      if (!segment) continue;
      var match = /^\\/([A-Za-z_]+)\\b([\\s\\S]*)$/.exec(segment);
      if (!match) continue;
      var name = match[1].toLowerCase();
      var rest = match[2].replace(/^\\s+/, '');
      if (name === 'send') postAction('send', rest);
      else if (name === 'continue' || name === 'cont') postAction('continue');
      else if (name === 'regenerate' || name === 'regen') postAction('regenerate');
      else if (name === 'trigger') continue;
      else if (name === 'setvar' || name === 'setchatvar' || name === 'delvar' || name === 'delchatvar') {
        var eq = rest.indexOf('=');
        var key = (eq >= 0 ? rest.slice(0, eq) : rest).replace(/^\\s+|\\s+$/g, '');
        if (!key) continue;
        var current = normalizeMvu(window.Mvu.getMvuData({ type: 'chat' }));
        if (name === 'delvar' || name === 'delchatvar') delete current.stat_data[key];
        else {
          var raw = eq >= 0 ? rest.slice(eq + 1).replace(/^\\s+/, '') : '';
          var value = raw;
          try { value = JSON.parse(raw); } catch (e) {}
          current.stat_data[key] = value;
        }
        window.Mvu.replaceMvuData(current, { type: 'chat' });
      }
    }
    return '';
  };
  window.substituteParams = function(value){
    if (typeof value !== 'string') return value;
    return value.replace(/\{\{([^{}]+)\}\}/g, function(all, expr){
      var body = String(expr).replace(/^\\s+|\\s+$/g, '');
      var lower = body.toLowerCase();
      if (lower === 'char' || lower === 'charname') return CHARACTER.name;
      if (lower === 'lastmessage') return String((MESSAGES[lastIndex()] || {}).text || '');
      return all;
    });
  };
  window.substitudeMacros = window.substituteParams;

  window.addEventListener('message', function(event){
    var data = event && event.data;
    if (!data || data.type !== MSG_VARS) return;
    if (data.variables && typeof data.variables === 'object') {
      CHAT_VARIABLES = normalizeMvu(data.variables);
      emit(MVU_EVENTS.VARIABLE_UPDATE_ENDED, clone(CHAT_VARIABLES));
    }
    if (data.type === MSG_EVENT && typeof data.event === 'string') {
      emit(data.event, data.payload);
      return;
    }
    if (Array.isArray(data.messages)) {
      var previous = MESSAGES.length;
      MESSAGES = data.messages;
      var chat = document.getElementById('chat');
      if (chat && MESSAGES.length > previous) {
        for (var index = previous; index < MESSAGES.length; index += 1) {
          var message = MESSAGES[index] || {};
          var row = document.createElement('div');
          row.className = 'mes';
          row.setAttribute('mesid', String(index));
          row.setAttribute('data-is-user', message.role === 'user' ? 'true' : 'false');
          var body = document.createElement('div');
          body.className = 'mes_text';
          body.textContent = String(message.text || '');
          row.appendChild(body);
          chat.appendChild(row);
        }
      }
      emit(window.tavern_events.MESSAGE_UPDATED, lastIndex());
    }
  });

  try {
    var composer = document.getElementById('send_textarea');
    if (composer) {
      composer.addEventListener('input', function(){
        try { HOST.postMessage({ v: VERSION, type: MSG_COMPOSE, text: String(composer.value || '') }, '*'); } catch (e) {}
      });
    }
  } catch (e) {}

  function report(){
    try {
      HOST.postMessage({
        v: VERSION,
        type: RESIZE,
        height: Math.max(120, document.documentElement.scrollHeight || 0)
      }, '*');
    } catch (e) {}
  }
  function reportError(value){
    var text = value && value.message ? value.message : String(value || '');
    if (!text) return;
    try { HOST.postMessage({ v: VERSION, type: ERROR_EVENT, level: 'error', text: text.slice(0, 300) }, '*'); } catch (e) {}
  }
  window.addEventListener('error', function(e){ reportError(e && (e.error || e.message)); });
  window.addEventListener('unhandledrejection', function(e){ reportError(e && e.reason); });
  if (window.ResizeObserver) {
    try { new ResizeObserver(report).observe(document.body); } catch (e) {}
  }
  window.addEventListener('load', function(){
    report();
    try { HOST.postMessage({ v: VERSION, type: READY }, '*'); } catch (e) {}
  });
  if (document.readyState !== 'loading') {
    report();
    try { HOST.postMessage({ v: VERSION, type: READY }, '*'); } catch (e) {}
  }
})();`
}

/** 组装作者运行时 iframe 的 srcDoc；没有脚本时返回空串。 */
export const buildAuthorDoc = (options: AuthorRuntimeOptions): string => {
  const scripts = (options.author.scripts ?? []).filter(
    (script): script is StAuthorScript =>
      !!script && typeof script.content === 'string' && script.content.trim().length > 0
  )
  if (scripts.length === 0) {
    return ''
  }
  const source = scripts
    .map((script) => adaptAuthorSource(script.content))
    .join('\n;\n')
  const shim = buildShim({
    messages: options.messages,
    variables: options.variables,
    character: options.character ?? options.author.character,
    writeContext: options.writeContext,
  })
  const importMap = buildImportMap(source)
  const chineseFallback =
    '<div class="mes" mesid="0"><div class="mes_text"><div class="yzdj-boot">面板脚本未启动</div></div></div>'
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="${contentSecurityPolicy(
    options.externalMedia === true
  )}">
<style>
html,body{margin:0;padding:0;background:transparent;color:#1e2330;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.6;}
#chat{display:block;}
.mes{display:block;}
.mes_text{display:block;}
.flyai-mirror-text{position:absolute;left:-99999px;top:0;width:1px;height:1px;overflow:hidden;white-space:pre-wrap;}
#send_textarea{position:absolute;left:-9999px;top:0;width:1px;height:1px;opacity:0;}
#send_form,#extensionsMenu,#top-settings-holder{display:none;}
</style>
</head>
<body>
${chatMarkup(options.messages, options.html) || chineseFallback}
<textarea id="send_textarea" tabindex="-1" aria-hidden="true"></textarea>
<div id="send_form"></div>
<div id="extensionsMenu"></div>
<div id="top-settings-holder"></div>
<script>${JQUERY_SOURCE.replace(/<\/script/gi, '<\\/script')}</script>
<script>${LODASH_SOURCE.replace(/<\/script/gi, '<\\/script')}</script>
<script>${ZOD_SOURCE.replace(/<\/script/gi, '<\\/script')}</script>
<script>${shim}</script>
${importMap}
<script type="module">${escapeScriptBody(source)}</script>
</body>
</html>`
}
