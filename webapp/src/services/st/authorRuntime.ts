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
import ST_CORE_SOURCE from './compat/stCore.js?raw'
import { buildEjsTemplateSource } from './native/ejsRuntime.js'
import {
  buildNativeImportMapEntries,
  ST_NATIVE_SNAPSHOT_SOURCE,
} from './native/stNative.js'

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
/** 卡 → 宿主：静默生成（手机短信/记忆整理），不进主聊天 */
export const AUTHOR_MSG_GENERATE = 'flyai-author-generate'
/** 宿主 → 卡：静默生成结果 */
export const AUTHOR_MSG_GENERATE_RESULT = 'flyai-author-generate-result'
/** 卡 → 宿主：手机主线摘要（供主聊天 EJS 的 DALPhoneMainline.read 使用） */
export const AUTHOR_MSG_PHONE_MAINLINE = 'flyai-author-phone-mainline'
/** 卡 → 宿主：临时提示词注入（剧情锚点/场记） */
export const AUTHOR_MSG_INJECT_PROMPTS = 'flyai-author-inject-prompts'
/** 卡 → 宿主：chatMetadata 持久化（地图/手札存档） */
export const AUTHOR_MSG_SAVE_METADATA = 'flyai-author-save-metadata'
/** 宿主 → 卡：chatMetadata 保存结果 */
export const AUTHOR_MSG_SAVE_METADATA_RESULT = 'flyai-author-save-metadata-result'
/** 卡 → 宿主：按消息 id 写回正文/隐藏/变量 */
export const AUTHOR_MSG_SAVE_MESSAGES = 'flyai-author-save-messages'
/** 宿主 → 卡：消息写回结果 */
export const AUTHOR_MSG_SAVE_MESSAGES_RESULT = 'flyai-author-save-messages-result'
/** 卡 → 宿主：主聊天生成（context.generate，可带 quiet_prompt） */
export const AUTHOR_MSG_GENERATE_MAIN = 'flyai-author-generate-main'
/** 宿主 → 卡：主聊天生成结果 */
export const AUTHOR_MSG_GENERATE_MAIN_RESULT = 'flyai-author-generate-main-result'

const MAX_MESSAGES = 200
const MAX_MESSAGE_CHARS = 8000

export type AuthorRuntimeMessage = {
  role: 'user' | 'assistant'
  text: string
  name?: string
  statData?: Record<string, unknown>
  /** 酒馆 message.extra（开场标记等），卡内 getChatMessages 读回 */
  extra?: Record<string, unknown>
  /** 后端消息 id：setChatMessages 落库时定位 */
  id?: string
}

export type AuthorRuntimeOptions = {
  /** 当前要渲染的面板 HTML（展示侧正则替换后的结果） */
  html: string
  author: StAuthorPayload
  messages: AuthorRuntimeMessage[]
  /** 会话级 MVU stat_data */
  variables?: Record<string, unknown> | null
  /** 会话级 chatMetadata（作者脚本地图/手札存档） */
  chatMetadata?: Record<string, unknown> | null
  character?: { name?: string; description?: string; avatar?: string | null }
  externalMedia?: boolean
  /** 世界书/角色副本读取快照（st.write_context） */
  writeContext?: CardWriteContext
  /** 会话/聊天标识；酒馆助手 context.chatId 需要 */
  sessionId?: string
  chatId?: string
  /** 玩家名；context.name1 需要 */
  userName?: string
  /** 是否允许写回（决定 confirm/Popup 与 helper 写入语义） */
  canWrite?: boolean
  /** 是否允许发送/继续/重生成 */
  canAct?: boolean
}

/** 会话可见消息 → 作者运行时镜像；只保留文本与变量快照。 */
export const authorRuntimeMessages = (
  messages: readonly Message[]
): AuthorRuntimeMessage[] => {
  const out: AuthorRuntimeMessage[] = []
  for (const message of messages) {
    if (message.role === 'user') {
      out.push({
        id: message.id,
        role: 'user',
        text: message.content.slice(0, MAX_MESSAGE_CHARS),
        statData: message.statData,
        extra: message.stExtra,
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
      id: message.id,
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
/**
 * 小手机（约会大作战）的 DALPhoneRuntime 需要真实酒馆的 `TavernHelper`、
 * `EjsTemplate` 以及 `/script.js`、`/scripts/world-info.js` 等原生模块，
 * 还会把请求发到 `/api/backends/chat-completions/generate`。这些在沙箱里都无法
 * 提供，所以用同一套接口的轻量实现覆盖：按手机预设拼系统提示 + 手机历史，再走
 * FlyAI 作者模型桥，回复统一包成 `<dal_phone_reply>` 供卡片解析。
 */
export const DAL_PHONE_RUNTIME_SOURCE = String.raw`
(function(){
  var PROTOCOL = '用当前联系人的口吻发消息，格式为 <dal_phone_reply><msg>一条消息</msg></dal_phone_reply>。每个 <msg> 是一个气泡，可用 sticker 属性发目录里的表情，可配文。不写旁白、动作或代码围栏。字面 <、>、& 转义为 &lt;、&gt;、&amp;。暂不回复就留空外层，打了字又删掉用 <typing/>。实收照片时按该次要求另附说明。';
  var RULES = '你正在扮演 {{char}}，通过手机与 {{user}} 私聊。只依据 {{char}} 本人亲历、当场目睹、{{user}} 已告知或公开传播的事实发言，不泄露场外秘密或他人私聊。结合联系人当前的所在地、在场、活动、心情及对玩家关系，用平时的口吻与长短句节奏自然回复；不替玩家发消息。';
  function text(value){
    return typeof value === 'string' ? value : (value == null ? '' : String(value));
  }
  function fill(value, char, user){
    return text(value).replace(/\{\{char\}\}/g, char).replace(/\{\{user\}\}/g, user);
  }
  function escapeXml(value){
    return text(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function historyMessages(args){
    var list = Array.isArray(args.phoneMessages) ? args.phoneMessages : [];
    return list.map(function(item){
      return {
        role: item && item.role === 'assistant' ? 'assistant' : 'user',
        content: text(item && (item.content != null ? item.content : item.text))
      };
    }).filter(function(item){ return item.content; });
  }
  function currentUser(args){
    var outgoing = Array.isArray(args.outgoing) ? args.outgoing : [];
    if (outgoing.length) {
      return outgoing.map(function(item){ return text(item && item.text); })
        .filter(Boolean).join('\n');
    }
    return text(args.currentInput);
  }
  function buildMessages(args){
    args = args || {};
    var contact = args.contact || {};
    var char = text(contact.name);
    var user = text(contact.userName);
    var sources = {
      charDescription: text(contact.description),
      charPersonality: text(contact.personality),
      scenario: text(contact.scenario),
      personaDescription: text(contact.persona),
      dialogueExamples: text(contact.examples),
      contactMemo: text(contact.memo),
      mainlineReference: (Array.isArray(args.mainline) ? args.mainline : []).map(function(item){
        return text(item && (item.content != null ? item.content : item.text || item));
      }).filter(Boolean).join('\n')
    };
    var history = historyMessages(args);
    var lastUser = currentUser(args);
    var preset = args.preset && Array.isArray(args.preset.prompts) ? args.preset : null;
    var messages = [];
    var inserted = false;
    function pushHistory(){
      if (inserted) return;
      inserted = true;
      for (var i = 0; i < history.length; i += 1) messages.push(history[i]);
      if (lastUser) messages.push({ role: 'user', content: lastUser });
    }
    if (preset) {
      preset.prompts.forEach(function(prompt){
        if (!prompt || prompt.enabled === false) return;
        var source = prompt.source || (prompt.marker ? prompt.id : null);
        if (source === 'chatHistory') { pushHistory(); return; }
        var body = prompt.content;
        if (source && Object.prototype.hasOwnProperty.call(sources, source)) {
          body = sources[source];
        }
        if (source === 'replyProtocol' && !body) body = PROTOCOL;
        if (typeof body !== 'string' || !body.trim()) return;
        messages.push({
          role: prompt.role === 'user' ? 'user'
            : (prompt.role === 'assistant' ? 'assistant' : 'system'),
          content: fill(body, char, user)
        });
      });
    }
    if (!preset || !messages.length) {
      messages.push({ role: 'system', content: fill(RULES, char, user) });
      messages.push({ role: 'system', content: PROTOCOL });
    }
    if (!inserted) pushHistory();
    if (!messages.some(function(item){ return item.role === 'user' && item.content; })) {
      messages.push({ role: 'user', content: lastUser || '……' });
    }
    return messages;
  }
  function normalizeReply(value){
    var body = text(value);
    if (/<dal_phone_reply[\s>]/i.test(body)) return body;
    var plain = body.replace(/<[^>]+>/g, '').trim();
    return '<dal_phone_reply><msg>' + escapeXml(plain) + '</msg></dal_phone_reply>';
  }
  function generate(messages){
    var run = window.__flyaiGenerate;
    if (typeof run !== 'function') {
      return Promise.reject(new Error('FlyAI 作者模型接口不可用，请重新打开面板。'));
    }
    return run('phone', { messages: messages }).then(function(value){
      return { choices: [{ message: { role: 'assistant', content: normalizeReply(value) } }] };
    });
  }
  var runtime = {
    prepareFromHost: function(args){
      return Promise.resolve({ messages: buildMessages(args), sentPhotos: [] });
    },
    prepareMemoryFromHost: function(args){
      return Promise.resolve({
        messages: Array.isArray(args && args.messages) ? args.messages : []
      });
    },
    prepareSocialFromHost: function(args){
      return Promise.resolve({
        messages: Array.isArray(args && args.messages) ? args.messages : []
      });
    },
    sendPrepared: function(prepared, options){
      if (options && typeof options.guard === 'function') options.guard();
      return generate(prepared && Array.isArray(prepared.messages) ? prepared.messages : []);
    },
    responseText: function(value){
      if (typeof value === 'string') return value;
      var choice = value && value.choices && value.choices[0];
      if (choice) {
        return text(choice.message && choice.message.content != null
          ? choice.message.content : choice.text);
      }
      return text(value && (value.content || value.text));
    },
    capabilities: { phone: true, source: 'flyai-author-bridge' }
  };
  window.DALPhoneRuntime = runtime;
  try {
    window.__DAL_PHONE_RUNTIME_SHA__ =
      '51f20c76a4d5f9a6d541e1ffc2613f0ba7962e8f9c5954053831a9f9577b0148';
  } catch (e) {}
})();
`

const buildImportMap = (
  source: string,
  extras?: Record<string, string>
): string => {
  const imports: Record<string, string> = { ...(extras ?? {}) }
  const mvu = `data:text/javascript,${encodeURIComponent(
    'export const Mvu = globalThis.Mvu; export default Mvu;'
  )}`
  // 卡会把 globalThis.registerMvuSchema 覆盖成这个导出函数（红楼批次115）。
  // 若转发时再动态取 globalThis，就会自己调自己无限递归；必须在模块求值
  // 时先抓住原始实现。
  const zod = `data:text/javascript,${encodeURIComponent(
    'const impl = globalThis.registerMvuSchema; ' +
      'export const registerMvuSchema = (...a) => ' +
      '(typeof impl === "function" ? impl : globalThis.registerMvuSchema)(...a);'
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
    // 手札舆图/约会大作战地图是卡内嵌套 iframe（srcdoc 或 CDN 页面）
    `frame-src 'self' data: blob:${externalMedia ? ' https:' : ''}`,
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
  chatMetadata?: Record<string, unknown> | null
  character?: { name?: string; description?: string; avatar?: string | null }
  writeContext?: CardWriteContext
  sessionId?: string
  chatId?: string
  userName?: string
  canWrite?: boolean
  canAct?: boolean
}

const buildShim = ({
  messages,
  variables,
  chatMetadata,
  character,
  writeContext,
  sessionId,
  chatId,
  userName,
  canWrite,
  canAct,
}: ShimOptions): string => {
  const messageData = JSON.stringify(messages)
  const chatVariables = JSON.stringify({
    stat_data:
      variables && typeof variables === 'object' ? variables : {},
  })
  const chatMetadataData = JSON.stringify(
    chatMetadata && typeof chatMetadata === 'object' ? chatMetadata : {}
  )
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
  var STCORE = globalThis.__FLYAI_ST_CORE__;
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
  var MSG_GENERATE = ${escapeForScript(AUTHOR_MSG_GENERATE)};
  var MSG_GENERATE_RESULT = ${escapeForScript(AUTHOR_MSG_GENERATE_RESULT)};
  var MSG_PHONE_MAINLINE = ${escapeForScript(AUTHOR_MSG_PHONE_MAINLINE)};
  var MSG_INJECT_PROMPTS = ${escapeForScript(AUTHOR_MSG_INJECT_PROMPTS)};
  var MSG_SAVE_METADATA = ${escapeForScript(AUTHOR_MSG_SAVE_METADATA)};
  var MSG_SAVE_METADATA_RESULT = ${escapeForScript(AUTHOR_MSG_SAVE_METADATA_RESULT)};
  var MSG_SAVE_MESSAGES = ${escapeForScript(AUTHOR_MSG_SAVE_MESSAGES)};
  var MSG_SAVE_MESSAGES_RESULT = ${escapeForScript(AUTHOR_MSG_SAVE_MESSAGES_RESULT)};
  var MSG_GENERATE_MAIN = ${escapeForScript(AUTHOR_MSG_GENERATE_MAIN)};
  var MSG_GENERATE_MAIN_RESULT = ${escapeForScript(AUTHOR_MSG_GENERATE_MAIN_RESULT)};
  var MESSAGES = JSON.parse(${escapeForScript(messageData)});
  // 酒馆 context().chat 必须是稳定、可原地修改的数组：扩展（小手机）会把
  // dal_phone_source 等楼层身份写进 message.extra/swipe_info，再靠 keys() 读回。
  // 如果每次 getContext() 都重新 map 一份，赋值会丢失，发送时报“剧情楼层尚未就绪”。
  var CHAT = [];
  var CHAT_VARIABLES = JSON.parse(${escapeForScript(chatVariables)});
  var CHARACTER = JSON.parse(${escapeForScript(characterData)});
  var WORLD = JSON.parse(${escapeForScript(worldbookData)});
  var WORLD_ENTRIES = Array.isArray(WORLD.entries) ? WORLD.entries : [];
  var CHARACTER_FIELDS = WORLD.character && typeof WORLD.character === 'object' ? WORLD.character : {};
  var SESSION_ID = ${escapeForScript(sessionId ?? '')};
  var CHAT_ID = ${escapeForScript(chatId ?? sessionId ?? '')};
  // 酒馆 chat_metadata.integrity：扩展用它识别同一聊天存档（约会大作战小手机）。
  // 宿主没有该字段，用稳定的会话 id 兜底，刷新后保持一致。
  function ensureChatIntegrity(meta){
    if (meta && typeof meta === 'object' && CHAT_ID &&
        (!meta.integrity || typeof meta.integrity !== 'string')) {
      meta.integrity = CHAT_ID;
    }
    return meta;
  }
  var USER_NAME = ${escapeForScript(userName ?? '')};
  var CAN_WRITE = ${canWrite === true};
  var CAN_ACT = ${canAct === true};
  function clone(value){
    try { return JSON.parse(JSON.stringify(value)); } catch (e) { return {}; }
  }
  // ST 形状的聊天镜像：保持对象引用，供 ensureSourceIds / branchKeys 原地写入。
  function ensureChatMirror(){
    var total = MESSAGES.length;
    for (var i = 0; i < total; i += 1) {
      var message = MESSAGES[i] || {};
      var role = message.role === 'user' ? 'user'
        : (message.role === 'system' ? 'system' : 'assistant');
      var item = CHAT[i];
      var text = String(message.text == null ? '' : message.text);
      var stat = message.statData && typeof message.statData === 'object'
        ? message.statData : null;
      if (!item) {
        item = CHAT[i] = {
          message_id: i,
          id: message.id ? String(message.id) : '',
          name: String(message.name || (role === 'user' ? USER_NAME : CHARACTER.name)),
          is_user: role === 'user',
          is_system: role === 'system',
          mes: text,
          message: text,
          swipe_id: 0,
          extra: message.extra && typeof message.extra === 'object' ? clone(message.extra) : {},
          swipe_info: [],
          variables: [{ stat_data: clone(stat || {}) }]
        };
      } else {
        item.message_id = i;
        if (!item.id && message.id) item.id = String(message.id);
        if (message.name) item.name = String(message.name);
        item.is_user = role === 'user';
        item.is_system = role === 'system';
        item.mes = text;
        item.message = text;
        if (!item.extra || typeof item.extra !== 'object') item.extra = {};
        if (message.extra && typeof message.extra === 'object') {
          for (var k in message.extra) {
            if (Object.prototype.hasOwnProperty.call(message.extra, k) &&
                item.extra[k] === undefined) {
              item.extra[k] = clone(message.extra[k]);
            }
          }
        }
        if (!Array.isArray(item.swipe_info)) item.swipe_info = [];
        if (stat) item.variables = [{ stat_data: clone(stat) }];
      }
    }
    if (CHAT.length > total) CHAT.splice(total);
    return CHAT;
  }
  // 与卡片侧同一套：会话 chatMetadata 保留键持久化，刷新后恢复。
  function storage(kind){
    var metaKey = kind === 'session' ? '__flyai_storage_session' : '__flyai_storage_local';
    var data = null;
    var saveTimer = null;
    function ensure(){
      if (data) return data;
      var source = CHAT_METADATA && CHAT_METADATA[metaKey];
      data = source && typeof source === 'object' ? clone(source) : {};
      return data;
    }
    function flush(){
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      saveMetadata().catch(function(){});
    }
    function persist(immediate){
      if (!CHAT_METADATA || typeof CHAT_METADATA !== 'object') return;
      CHAT_METADATA[metaKey] = clone(ensure());
      // 合并高频 localStorage 写入；清理旧草稿立即落库。
      if (immediate) { flush(); return; }
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function(){ saveTimer = null; flush(); }, 400);
    }
    try { window.addEventListener('pagehide', flush); } catch (e) {}
    return {
      getItem: function(k){ k = String(k); var d = ensure(); return Object.prototype.hasOwnProperty.call(d, k) ? d[k] : null; },
      setItem: function(k, v){ ensure()[String(k)] = String(v); persist(false); },
      removeItem: function(k){ delete ensure()[String(k)]; persist(true); },
      clear: function(){ data = {}; persist(true); },
      key: function(i){ return Object.keys(ensure())[i] || null; },
      get length(){ return Object.keys(ensure()).length; }
    };
  }
  try { Object.defineProperty(window, 'localStorage', { value: storage('local'), configurable: true }); } catch (e) {}
  try { Object.defineProperty(window, 'sessionStorage', { value: storage('session'), configurable: true }); } catch (e) {}
  // 沙箱内原生 indexedDB.open 抛 SecurityError；扩展只要探测到 indexedDB 存在就
  // 启用日志/媒体库，随后整个手机流程卡死。换成进程内桩，刷新后由聊天文件回读。
  try { if (STCORE.installIndexedDbShim) STCORE.installIndexedDbShim(window); } catch (e) {}
  // 沙箱 iframe 里 navigator.locks.request 会抛 SecurityError；换成页面内串行桩
  try {
    var lockChains = {};
    Object.defineProperty(window.navigator, 'locks', {
      configurable: true,
      value: {
        request: function(name, options, callback){
          if (typeof options === 'function') { callback = options; options = null; }
          var key = String(name || '');
          var prev = lockChains[key] || Promise.resolve();
          var run = function(){
            return typeof callback === 'function'
              ? callback({ name: key, mode: 'exclusive' })
              : undefined;
          };
          var result = prev.then(run, run);
          lockChains[key] = result.then(function(){}, function(){});
          return result;
        },
        query: function(){ return Promise.resolve({ held: [], pending: [] }); }
      }
    });
  } catch (e) {}

  var listeners = {};
  function on(event, handler, once, first){
    if (typeof handler !== 'function') return;
    var key = String(event);
    var list = listeners[key] = listeners[key] || [];
    var item = { fn: handler, once: !!once };
    if (first) list.unshift(item); else list.push(item);
  }
  function off(event, handler){
    var key = String(event);
    var list = listeners[key];
    if (!list) return;
    if (!handler) { delete listeners[key]; return; }
    var next = [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].fn !== handler) next.push(list[i]);
    }
    listeners[key] = next;
  }
  function emit(event){
    var key = String(event);
    var args = Array.prototype.slice.call(arguments, 1);
    var list = (listeners[key] || []).slice();
    var results = [];
    for (var i = 0; i < list.length; i += 1) {
      var item = list[i];
      if (item.once) off(key, item.fn);
      // 同步调用：卡内监听器常依赖“emit 后立刻可读”；
      // 返回 Promise 供 eventEmitAndWait 等待异步处理器。
      try { results.push(item.fn.apply(null, args)); } catch (e) { try { console.error(e); } catch (ignore) {} }
    }
    return Promise.resolve(results);
  }
  function eventReturn(event, handler){
    on(event, handler, false, false);
    return { stop: function(){ off(event, handler); } };
  }
  var eventSource = {
    on: function(event, handler){ return eventReturn(event, handler); },
    once: function(event, handler){ on(event, handler, true, false); return { stop: function(){ off(event, handler); } }; },
    makeFirst: function(event, handler){ on(event, handler, false, true); return { stop: function(){ off(event, handler); } }; },
    makeLast: function(event, handler){ on(event, handler, false, false); return { stop: function(){ off(event, handler); } }; },
    off: function(event, handler){ off(event, handler); },
    removeListener: function(event, handler){ off(event, handler); },
    emit: emit,
    emitAndWait: emit
  };
  window.eventOn = function(event, handler){ return eventReturn(event, handler); };
  window.eventOnce = function(event, handler){
    on(event, handler, true, false);
    return { stop: function(){ off(event, handler); } };
  };
  window.eventOff = function(event, handler){ off(event, handler); };
  window.eventEmit = emit;
  window.eventEmitAndWait = emit;
  window.eventMakeFirst = function(event, handler){
    on(event, handler, false, true);
    return { stop: function(){ off(event, handler); } };
  };
  window.eventMakeLast = function(event, handler){
    on(event, handler, false, false);
    return { stop: function(){ off(event, handler); } };
  };
  // 酒馆助手的脚本按钮：getButtonEvent(name) 注册事件，同时把按钮挂到面板
  // 右下角；否则卡里的“随身手札”等入口没有可点击的宿主 UI。
  var customButtonEvents = {};
  window.getButtonEvent = function(name){
    var key = String(name || '');
    var eventName = 'flyai_script_button_' + key;
    if (!customButtonEvents[key]) {
      customButtonEvents[key] = true;
      try {
        var button = document.createElement('button');
        button.type = 'button';
        button.textContent = key;
        button.setAttribute('data-flyai-author-button', key);
        button.style.cssText = 'position:fixed;right:12px;bottom:12px;z-index:2147483647;' +
          'padding:6px 12px;border-radius:999px;border:1px solid rgba(120,90,50,.45);' +
          'background:#fffaf0;color:#5b4630;font:13px/1.4 system-ui,sans-serif;' +
          'box-shadow:0 2px 8px rgba(0,0,0,.16);cursor:pointer;';
        button.addEventListener('click', function(){ emit(eventName); });
        (document.body || document.documentElement).appendChild(button);
      } catch (e) {}
    }
    return eventName;
  };
  window.eventRemoveListener = function(event, handler){ off(event, handler); };
  window.eventClearEvent = function(event){ off(event); };
  window.eventClearListener = function(handler){
    for (var key in listeners) off(key, handler);
  };
  window.eventClearAll = function(){ listeners = {}; };
  window.tavern_events = STCORE.tavernEvents;
  window.iframe_events = STCORE.iframeEvents;
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
  window.getChatMessages = function(range, options){
    return STCORE.getChatMessages(
      MESSAGES,
      range == null ? -1 : range,
      options || {},
      { charName: CHARACTER.name, userName: USER_NAME }
    );
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
  // 酒馆助手的 getVariables/updateVariablesWith 面向“扁平变量表”，
  // 不是 MVU 的 {stat_data} 信封。手机存档（dal_phone / dal_phone_config）
  // 直接按顶层键读写，这里必须保持同一形状。
  function currentVariables(option){
    var type = option && option.type ? String(option.type) : 'chat';
    if (type === 'message') {
      var index = resolveId(option.message_id);
      var stat = messageVariables(index);
      return clone(stat && typeof stat === 'object' ? stat : chatStat());
    }
    return clone(chatStat());
  }
  function writeVariables(next, option){
    var type = option && option.type ? String(option.type) : 'chat';
    var value = next && typeof next === 'object' ? next : {};
    var envelope = value.stat_data && typeof value.stat_data === 'object'
      ? clone(value)
      : { stat_data: clone(value) };
    return window.Mvu.replaceMvuData(envelope, { type: type, message_id: option && option.message_id });
  }
  window.getAllVariables = function(){ return clone(chatStat()); };
  window.getVariables = function(option){ return currentVariables(option); };
  window.replaceVariables = function(value, option){ return writeVariables(value, option); };
  window.updateVariablesWith = function(updater, option){
    var current = currentVariables(option);
    var next = typeof updater === 'function' ? updater(clone(current)) : current;
    writeVariables(next, option);
    return next;
  };
  window.insertOrAssignVariables = function(value, option){
    var current = currentVariables(option);
    var patch = normalizeMvu(value).stat_data;
    var next = STCORE.merge(current, patch, true);
    writeVariables(next, option);
    return next;
  };
  window.insertVariables = function(value, option){
    var current = currentVariables(option);
    var patch = normalizeMvu(value).stat_data;
    var next = STCORE.mergeKeep(current, patch);
    writeVariables(next, option);
    return next;
  };
  window.deleteVariable = function(key, option){
    var current = currentVariables(option);
    var deleted = STCORE.deletePath(current, key);
    if (deleted) writeVariables(current, option);
    return { variables: clone(current), delete_occurred: deleted };
  };
  window.getChatVariable = function(key){ return chatStat()[String(key)]; };
  window.setChatVariable = function(key, value){ window.insertOrAssignVariables({ [String(key)]: value }, { type: 'chat' }); return true; };
  window.getGlobalVariables = function(){ return {}; };
  window.setGlobalVariable = function(){ return false; };
  window.setVariables = window.replaceVariables;

  // ── 世界书本地副本 ─────────────────────────────────────────────
  var WORLDBOOKS = {};
  function primaryWorldName(){
    return WORLD.charWorldName || WORLD.name || CHARACTER.name || 'FlyAI';
  }
  function characterWorldSlots(){
    try {
      var manifest = window.DALOpeningWorldbook;
      var slots = manifest && Array.isArray(manifest.slots) ? manifest.slots : [];
      var out = {};
      for (var i = 0; i < slots.length; i += 1) {
        var slot = slots[i] || {};
        if (slot.title && slot.uid != null) out[String(slot.title)] = slot;
      }
      return out;
    } catch (e) { return {}; }
  }
  function worldbookFromCharacter(){
    var table = CHARACTER_FIELDS && Array.isArray(CHARACTER_FIELDS.table)
      ? CHARACTER_FIELDS.table : [];
    if (!table.length) return { entries: {} };
    var named = characterWorldSlots();
    var entries = {};
    for (var i = 0; i < table.length; i += 1) {
      var item = table[i] || {};
      var title = String(item.title == null ? '' : item.title);
      var slot = named[title];
      var uid = slot ? Number(slot.uid) : i + 1;
      if (!isFinite(uid) || uid < 0) uid = i + 1;
      while (entries[uid]) uid += 1;
      entries[uid] = {
        uid: uid,
        comment: title || String(uid),
        key: Array.isArray(item.keys) ? item.keys.slice() : [],
        content: String(item.content == null ? '' : item.content),
        constant: slot ? !!slot.constant : !!item.pinned,
        disable: false,
        position: 0,
        depth: Number(item.depth == null ? 4 : item.depth),
        order: Number(item.insert_order == null ? 100 : item.insert_order),
        probability: 100
      };
    }
    return { entries: entries };
  }
  function worldbookOf(name){
    var key = String(name || primaryWorldName());
    var book = WORLDBOOKS[key];
    var hasEntries = book && book.entries && Object.keys(book.entries).length > 0;
    if (!book || (!hasEntries && key === primaryWorldName() && CHARACTER_FIELDS && CHARACTER_FIELDS.table)) {
      book = key === primaryWorldName() && CHARACTER_FIELDS && CHARACTER_FIELDS.table
        ? worldbookFromCharacter()
        : (book || { entries: {} });
      WORLDBOOKS[key] = book;
    }
    return book;
  }
  function worldbookEntries(data){
    return STCORE.worldbookHostEntries(data);
  }
  function saveWorldbook(name, data){
    if (!CAN_WRITE) return Promise.reject(new Error('flyai-card-read-only'));
    var key = String(name || primaryWorldName());
    var book;
    if (Array.isArray(data)) {
      book = STCORE.worldbookFromList(data, key);
    } else if (data && typeof data === 'object') {
      book = clone(data);
      if (!book.entries || typeof book.entries !== 'object') book.entries = {};
    } else {
      book = { entries: {} };
    }
    WORLDBOOKS[key] = book;
    var entries = worldbookEntries(book);
    if (entries.length) postWorld(entries);
    return Promise.resolve(clone(book));
  }
  window.getWorldbookNames = function(){
    var names = [];
    if (primaryWorldName()) names.push(primaryWorldName());
    if (WORLD.name && names.indexOf(WORLD.name) < 0) names.push(WORLD.name);
    return names;
  };
  window.getCharWorldbookNames = function(){
    return { primary: WORLD.charWorldName || WORLD.name || null, additional: [] };
  };
  window.getGlobalWorldbookNames = function(){ return []; };
  window.getChatWorldbookName = function(){
    return primaryWorldName() || null;
  };
  window.getWorldbook = function(name){ return clone(STCORE.worldbookEntryList(worldbookOf(name))); };
  window.updateWorldbookWith = function(name, updater){
    var current = STCORE.worldbookEntryList(worldbookOf(name));
    var next = typeof updater === 'function' ? updater(clone(current)) : current;
    if (!Array.isArray(next)) return Promise.resolve(clone(current));
    return saveWorldbook(name, next).then(function(){ return clone(STCORE.worldbookEntryList(WORLDBOOKS[String(name || primaryWorldName())])); });
  };
  window.updateWorldbook = window.updateWorldbookWith;
  window.replaceWorldbook = function(first, second){
    var name = typeof first === 'string' ? first : (WORLD.name || primaryWorldName());
    var data = typeof first === 'string' ? second : first;
    return saveWorldbook(name, Array.isArray(data) ? data : (data && data.entries ? data : []));
  };

  // 本地 worldinfo HTTP：与开场卡一致，手机/面板调用 /api/worldinfo/* 时
  // 不再直接联网（CSP 也不允许），返回本地副本。
  function jsonResponse(data){
    var text = JSON.stringify(data == null ? {} : data);
    try {
      return new Response(text, { status: 200, headers: { 'Content-Type': 'application/json' } });
    } catch (e) {
      return { ok: true, status: 200, json: function(){ return Promise.resolve(data); }, text: function(){ return Promise.resolve(text); } };
    }
  }
  function worldinfoFetch(url, init){
    var path = String(url || '');
    if (path.indexOf('/api/worldinfo/') < 0) return null;
    var body = init && init.body;
    var data = {};
    try {
      if (typeof body === 'string') data = JSON.parse(body);
      else if (body && typeof body === 'object' && !(body instanceof ArrayBuffer)) data = body;
    } catch (e) {}
    if (path.indexOf('/api/worldinfo/get') >= 0) {
      return Promise.resolve(jsonResponse(worldbookOf(data.name)));
    }
    if (path.indexOf('/api/worldinfo/edit') >= 0 || path.indexOf('/api/worldinfo/create') >= 0) {
      var name = data.name || data.world || '';
      var payload = data.data || data.world_info || data;
      return saveWorldbook(name, payload).then(function(){ return jsonResponse({ ok: true }); });
    }
    if (path.indexOf('/api/worldinfo/delete') >= 0) {
      delete WORLDBOOKS[String(data.name || '')];
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    return Promise.resolve(jsonResponse({}));
  }
  var nativeFetch = typeof window.fetch === 'function' ? window.fetch.bind(window) : null;
  // 酒馆聊天文件接口：约会大作战手机用它核对 chat_metadata 是否真的落盘。
  // 本地没有 jsonl 文件，按 ST 形状把当前聊天镜像 + CHAT_METADATA 拼回去。
  function chatFileData(){
    var mirror = ensureChatMirror();
    var metadata = clone(CHAT_METADATA);
    // 酒馆的 chat_metadata.variables 是扩展（小手机）存放 dal_phone 等存档的位置；
    // 宿主把聊天变量映射在 MVU stat_data 上，这里合并成同一份读侧快照。
    var flatVars = currentVariables({ type: 'chat' });
    var storedVars = metadata.variables && typeof metadata.variables === 'object'
      ? clone(metadata.variables) : {};
    metadata.variables = Object.assign(
      {},
      flatVars && typeof flatVars === 'object' ? clone(flatVars) : {},
      storedVars
    );
    var out = [{
      user_name: USER_NAME,
      character_name: CHARACTER.name,
      create_date: '',
      chat_metadata: metadata
    }];
    for (var i = 0; i < mirror.length; i += 1) {
      var item = mirror[i] || {};
      var text = String(item.mes == null ? '' : item.mes);
      out.push({
        name: item.name || (item.is_user ? USER_NAME : CHARACTER.name),
        is_user: !!item.is_user,
        is_system: !!item.is_system,
        send_date: '',
        mes: text,
        extra: item.extra && typeof item.extra === 'object' ? clone(item.extra) : {},
        swipe_id: typeof item.swipe_id === 'number' ? item.swipe_id : 0,
        swipes: [text],
        swipe_info: Array.isArray(item.swipe_info) && item.swipe_info.length
          ? clone(item.swipe_info)
          : [{}],
        variables: Array.isArray(item.variables) && item.variables.length
          ? clone(item.variables)
          : [{}]
      });
    }
    return out;
  }
  // ST chat-completions 的响应形状。卡运行时（约会大作战手机）直接读
  // choices[0].message.content；stream:true 时合成为一段完整 SSE，P1 再接真实流。
  function chatCompletionResponse(text, data){
    var model = String((data && data.model) || '');
    var payload = {
      id: 'flyai-' + Date.now(),
      object: 'chat.completion',
      model: model,
      choices: [{
        index: 0,
        message: { role: 'assistant', content: text },
        text: text,
        finish_reason: 'stop'
      }]
    };
    if (!data || data.stream !== true) return jsonResponse(payload);
    var chunk = function(delta, finish){
      return 'data: ' + JSON.stringify({
        id: payload.id,
        object: 'chat.completion.chunk',
        model: model,
        choices: [{ index: 0, delta: delta, finish_reason: finish || null }]
      }) + '\\n\\n';
    };
    var body = chunk({ role: 'assistant', content: text }, null) +
      chunk({}, 'stop') + 'data: [DONE]\\n\\n';
    if (typeof Response === 'function') {
      return new Response(body, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' }
      });
    }
    return jsonResponse(payload);
  }
  function chatApiFetch(url, init){
    var path = String(url || '');
    var data = {};
    try {
      var body = init && init.body;
      if (typeof body === 'string') data = JSON.parse(body);
      else if (body && typeof body === 'object') data = body;
    } catch (e) { data = {}; }
    if (path.indexOf('/api/backends/chat-completions/generate') >= 0) {
      var chatMessages = Array.isArray(data.messages) ? data.messages : [];
      var maxTokens = Number(data.max_tokens || data.max_completion_tokens || 0) || 0;
      return requestGenerate({
        purpose: 'phone',
        messages: chatMessages,
        maxTokens: maxTokens
      }).then(function(text){
        return chatCompletionResponse(text, data);
      });
    }
    if (path.indexOf('/api/backends/chat-completions/status') >= 0) {
      return Promise.resolve(jsonResponse([{
        id: 'flyai',
        name: 'FlyAI',
        object: 'model',
        owned_by: 'flyai'
      }]));
    }
    if (path.indexOf('/api/backends/chat-completions/process') >= 0 ||
        path.indexOf('/api/backends/chat-completions/bias') >= 0) {
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (path.indexOf('/api/chats/get') >= 0) {
      return Promise.resolve(jsonResponse(chatFileData()));
    }
    if (path.indexOf('/api/chats/save') >= 0) {
      var chat = data.chat;
      if (Array.isArray(chat) && chat[0] && chat[0].chat_metadata &&
          typeof chat[0].chat_metadata === 'object') {
        CHAT_METADATA = ensureChatIntegrity(clone(chat[0].chat_metadata));
        saveMetadata().catch(function(){});
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (path.indexOf('/api/chats/rename') >= 0 ||
        path.indexOf('/api/chats/delete') >= 0) {
      // 酒馆聊天文件名只存在于 iframe 镜像里；返回成功避免卡内分支流程中断。
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (path.indexOf('/api/users/me') >= 0) {
      return Promise.resolve(jsonResponse({
        handle: USER_NAME || 'flyai-user',
        name: USER_NAME || 'User',
        admin: false
      }));
    }
    if (path.indexOf('/api/files/sanitize-filename') >= 0) {
      var name = String(data.fileName || data.file_name || '');
      name = name.replace(/[\\\\/:*?"<>|]/g, '_').replace(/^\\s+|\\s+$/g, '');
      return Promise.resolve(jsonResponse({ fileName: name }));
    }
    return null;
  }
  window.fetch = function(input, init){
    var url = typeof input === 'string' ? input : (input && input.url ? String(input.url) : String(input));
    var local = worldinfoFetch(url, init);
    if (local) return local;
    var chatApi = chatApiFetch(url, init);
    if (chatApi) return chatApi;
    // 其余外网请求仍保持禁止（CSP connect-src 'none'）。
    if (nativeFetch) {
      try { return nativeFetch(input, init); } catch (e) { return Promise.reject(e); }
    }
    return Promise.reject(new TypeError('Failed to fetch'));
  };

  window.errorCatched = function(fn){
    return function(){
      try {
        var result = fn.apply(this, arguments);
        if (result && typeof result.then === 'function') {
          return result.then(function(value){ return value; }, function(err){
            try { console.error(err); } catch (ignore) {}
          });
        }
        return result;
      } catch (err) {
        try { console.error(err); } catch (ignore) {}
      }
    };
  };
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
  window.getCharName = function(){ return CHARACTER.name; };
  window.getCurrentCharName = window.getCharName;
  window.getCharAvatar = function(){ return CHARACTER.avatar; };
  window.getCurrentPersonaName = function(){ return USER_NAME; };
  window.getCurrentPersonaId = function(){ return ''; };
  window.retrieveDisplayedMessage = function(id){
    var list = STCORE.getChatMessages(
      MESSAGES, [id], {}, { charName: CHARACTER.name, userName: USER_NAME }
    );
    return list.length ? String(list[0].message || '') : '';
  };

  var GLOBAL_VARS = {};
  function variableStore(scope){
    var isGlobal = scope === 'global';
    function read(){ return isGlobal ? GLOBAL_VARS : chatStat(); }
    function write(next){
      if (isGlobal) { GLOBAL_VARS = next && typeof next === 'object' ? next : {}; return; }
      window.replaceVariables(next, { type: 'chat' });
    }
    return {
      get: function(key){ var v = read()[String(key)]; return v === undefined ? '' : v; },
      set: function(key, value){ var next = Object.assign({}, read()); next[String(key)] = value; write(next); return value; },
      has: function(key){ return Object.prototype.hasOwnProperty.call(read(), String(key)); },
      delete: function(key){ var next = Object.assign({}, read()); delete next[String(key)]; write(next); return ''; },
      inc: function(key, delta){ var n = Number(read()[String(key)] || 0) + (delta == null ? 1 : Number(delta)); var next = Object.assign({}, read()); next[String(key)] = n; write(next); return n; },
      dec: function(key, delta){ var n = Number(read()[String(key)] || 0) - (delta == null ? 1 : Number(delta)); var next = Object.assign({}, read()); next[String(key)] = n; write(next); return n; },
      add: function(key, delta){ return this.inc(key, delta); }
    };
  }
  var CHAT_METADATA = ensureChatIntegrity(${chatMetadataData});
  // accountStorage：跨刷新保留账号 handle 等少量用户级键；存进会话 chatMetadata，
  // 通过 saveMetadata 落库（容量小，不做大数据缓存）。
  function accountStorage(){
    function bag(){
      if (!CHAT_METADATA.account_storage || typeof CHAT_METADATA.account_storage !== 'object') {
        CHAT_METADATA.account_storage = {};
      }
      return CHAT_METADATA.account_storage;
    }
    return {
      getItem: function(key){
        var value = bag()[String(key)];
        return value == null ? null : String(value);
      },
      setItem: function(key, value){
        bag()[String(key)] = String(value);
        saveMetadata().catch(function(){});
        return String(value);
      },
      removeItem: function(key){
        delete bag()[String(key)];
        saveMetadata().catch(function(){});
      },
      clear: function(){
        CHAT_METADATA.account_storage = {};
        saveMetadata().catch(function(){});
      }
    };
  }

  // 手札 shell 以 srcdoc 嵌在作者 iframe 内。沙箱没有 allow-same-origin，
  // 它直接读 window.parent.HLMHandbookAdapter 会被浏览器拦截；在 srcdoc
  // 写入时注入一个 postMessage RPC 代理，保持卡内调用方式不变。
  function handbookBridgeSource(){
    return [
      '(function(){',
      'var pending={},seq=0;',
      'window.addEventListener("message",function(e){',
      'var d=e&&e.data;if(!d||d.type!=="flyai-handbook-rpc-result"||!d.id)return;',
      'var p=pending[d.id];if(!p)return;delete pending[d.id];',
      'if(d.ok)p.resolve(d.value);else p.reject(new Error(d.error||"rpc-failed"));',
      '});',
      'function call(method,args){return new Promise(function(resolve,reject){',
      'var id="rpc-"+(++seq);pending[id]={resolve:resolve,reject:reject};',
      'try{parent.postMessage({type:"flyai-handbook-rpc",id:id,method:method,args:args||[]},"*");}',
      'catch(err){delete pending[id];reject(err);}',
      '});}',
      'window.HLMHandbookAdapter={',
      'readContext:function(){return call("readContext",[]);},',
      'readBook:function(chatId){return call("readBook",[chatId]);},',
      'commitBook:function(book,expected){return call("commitBook",[book,expected]);},',
      'setPlotConstraint:function(content,expected){return call("setPlotConstraint",[content,expected]);},',
      'clearPlotConstraint:function(){return call("clearPlotConstraint",[]);}',
      '};',
      'window.__FLYAI_MAP_RUNTIME__=undefined;',
      '})();'
    ].join('');
  }
  function patchHandbookSrcdoc(html){
    var text = String(html == null ? '' : html);
    if (text.indexOf('HLMHandbookAdapter') < 0 &&
        text.indexOf('HLMMapRuntime') < 0) {
      return text;
    }
    text = text
      .replace(/window\\.parent\\.HLMHandbookAdapter/g, 'window.HLMHandbookAdapter')
      .replace(/window\\.parent\\.HLMMapRuntime/g, 'window.__FLYAI_MAP_RUNTIME__')
      .replace(/window\\.top\\.HLMMapRuntime/g, 'window.__FLYAI_MAP_RUNTIME__')
      .replace(/stateViewHost\\s*=\\s*window\\.parent\\s*\\|\\|\\s*window/g, 'stateViewHost=window');
    var script = '<script>' + handbookBridgeSource() + '</scr' + 'ipt>';
    var head = text.match(/<head[^>]*>/i);
    if (head && typeof head.index === 'number') {
      var at = head.index + head[0].length;
      return text.slice(0, at) + script + text.slice(at);
    }
    var body = text.match(/<body[^>]*>/i);
    if (body && typeof body.index === 'number') {
      var bat = body.index + body[0].length;
      return text.slice(0, bat) + script + text.slice(bat);
    }
    return script + text;
  }
  try {
    var iframeProto = window.HTMLIFrameElement && window.HTMLIFrameElement.prototype;
    var srcdocDesc = iframeProto && Object.getOwnPropertyDescriptor(iframeProto, 'srcdoc');
    if (srcdocDesc && srcdocDesc.set) {
      var patchedSet = function(value){
        return srcdocDesc.set.call(this, patchHandbookSrcdoc(value));
      };
      Object.defineProperty(iframeProto, 'srcdoc', {
        configurable: true,
        enumerable: srcdocDesc.enumerable,
        get: srcdocDesc.get,
        set: patchedSet
      });
    }
  } catch (e) {}
  window.SillyTavern = window.SillyTavern || {};
  window.SillyTavern.getContext = function(){
    return {
      characters: [{
        name: CHARACTER.name,
        avatar: CHARACTER.avatar,
        description: CHARACTER.description,
        data: {
          name: CHARACTER.name,
          avatar: CHARACTER.avatar,
          description: CHARACTER.description,
          extensions: { tavern_helper: { scripts: [] } }
        }
      }],
      characterId: 0,
      chatId: CHAT_ID,
      groupId: null,
      chat: ensureChatMirror(),
      chatMetadata: CHAT_METADATA,
      updateChatMetadata: updateChatMetadata,
      name1: USER_NAME,
      name2: CHARACTER.name,
      eventSource: eventSource,
      eventTypes: window.tavern_events,
      extensionSettings: { variables: { global: GLOBAL_VARS } },
      saveSettingsDebounced: function(){},
      saveMetadata: saveMetadata,
      variables: { global: variableStore('global'), local: variableStore('local') },
      mainApi: 'openai',
      getPresetManager: function(){ return null; },
      tagMap: {},
      getRequestHeaders: function(){ return {}; },
      accountStorage: accountStorage(),
      extensionPrompts: {},
      setExtensionPrompt: function(key, value, position, depth, scan, role){
        injectPrompts([{
          id: String(key || ''),
          content: String(value || ''),
          position: position,
          depth: depth,
          scan: scan,
          role: role
        }]);
      },
      loadWorldInfo: function(name){ return Promise.resolve(clone(worldbookOf(name))); },
      saveWorldInfo: function(name, data){ return saveWorldbook(name, data); },
      saveChat: saveChat,
      generate: contextGenerate,
      generateRaw: generateRaw,
      stopGenerationById: stopGenerationById,
      stopGeneration: function(){ emit(window.tavern_events.GENERATION_STOPPED); },
      getCurrentMessageId: function(){ return lastIndex(); }
    };
  };
  function getChatHistoryDetail(files){
    var out = {};
    var name = (CHAT_ID || 'chat') + '.jsonl';
    var list = ensureChatMirror().map(function(item, index){
      return {
        message_id: index,
        name: item.name,
        is_user: !!item.is_user,
        is_system: !!item.is_system,
        mes: String(item.mes || ''),
        swipe_id: typeof item.swipe_id === 'number' ? item.swipe_id : 0,
        extra: clone(item.extra || {}),
        swipe_info: Array.isArray(item.swipe_info) ? clone(item.swipe_info) : [],
        variables: Array.isArray(item.variables) ? clone(item.variables) : [{}]
      };
    });
    if (Array.isArray(files) && files.length) {
      for (var i = 0; i < files.length; i += 1) {
        var file = files[i] || {};
        out[String(file.file_name || name)] = list;
      }
    } else {
      out[name] = list;
    }
    return Promise.resolve(out);
  }
  function createChatMessages(messages){
    var list = Array.isArray(messages) ? messages : [messages];
    var created = [];
    var payload = [];
    var base = MESSAGES.length;
    for (var i = 0; i < list.length; i += 1) {
      var raw = list[i] || {};
      var role = String(raw.role || 'user');
      var item = {
        message_id: MESSAGES.length,
        name: String(raw.name || (role === 'user' ? USER_NAME : CHARACTER.name)),
        is_user: role === 'user',
        is_system: role === 'system',
        mes: String(raw.message != null ? raw.message : (raw.mes != null ? raw.mes : '')),
        swipe_id: 0,
        extra: raw.extra && typeof raw.extra === 'object' ? clone(raw.extra) : {},
        variables: raw.data && typeof raw.data === 'object'
          ? [clone(raw.data)]
          : (Array.isArray(raw.variables) ? clone(raw.variables) : undefined)
      };
      var mirror = {
        role: role === 'user' ? 'user' : 'assistant',
        text: item.mes,
        name: item.name,
        extra: item.extra,
        statData: item.variables && item.variables[0] ? item.variables[0].stat_data : undefined
      };
      MESSAGES.push(mirror);
      created.push(item);
      payload.push({
        role: role,
        name: item.name,
        message: item.mes,
        extra: item.extra,
        data: item.variables && item.variables[0] ? item.variables[0] : undefined,
        is_hidden: raw.is_hidden === true
      });
    }
    ensureChatMirror();
    emit(window.tavern_events.MESSAGE_SENT, MESSAGES.length - 1);
    if (!CAN_WRITE) return Promise.resolve(created);
    // 与卡片侧一致：先把新楼层落到宿主会话，再让 generate() 基于这段上下文生成。
    return requestMessagePatch([], payload).then(function(result){
      var rows = result && Array.isArray(result.created) ? result.created : [];
      for (var j = 0; j < rows.length; j += 1) {
        var row = rows[j] || {};
        if (created[j] && row.id) created[j].id = String(row.id);
        if (MESSAGES[base + j] && row.id) MESSAGES[base + j].id = String(row.id);
        if (CHAT[base + j] && row.id) CHAT[base + j].id = String(row.id);
      }
      return created;
    });
  }
  // 面板侧 setChatMessages：先改本地镜像，再按后端消息 id 落库。
  window.setChatMessages = function(changes, options){
    var list = Array.isArray(changes) ? changes : (changes ? [changes] : []);
    var patches = [];
    for (var i = 0; i < list.length; i += 1) {
      var change = list[i] || {};
      var id = Number(change.message_id);
      if (!isFinite(id) || id < 0 || id >= MESSAGES.length) continue;
      var target = MESSAGES[id];
      var patch = { id: String(target.id || '') };
      if (change.message != null || change.mes != null) {
        target.text = String(change.message != null ? change.message : change.mes);
        patch.message = target.text;
      }
      if (change.name != null) target.name = String(change.name);
      if (change.role != null) target.role = change.role === 'user' ? 'user' : 'assistant';
      if (change.is_hidden != null) patch.is_hidden = !!change.is_hidden;
      if (change.data && typeof change.data === 'object') {
        target.statData = (change.data.stat_data && typeof change.data.stat_data === 'object')
          ? clone(change.data.stat_data)
          : clone(change.data);
        patch.data = clone(change.data);
      }
      if (change.extra && typeof change.extra === 'object') {
        target.extra = clone(change.extra);
        patch.extra = clone(change.extra);
      }
      if (patch.id && Object.keys(patch).length > 1) patches.push(patch);
    }
    ensureChatMirror();
    emit(window.tavern_events.MESSAGE_UPDATED, lastIndex());
    if (patches.length) return requestMessagePatch(patches).then(function(){ return undefined; });
    return Promise.resolve();
  };
  window.deleteChatMessages = function(ids, options){
    var list = (Array.isArray(ids) ? ids : [ids]).map(Number).filter(function(id){
      return isFinite(id) && id >= 0 && id < MESSAGES.length;
    }).sort(function(a, b){ return b - a; });
    for (var i = 0; i < list.length; i += 1) {
      MESSAGES.splice(list[i], 1);
      CHAT.splice(list[i], 1);
    }
    ensureChatMirror();
    return Promise.resolve();
  };
  window.rotateChatMessages = function(begin, middle, end, options){
    begin = Number(begin); middle = Number(middle); end = Number(end);
    if (!(begin >= 0 && middle >= begin && end >= middle && end <= MESSAGES.length)) {
      return Promise.reject(new Error('invalid rotate range'));
    }
    var next = MESSAGES.slice(0, begin)
      .concat(MESSAGES.slice(middle, end), MESSAGES.slice(begin, middle), MESSAGES.slice(end));
    for (var i = 0; i < next.length; i += 1) MESSAGES[i] = next[i];
    MESSAGES.length = next.length;
    CHAT.length = 0;
    ensureChatMirror();
    return Promise.resolve();
  };
  function injectPrompts(prompts, options){
    var list = Array.isArray(prompts) ? clone(prompts) : [];
    try { HOST.postMessage({ v: VERSION, type: MSG_INJECT_PROMPTS, prompts: list, remove: [] }, '*'); } catch (e) {}
    var deleted = false;
    var ids = list.map(function(p){ return String((p && p.id) || ''); }).filter(Boolean);
    function uninject(){
      if (deleted) return;
      deleted = true;
      uninjectPrompts(ids);
    }
    if (options && options.once) {
      on(window.tavern_events.GENERATION_ENDED, uninject, true, false);
      on(window.tavern_events.GENERATION_STOPPED, uninject, true, false);
      on(window.iframe_events.GENERATION_ENDED, uninject, true, false);
    }
    return { uninject: uninject };
  }
  function uninjectPrompts(ids){
    var list = Array.isArray(ids) ? ids.map(String) : [];
    try { HOST.postMessage({ v: VERSION, type: MSG_INJECT_PROMPTS, prompts: [], remove: list }, '*'); } catch (e) {}
  }

  var helper = window.TavernHelper || window.tavernHelper || {};
  helper.getVariables = window.getVariables;
  helper.setVariables = window.setVariables;
  helper.replaceVariables = window.replaceVariables;
  helper.updateVariablesWith = window.updateVariablesWith;
  helper.insertOrAssignVariables = window.insertOrAssignVariables;
  helper.insertVariables = window.insertVariables;
  helper.deleteVariable = window.deleteVariable;
  helper.deleteVariables = window.deleteVariable;
  helper.getAllVariables = window.getAllVariables;
  helper.getChatVariable = window.getChatVariable;
  helper.setChatVariable = window.setChatVariable;
  helper.getGlobalVariables = window.getGlobalVariables;
  helper.setGlobalVariable = window.setGlobalVariable;
  helper.getChatMessages = window.getChatMessages;
  helper.createChatMessages = createChatMessages;
  helper.setChatMessages = window.setChatMessages;
  helper.deleteChatMessages = window.deleteChatMessages;
  helper.rotateChatMessages = window.rotateChatMessages;
  helper.getChatHistoryDetail = getChatHistoryDetail;
  helper.getCurrentMessageId = window.getCurrentMessageId;
  helper.getLastMessageId = window.getLastMessageId;
  helper.getWorldbookNames = window.getWorldbookNames;
  helper.getCharWorldbookNames = window.getCharWorldbookNames;
  helper.getGlobalWorldbookNames = window.getGlobalWorldbookNames;
  helper.getWorldbook = window.getWorldbook;
  helper.updateWorldbookWith = window.updateWorldbookWith;
  helper.updateWorldbook = window.updateWorldbook;
  helper.replaceWorldbook = window.replaceWorldbook;
  helper.getCharName = window.getCharName;
  helper.getCurrentCharName = window.getCurrentCharName;
  helper.getCharAvatar = window.getCharAvatar;
  helper.getCurrentPersonaName = window.getCurrentPersonaName;
  helper.getCurrentPersonaId = window.getCurrentPersonaId;
  helper.retrieveDisplayedMessage = window.retrieveDisplayedMessage;
  helper.getCurrentCharacterId = function(){ return CHARACTER.avatar || CHARACTER.name; };
  helper.getChatWorldbookName = window.getChatWorldbookName;
  helper.getCharacter = function(){ return clone(CHARACTER); };
  helper.getScriptId = function(){ return ''; };
  helper.getPresetNames = function(){ return []; };
  helper.triggerSlash = function(){ return window.triggerSlash.apply(window, arguments); };
  helper.substituteParams = function(){ return window.substituteParams.apply(window, arguments); };
  helper.substitudeMacros = function(){ return window.substituteParams.apply(window, arguments); };
  helper.eventOn = window.eventOn;
  helper.eventOnce = window.eventOnce;
  helper.eventOff = window.eventOff;
  helper.eventEmit = window.eventEmit;
  helper.eventEmitAndWait = window.eventEmitAndWait;
  helper.eventMakeFirst = window.eventMakeFirst;
  helper.eventMakeLast = window.eventMakeLast;
  helper.getButtonEvent = window.getButtonEvent;
  helper.eventRemoveListener = window.eventRemoveListener;
  helper.eventClearEvent = window.eventClearEvent;
  helper.eventClearListener = window.eventClearListener;
  helper.eventClearAll = window.eventClearAll;
  helper.getCharacterIds = function(){ return CHARACTER.avatar || CHARACTER.name ? [CHARACTER.avatar || CHARACTER.name] : []; };
  helper.getCharacterNames = function(){ return CHARACTER.name ? [CHARACTER.name] : []; };
  helper.initializeGlobal = function(name, value){ window[String(name)] = value; };
  helper.injectPrompts = injectPrompts;
  helper.uninjectPrompts = uninjectPrompts;
  window.injectPrompts = injectPrompts;
  window.uninjectPrompts = uninjectPrompts;
  window.TavernHelper = helper;
  window.tavernHelper = helper;
  // EJS 世界书条目会调用 globalThis.DALPhoneMainline.read()；作者面板里的
  // 手机模块会赋值该对象，这里包一层，把主线摘要同步给宿主 EJS 运行时。
  var mainlineValue = null;
  try {
    Object.defineProperty(window, 'DALPhoneMainline', {
      configurable: true,
      get: function(){ return mainlineValue; },
      set: function(value){
        if (value && typeof value.read === 'function') {
          var original = value.read;
          value.read = function(){
            return Promise.resolve(original.apply(value, arguments)).then(function(text){
              try {
                HOST.postMessage({ v: VERSION, type: MSG_PHONE_MAINLINE, text: String(text == null ? '' : text).slice(0, 131072) }, '*');
              } catch (e) {}
              return text;
            });
          };
        }
        mainlineValue = value;
      }
    });
  } catch (e) {}
  // 面板打开期间周期性刷新主线摘要（read 已被上面的 setter 包成“顺带上报”）
  try {
    var syncMainline = function(){
      try {
        if (mainlineValue && typeof mainlineValue.read === 'function') {
          Promise.resolve(mainlineValue.read()).catch(function(){});
        }
      } catch (e) {}
    };
    setTimeout(syncMainline, 3000);
    setInterval(syncMainline, 15000);
  } catch (e) {}

  window.toastr = {
    info: function(){}, success: function(){}, warning: function(){}, error: function(){}, clear: function(){}
  };
  try { window.alert = function(message){ try { HOST.postMessage({ v: VERSION, type: 'flyai-card-toast', level: 'info', text: String(message || '') }, '*'); } catch (e) {} }; } catch (e) {}
  try { window.confirm = function(){ return !!CAN_WRITE; }; } catch (e) {}
  try { window.Popup = window.Popup || {}; window.Popup.show = { confirm: function(){ return Promise.resolve(CAN_WRITE ? 1 : 0); } }; window.POPUP_RESULT = { AFFIRMATIVE: 1, CANCELLED: 0 }; } catch (e) {}
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

  var pendingGenerates = {};
  var externalGenerates = {};
  var generateSeq = 0;
  var pendingMetadata = {};
  var metadataSeq = 0;

  function saveMetadata(){
    var id = 'm' + (++metadataSeq);
    var saving = new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingMetadata[id];
        reject(new Error('flyai-save-metadata-timeout'));
      }, 20000);
      pendingMetadata[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        HOST.postMessage({
          v: VERSION,
          type: MSG_SAVE_METADATA,
          id: id,
          metadata: CHAT_METADATA
        }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingMetadata[id];
        reject(e);
      }
    });
    // 小手机保存手机状态时只调 saveMetadata；顺带把消息 extra（楼层身份）落库。
    return saving.then(function(value){
      return persistChatExtras().then(
        function(){ return value; },
        function(){ return value; }
      );
    });
  }
  function settleMetadata(data){
    if (!data || data.id == null) return;
    var item = pendingMetadata[data.id];
    if (!item) return;
    delete pendingMetadata[data.id];
    clearTimeout(item.timer);
    if (data.ok) item.resolve(true);
    else item.reject(new Error(String(data.error || 'save-metadata-failed')));
  }
  var pendingMessages = {};
  var messagesSeq = 0;
  function requestMessagePatch(changes, creates){
    var id = 'p' + (++messagesSeq);
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingMessages[id];
        reject(new Error('flyai-message-patch-timeout'));
      }, 20000);
      pendingMessages[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        HOST.postMessage({
          v: VERSION,
          type: MSG_SAVE_MESSAGES,
          id: id,
          changes: changes || [],
          creates: creates || []
        }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingMessages[id];
        reject(e);
      }
    });
  }
  function settleMessages(data){
    if (!data || data.id == null) return;
    var item = pendingMessages[data.id];
    if (!item) return;
    delete pendingMessages[data.id];
    clearTimeout(item.timer);
    if (data.ok) item.resolve(data.result);
    else item.reject(new Error(String(data.error || 'patch-messages-failed')));
  }
  // 扩展原地写入 message.extra（如 dal_phone_source）后，通过 saveChat /
  // saveMetadata 落库；只提交变化过的 extra，避免每次保存都整表回写。
  var persistedExtras = {};
  function collectExtraPatches(){
    var mirror = ensureChatMirror();
    var patches = [];
    var staged = {};
    for (var i = 0; i < mirror.length; i += 1) {
      var item = mirror[i];
      if (!item || !item.id) continue;
      var extra = item.extra;
      if (!extra || typeof extra !== 'object') continue;
      var json;
      try { json = JSON.stringify(extra); } catch (e) { continue; }
      if (!json || json === '{}') continue;
      staged[item.id] = json;
      if (MESSAGES[i]) MESSAGES[i].extra = clone(extra);
      if (persistedExtras[item.id] === json) continue;
      patches.push({ id: String(item.id), extra: clone(extra) });
    }
    return { patches: patches, staged: staged };
  }
  function persistChatExtras(){
    if (!CAN_WRITE) return Promise.resolve(false);
    var job = collectExtraPatches();
    if (!job.patches.length) return Promise.resolve(false);
    var list = job.patches.length > 200
      ? job.patches.slice(job.patches.length - 200)
      : job.patches;
    return requestMessagePatch(list).then(function(){
      for (var id in job.staged) {
        if (Object.prototype.hasOwnProperty.call(job.staged, id)) {
          persistedExtras[id] = job.staged[id];
        }
      }
      return true;
    });
  }
  function saveChat(){
    return persistChatExtras().then(function(){ return true; }, function(){ return true; });
  }
  var pendingMainGenerates = {};
  var mainGenerateSeq = 0;
  function requestGenerateMain(quietPrompt){
    var id = 'g' + (++mainGenerateSeq);
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingMainGenerates[id];
        reject(new Error('flyai-generate-timeout'));
      }, 180000);
      pendingMainGenerates[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        HOST.postMessage({
          v: VERSION,
          type: MSG_GENERATE_MAIN,
          id: id,
          quiet_prompt: String(quietPrompt == null ? '' : quietPrompt),
          automatic_trigger: true
        }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingMainGenerates[id];
        reject(e);
      }
    });
  }
  function settleMainGenerate(data){
    if (!data || data.id == null) return;
    var item = pendingMainGenerates[data.id];
    if (!item) return;
    delete pendingMainGenerates[data.id];
    clearTimeout(item.timer);
    if (data.ok) item.resolve(true);
    else item.reject(new Error(String(data.error || 'generate-failed')));
  }
  function contextGenerate(type, options){
    if (options && typeof options === 'object' &&
        typeof options.quiet_prompt === 'string' && options.quiet_prompt) {
      return requestGenerateMain(options.quiet_prompt);
    }
    return window.__flyaiGenerate ? window.__flyaiGenerate(type, options) : Promise.resolve(false);
  }
  function updateChatMetadata(patch, reset){
    if (!patch || typeof patch !== 'object') return;
    var next = clone(patch);
    if (reset === true) {
      CHAT_METADATA = next;
    } else {
      // 酒馆语义：默认按顶层键合并，未提及的命名空间保持不变。
      Object.keys(next).forEach(function(key){ CHAT_METADATA[key] = next[key]; });
    }
    ensureChatIntegrity(CHAT_METADATA);
    var context = window.SillyTavern && window.SillyTavern.getContext
      ? window.SillyTavern.getContext() : null;
    if (context) context.chatMetadata = CHAT_METADATA;
  }
  function requestGenerate(payload, externalId){
    var id = 'g' + (++generateSeq);
    var ext = externalId ? String(externalId) : '';
    if (ext) externalGenerates[ext] = id;
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingGenerates[id];
        if (ext && externalGenerates[ext] === id) delete externalGenerates[ext];
        reject(new Error('flyai-author-generate-timeout'));
      }, 180000);
      pendingGenerates[id] = {
        resolve: resolve, reject: reject, timer: timer, externalId: ext
      };
      try {
        HOST.postMessage({
          v: VERSION,
          type: MSG_GENERATE,
          id: id,
          purpose: String((payload && payload.purpose) || 'phone'),
          messages: Array.isArray(payload && payload.messages) ? payload.messages : [],
          systemPrompt: String((payload && payload.systemPrompt) || ''),
          maxTokens: Number((payload && payload.maxTokens) || 0) || undefined
        }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingGenerates[id];
        if (ext && externalGenerates[ext] === id) delete externalGenerates[ext];
        reject(e);
      }
    });
  }
  function settleGenerate(data){
    if (!data || data.id == null) return;
    var item = pendingGenerates[data.id];
    if (!item) return;
    delete pendingGenerates[data.id];
    if (item.externalId && externalGenerates[item.externalId] === data.id) {
      delete externalGenerates[item.externalId];
    }
    clearTimeout(item.timer);
    if (data.ok) item.resolve(String(data.text || ''));
    else item.reject(new Error(String(data.error || 'generate-failed')));
  }
  window.__flyaiGenerate = function(type, options){
    var opts = options && typeof options === 'object' ? options : {};
    var messages = Array.isArray(opts.messages) ? opts.messages : [];
    var prompt = opts.quiet_prompt || opts.prompt || opts.text || '';
    if (!messages.length && prompt) messages = [{ role: 'user', content: String(prompt) }];
    return requestGenerate({
      purpose: opts.purpose || 'phone',
      messages: messages,
      systemPrompt: opts.systemPrompt || opts.system_prompt || '',
      maxTokens: opts.max_tokens || opts.maxTokens || 0
    });
  };

  function normalizeGenerateContent(value){
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      return value.map(function(item){
        if (typeof item === 'string') return item;
        if (item && typeof item.text === 'string') return item.text;
        return '';
      }).join('');
    }
    if (value && typeof value === 'object') {
      // TavernHelper 的提示词项可能是 {role, content: [{type:'text',text}]}
      try { return JSON.stringify(value); } catch (e) { return String(value); }
    }
    return '';
  }
  function generateRaw(request, maybeOptions){
    if (request && typeof request === 'object' &&
        (Array.isArray(request.ordered_prompts) || Array.isArray(request.messages))) {
      var list = Array.isArray(request.ordered_prompts)
        ? request.ordered_prompts
        : request.messages;
      var messages = list.map(function(item){
        var role = item && item.role ? String(item.role) : 'user';
        var content = item && item.content != null ? item.content : (item && item.text);
        return { role: role, content: normalizeGenerateContent(content) };
      }).filter(function(item){ return item.content; });
      var maxTokens = 0;
      if (request.custom_api && request.custom_api.max_tokens) {
        maxTokens = Number(request.custom_api.max_tokens) || 0;
      } else if (request.max_tokens) {
        maxTokens = Number(request.max_tokens) || 0;
      }
      // 卡内 custom_api 的 endpoint/key 不透传：作者脚本不能借宿主的网络
      // 把请求发到卡自带地址；这里统一走会话模型，并在宿主弹确认。
      return requestGenerate({
        purpose: String(request.purpose || 'handbook'),
        messages: messages,
        systemPrompt: '',
        maxTokens: maxTokens
      }, request.generation_id);
    }
    return window.__flyaiGenerate(request, maybeOptions);
  }
  function stopGenerationById(generationId){
    var ext = generationId == null ? '' : String(generationId);
    var local = externalGenerates[ext];
    if (!local) return false;
    var item = pendingGenerates[local];
    if (!item) return false;
    delete pendingGenerates[local];
    delete externalGenerates[ext];
    clearTimeout(item.timer);
    // 宿主目前没有逐次取消通道；拒绝前端 Promise 后，迟到的结果会被丢弃。
    item.reject(new Error('cancelled'));
    return true;
  }
  window.generateRaw = generateRaw;
  window.stopGenerationById = stopGenerationById;

  var iframeGenerationId = null;
  var iframeGenerationSeq = 0;
  window.addEventListener('message', function(event){
    var data = event && event.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === MSG_GENERATE_RESULT) { settleGenerate(data); return; }
    if (data.type === MSG_SAVE_METADATA_RESULT) { settleMetadata(data); return; }
    if (data.type === MSG_SAVE_MESSAGES_RESULT) { settleMessages(data); return; }
    if (data.type === MSG_GENERATE_MAIN_RESULT) { settleMainGenerate(data); return; }
    if (data.type === 'flyai-handbook-rpc' && event.source) {
      // 手札 shell 是嵌套 iframe（沙箱不给 allow-same-origin，不能直接读
      // 父窗口的 adapter），用 postMessage RPC 转发到本帧的 HLMHandbookAdapter。
      var source = event.source;
      var reply = function(ok, value, error){
        try {
          source.postMessage({
            type: 'flyai-handbook-rpc-result',
            id: data.id,
            ok: ok,
            value: value,
            error: error
          }, '*');
        } catch (e) {}
      };
      var adapter = window.HLMHandbookAdapter;
      var method = String(data.method || '');
      if (!adapter || typeof adapter[method] !== 'function') {
        reply(false, null, 'unsupported-method');
        return;
      }
      try {
        Promise.resolve(adapter[method].apply(adapter, Array.isArray(data.args) ? data.args : []))
          .then(function(value){ reply(true, value, null); },
                function(error){ reply(false, null, String((error && error.message) || error)); });
      } catch (e) {
        reply(false, null, String((e && e.message) || e));
      }
      return;
    }
    if (data.type === MSG_EVENT && typeof data.event === 'string') {
      emit(data.event, data.payload);
      if (data.event === 'GENERATION_STARTED') {
        iframeGenerationSeq += 1;
        iframeGenerationId = 'flyai-generation-' + iframeGenerationSeq;
        emit(window.iframe_events.GENERATION_REQUESTED, iframeGenerationId);
      } else if (data.event === 'GENERATION_ENDED' || data.event === 'GENERATION_STOPPED') {
        emit(window.iframe_events.GENERATION_ENDED, '', iframeGenerationId);
        iframeGenerationId = null;
      }
      return;
    }
    if (data.type !== MSG_VARS) return;
    if (data.variables && typeof data.variables === 'object') {
      CHAT_VARIABLES = normalizeMvu(data.variables);
      emit(MVU_EVENTS.VARIABLE_UPDATE_ENDED, clone(CHAT_VARIABLES));
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
          if (message.role === 'user') {
            emit(window.tavern_events.USER_MESSAGE_RENDERED, index);
          } else {
            emit(window.tavern_events.CHARACTER_MESSAGE_RENDERED, index);
            emit(window.tavern_events.MESSAGE_RECEIVED, index, 'normal');
          }
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
  // 每个脚本独立成一个 module：酒馆助手按脚本隔离作用域，卡里允许不同脚本
  // declares 同名顶层函数（红楼两个 MVU 脚本都有 hlmImportWatched）。
  // 拼进同一个 module 会因严格模式重复声明让整包脚本解析失败。
  const sources = scripts.map((script) => adaptAuthorSource(script.content))
  const source = sources.join('\n;\n')
  const shim = buildShim({
    messages: options.messages,
    variables: options.variables,
    chatMetadata: options.chatMetadata,
    character: options.character ?? options.author.character,
    writeContext: options.writeContext,
    sessionId: options.sessionId,
    chatId: options.chatId ?? options.sessionId,
    userName: options.userName,
    canWrite: options.canWrite,
    canAct: options.canAct,
  })
  const importMap = buildImportMap(source, buildNativeImportMapEntries())
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
<script>(function(){${LODASH_SOURCE.replace(
        /<\/script/gi,
        '<\\/script'
      )}\nif (typeof window._ === 'undefined') window._ = __lodashVendor;})();</script>
<script>${ZOD_SOURCE.replace(/<\/script/gi, '<\\/script')}</script>
<script>${escapeScriptBody(ST_CORE_SOURCE)}</script>
<script>${shim}</script>
<script>${escapeScriptBody(ST_NATIVE_SNAPSHOT_SOURCE)}</script>
<script>${escapeScriptBody(buildEjsTemplateSource())}</script>
${importMap}
${sources
  .map((body) => `<script type="module">${escapeScriptBody(body)}</script>`)
  .join('\n')}
<script type="module">${escapeScriptBody(
    // 原生宿主可用时让卡自带的运行时生效；缺失时保留卡特定覆盖做回退。
    `if (!(window.__FLYAI_NATIVE_HOST__ && window.DALPhoneRuntime)) {\n${DAL_PHONE_RUNTIME_SOURCE}\n}`
  )}</script>
</body>
</html>`
}
