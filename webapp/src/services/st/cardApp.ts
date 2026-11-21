/**
 * 酒馆卡「HTML + JS 整页应用」的沙箱装载器（可选特性）。
 *
 * 卡内展示正则常把占位符替换成一份完整的 HTML 文档（自带 `<style>`/`<script>`），
 * 传统白名单清洗只能还原静态部分。开启沙箱后：
 * - 正则引擎把这类替换包上哨兵标记，解析前抽出来换成占位符；
 * - 渲染时塞进 `sandbox="allow-scripts"` 的 null-origin iframe，用 CSP 限制网络；
 * - 注入 shim 提供 `getChatMessages`/`triggerSlash`/`getVariables` 等常用 API。
 *
 * 关键边界：iframe 没有同源权限，读不到 localStorage、Cookie 或页面里的 bearer
 * token；CSP 默认 `connect-src 'none'`，卡内脚本无法把数据外传。
 */

// 内置 jQuery / lodash（MIT，见 vendor/README.md）：卡片引用时内联注入，
// 断网或 CDN 被屏蔽也能初始化脚本。酒馆主界面本来就带 lodash 全局 `_`，
// 大量卡内应用直接 `_.get(stat_data, …)`，缺了会在 render 里直接抛错。
import JQUERY_SOURCE from './vendor/jquery-3.6.0.min.js?raw'
import LODASH_SOURCE from './vendor/lodash-4.18.1.min.js?raw'

/** 正则引擎在替换结果外层加的哨兵；U+0000 不会出现在正常文本里 */
export const APP_START = '\u0000FLYAI_APP_START\u0000'
export const APP_END = '\u0000FLYAI_APP_END\u0000'

/** 抽离后写进 markdown 的占位符（渲染前再换成 cardApp 节点） */
export const APP_PLACEHOLDER_PREFIX = '@@FLYAI_CARD_APP_'
export const APP_PLACEHOLDER_RE = /@@FLYAI_CARD_APP_(\d+)@@/g

/** 单份卡内应用上限；超限仍按普通代码块/清洗 HTML 处理 */
export const MAX_CARD_APP_LENGTH = 512 * 1024

/** 父子 iframe 的消息协议版本；不匹配的消息两边都忽略 */
export const CARD_APP_PROTOCOL_VERSION = 1
export const CARD_MSG_RESIZE = 'flyai-card-resize'
export const CARD_MSG_READY = 'flyai-card-ready'
export const CARD_MSG_VARS = 'flyai-card-vars'
export const CARD_MSG_MESSAGE = 'flyai-card-message'
export const CARD_MSG_TOAST = 'flyai-card-toast'
export const CARD_MSG_ERROR = 'flyai-card-error'
/** 卡 → 宿主：动作/写入意图（发送、重新生成、继续、变量/世界书/角色写入） */
export const CARD_MSG_ACTION = 'flyai-card-action'
/** 宿主 → 卡：意图结果 `{id, ok, result?, error?}` */
export const CARD_MSG_ACTION_RESULT = 'flyai-card-action-result'
/** 卡 → 宿主：受控网络代理请求 */
export const CARD_MSG_FETCH = 'flyai-card-fetch'
/** 宿主 → 卡：代理响应 `{id, ok, status, headers, bodyBase64, error?}` */
export const CARD_MSG_FETCH_RESULT = 'flyai-card-fetch-result'
/** 卡 → 宿主：取消仍在处理的代理请求 */
export const CARD_MSG_FETCH_ABORT = 'flyai-card-fetch-abort'
/** 宿主 → 卡：世界书副本与角色白名单字段快照（写入 API 的读侧） */
export const CARD_MSG_WRITE_CTX = 'flyai-card-write-context'
export const CARD_CODE_READ_ONLY = 'read-only'
export const CARD_CODE_VARS_TOO_LARGE = 'vars-too-large'
/** 用户取消了确认弹窗 */
export const CARD_CODE_ACTION_REJECTED = 'action-rejected'
/** 当前上下文不允许该动作（只读/群聊/流式/非末条） */
export const CARD_CODE_UNAVAILABLE = 'unavailable'
/** 宿主侧限流 */
export const CARD_CODE_RATE_LIMITED = 'rate-limited'
/** 动作执行失败 */
export const CARD_CODE_ACTION_FAILED = 'action-failed'
/** 网络代理请求失败（后端错误、超时、响应过大等） */
export const CARD_CODE_NET_FAILED = 'net-failed'
/** 网络策略拒绝（开关关闭、域名不在白名单、内网地址） */
export const CARD_CODE_NET_DENIED = 'net-denied'

/** 卡内脚本错误上报上限（去重后）；超出后不再上报，避免错误风暴刷屏 */
export const MAX_CARD_APP_ERRORS = 20
export const MAX_CARD_APP_ERROR_LENGTH = 300

/** 宿主提供给卡内脚本的角色信息（名称/头像 URL，均可缺省） */
export type CardAppCharacter = {
  name?: string
  avatar?: string | null
}

/**
 * 卡内可用的授权能力。缺省/全部关闭时 shim 保持 L3 的纯只读行为；
 * 每项都由用户设置开关与当前消息上下文共同决定，卡本身不能自行开启。
 */
export type CardAppCapabilities = {
  /** 发送 / 重新生成 / 继续（仅单角色 DM 的最后一条消息） */
  actions: boolean
  /** 变量、世界书副本、角色副本字段写回 */
  write: boolean
  /** 受控网络代理 */
  net: boolean
}

/** 宿主下发给卡内脚本的写入读侧快照（`flyai-card-write-context`） */
export type CardAppWriteContext = {
  /** 绑定的世界书副本；未绑定时为 null */
  worldbook?: { name?: string; entries?: unknown[] } | null
  /** 角色卡自带世界书的名称（CC 的 extensions.world）；角色卡内嵌条目在 character.table */
  characterWorldName?: string
  /** 角色副本的白名单字段快照 */
  character?: Record<string, unknown> | null
}

/** 变量快照上限；超限退化为空状态，避免把几十 MB 塞进 srcDoc */
export const MAX_CARD_APP_VARIABLES_LENGTH = 128 * 1024
export const MAX_CARD_APP_VARIABLE_DEPTH = 16
export const MAX_CARD_APP_VARIABLE_NODES = 20_000
export const MAX_CARD_APP_VARIABLE_STRING = 32 * 1024
export const MAX_CARD_APP_VARIABLE_ARRAY = 500
export const MAX_CARD_APP_VARIABLE_KEYS = 1_000
export const MAX_CARD_APP_TOAST_LENGTH = 500

export type SanitizedCardAppVariables = {
  /** 归一化为 `{ stat_data: … }` 的只读快照 */
  variables: Record<string, unknown>
  /** 序列化结果，父子两侧据此去重 */
  serialized: string
  /** 是否因循环/深度/数量/体积限制发生了裁剪 */
  truncated: boolean
}

const EMPTY_VARIABLES = '{"stat_data":{}}'

const emptyVariables = (truncated = false): SanitizedCardAppVariables => ({
  variables: { stat_data: {} },
  serialized: EMPTY_VARIABLES,
  truncated,
})

/**
 * 把 MVU 状态整理成 `{ stat_data: … }` 并做体积保护。
 *
 * - 含对象型 `stat_data` 的入参视为完整状态；其它对象包装成 `{ stat_data: 入参 }`。
 * - 循环引用替换为 `"[Circular]"`，超限节点/字符串/数组/键数量按上限裁剪。
 * - 序列化后超过 128KB 或抛错时回退空状态并标记 `truncated`。
 * - 不修改入参；函数、Symbol、BigInt、非有限数字都转成可序列化的安全值。
 */
export const sanitizeCardAppVariables = (
  input: Record<string, unknown> | null | undefined
): SanitizedCardAppVariables => {
  if (!input || typeof input !== 'object') {
    return emptyVariables()
  }
  let truncated = false
  let nodes = 0
  const ancestors = new WeakSet<object>()

  const clone = (value: unknown, depth: number): unknown => {
    nodes += 1
    if (nodes > MAX_CARD_APP_VARIABLE_NODES) {
      truncated = true
      return '[TooManyNodes]'
    }
    if (value === null) {
      return null
    }
    const type = typeof value
    if (type === 'string') {
      const text = value as string
      if (text.length > MAX_CARD_APP_VARIABLE_STRING) {
        truncated = true
        return text.slice(0, MAX_CARD_APP_VARIABLE_STRING)
      }
      return text
    }
    if (type === 'number') {
      return Number.isFinite(value as number) ? value : null
    }
    if (type === 'boolean') {
      return value
    }
    if (type === 'undefined') {
      return null
    }
    if (type === 'bigint' || type === 'symbol') {
      return String(value)
    }
    if (type === 'function') {
      return '[Function]'
    }
    if (depth > MAX_CARD_APP_VARIABLE_DEPTH) {
      truncated = true
      return '[MaxDepth]'
    }
    const object = value as object
    if (ancestors.has(object)) {
      return '[Circular]'
    }
    if (object instanceof Date) {
      try {
        return object.toISOString()
      } catch (err) {
        return null
      }
    }
    if (object instanceof RegExp) {
      return String(object)
    }
    ancestors.add(object)
    let out: unknown
    if (Array.isArray(object)) {
      if (object.length > MAX_CARD_APP_VARIABLE_ARRAY) {
        truncated = true
      }
      const length = Math.min(object.length, MAX_CARD_APP_VARIABLE_ARRAY)
      const list: unknown[] = []
      for (let index = 0; index < length; index += 1) {
        list.push(clone(object[index], depth + 1))
      }
      out = list
    } else {
      const record = object as Record<string, unknown>
      const keys = Object.keys(record)
      if (keys.length > MAX_CARD_APP_VARIABLE_KEYS) {
        truncated = true
      }
      const length = Math.min(keys.length, MAX_CARD_APP_VARIABLE_KEYS)
      const result: Record<string, unknown> = {}
      for (let index = 0; index < length; index += 1) {
        const key = keys[index]
        let child: unknown
        try {
          child = clone(record[key], depth + 1)
        } catch (err) {
          truncated = true
          child = '[Unserializable]'
        }
        if (key === '__proto__') {
          Object.defineProperty(result, key, {
            value: child,
            enumerable: true,
            writable: true,
            configurable: true,
          })
        } else {
          result[key] = child
        }
      }
      out = result
    }
    ancestors.delete(object)
    return out
  }

  try {
    const statData = (input as Record<string, unknown>).stat_data
    const fullState = !!statData && typeof statData === 'object'
    const variables = (
      fullState ? clone(input, 0) : { stat_data: clone(input, 1) }
    ) as Record<string, unknown>
    if (!variables || typeof variables !== 'object') {
      return emptyVariables(truncated)
    }
    const serialized = JSON.stringify(variables)
    if (
      typeof serialized !== 'string' ||
      serialized.length > MAX_CARD_APP_VARIABLES_LENGTH
    ) {
      return emptyVariables(true)
    }
    return { variables, serialized, truncated }
  } catch (err) {
    return emptyVariables(true)
  }
}

const FENCE_RE = /^\s*```[^\n]*\r?\n([\s\S]*?)\r?\n```\s*$/

/**
 * 是否值得放进沙箱：完整 HTML 文档且带脚本。
 * 只有 `<style>` 的文档继续走白名单清洗，避免改变现有行为。
 */
export const isCardAppReplacement = (replace: string): boolean => {
  if (!replace || replace.length > MAX_CARD_APP_LENGTH) {
    return false
  }
  if (!/<script[\s>]/i.test(replace)) {
    return false
  }
  return /<!doctype\s+html|<html[\s>]|<body[\s>]/i.test(replace)
}

/** 去掉包在整页 HTML 外的 ```html 围栏 */
export const stripAppFence = (value: string): string => {
  const match = FENCE_RE.exec(value)
  return match ? match[1] : value
}

export type ExtractedCardApps = {
  /** 占位符替换后的正文 */
  text: string
  /** 按占位符序号排列的整页 HTML（已去掉围栏） */
  apps: string[]
}

/**
 * 把哨兵包裹的整页 HTML 抽成占位符。没有哨兵时原样返回。
 * 哨兵不完整（理论上不会发生）时保留原文，宁可显示源码也不丢内容。
 */
export const extractCardApps = (text: string): ExtractedCardApps => {
  if (!text || text.indexOf(APP_START) < 0) {
    return { text, apps: [] }
  }
  const apps: string[] = []
  let out = ''
  let pos = 0
  for (;;) {
    const start = text.indexOf(APP_START, pos)
    if (start < 0) {
      out += text.slice(pos)
      break
    }
    const end = text.indexOf(APP_END, start + APP_START.length)
    if (end < 0) {
      out += text.slice(pos)
      break
    }
    out += text.slice(pos, start)
    const raw = text.slice(start + APP_START.length, end)
    const html = stripAppFence(raw)
    if (isCardAppReplacement(html)) {
      out += `\n\n${APP_PLACEHOLDER_PREFIX}${apps.length}@@\n\n`
      apps.push(html)
    } else {
      out += raw
    }
    pos = end + APP_END.length
  }
  return { text: out, apps }
}

/** 把任意字符串安全地嵌进内联 `<script>`（防止 `</script>` 截断） */
export const escapeForScript = (value: string): string =>
  JSON.stringify(value)
    .replace(/</g, '\\u003c')
    .replace(/>/g, '\\u003e')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029')

const SCRIPT_HOSTS = [
  'https://cdn.tailwindcss.com',
  'https://code.jquery.com',
  'https://cdn.jsdelivr.net',
  'https://cdnjs.cloudflare.com',
  'https://unpkg.com',
].join(' ')

const STYLE_HOSTS = [
  'https://cdn.tailwindcss.com',
  'https://cdn.jsdelivr.net',
  'https://cdnjs.cloudflare.com',
  'https://unpkg.com',
  'https://fonts.googleapis.com',
].join(' ')

const FONT_HOSTS = [
  'https://cdn.jsdelivr.net',
  'https://cdnjs.cloudflare.com',
  'https://unpkg.com',
  'https://fonts.gstatic.com',
].join(' ')

/** 归一化允许的图片来源：只接受 http(s) 的 `scheme://host[:port]` */
const normalizeMediaOrigin = (origin: string): string | null => {
  try {
    const url = new URL(origin)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      return null
    }
    return `${url.protocol}//${url.host}`
  } catch (err) {
    return null
  }
}

/**
 * 卡内应用的 CSP：默认禁止一切网络，只放行常见的 UI 库 CDN。
 * `externalMedia` 与「外链插图」开关一致，控制图片/音频能否直连外网。
 * `mediaOrigins` 是宿主明确提供的图片来源（如角色头像所在源），
 * 即使关闭外链插图也放行，避免头像被 CSP 拦成空白。
 */
export const contentSecurityPolicy = (
  externalMedia: boolean,
  mediaOrigins: string[] = []
): string => {
  const remote = externalMedia ? ' https:' : ''
  const origins: string[] = []
  const seen: Record<string, boolean> = {}
  mediaOrigins.forEach((item) => {
    const origin = normalizeMediaOrigin(item)
    if (origin && !seen[origin]) {
      seen[origin] = true
      origins.push(origin)
    }
  })
  const originExtra = origins.length ? ` ${origins.join(' ')}` : ''
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline' 'unsafe-eval' ${SCRIPT_HOSTS}`,
    `style-src 'unsafe-inline' ${STYLE_HOSTS}`,
    `font-src data: ${FONT_HOSTS}`,
    `img-src data: blob:${remote}${originExtra}`,
    `media-src data: blob:${remote}`,
    "connect-src 'none'",
    "frame-src 'none'",
    "worker-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join('; ')
}

const JQUERY_TAG_RE =
  /<script\b[^>]*\bsrc\s*=\s*["'][^"']*jquery[^"']*["'][^>]*>\s*<\/script\s*>/gi
const JQUERY_HINT_RE = /\bjQuery\b|\$\s*\(/

/**
 * 卡片是否需要 jQuery：显式引用 jQuery 的 `<script src>`，或脚本里直接用了
 * `jQuery` / `$(`。命中时注入内置版本，避免依赖 CDN 是否可达。
 */
export const needsJQuery = (html: string): boolean => {
  JQUERY_TAG_RE.lastIndex = 0
  if (JQUERY_TAG_RE.test(html)) {
    return true
  }
  return JQUERY_HINT_RE.test(html)
}

/** 去掉卡片自己的 jQuery CDN 标签，避免与内置版本重复加载/联网 */
export const stripJQueryTags = (html: string): string => {
  JQUERY_TAG_RE.lastIndex = 0
  return html.replace(JQUERY_TAG_RE, '')
}

const LODASH_TAG_RE =
  /<script\b[^>]*\bsrc\s*=\s*["'][^"']*lodash[^"']*["'][^>]*>\s*<\/script\s*>/gi
// `_.get(…)` / `_(…)` / `lodash.get(…)`；前面排除单词字符，
// 避免把 `snake_case`、`foo_bar` 这类普通标识符误判成 lodash。
const LODASH_HINT_RE = /(^|[^\w$])_(?:\s*\.\s*[A-Za-z_$]|\s*\()|\blodash\s*\./

/**
 * 卡片是否需要 lodash：显式引用 lodash 的 `<script src>`，或脚本里直接用了
 * `_` / `lodash`。酒馆主界面自带 lodash 全局，卡内应用默认按它存在来写。
 */
export const needsLodash = (html: string): boolean => {
  LODASH_TAG_RE.lastIndex = 0
  if (LODASH_TAG_RE.test(html)) {
    return true
  }
  return LODASH_HINT_RE.test(html)
}

/** 去掉卡片自己的 lodash CDN 标签，避免与内置版本重复加载/联网 */
export const stripLodashTags = (html: string): string => {
  LODASH_TAG_RE.lastIndex = 0
  return html.replace(LODASH_TAG_RE, '')
}

/** 以内联脚本嵌入任意 JS 源码；`</script` 做防御性转义 */
const inlineScript = (source: string): string =>
  `<script>${source.replace(/<\/script/gi, '<\\/script')}</script>`

/**
 * 注入 iframe 的 shim：把酒馆侧常用 API 换成无副作用的本地实现。
 * 只给当前消息文本（整条消息的原文）与当前变量快照，不暴露其它用户数据。
 * 所有写入 API 都是只读桩：发宿主 toast，不改会话状态。
 *
 * 导出是为了用 `node:vm` 直接测试生成的脚本；正常调用经 `buildCardAppDoc`。
 */
export const buildCardAppShim = (
  message: string,
  variables?: Record<string, unknown> | null,
  character?: CardAppCharacter | null,
  capabilities?: CardAppCapabilities | null
): string => {
  const state = sanitizeCardAppVariables(variables)
  return `(function(){
  var MESSAGE = ${escapeForScript(message)};
  var VARIABLES = JSON.parse(${escapeForScript(state.serialized)});
  var CURRENT_MESSAGE = MESSAGE;
  var CHAR_NAME = ${escapeForScript(character?.name ?? '')};
  var CHAR_AVATAR = ${escapeForScript(character?.avatar ?? '')};
  var CAP_ACTIONS = ${capabilities?.actions === true};
  var CAP_WRITE = ${capabilities?.write === true};
  var CAP_NET = ${capabilities?.net === true};
  var RESIZE = ${escapeForScript(CARD_MSG_RESIZE)};
  var READY = ${escapeForScript(CARD_MSG_READY)};
  var VARS = ${escapeForScript(CARD_MSG_VARS)};
  var MESSAGE_EVENT = ${escapeForScript(CARD_MSG_MESSAGE)};
  var TOAST_EVENT = ${escapeForScript(CARD_MSG_TOAST)};
  var ERROR_EVENT = ${escapeForScript(CARD_MSG_ERROR)};
  var ACTION_EVENT = ${escapeForScript(CARD_MSG_ACTION)};
  var ACTION_RESULT_EVENT = ${escapeForScript(CARD_MSG_ACTION_RESULT)};
  var FETCH_EVENT = ${escapeForScript(CARD_MSG_FETCH)};
  var FETCH_RESULT_EVENT = ${escapeForScript(CARD_MSG_FETCH_RESULT)};
  var FETCH_ABORT_EVENT = ${escapeForScript(CARD_MSG_FETCH_ABORT)};
  var WRITE_CTX_EVENT = ${escapeForScript(CARD_MSG_WRITE_CTX)};
  var CODE_READ_ONLY = ${escapeForScript(CARD_CODE_READ_ONLY)};
  var CODE_NET_DENIED = ${escapeForScript(CARD_CODE_NET_DENIED)};
  var VERSION = ${CARD_APP_PROTOCOL_VERSION};
  var MAX_TOAST = ${MAX_CARD_APP_TOAST_LENGTH};
  var MAX_ERRORS = ${MAX_CARD_APP_ERRORS};
  var MAX_ERROR_LENGTH = ${MAX_CARD_APP_ERROR_LENGTH};
  var ACTION_TIMEOUT = 30000;
  var FETCH_TIMEOUT = 60000;
  // 沙箱 iframe 是 opaque origin：读取 window.parent 上的任何具名属性都会被浏览器
  // 判为跨源访问并抛 SecurityError，直接打断整段卡片脚本（常见于用
  // \`window.parent.getWorldbookNames\` 探测酒馆父窗口的写法）。这里先记住真正的
  // 宿主通道，再把 parent 指回自身，让探测落到本 shim 的同名 API 上；之后所有与
  // 宿主通信都必须走 HOST，不能再读 parent。
  //
  // top 只能尽力而为：Chrome 把 window.top 定义为不可配置的自有访问器，
  // defineProperty 会抛 TypeError（parent 则是可配置的），所以探测
  // \`window.top.xxx\` 的卡片仍可能抛 SecurityError——这是沙箱平台的限制。
  var HOST = window.parent || window;
  try { Object.defineProperty(window, 'parent', { value: window, configurable: true }); } catch (e) {}
  try { Object.defineProperty(window, 'top', { value: window, configurable: true }); } catch (e) {}
  // 宿主下发的写入读侧快照；未收到前世界书与角色字段为空
  var WORLD_NAME = '';
  var WORLD_ENTRIES = [];
  var CHARACTER_FIELDS = {};
  // 角色卡自带世界书名（CC extensions.world）；条目在 CHARACTER_FIELDS.table
  var CHAR_WORLD_NAME = '';
  var MVU_EVENTS = {
    VARIABLE_UPDATE_STARTED: 'mag_variable_update_started',
    VARIABLE_UPDATE_ENDED: 'mag_variable_update_ended',
    VARIABLE_UPDATE_AVAILABLE: 'mag_variable_update_available',
    COMMAND_PARSED: 'mag_command_parsed',
    COMMAND_EXECUTED: 'mag_command_executed'
  };
  function clone(value){
    try { return JSON.parse(JSON.stringify(value)); } catch (e) { return { stat_data: {} }; }
  }
  function memStorage(){
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
  try { Object.defineProperty(window, 'localStorage', { value: memStorage(), configurable: true }); } catch (e) {}
  try { Object.defineProperty(window, 'sessionStorage', { value: memStorage(), configurable: true }); } catch (e) {}

  var noop = function(){};
  function toast(level, text, code){
    try {
      HOST.postMessage({
        v: VERSION,
        type: TOAST_EVENT,
        level: level || 'info',
        text: text == null ? '' : String(text).slice(0, MAX_TOAST),
        code: code || ''
      }, '*');
    } catch (e) {}
  }
  function readOnly(){ toast('warning', '', CODE_READ_ONLY); }

  // 错误上报：去重 + 上限，宿主在工具栏显示错误角标而不是刷一屏 toast
  var errorCount = 0;
  var errorSeen = {};
  function describeError(value){
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'object' && typeof value.message === 'string') return value.message;
    try { return String(value); } catch (e) { return ''; }
  }
  function reportError(level, parts){
    if (errorCount >= MAX_ERRORS) return;
    var text = '';
    for (var i = 0; i < parts.length; i += 1) {
      var part = describeError(parts[i]);
      if (part) text += (text ? ' ' : '') + part;
    }
    text = text.slice(0, MAX_ERROR_LENGTH);
    var dedupeKey = '$' + text;
    if (!text || Object.prototype.hasOwnProperty.call(errorSeen, dedupeKey)) return;
    errorSeen[dedupeKey] = true;
    errorCount += 1;
    try {
      HOST.postMessage({ v: VERSION, type: ERROR_EVENT, level: level || 'error', text: text }, '*');
    } catch (e) {}
  }
  try {
    var hostConsoleError = console.error;
    console.error = function(){
      try { reportError('error', arguments); } catch (e) {}
      if (typeof hostConsoleError === 'function') {
        try { hostConsoleError.apply(console, arguments); } catch (e) {}
      }
    };
  } catch (e) {}
  try {
    window.addEventListener('error', function(e){
      reportError('error', [e && e.error, e && e.message]);
    });
    window.addEventListener('unhandledrejection', function(e){
      reportError('error', [e && e.reason]);
    });
  } catch (e) {}

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
    for (var i = 0; i < list.length; i += 1) {
      var item = list[i];
      if (item.once) off(key, item.fn);
      try { item.fn.apply(null, args); } catch (e) { try { console.error(e); } catch (ignore) {} }
    }
  }
  var eventSource = {
    on: function(event, handler){ on(event, handler); },
    once: function(event, handler){ on(event, handler, true); },
    off: function(event, handler){ off(event, handler); },
    removeListener: function(event, handler){ off(event, handler); },
    emit: emit
  };

  // 应用宿主下发的权威变量快照（只读路径；写入路径是乐观更新 + 宿主回发校正）
  function applyVariables(next){
    if (!next || typeof next !== 'object') return;
    var normalized = (next.stat_data && typeof next.stat_data === 'object')
      ? next
      : { stat_data: next };
    var text;
    try { text = JSON.stringify(normalized); } catch (e) { return; }
    if (typeof text !== 'string') return;
    var current;
    try { current = JSON.stringify(VARIABLES); } catch (e) { current = ''; }
    if (text === current) return;
    VARIABLES = clone(normalized);
    emit(MVU_EVENTS.VARIABLE_UPDATE_ENDED, clone(VARIABLES));
  }

  // 写入意图统一经宿主逐次确认：携带自增 id，宿主回结果时 resolve/reject，超时 30s。
  var actionSeq = 0;
  var pendingActions = {};
  function requestAction(intent){
    var id = 'a' + (++actionSeq);
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingActions[id];
        reject(new Error('flyai-card-action-timeout'));
      }, ACTION_TIMEOUT);
      pendingActions[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        HOST.postMessage({ v: VERSION, type: ACTION_EVENT, id: id, intent: intent }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingActions[id];
        reject(e);
      }
    });
  }
  function settleAction(data){
    if (!data || data.id == null) return;
    var item = pendingActions[data.id];
    if (!item) return;
    delete pendingActions[data.id];
    clearTimeout(item.timer);
    if (data.ok) { item.resolve(data.result); return; }
    item.reject(new Error(String(data.error || 'action-failed')));
  }
  function applyWriteContext(context){
    var data = context && typeof context === 'object' ? context : {};
    var worldbook = data.worldbook && typeof data.worldbook === 'object' ? data.worldbook : null;
    WORLD_NAME = worldbook && typeof worldbook.name === 'string' ? worldbook.name : '';
    WORLD_ENTRIES = worldbook && Array.isArray(worldbook.entries) ? clone(worldbook.entries) : [];
    CHAR_WORLD_NAME = typeof data.characterWorldName === 'string' ? data.characterWorldName : '';
    CHARACTER_FIELDS = data.character && typeof data.character === 'object'
      ? clone(data.character)
      : {};
  }
  function jsonPointer(key){
    return '/' + String(key).replace(/~/g, '~0').replace(/\\//g, '~1');
  }
  function normalizeVariables(next){
    if (!next || typeof next !== 'object') return null;
    return (next.stat_data && typeof next.stat_data === 'object')
      ? clone(next)
      : { stat_data: clone(next) };
  }
  function statDataOf(state){
    var stat = state && state.stat_data;
    return stat && typeof stat === 'object' ? stat : {};
  }
  function requestVars(mode, payload, optimistic){
    if (optimistic) applyVariables(optimistic);
    return requestAction(
      mode === 'replace'
        ? { kind: 'vars', mode: 'replace', stat_data: payload }
        : { kind: 'vars', mode: 'patch', ops: payload }
    );
  }
  function writeChatVariable(key, value){
    var name = String(key);
    var state = clone(VARIABLES);
    var stat = statDataOf(state);
    stat[name] = clone(value);
    state.stat_data = stat;
    requestVars('patch', [{ op: 'add', path: jsonPointer(name), value: value }], state);
    return true;
  }
  function deleteChatVariable(key){
    var name = String(key);
    var state = clone(VARIABLES);
    var stat = statDataOf(state);
    if (!Object.prototype.hasOwnProperty.call(stat, name)) return true;
    delete stat[name];
    state.stat_data = stat;
    requestVars('patch', [{ op: 'remove', path: jsonPointer(name) }], state);
    return true;
  }
  function replaceChatVariables(next){
    var normalized = normalizeVariables(next);
    if (!normalized) return false;
    requestVars('replace', statDataOf(normalized), normalized);
    return true;
  }
  function mergeChatVariables(next, overwrite){
    var normalized = normalizeVariables(next);
    if (!normalized) return false;
    var state = clone(VARIABLES);
    var stat = statDataOf(state);
    var incoming = statDataOf(normalized);
    var keys = Object.keys(incoming);
    for (var i = 0; i < keys.length; i += 1) {
      if (overwrite || !Object.prototype.hasOwnProperty.call(stat, keys[i])) {
        stat[keys[i]] = incoming[keys[i]];
      }
    }
    state.stat_data = stat;
    requestVars('replace', clone(stat), state);
    return true;
  }
  function readonlyPromise(){ readOnly(); return Promise.resolve(undefined); }
  function readonlyReject(){
    readOnly();
    return Promise.reject(new Error('flyai-card-read-only'));
  }

  function messageIdOf(id){
    if (typeof id === 'number') return id;
    if (Array.isArray(id) && id.length) return messageIdOf(id[0]);
    if (typeof id === 'string') { var m = id.match(/\\d+/); if (m) return Number(m[0]); }
    return 0;
  }
  function chatMessage(id){
    return {
      message_id: messageIdOf(id),
      message: CURRENT_MESSAGE,
      name: CHAR_NAME,
      is_user: false,
      is_system: false,
      swipes: [CURRENT_MESSAGE],
      extra: { variables: clone(VARIABLES) }
    };
  }
  function getChatMessages(id){
    if (Array.isArray(id)) {
      var list = [];
      for (var i = 0; i < id.length && i < 50; i += 1) list.push(chatMessage(id[i]));
      return list;
    }
    return [chatMessage(id)];
  }
  function chatVariableValue(key){
    var name = String(key);
    if (Object.prototype.hasOwnProperty.call(VARIABLES, name)) return VARIABLES[name];
    var stat = VARIABLES.stat_data;
    if (stat && typeof stat === 'object' &&
        Object.prototype.hasOwnProperty.call(stat, name)) return stat[name];
    return undefined;
  }
  window.getChatMessages = getChatMessages;
  window.getCurrentMessageId = function(){ return 0; };
  window.getLastMessageId = function(){ return 0; };
  window.getLastMessage = function(){ return CURRENT_MESSAGE; };
  window.getAllVariables = function(){ return clone(VARIABLES); };
  window.getVariables = function(options){
    var type = options && options.type ? String(options.type) : 'chat';
    if (type === 'chat' || type === 'message') return clone(VARIABLES);
    return {};
  };
  window.getChatVariable = function(key){ return chatVariableValue(key); };
  window.setChatVariable = CAP_WRITE
    ? function(key, value){ return writeChatVariable(key, value); }
    : function(){ readOnly(); return false; };
  window.setVariables = CAP_WRITE
    ? function(next){ return replaceChatVariables(next); }
    : function(){ readOnly(); return false; };
  window.insertVariables = CAP_WRITE
    ? function(next){ return mergeChatVariables(next, false); }
    : function(){ readOnly(); return false; };
  window.insertOrAssignVariables = CAP_WRITE
    ? function(next){ return mergeChatVariables(next, true); }
    : function(){ readOnly(); return false; };
  window.deleteVariable = CAP_WRITE
    ? function(key){ return deleteChatVariable(key); }
    : function(){ readOnly(); return false; };
  window.getGlobalVariables = function(){ return {}; };
  window.setGlobalVariable = function(){ readOnly(); return false; };

  // 世界书：卡片看到的是「角色卡自带世界书 + 会话挂载世界书」的合并视图。
  // 角色卡的书（CC character_book）随角色副本下发在 CHARACTER_FIELDS.table；
  // 名称取自 extensions.world。读接口无视卡片请求的名字，避免卡内写死的书名
  // 与导入后的实际书名（如带 "(1)" 后缀）不一致时整段初始化被跳过。
  // 能力关闭时读接口返回空表，写接口提示只读并安全完成 Promise，避免卡片
  // 链式写入时抛未处理拒绝。
  function cloneWorldEntries(){ return clone(WORLD_ENTRIES); }
  function charWorldEntries(){
    return CHARACTER_FIELDS && Array.isArray(CHARACTER_FIELDS.table)
      ? clone(CHARACTER_FIELDS.table)
      : [];
  }
  function entryKey(entry){
    if (!entry || typeof entry !== 'object') return String(entry);
    return String(entry.title || '') + '\u0000' + String(entry.content || '');
  }
  function mergedWorldEntries(){
    var out = [];
    var seen = {};
    function add(entry){
      var key = entryKey(entry);
      if (seen[key]) return;
      seen[key] = true;
      out.push(clone(entry));
    }
    var table = charWorldEntries();
    var i;
    for (i = 0; i < table.length; i += 1) add(table[i]);
    for (i = 0; i < WORLD_ENTRIES.length; i += 1) add(WORLD_ENTRIES[i]);
    return out;
  }
  function worldbookNames(){
    var names = [];
    if (CHAR_WORLD_NAME) names.push(CHAR_WORLD_NAME);
    if (WORLD_NAME && names.indexOf(WORLD_NAME) < 0) names.push(WORLD_NAME);
    return names;
  }
  function sendWorld(entries){
    var list = Array.isArray(entries) ? clone(entries) : [];
    if (WORLD_NAME) {
      WORLD_ENTRIES = list;
      return requestAction({ kind: 'world', entries: list }).then(function(result){
        if (result && Array.isArray(result.entries)) {
          WORLD_ENTRIES = clone(result.entries);
        }
        return cloneWorldEntries();
      });
    }
    // 没有挂载世界书时，把角色卡自带的书写回会话角色副本的 table；
    // 根卡/公共卡永不改动，后端只会 fork 到会话副本。
    CHARACTER_FIELDS.table = clone(list);
    return requestAction({ kind: 'character', fields: { table: clone(list) } })
      .then(function(){ return clone(list); });
  }
  function updateWorld(name, updater){
    var current = mergedWorldEntries();
    var next = current;
    if (typeof updater === 'function') {
      try { next = updater(current); } catch (e) { return Promise.reject(e); }
    }
    return sendWorld(next);
  }
  // SillyTavern 的这些接口是同步的（返回数组/对象），卡片会直接 names.includes(...)、
  // [...names] 使用；这里必须同样同步返回，不能包 Promise。
  window.getWorldbookNames = function(){
    return CAP_WRITE ? worldbookNames() : [];
  };
  window.getCharWorldbookNames = function(){
    if (!CAP_WRITE) return { primary: null, additional: [] };
    return { primary: CHAR_WORLD_NAME || WORLD_NAME || null, additional: [] };
  };
  window.getGlobalWorldbookNames = function(){ return []; };
  window.getWorldbook = function(name){
    // 无视卡片传入的书名：直接返回角色卡世界书 + 挂载世界书的合并视图
    if (!CAP_WRITE) return [];
    return mergedWorldEntries();
  };
  window.updateWorldbookWith = CAP_WRITE
    ? function(name, updater){ return updateWorld(name, updater); }
    : function(){ readOnly(); return Promise.resolve(undefined); };
  window.updateWorldbook = CAP_WRITE
    ? function(name, updater){ return updateWorld(name, updater); }
    : function(){ readOnly(); return Promise.resolve(undefined); };
  window.replaceWorldbook = CAP_WRITE
    ? function(first, second){
        return sendWorld(Array.isArray(first) ? first : second);
      }
    : function(){ readOnly(); return Promise.resolve(undefined); };
  var WORLD_READ_ONLY_APIS = ['deleteWorldbook', 'createWorldbook', 'rebindCharWorldbooks'];
  for (var wi = 0; wi < WORLD_READ_ONLY_APIS.length; wi += 1) {
    (function(name){
      window[name] = function(){ readOnly(); return Promise.resolve(undefined); };
    })(WORLD_READ_ONLY_APIS[wi]);
  }

  window.getCharName = function(){ return CHAR_NAME; };
  window.getCurrentCharName = window.getCharName;
  window.getCharAvatar = function(){ return CHAR_AVATAR; };

  var MUTATING_SLASH = /^\\s*\\/(setvar|setglobalvar|addvar|incvar|decvar|delvar|send|run|trigger|generate|gen|continue|regenerate|sys|user|assistant|comment)\\b/i;
  function parseSlashValue(text){
    if (text === '') return '';
    try { return JSON.parse(text); } catch (e) { return text; }
  }
  function slashVariableKey(rest){
    var eq = rest.indexOf('=');
    return (eq >= 0 ? rest.slice(0, eq) : rest).replace(/^\\s+|\\s+$/g, '');
  }
  function slashSetVar(rest){
    var eq = rest.indexOf('=');
    var key = slashVariableKey(rest);
    if (!key) return;
    writeChatVariable(key, eq >= 0 ? parseSlashValue(rest.slice(eq + 1).replace(/^\\s+/, '')) : '');
  }
  function slashNumberVar(cmd, rest){
    var eq = rest.indexOf('=');
    var key = slashVariableKey(rest);
    if (!key) return;
    var amount = cmd === 'incvar' && eq < 0
      ? 1
      : Number(parseSlashValue(eq >= 0 ? rest.slice(eq + 1).replace(/^\\s+/, '') : '1'));
    if (typeof amount !== 'number' || !isFinite(amount)) amount = 1;
    var current = Number(chatVariableValue(key));
    if (!isFinite(current)) current = 0;
    writeChatVariable(key, cmd === 'decvar' ? current - amount : current + amount);
  }
  // SillyTavern 的斜杠管线：多条命令用 | 串联，反斜杠 + | 表示字面竖线。
  // 卡常写 triggerSlash('/send 文本|/trigger') 表示「发送后再触发快捷回复」；
  // 不拆链的话 |/trigger 会整段混进发送文本，模型就收到这条尾巴。
  function splitSlashChain(text){
    var parts = [];
    var current = '';
    for (var i = 0; i < text.length; i += 1) {
      var ch = text.charAt(i);
      if (ch === '\\\\' && text.charAt(i + 1) === '|') {
        current += '|';
        i += 1;
      } else if (ch === '|') {
        parts.push(current);
        current = '';
      } else {
        current += ch;
      }
    }
    parts.push(current);
    return parts;
  }
  // 返回 true 表示已识别（含不支持但无副作用的命令），false 表示未识别
  function runSlashCommand(text){
    var match = /^\\s*\\/([A-Za-z_]+)\\b([\\s\\S]*)$/.exec(text);
    if (!match) return false;
    var name = match[1].toLowerCase();
    var rest = match[2].replace(/^\\s+/, '');
    if (name === 'send') {
      if (!CAP_ACTIONS) return false;
      requestAction({ kind: 'action', action: 'send', text: rest });
      return true;
    }
    if (name === 'continue' || name === 'cont') {
      if (!CAP_ACTIONS) return false;
      requestAction({ kind: 'action', action: 'continue' });
      return true;
    }
    if (name === 'regenerate' || name === 'regen') {
      if (!CAP_ACTIONS) return false;
      requestAction({ kind: 'action', action: 'regenerate' });
      return true;
    }
    if (name === 'setvar' || name === 'setchatvar') {
      if (!CAP_WRITE) return false;
      slashSetVar(rest);
      return true;
    }
    if (name === 'delvar' || name === 'delchatvar') {
      if (!CAP_WRITE) return false;
      deleteChatVariable(slashVariableKey(rest));
      return true;
    }
    if (name === 'addvar' || name === 'incvar' || name === 'decvar') {
      if (!CAP_WRITE) return false;
      slashNumberVar(name, rest);
      return true;
    }
    // 快捷回复系统未移植：/trigger 在 ST 里只负责触发 QR，这里静默忽略
    if (name === 'trigger') return true;
    return false;
  }
  window.triggerSlash = function(command){
    var text = String(command == null ? '' : command);
    var parts = splitSlashChain(text);
    for (var i = 0; i < parts.length; i += 1) {
      var segment = parts[i].replace(/^\\s+/, '');
      if (!segment) continue;
      if (!runSlashCommand(segment) && MUTATING_SLASH.test(segment)) readOnly();
    }
    return '';
  };
  window.substituteParams = function(value){
    if (typeof value !== 'string') return value;
    return value.replace(/\\{\\{([^{}]+)\\}\\}/g, function(all, expr){
      var body = String(expr).replace(/^\\s+|\\s+$/g, '');
      var lower = body.toLowerCase();
      if (lower === 'char' || lower === 'charname') return CHAR_NAME;
      if (lower === 'user' || lower === 'username') return '';
      if (lower === 'lastmessage') return CURRENT_MESSAGE;
      if (lower === 'charavatar' || lower === 'char_avatar') return CHAR_AVATAR;
      if (lower.indexOf('getvar::') === 0) {
        var local = chatVariableValue(body.slice(8).replace(/^\\s+|\\s+$/g, ''));
        return local == null ? '' : String(local);
      }
      if (lower.indexOf('getglobalvar::') === 0) return '';
      return all;
    });
  };
  // SillyTavern 里拼作 substitudeMacros（历史拼写），卡片按此名探测
  window.substitudeMacros = window.substituteParams;
  window.eventOn = function(event, handler){ on(event, handler); };
  window.eventOnce = function(event, handler){ on(event, handler, true); };
  window.eventOff = function(event, handler){ off(event, handler); };
  window.eventEmit = emit;
  window.eventRemoveListener = function(event, handler){ off(event, handler); };
  window.toastr = {
    info: function(m){ toast('info', m); },
    success: function(m){ toast('success', m); },
    warning: function(m){ toast('warning', m); },
    error: function(m){ toast('error', m); },
    clear: noop
  };
  try { window.alert = function(message){ toast('info', message); }; } catch (e) {}
  try { window.confirm = function(){ return false; }; } catch (e) {}
  try { window.prompt = function(){ return null; }; } catch (e) {}
  window.isEditMode = false;

  window.waitGlobalInitialized = function(name){
    var key = String(name);
    return new Promise(function(resolve){
      if (typeof window[key] !== 'undefined') { resolve(); return; }
      var started = Date.now();
      var timer = setInterval(function(){
        if (typeof window[key] !== 'undefined' || Date.now() - started >= 10000) {
          clearInterval(timer);
          resolve();
        }
      }, 50);
    });
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

  window.Mvu = {
    events: MVU_EVENTS,
    getMvuData: function(){
      var stat = VARIABLES && VARIABLES.stat_data;
      return {
        stat_data: clone(stat && typeof stat === 'object' ? stat : {}),
        display_data: {},
        delta_data: {}
      };
    },
    replaceMvuData: CAP_WRITE
      ? function(data){
          var normalized = normalizeVariables(data) || { stat_data: {} };
          return requestVars('replace', statDataOf(normalized), normalized);
        }
      : function(){
          readOnly();
          return Promise.reject(new Error('flyai-card-read-only'));
        },
    setMvuData: CAP_WRITE
      ? function(data){
          var normalized = normalizeVariables(data);
          if (!normalized) return false;
          requestVars('replace', statDataOf(normalized), normalized);
          return true;
        }
      : function(){
          readOnly();
          return false;
        }
  };

  // FlyAI 扩展：角色副本白名单字段写回；能力关闭时提示只读。
  window.FlyAI = window.FlyAI || {};
  window.FlyAI.capabilities = {
    actions: CAP_ACTIONS,
    write: CAP_WRITE,
    net: CAP_NET
  };
  window.FlyAI.updateCharacter = CAP_WRITE
    ? function(fields){
        if (!fields || typeof fields !== 'object') return Promise.reject(new Error('flyai-card-invalid-fields'));
        return requestAction({ kind: 'character', fields: clone(fields) });
      }
    : function(){ return readonlyReject(); };
  window.FlyAI.getCharacterFields = function(){ return clone(CHARACTER_FIELDS); };
  window.FlyAI.getChatContext = function(){
    return { message: CURRENT_MESSAGE, variables: clone(VARIABLES) };
  };

  window.SillyTavern = window.SillyTavern || {};
  window.SillyTavern.getContext = function(){
    return {
      variables: clone(VARIABLES),
      chat: [],
      chatMetadata: {},
      characters: (CHAR_NAME || CHAR_AVATAR)
        ? [{
            name: CHAR_NAME,
            avatar: CHAR_AVATAR,
            description: '',
            personality: '',
            scenario: '',
            first_mes: ''
          }]
        : [],
      characterId: 0,
      name1: '',
      name2: '',
      groups: [],
      eventSource: eventSource,
      getCurrentMessageId: function(){ return 0; }
    };
  };

  var helper = window.TavernHelper || window.tavernHelper || {};
  helper.getChatMessages = getChatMessages;
  helper.getCurrentMessageId = window.getCurrentMessageId;
  helper.getLastMessageId = window.getLastMessageId;
  helper.getAllVariables = window.getAllVariables;
  helper.getVariables = window.getVariables;
  helper.getChatVariable = window.getChatVariable;
  helper.setChatVariable = window.setChatVariable;
  helper.getGlobalVariables = window.getGlobalVariables;
  helper.setGlobalVariable = window.setGlobalVariable;
  helper.triggerSlash = window.triggerSlash;
  helper.substituteParams = window.substituteParams;
  helper.eventOn = window.eventOn;
  helper.eventOnce = window.eventOnce;
  helper.eventOff = window.eventOff;
  helper.eventEmit = window.eventEmit;
  helper.eventRemoveListener = window.eventRemoveListener;
  var HELPER_SHARED = [
    'getLastMessage', 'setVariables', 'insertVariables', 'insertOrAssignVariables',
    'deleteVariable', 'getWorldbookNames', 'getCharWorldbookNames',
    'getGlobalWorldbookNames', 'getWorldbook', 'updateWorldbookWith',
    'replaceWorldbook', 'getCharName', 'getCurrentCharName', 'getCharAvatar'
  ];
  for (var hi = 0; hi < HELPER_SHARED.length; hi += 1) {
    var helperName = HELPER_SHARED[hi];
    if (typeof window[helperName] === 'function') helper[helperName] = window[helperName];
  }
  window.TavernHelper = helper;
  window.tavernHelper = helper;

  // 外链统一交给新窗口，避免 iframe 自己被导航走
  document.addEventListener('click', function(e){
    var target = e.target;
    var a = target && target.closest ? target.closest('a[href]') : null;
    if (!a) return;
    var href = a.getAttribute('href') || '';
    if (/^https?:/i.test(href)) {
      e.preventDefault();
      try { window.open(href, '_blank', 'noopener,noreferrer'); } catch (err) {}
    }
  }, true);

  // 受控网络：只在宿主授予 net 能力时替换 fetch/XHR，所有请求经宿主逐跳校验后代理。
  // CSP 仍是 connect-src 'none'，即使 shim 被盗用也不会真正直连外网。
  var settleFetch = null;
  if (CAP_NET) {
    var fetchSeq = 0;
    var pendingFetches = {};
    function abortError(){
      try { return new DOMException('The operation was aborted.', 'AbortError'); }
      catch (e) {
        var err = new Error('The operation was aborted.');
        err.name = 'AbortError';
        return err;
      }
    }
    function bytesToBase64(bytes){
      var text = '';
      for (var i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i]);
      return btoa(text);
    }
    function base64ToBytes(value){
      var binary = atob(String(value || ''));
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      return bytes;
    }
    function encodeBody(body){
      if (body == null) return '';
      if (typeof body === 'string') return bytesToBase64(new TextEncoder().encode(body));
      if (body instanceof ArrayBuffer) return bytesToBase64(new Uint8Array(body));
      if (typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView(body)) {
        return bytesToBase64(new Uint8Array(body.buffer, body.byteOffset, body.byteLength));
      }
      throw new TypeError('Unsupported request body');
    }
    function pickHeaders(headers){
      var out = {};
      if (!headers) return out;
      function add(name, value){
        var lower = String(name).toLowerCase();
        if (lower === 'accept' || lower === 'content-type') out[lower] = String(value);
      }
      try {
        if (typeof Headers !== 'undefined' && headers instanceof Headers) {
          headers.forEach(function(value, name){ add(name, value); });
        } else if (Array.isArray(headers)) {
          for (var i = 0; i < headers.length; i += 1) {
            if (headers[i]) add(headers[i][0], headers[i][1]);
          }
        } else if (typeof headers === 'object') {
          for (var name in headers) {
            if (Object.prototype.hasOwnProperty.call(headers, name)) add(name, headers[name]);
          }
        }
      } catch (e) {}
      return out;
    }
    function postAbort(id){
      try { HOST.postMessage({ v: VERSION, type: FETCH_ABORT_EVENT, id: id }, '*'); } catch (e) {}
    }
    function requestFetch(method, url, headers, bodyBase64, signal){
      var id = 'f' + (++fetchSeq);
      return new Promise(function(resolve, reject){
        if (signal && signal.aborted) { reject(abortError()); return; }
        var done = false;
        function cleanup(){
          clearTimeout(timer);
          if (signal) { try { signal.removeEventListener('abort', onAbort); } catch (e) {} }
          delete pendingFetches[id];
        }
        var timer = setTimeout(function(){
          if (done) return;
          done = true;
          cleanup();
          postAbort(id);
          reject(abortError());
        }, FETCH_TIMEOUT);
        function onAbort(){
          if (done) return;
          done = true;
          cleanup();
          postAbort(id);
          reject(abortError());
        }
        if (signal) { try { signal.addEventListener('abort', onAbort); } catch (e) {} }
        pendingFetches[id] = {
          resolve: function(value){
            if (done) return;
            done = true;
            cleanup();
            resolve(value);
          },
          reject: function(err){
            if (done) return;
            done = true;
            cleanup();
            reject(err);
          }
        };
        try {
          HOST.postMessage({
            v: VERSION,
            type: FETCH_EVENT,
            id: id,
            request: { method: method, url: url, headers: headers, bodyBase64: bodyBase64 }
          }, '*');
        } catch (e) {
          pendingFetches[id].reject(new TypeError('Failed to fetch'));
        }
      });
    }
    function dispatchFetch(method, url, headers, bodyBase64, signal){
      method = String(method || 'GET').toUpperCase();
      if (method !== 'GET' && method !== 'HEAD' && method !== 'POST') {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      if (!/^https?:\\/\\//i.test(String(url))) {
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      return requestFetch(method, String(url), headers || {}, bodyBase64 || '', signal);
    }
    settleFetch = function(data){
      if (!data || data.id == null) return;
      var item = pendingFetches[data.id];
      if (!item) return;
      if (!data.ok) {
        var err = new TypeError('Failed to fetch');
        if (data.error === CODE_NET_DENIED) {
          try { err.flyaiCode = CODE_NET_DENIED; } catch (e) {}
        }
        item.reject(err);
        return;
      }
      var status = Number(data.status);
      if (!isFinite(status) || status < 200 || status > 599) {
        item.reject(new TypeError('Failed to fetch'));
        return;
      }
      var headers = new Headers();
      var extra = data.headers || {};
      for (var name in extra) {
        if (!Object.prototype.hasOwnProperty.call(extra, name)) continue;
        try { headers.set(name, String(extra[name])); } catch (e) {}
      }
      var body = null;
      if (status !== 204 && status !== 205 && status !== 304) {
        try { body = base64ToBytes(data.bodyBase64 || ''); } catch (e) { body = new Uint8Array(0); }
      }
      try {
        item.resolve(new Response(body, { status: status, headers: headers }));
      } catch (e) {
        item.reject(new TypeError('Failed to fetch'));
      }
    };
    window.fetch = function(input, init){
      var options = init || {};
      if (typeof Request !== 'undefined' && input instanceof Request) {
        return input.clone().arrayBuffer().then(function(buffer){
          return dispatchFetch(
            options.method || input.method,
            input.url,
            pickHeaders(options.headers || input.headers),
            bytesToBase64(new Uint8Array(buffer)),
            options.signal
          );
        }, function(){ return Promise.reject(new TypeError('Failed to fetch')); });
      }
      var url = typeof input === 'string' ? input : String(input);
      var method = String(options.method || 'GET').toUpperCase();
      var body = '';
      if (method !== 'GET' && method !== 'HEAD' && options.body != null) {
        try { body = encodeBody(options.body); } catch (err) { return Promise.reject(err); }
      }
      return dispatchFetch(method, url, pickHeaders(options.headers), body, options.signal);
    };
    function CardXHR(){
      this.readyState = 0;
      this.status = 0;
      this.statusText = '';
      this.responseType = '';
      this.response = null;
      this.responseText = '';
      this.onreadystatechange = null;
      this.onload = null;
      this.onerror = null;
      this.onabort = null;
      this.ontimeout = null;
      this.timeout = 0;
      this.withCredentials = false;
      this._method = 'GET';
      this._url = '';
      this._headers = {};
      this._responseHeaders = {};
      this._listeners = {};
      this._bridge = null;
    }
    CardXHR.prototype.open = function(method, url){
      this._method = String(method || 'GET');
      this._url = String(url || '');
      this.readyState = 1;
      this._fire('readystatechange');
    };
    CardXHR.prototype.setRequestHeader = function(name, value){
      if (name) this._headers[String(name)] = String(value);
    };
    CardXHR.prototype.getResponseHeader = function(name){
      var lower = String(name).toLowerCase();
      return Object.prototype.hasOwnProperty.call(this._responseHeaders, lower)
        ? this._responseHeaders[lower]
        : null;
    };
    CardXHR.prototype.getAllResponseHeaders = function(){
      var out = '';
      for (var name in this._responseHeaders) {
        if (Object.prototype.hasOwnProperty.call(this._responseHeaders, name)) {
          out += name + ': ' + this._responseHeaders[name] + '\\r\\n';
        }
      }
      return out;
    };
    CardXHR.prototype.addEventListener = function(type, fn){
      var key = String(type);
      (this._listeners[key] = this._listeners[key] || []).push(fn);
    };
    CardXHR.prototype.removeEventListener = function(type, fn){
      var key = String(type);
      var list = this._listeners[key] || [];
      var next = [];
      for (var i = 0; i < list.length; i += 1) if (list[i] !== fn) next.push(list[i]);
      this._listeners[key] = next;
    };
    CardXHR.prototype._fire = function(type, arg){
      var handler = this['on' + type];
      if (typeof handler === 'function') {
        try { handler.call(this, arg); } catch (e) { try { console.error(e); } catch (ignore) {} }
      }
      var list = (this._listeners[type] || []).slice();
      for (var i = 0; i < list.length; i += 1) {
        try { list[i].call(this, arg); } catch (e) { try { console.error(e); } catch (ignore) {} }
      }
    };
    CardXHR.prototype._fail = function(){
      this.readyState = 4;
      this.status = 0;
      this._fire('readystatechange');
      this._fire('error');
    };
    CardXHR.prototype.send = function(body){
      var self = this;
      if (this.readyState !== 1) return;
      var method = this._method.toUpperCase();
      var encoded = '';
      if (method !== 'GET' && method !== 'HEAD' && body != null) {
        try { encoded = encodeBody(body); } catch (e) { this._fail(); return; }
      }
      var bridge = new AbortController();
      this._bridge = bridge;
      requestFetch(method, this._url, pickHeaders(this._headers), encoded, bridge.signal).then(function(response){
        self.status = response.status;
        self.statusText = response.statusText || '';
        self._responseHeaders = {};
        try {
          response.headers.forEach(function(value, name){
            self._responseHeaders[String(name).toLowerCase()] = value;
          });
        } catch (e) {}
        self.readyState = 2;
        self._fire('readystatechange');
        return response.arrayBuffer().then(function(buffer){
          var bytes = new Uint8Array(buffer);
          if (self.responseType === 'arraybuffer') {
            self.response = buffer;
          } else if (self.responseType === 'blob') {
            self.response = new Blob([buffer]);
          } else {
            self.response = new TextDecoder().decode(bytes);
            self.responseText = self.response;
          }
          self.readyState = 4;
          self._fire('readystatechange');
          self._fire('load');
        });
      }).catch(function(err){
        if (err && err.name === 'AbortError') { self._fire('abort'); return; }
        self._fail();
      });
    };
    CardXHR.prototype.abort = function(){
      if (this._bridge) { try { this._bridge.abort(); } catch (e) {} }
      this.readyState = 0;
    };
    window.XMLHttpRequest = CardXHR;
  }

  // 只接受真实父窗口的合法消息；变量未变化时忽略（ready/load 重放去重）。
  window.addEventListener('message', function(e){
    if (!e || e.source !== HOST) return;
    var data = e.data;
    if (!data || typeof data !== 'object' || data.v !== VERSION) return;
    if (data.type === VARS) { applyVariables(data.variables); return; }
    if (data.type === MESSAGE_EVENT) {
      CURRENT_MESSAGE = data.message == null ? '' : String(data.message);
      return;
    }
    if (data.type === ACTION_RESULT_EVENT) { settleAction(data); return; }
    if (data.type === WRITE_CTX_EVENT) { applyWriteContext(data.context); return; }
    if (settleFetch && data.type === FETCH_RESULT_EVENT) { settleFetch(data); return; }
  });

  function report(){
    try {
      // 不能用文档根节点的 scrollHeight：它至少等于 iframe 视口高度，
      // 一旦宿主把 iframe 撑高（如展开面板），内容收起后永远报不回小值。
      var b = document.body;
      var h = Math.max(
        b ? b.scrollHeight : 0,
        b ? b.offsetHeight : 0,
        b ? Math.ceil(b.getBoundingClientRect().height) : 0
      );
      HOST.postMessage({ type: RESIZE, height: h }, '*');
    } catch (e) {}
  }
  function ready(){
    try { HOST.postMessage({ v: VERSION, type: READY }, '*'); } catch (e) {}
  }
  function setup(){
    report();
    if (window.ResizeObserver) {
      try { new ResizeObserver(report).observe(document.body); } catch (e) {}
    }
    ready();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
  window.addEventListener('load', function(){ report(); ready(); });
  var ticks = 0;
  var timer = setInterval(function(){ report(); if (++ticks > 10) clearInterval(timer); }, 500);
})();`
}

export type CardAppDocOptions = {
  /** 当前消息原文（卡内脚本用 getChatMessages 读取） */
  message: string
  /** 当前变量快照（通常是消息的 stat_data），shim 只读暴露 */
  variables?: Record<string, unknown> | null
  /** 允许图片/音频直连外网（与 st_external_images 开关一致） */
  externalMedia?: boolean
  /** 角色信息：shim 暴露名称，头像源加入 CSP img-src */
  character?: CardAppCharacter | null
  /** 用户授权能力；缺省全部关闭，保持只读 shim */
  capabilities?: CardAppCapabilities | null
}

/**
 * 组装 iframe 的 srcDoc：注入 CSP 与 shim，并保证 shim 先于卡内脚本执行。
 * 卡片引用 jQuery / lodash 时先内联内置版本，再移除卡片自己的 CDN 标签。
 * 缺 `<html>` 的片段会被补成完整文档。
 */
export const buildCardAppDoc = (
  html: string,
  options: CardAppDocOptions
): string => {
  let doc = stripAppFence(html).trim()
  if (!doc) {
    return ''
  }
  const withJQuery = needsJQuery(doc)
  if (withJQuery) {
    doc = stripJQueryTags(doc)
  }
  const withLodash = needsLodash(doc)
  if (withLodash) {
    doc = stripLodashTags(doc)
  }
  if (!/<html[\s>]/i.test(doc)) {
    doc = `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>${doc}</body></html>`
  }
  const avatar = options.character?.avatar || ''
  const avatarOrigin = avatar ? normalizeMediaOrigin(avatar) : null
  const inject =
    `<meta http-equiv="Content-Security-Policy" content="${contentSecurityPolicy(
      options.externalMedia === true,
      avatarOrigin ? [avatarOrigin] : []
    )}">` +
    (withJQuery ? inlineScript(JQUERY_SOURCE) : '') +
    // lodash 全局 `_`：包在函数作用域里，避免卡内声明同名变量时冲突；
    // 卡片自己已经定义了 `_` 时不覆盖。
    (withLodash
      ? inlineScript(
          `(function(){${LODASH_SOURCE}\n` +
            `if (typeof window._ === 'undefined') window._ = __lodashVendor;})();`
        )
      : '') +
    `<script>${buildCardAppShim(
      options.message,
      options.variables,
      options.character,
      options.capabilities
    )}</script>`

  const head = /<head\b[^>]*>/i.exec(doc)
  if (head) {
    const at = head.index + head[0].length
    return doc.slice(0, at) + inject + doc.slice(at)
  }
  const root = /<html\b[^>]*>/i.exec(doc)
  if (root) {
    const at = root.index + root[0].length
    return doc.slice(0, at) + `<head>${inject}</head>` + doc.slice(at)
  }
  return inject + doc
}
