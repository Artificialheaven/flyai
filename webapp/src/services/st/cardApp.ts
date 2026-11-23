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
import ST_CORE_SOURCE from './compat/stCore.js?raw'
import { buildEjsTemplateSource } from './native/ejsRuntime.js'
import {
  buildNativeImportMapEntries,
  ST_NATIVE_SNAPSHOT_SOURCE,
} from './native/stNative.js'

/** 正则引擎在替换结果外层加的哨兵；U+0000 不会出现在正常文本里 */
export const APP_START = '\u0000FLYAI_APP_START\u0000'
export const APP_END = '\u0000FLYAI_APP_END\u0000'

/** 抽离后写进 markdown 的占位符（渲染前再换成 cardApp 节点） */
export const APP_PLACEHOLDER_PREFIX = '@@FLYAI_CARD_APP_'
export const APP_PLACEHOLDER_RE = /@@FLYAI_CARD_APP_(\d+)@@/g

/** 单份卡内应用上限；超限仍按普通代码块/清洗 HTML 处理。
 *  实测卡（约会大作战开场建档页）约 903KB，512KB 会把整页应用降级成代码块，
 *  因此放宽到 2MB，与正则输出上限保持一致。 */
export const MAX_CARD_APP_LENGTH = 2 * 1024 * 1024

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
/** 宿主 → 卡：当前会话的输入框草稿（酒馆 #send_textarea 兼容层） */
export const CARD_MSG_DRAFT = 'flyai-card-draft'
/** 卡 → 宿主：卡内脚本往酒馆输入框写入的文本（不直接发送） */
export const CARD_MSG_COMPOSE = 'flyai-card-compose'
/** 宿主 → 卡：完整聊天快照（楼层检测、createChatMessages / getChatHistoryDetail） */
export const CARD_MSG_CHAT = 'flyai-card-chat'
/** 宿主 → 卡：chatMetadata 快照（地图/手札存档） */
export const CARD_MSG_METADATA = 'flyai-card-metadata'
/** 卡 → 宿主：保存 chatMetadata */
export const CARD_MSG_METADATA_SET = 'flyai-card-metadata-set'
/** 宿主 → 卡：chatMetadata 保存结果 */
export const CARD_MSG_METADATA_RESULT = 'flyai-card-metadata-result'
/** 卡 → 宿主：按消息 id 写回正文/隐藏/变量 */
export const CARD_MSG_MESSAGES_PATCH = 'flyai-card-messages-patch'
/** 宿主 → 卡：消息写回结果 */
export const CARD_MSG_MESSAGES_RESULT = 'flyai-card-messages-result'
/** 卡 → 宿主：主聊天生成（context.generate，可带 quiet_prompt） */
export const CARD_MSG_GENERATE = 'flyai-card-generate'
/** 宿主 → 卡：主聊天生成结果 */
export const CARD_MSG_GENERATE_RESULT = 'flyai-card-generate-result'
/** 卡 → 宿主：按显式 messages 静默生成（ST chat-completions 兼容） */
export const CARD_MSG_GENERATE_TEXT = 'flyai-card-generate-text'
/** 宿主 → 卡：静默生成文本 `{id, ok, text?, error?}` */
export const CARD_MSG_GENERATE_TEXT_RESULT = 'flyai-card-generate-text-result'
/** 卡 → 宿主：临时提示词注入/移除 */
export const CARD_MSG_INJECT_PROMPTS = 'flyai-card-inject-prompts'
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

/** 宿主提供给卡内脚本的聊天快照（只读镜像；写入仍走授权动作） */
export type CardAppChatMessage = {
  /** 后端消息 id：卡内 setChatMessages 写回时用于定位 */
  id?: string
  role: 'user' | 'assistant' | 'system'
  name?: string
  text: string
  statData?: Record<string, unknown> | null
  /** 酒馆 message.extra：卡内 createChatMessages 写入，刷新后读回 */
  extra?: Record<string, unknown> | null
}

export type CardAppChat = {
  chatId: string
  userName: string
  characterName: string
  characterAvatar?: string | null
  messages: CardAppChatMessage[]
  /** 会话级 chatMetadata（作者脚本/卡片存档） */
  metadata?: Record<string, unknown> | null
}

/** 卡内聊天镜像上限：避免长会话把整段历史塞进 srcDoc */
export const MAX_CARD_CHAT_MESSAGES = 200
export const MAX_CARD_CHAT_MESSAGE_CHARS = 32 * 1024

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
  /** 会话级 chatMetadata（卡片存档） */
  chatMetadata?: Record<string, unknown> | null
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
    // data: 供单文件卡把主逻辑内嵌成 `script src="data:text/javascript;base64,..."`；
    // 沙箱本身已是 null origin，且已允许 inline/eval，放行 data: 不扩大权限面。
    `script-src 'unsafe-inline' 'unsafe-eval' data: ${SCRIPT_HOSTS}`,
    `style-src 'unsafe-inline' ${STYLE_HOSTS}`,
    `font-src data: ${FONT_HOSTS}`,
    `img-src data: blob:${remote}${originExtra}`,
    `media-src data: blob:${remote}`,
    "connect-src 'none'",
    // srcdoc/data/blob 供卡内嵌套地图/面板；外链帧跟随外链图片开关
    `frame-src 'self' data: blob:${remote}`,
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
  capabilities?: CardAppCapabilities | null,
  chat?: CardAppChat | null
): string => {
  const state = sanitizeCardAppVariables(variables)
  const chatSeed = (Array.isArray(chat?.messages) ? chat.messages : [])
    .slice(-MAX_CARD_CHAT_MESSAGES)
    .map((item, index) => ({
      message_id: index,
      id: String(item.id ?? ''),
      name: String(
        item.name ||
          (item.role === 'user' ? chat?.userName : chat?.characterName) ||
          ''
      ),
      is_user: item.role === 'user',
      is_system: item.role === 'system',
      mes: String(item.text ?? '').slice(0, MAX_CARD_CHAT_MESSAGE_CHARS),
      swipe_id: 0,
      extra:
        item.extra && typeof item.extra === 'object' ? (item.extra as Record<string, unknown>) : {},
      variables:
        item.statData && typeof item.statData === 'object'
          ? { stat_data: item.statData }
          : undefined,
    }))
  return `${ST_CORE_SOURCE}
(function(){
  var MESSAGE = ${escapeForScript(message)};
  var VARIABLES = JSON.parse(${escapeForScript(state.serialized)});
  var CHAT_METADATA = JSON.parse(${escapeForScript(JSON.stringify(
    chat?.metadata && typeof chat.metadata === 'object' ? chat.metadata : {}
  ))});
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
  var DRAFT_EVENT = ${escapeForScript(CARD_MSG_DRAFT)};
  var COMPOSE_EVENT = ${escapeForScript(CARD_MSG_COMPOSE)};
  var CHAT_EVENT = ${escapeForScript(CARD_MSG_CHAT)};
  var METADATA_EVENT = ${escapeForScript(CARD_MSG_METADATA)};
  var METADATA_SET_EVENT = ${escapeForScript(CARD_MSG_METADATA_SET)};
  var METADATA_RESULT_EVENT = ${escapeForScript(CARD_MSG_METADATA_RESULT)};
  var MESSAGES_PATCH_EVENT = ${escapeForScript(CARD_MSG_MESSAGES_PATCH)};
  var MESSAGES_RESULT_EVENT = ${escapeForScript(CARD_MSG_MESSAGES_RESULT)};
  var GENERATE_EVENT = ${escapeForScript(CARD_MSG_GENERATE)};
  var GENERATE_RESULT_EVENT = ${escapeForScript(CARD_MSG_GENERATE_RESULT)};
  var GENERATE_TEXT_EVENT = ${escapeForScript(CARD_MSG_GENERATE_TEXT)};
  var GENERATE_TEXT_RESULT_EVENT = ${escapeForScript(CARD_MSG_GENERATE_TEXT_RESULT)};
  var INJECT_PROMPTS_EVENT = ${escapeForScript(CARD_MSG_INJECT_PROMPTS)};
  var CHAT_ID = ${escapeForScript(chat?.chatId ?? '')};
  var USER_NAME = ${escapeForScript(chat?.userName ?? '')};
  // 酒馆每个聊天文件的 chat_metadata.integrity 是扩展识别「同一存档」的凭据
  // （约会大作战小手机用它派生 IndexedDB 房间键）。宿主会话没有这个字段，
  // 用稳定的会话 id 兜底，保证刷新/重建 iframe 后身份不变。
  function ensureChatIntegrity(meta){
    if (meta && typeof meta === 'object' && CHAT_ID &&
        (!meta.integrity || typeof meta.integrity !== 'string')) {
      meta.integrity = CHAT_ID;
    }
    return meta;
  }
  CHAT_METADATA = ensureChatIntegrity(CHAT_METADATA);
  var CHAT = JSON.parse(${escapeForScript(JSON.stringify(chatSeed))});
  var STCORE = globalThis.__FLYAI_ST_CORE__;
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
  // 展示侧正则里的内联脚本会在源码层把 window.top/parent 改写成
  // __FLYAI_HOST__；代理转发 DOM/事件，postMessage 回到真实宿主。
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
  // 沙箱读不到真实 localStorage。这里用会话 chatMetadata 的保留键做持久化：
  // 刷新/重开同一会话后恢复，键不与其他卡的 metadata 混用。
  function persistentStorage(kind){
    var metaKey = kind === 'session' ? '__flyai_storage_session' : '__flyai_storage_local';
    var source = CHAT_METADATA[metaKey];
    var data = source && typeof source === 'object' ? clone(source) : {};
    var saveTimer = null;
    function flush(){
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      if (CAP_WRITE) saveMetadata().catch(function(){});
    }
    function persist(immediate){
      CHAT_METADATA[metaKey] = clone(data);
      syncChatMetadata();
      if (!CAP_WRITE) return;
      // setItem 可能按每次输入写入：合并成一次 metadata 保存；
      // removeItem/clear（清理旧草稿）必须立即落库，否则 iframe 重建会读回旧值。
      if (immediate) { flush(); return; }
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function(){ saveTimer = null; flush(); }, 400);
    }
    try { window.addEventListener('pagehide', flush); } catch (e) {}
    return {
      getItem: function(k){ k = String(k); return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
      setItem: function(k, v){ data[String(k)] = String(v); persist(false); },
      removeItem: function(k){ delete data[String(k)]; persist(true); },
      clear: function(){ data = {}; persist(true); },
      key: function(i){ return Object.keys(data)[i] || null; },
      get length(){ return Object.keys(data).length; }
    };
  }
  try { Object.defineProperty(window, 'localStorage', { value: persistentStorage('local'), configurable: true }); } catch (e) {}
  try { Object.defineProperty(window, 'sessionStorage', { value: persistentStorage('session'), configurable: true }); } catch (e) {}
  // 沙箱内原生 indexedDB.open 抛 SecurityError；扩展只要探测到 indexedDB 存在就
  // 启用日志/媒体库，随后整个手机流程卡死。换成进程内桩，刷新后由聊天文件回读。
  try {
    if (STCORE.installIndexedDbShim) {
      STCORE.installIndexedDbShim(window, {
        load: function(){
          return CHAT_METADATA && CHAT_METADATA.__flyai_idb__;
        },
        save: function(data){
          if (!data || typeof data !== 'object') return;
          CHAT_METADATA.__flyai_idb__ = data;
          try { saveMetadata().catch(function(){}); } catch (e) {}
        }
      });
    }
  } catch (e) {}
  // 沙箱 iframe 是 opaque origin，navigator.locks.request 会抛
  // “Access to the Locks API is denied in this context”。卡片的开场建档
  // 会先探测 navigator.locks，再走锁内世界书写入；这里换成页面内串行桩，
  // 保证同一书名不会并发写，又不会因为浏览器拒绝而中断流程。
  function installLockShim(){
    var chains = {};
    var shim = {
      request: function(name, options, callback){
        if (typeof options === 'function') {
          callback = options;
          options = null;
        }
        var key = String(name || '');
        var prev = chains[key] || Promise.resolve();
        var run = function(){
          return typeof callback === 'function'
            ? callback({ name: key, mode: 'exclusive' })
            : undefined;
        };
        var result = prev.then(run, run);
        chains[key] = result.then(noop, noop);
        return result;
      },
      query: function(){ return Promise.resolve({ held: [], pending: [] }); }
    };
    try {
      Object.defineProperty(window.navigator, 'locks', { value: shim, configurable: true });
    } catch (e) {
      try { window.navigator.locks = shim; } catch (e2) {}
    }
  }
  try {
    if (window.navigator) installLockShim();
  } catch (e) {}

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

  // chatMetadata：本地立即更新，异步落库；保存失败只回滚 Promise，不影响卡内状态。
  var metadataSeq = 0;
  var pendingMetadata = {};
  function syncChatMetadata(){
    if (typeof ST_CONTEXT !== 'undefined' && ST_CONTEXT) {
      ST_CONTEXT.chatMetadata = CHAT_METADATA;
    }
  }
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
        if (CAP_WRITE) saveMetadata().catch(function(){});
        return String(value);
      },
      removeItem: function(key){
        delete bag()[String(key)];
        if (CAP_WRITE) saveMetadata().catch(function(){});
      },
      clear: function(){
        CHAT_METADATA.account_storage = {};
        if (CAP_WRITE) saveMetadata().catch(function(){});
      }
    };
  }
  function updateChatMetadata(patch, reset){
    if (!CAP_WRITE) { readOnly(); return; }
    if (reset === true) CHAT_METADATA = {};
    if (patch && typeof patch === 'object') {
      for (var key in patch) {
        if (Object.prototype.hasOwnProperty.call(patch, key)) {
          CHAT_METADATA[key] = clone(patch[key]);
        }
      }
    }
    ensureChatIntegrity(CHAT_METADATA);
    syncChatMetadata();
  }
  function saveMetadata(){
    var id = 'm' + (++metadataSeq);
    var saving = new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingMetadata[id];
        reject(new Error('flyai-save-metadata-timeout'));
      }, ACTION_TIMEOUT);
      pendingMetadata[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        HOST.postMessage({
          v: VERSION,
          type: METADATA_SET_EVENT,
          id: id,
          metadata: clone(CHAT_METADATA)
        }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingMetadata[id];
        reject(e);
      }
    });
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
    if (data.ok) { item.resolve(data.result); return; }
    item.reject(new Error(String(data.error || 'save-metadata-failed')));
  }

  // setChatMessages 已改动的镜像同步给宿主持久化；失败结果只影响 Promise。
  var messagesSeq = 0;
  var pendingMessages = {};
  function requestMessagePatch(changes, creates){
    var id = 'p' + (++messagesSeq);
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingMessages[id];
        reject(new Error('flyai-message-patch-timeout'));
      }, ACTION_TIMEOUT);
      pendingMessages[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        HOST.postMessage({
          v: VERSION,
          type: MESSAGES_PATCH_EVENT,
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
    if (data.ok) { item.resolve(data.result); return; }
    item.reject(new Error(String(data.error || 'patch-messages-failed')));
  }
  // 酒馆 context.saveChat/saveMetadata：扩展（小手机）会把 dal_phone_source 等
  // 楼层身份写进 message.extra 后保存。宿主没有 jsonl 文件，这里把变化过的
  // extra 逐条回写会话，刷新/重建 iframe 后仍能按同一分支身份对上。
  var persistedExtras = {};
  function persistChatExtras(){
    if (!CAP_WRITE) return Promise.resolve(false);
    var patches = [];
    var staged = {};
    for (var i = 0; i < CHAT.length; i += 1) {
      var item = CHAT[i];
      if (!item || !item.id) continue;
      var extra = item.extra;
      if (!extra || typeof extra !== 'object') continue;
      var json;
      try { json = JSON.stringify(extra); } catch (e) { continue; }
      if (!json || json === '{}') continue;
      staged[item.id] = json;
      if (persistedExtras[item.id] === json) continue;
      patches.push({ id: String(item.id), extra: clone(extra) });
    }
    if (!patches.length) return Promise.resolve(false);
    if (patches.length > 200) patches = patches.slice(patches.length - 200);
    return requestMessagePatch(patches).then(function(){
      for (var id in staged) {
        if (Object.prototype.hasOwnProperty.call(staged, id)) {
          persistedExtras[id] = staged[id];
        }
      }
      return true;
    });
  }
  function saveChat(){
    return persistChatExtras().then(function(){ return true; }, function(){ return true; });
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
    if (data.chatMetadata && typeof data.chatMetadata === 'object') {
      CHAT_METADATA = ensureChatIntegrity(clone(data.chatMetadata));
      syncChatMetadata();
    }
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
  function jsonPointerPath(path){
    return '/' + String(path).split('.').map(function(part){
      return part.replace(/~/g, '~0').replace(/\\//g, '~1');
    }).join('/');
  }
  function deleteChatVariable(key){
    var name = String(key);
    var state = clone(VARIABLES);
    var stat = statDataOf(state);
    var existed = STCORE.deletePath(stat, name);
    state.stat_data = stat;
    if (existed) requestVars('patch', [{ op: 'remove', path: jsonPointerPath(name) }], state);
    return { variables: clone(stat), delete_occurred: existed };
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
    var merged = overwrite ? STCORE.merge(stat, incoming, true) : STCORE.mergeKeep(stat, incoming);
    state.stat_data = merged;
    requestVars('replace', clone(merged), state);
    return merged;
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
  function getChatMessages(range, options){
    var messages = STCORE.getChatMessages(
      CHAT,
      range == null ? -1 : range,
      options || {},
      { charName: CHAR_NAME, userName: USER_NAME }
    );
    if (messages.length || CHAT.length) return messages;
    // 没有聊天镜像时退化为当前楼层，保持旧行为；数字 id 原样带上，
    // 让卡内按楼层号写的逻辑仍能拿到对应 message_id。
    function fallbackIdOf(id){
      if (typeof id === 'number' && isFinite(id) && id >= 0) return id;
      if (typeof id === 'string') {
        var m = id.match(/^-?(\\d+)(?:\\s*-\\s*-?\\d+)?$/);
        if (m) return Number(m[1]);
      }
      return 0;
    }
    function synthetic(id){
      return {
        message_id: fallbackIdOf(id),
        name: CHAR_NAME,
        role: 'assistant',
        is_hidden: false,
        message: CURRENT_MESSAGE,
        swipe_id: 0,
        swipes: [CURRENT_MESSAGE],
        swipes_data: [{ stat_data: clone(VARIABLES) }],
        swipes_info: [{}],
        data: { stat_data: clone(VARIABLES) },
        extra: { variables: clone(VARIABLES) }
      };
    }
    if (Array.isArray(range)) return range.map(synthetic);
    return [synthetic(range)];
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
  window.createChatMessages = createChatMessages;
  window.getChatHistoryDetail = getChatHistoryDetail;
  window.getCurrentMessageId = function(){ return Math.max(0, CHAT.length - 1); };
  window.getLastMessageId = function(){ return Math.max(0, CHAT.length - 1); };
  window.getLastMessage = function(){ return CURRENT_MESSAGE; };
  window.setChatMessages = function(changes, options){
    var list = Array.isArray(changes) ? changes : (changes ? [changes] : []);
    var patches = [];
    for (var i = 0; i < list.length; i += 1) {
      var change = list[i] || {};
      var id = Number(change.message_id);
      if (!isFinite(id) || id < 0 || id >= CHAT.length) continue;
      var target = CHAT[id];
      if (!target) continue;
      var patch = { id: String(target.id || '') };
      if (change.message != null || change.mes != null) {
        target.mes = String(change.message != null ? change.message : change.mes);
        patch.message = target.mes;
        if (id === CHAT.length - 1) CURRENT_MESSAGE = target.mes;
      }
      if (change.name != null) target.name = String(change.name);
      if (change.is_hidden != null) {
        target.is_hidden = !!change.is_hidden;
        patch.is_hidden = target.is_hidden;
      }
      if (change.role != null) {
        target.is_user = change.role === 'user';
        target.is_system = change.role === 'system';
      }
      if (change.data && typeof change.data === 'object') {
        target.variables = [clone(change.data)];
        patch.data = clone(change.data);
      }
      if (change.extra && typeof change.extra === 'object') target.extra = clone(change.extra);
      if (Array.isArray(change.swipes)) target.swipes = clone(change.swipes);
      if (typeof change.swipe_id === 'number') target.swipe_id = change.swipe_id;
      if (patch.id && Object.keys(patch).length > 1) patches.push(patch);
    }
    emit(window.tavern_events.MESSAGE_UPDATED, window.getLastMessageId());
    if (CAP_WRITE && patches.length) {
      return requestMessagePatch(patches).then(function(){ return undefined; });
    }
    return Promise.resolve();
  };
  window.deleteChatMessages = function(ids, options){
    var list = (Array.isArray(ids) ? ids : [ids]).map(Number).filter(function(id){
      return isFinite(id) && id >= 0 && id < CHAT.length;
    }).sort(function(a, b){ return b - a; });
    for (var i = 0; i < list.length; i += 1) CHAT.splice(list[i], 1);
    for (var j = 0; j < CHAT.length; j += 1) CHAT[j].message_id = j;
    return Promise.resolve();
  };
  window.rotateChatMessages = function(begin, middle, end, options){
    begin = Number(begin); middle = Number(middle); end = Number(end);
    if (!(begin >= 0 && middle >= begin && end >= middle && end <= CHAT.length)) {
      return Promise.reject(new Error('invalid rotate range'));
    }
    var head = CHAT.slice(0, begin);
    var middlePart = CHAT.slice(begin, middle);
    var tailPart = CHAT.slice(middle, end);
    var rest = CHAT.slice(end);
    var next = head.concat(tailPart, middlePart, rest);
    for (var i = 0; i < next.length; i += 1) {
      CHAT[i] = next[i];
      CHAT[i].message_id = i;
    }
    CHAT.length = next.length;
    return Promise.resolve();
  };
  window.initializeGlobal = function(name, value){ window[String(name)] = value; };
  window.getCurrentCharacterId = function(){ return CHAR_AVATAR || CHAR_NAME || null; };
  window.getCurrentCharacterName = function(){ return CHAR_NAME || null; };
  window.getCharacterNames = function(){ return CHAR_NAME ? [CHAR_NAME] : []; };
  window.getCharacterIds = function(){ return CHAR_AVATAR ? [CHAR_AVATAR] : []; };
  window.getCharacter = function(){
    return Promise.resolve({
      name: CHAR_NAME,
      avatar: CHAR_AVATAR,
      description: '',
      first_messages: [],
      extensions: {},
    });
  };
  window.getPresetNames = function(){ return []; };
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
  function resolveEntryUid(entry, named, titleUids){
    if (!entry || typeof entry !== 'object') return null;
    var title = String(entry.title == null ? '' : entry.title);
    var slot = named[title];
    if (slot && slot.uid != null && isFinite(Number(slot.uid))) {
      return Number(slot.uid);
    }
    if (titleUids && title && titleUids[title] != null &&
        isFinite(Number(titleUids[title]))) {
      return Number(titleUids[title]);
    }
    if (entry.uid != null && entry.uid !== '' && isFinite(Number(entry.uid))) {
      return Number(entry.uid);
    }
    return null;
  }
  function mergedWorldEntries(){
    var named = characterWorldSlots();
    var catalog = catalogUidMap();
    var titleUids = catalog ? catalog.byTitle : null;
    var reserved = reservedWorldUids(named, catalog);
    var out = [];
    var used = {};
    var usedTitles = {};
    var seenContent = {};
    function add(entry, index){
      if (!entry || typeof entry !== 'object') return;
      var title = String(entry.title == null ? '' : entry.title);
      var content = String(entry.content == null ? '' : entry.content);
      var contentKey = title + '\u0000' + content;
      if (seenContent[contentKey]) return;
      var uid = resolveEntryUid(entry, named, titleUids);
      if (uid != null && used[uid] && usedTitles[uid] !== title) uid = null;
      if (uid == null) {
        uid = index + 1;
        while (used[uid] || reserved[uid]) uid += 1;
      }
      if (used[uid]) return; // 同 uid：会话世界书副本优先
      seenContent[contentKey] = true;
      used[uid] = true;
      usedTitles[uid] = title;
      var copy = clone(entry);
      copy.uid = uid;
      out.push(copy);
    }
    // 会话世界书副本是卡写回的目标，比角色卡原始 table 新；重复条目的
    // 启用状态以副本为准，否则卡刚启用的剧情条目回读仍是旧的禁用状态。
    var i;
    for (i = 0; i < WORLD_ENTRIES.length; i += 1) add(WORLD_ENTRIES[i], i);
    var table = charWorldEntries();
    for (i = 0; i < table.length; i += 1) add(table[i], i);
    if (catalog) {
      // 与 buildWorldbookFromRows 一致：清单里缺映射的 uid 补禁用占位，
      // 卡启用这些 uid 后回读仍能拿到条目，不会判定同步失败。
      for (i = 0; i < catalog.owned.length; i += 1) {
        var ownedUid = catalog.owned[i];
        if (used[ownedUid]) continue;
        used[ownedUid] = true;
        out.push({
          uid: ownedUid,
          title: 'UID ' + ownedUid,
          content: '',
          pinned: false,
          keys: [],
          enabled: false,
          constant: false,
          position: 'system',
          depth: 4,
          insert_order: 100,
          probability: 100
        });
      }
    }
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
  function worldEntriesArray(){
    return STCORE.worldbookEntryList({ entries: mergedWorldEntries() });
  }
  function worldEntriesHost(list){
    return STCORE.worldbookHostEntries(STCORE.worldbookFromList(list));
  }
  function updateWorld(name, updater){
    var current = worldEntriesArray();
    var next = current;
    if (typeof updater === 'function') {
      try { next = updater(current); } catch (e) { return Promise.reject(e); }
    }
    if (!Array.isArray(next)) next = current;
    return sendWorld(worldEntriesHost(next));
  }
  // SillyTavern 的这些接口是同步的（返回数组/对象），卡片会直接 names.includes(...)、
  // [...names] 使用；这里必须同样同步返回，不能包 Promise。
  // 世界书读接口不受写权限门控：上游读永远可用，默认未开卡内写回的用户
  // 也必须能读到角色卡/挂载世界书（否则大量卡会误判“不支持世界书”降级）。
  window.getWorldbookNames = function(){ return worldbookNames(); };
  window.getCharWorldbookNames = function(){
    // 卡常要求存在一个主世界书才肯写档；没有显式绑定时用稳定名称兜底
    return { primary: primaryWorldName(), additional: [] };
  };
  window.getGlobalWorldbookNames = function(){ return []; };
  window.getChatWorldbookName = function(){
    return WORLD_NAME || CHAR_WORLD_NAME || null;
  };
  window.getWorldbook = function(name){
    var wanted = name == null ? '' : String(name);
    if (!wanted) return worldEntriesArray();
    // 角色书与挂载书同名（同一本）时返回合并视图，保留 uid 占位与副本优先级
    if (wanted === WORLD_NAME && wanted === CHAR_WORLD_NAME) return worldEntriesArray();
    if (wanted === CHAR_WORLD_NAME) {
      return STCORE.worldbookEntryList({ entries: charWorldEntries() });
    }
    if (wanted === WORLD_NAME) {
      return STCORE.worldbookEntryList({ entries: WORLD_ENTRIES });
    }
    return [];
  };
  window.updateWorldbookWith = CAP_WRITE
    ? function(name, updater){ return updateWorld(name, updater); }
    : function(){ readOnly(); return Promise.resolve(undefined); };
  window.updateWorldbook = CAP_WRITE
    ? function(name, updater){ return updateWorld(name, updater); }
    : function(){ readOnly(); return Promise.resolve(undefined); };
  window.replaceWorldbook = CAP_WRITE
    ? function(first, second){
        var list = Array.isArray(first) ? first : second;
        return sendWorld(worldEntriesHost(Array.isArray(list) ? list : []));
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
  window.getCurrentPersonaName = function(){ return USER_NAME; };
  window.getCurrentPersonaId = function(){ return ''; };
  window.retrieveDisplayedMessage = function(id){
    var list = getChatMessages([id], {});
    return list.length ? String(list[0].message || '') : '';
  };

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
  window.tavern_events = STCORE.tavernEvents;
  window.iframe_events = STCORE.iframeEvents;
  window.eventOn = function(event, handler){
    on(event, handler, false, false);
    return { stop: function(){ off(event, handler); } };
  };
  window.eventOnce = function(event, handler){
    on(event, handler, true, false);
    return { stop: function(){ off(event, handler); } };
  };
  window.eventMakeFirst = function(event, handler){
    on(event, handler, false, true);
    return { stop: function(){ off(event, handler); } };
  };
  window.eventMakeLast = function(event, handler){
    on(event, handler, false, false);
    return { stop: function(){ off(event, handler); } };
  };
  window.eventOff = function(event, handler){ off(event, handler); };
  window.eventEmit = emit;
  window.eventEmitAndWait = emit;
  window.eventRemoveListener = function(event, handler){ off(event, handler); };
  window.eventClearEvent = function(event){ off(event); };
  window.eventClearListener = function(handler){
    for (var key in listeners) off(key, handler);
  };
  window.eventClearAll = function(){ listeners = {}; };
  window.toastr = {
    info: function(m){ toast('info', m); },
    success: function(m){ toast('success', m); },
    warning: function(m){ toast('warning', m); },
    error: function(m){ toast('error', m); },
    clear: noop
  };
  try { window.alert = function(message){ toast('info', message); }; } catch (e) {}
  // 卡片常用 window.confirm 做“确认覆盖”前置询问。沙箱里同步弹窗不可用，
  // 但所有实际写入仍会经宿主确认，因此在有写权限时返回 true，让流程进入
  // 宿主确认；只读卡片继续返回 false，保持无副作用。
  try { window.confirm = function(){ return !!CAP_WRITE; }; } catch (e) {}
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

  // ── 酒馆输入框兼容层 ────────────────────────────────────────────────
  // 展示侧脚本（如小猫之神的回应选项卡）会直接读父窗口里的 #send_textarea
  // 与 #send_but：前者填入/追加文本，后者或 context.generate() 触发发送。
  // 沙箱里没有这些元素，脚本就会停在上「未找到酒馆输入框」。这里提供离屏
  // 虚拟输入框：值变化同步会话草稿；发送仍走宿主逐次确认的发送意图。
  var extensionSettings = {};
  var composer = null;
  var sendButton = null;
  var sendForm = null;
  var composeTimer = 0;
  var lastComposed = '';
  function postCompose(text){
    try {
      HOST.postMessage({
        v: VERSION,
        type: COMPOSE_EVENT,
        text: String(text == null ? '' : text).slice(0, 32768)
      }, '*');
    } catch (e) {}
  }
  function syncCompose(){
    if (!composer) return;
    var value = String(composer.value == null ? '' : composer.value);
    if (value === lastComposed) return;
    lastComposed = value;
    postCompose(value);
  }
  function ensureComposer(){
    if (composer) return composer;
    try {
      composer = document.createElement('textarea');
      composer.id = 'send_textarea';
      composer.setAttribute('aria-hidden', 'true');
      composer.tabIndex = -1;
      composer.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;opacity:0;pointer-events:none;';
      composer.addEventListener('input', syncCompose);
      composeTimer = setInterval(syncCompose, 250);
    } catch (e) { composer = null; }
    return composer;
  }
  function requestSend(text){
    var value = String(text == null ? '' : text);
    if (!value.trim()) return false;
    if (!CAP_ACTIONS) { readOnly(); return false; }
    requestAction({ kind: 'action', action: 'send', text: value });
    return true;
  }

  // ── 聊天镜像 / 楼层检测兼容层 ──────────────────────────────────────
  // 部分卡（如约会大作战开场页）会通过 SillyTavern.getContext() 读取
  // name1/chat/chatId，并调用 createChatMessages / getChatHistoryDetail
  // 判断自己是否在 0 楼。这里维护一份只读镜像；真正的发送仍走
  // requestSend → 宿主逐次确认，卡内不能绕过授权直接改会话。
  var PENDING_SEND = '';
  var PENDING_GENERATE = null;
  function chatMessageFrom(raw, index){
    var role = String(raw && raw.role || 'assistant');
    if (role !== 'user' && role !== 'system') role = 'assistant';
    return {
      message_id: index,
      id: raw && raw.id ? String(raw.id) : '',
      name: String(raw && raw.name || (role === 'user' ? USER_NAME : CHAR_NAME)),
      is_user: role === 'user',
      is_system: role === 'system',
      mes: String(raw && raw.text != null ? raw.text : ''),
      swipe_id: 0,
      extra: raw && raw.extra && typeof raw.extra === 'object' ? clone(raw.extra) : {},
      swipe_info: Array.isArray(raw && raw.swipe_info) ? clone(raw.swipe_info) : [],
      // ST 的消息变量按 swipe 下标存放：variables[swipe_id] 是整份变量信封。
      variables: raw && raw.statData && typeof raw.statData === 'object'
        ? [{ stat_data: raw.statData }]
        : undefined
    };
  }
  function applyChatUpdate(list){
    var next = [];
    if (Array.isArray(list)) {
      for (var i = 0; i < list.length; i += 1) next.push(chatMessageFrom(list[i], i));
    }
    for (var j = 0; j < next.length; j += 1) {
      var cur = CHAT[j];
      // 保留已有对象（尤其 chat[0]）的引用：卡的 target() 用 === 比较开场白
      if (cur && (j === 0 || cur.is_user === next[j].is_user)) {
        if (!cur.id && next[j].id) cur.id = next[j].id;
        cur.name = next[j].name;
        cur.is_user = next[j].is_user;
        cur.is_system = next[j].is_system;
        cur.mes = next[j].mes;
        // ensureSourceIds 写入的楼层身份（dal_phone_source）不能因宿主快照
        // 没有对应字段而被清掉；宿主带来的新键优先，旧键补缺。
        var mergedExtra = next[j].extra && typeof next[j].extra === 'object'
          ? next[j].extra : {};
        var oldExtra = cur.extra && typeof cur.extra === 'object' ? cur.extra : {};
        for (var ek in oldExtra) {
          if (Object.prototype.hasOwnProperty.call(oldExtra, ek) &&
              mergedExtra[ek] === undefined) {
            mergedExtra[ek] = clone(oldExtra[ek]);
          }
        }
        cur.extra = mergedExtra;
        if ((!Array.isArray(cur.swipe_info) || !cur.swipe_info.length) &&
            Array.isArray(next[j].swipe_info)) {
          cur.swipe_info = next[j].swipe_info;
        }
        // 宿主只提供 stat_data，无法还原卡写入的完整变量信封
        // （schema / initialized_lorebooks）。卡刚建立的楼层必须保留原信封，
        // 否则开场校验会误判为“1 楼已有其他变量记录”。
        var nextVars = next[j].variables;
        var keepEnvelope = cur.variables && cur.variables[0] &&
          !(nextVars && nextVars[0] && nextVars[0].schema);
        if (!keepEnvelope) cur.variables = nextVars;
      } else {
        CHAT[j] = next[j];
      }
    }
    CHAT.length = next.length;
    // generate() 等待回复落地：宿主回放里出现新楼层后解除等待
    if (PENDING_GENERATE && CHAT.length > PENDING_GENERATE.startLen) {
      PENDING_GENERATE.finish();
    }
  }
  function createChatMessages(messages){
    var list = Array.isArray(messages) ? messages : [messages];
    var created = [];
    var payload = [];
    for (var i = 0; i < list.length; i += 1) {
      var raw = list[i] || {};
      var role = String(raw.role || 'user');
      var item = {
        message_id: CHAT.length,
        name: String(raw.name || (role === 'user' ? USER_NAME : CHAR_NAME)),
        is_user: role === 'user',
        is_system: role === 'system',
        mes: String(raw.message != null ? raw.message : (raw.mes != null ? raw.mes : '')),
        swipe_id: 0,
        extra: raw.extra && typeof raw.extra === 'object' ? clone(raw.extra) : {},
        // 完整变量信封（含 schema/initialized_lorebooks）按 swipe 存入；
        // 卡的 repair/校验逻辑会直接读取 variables[0]。
        variables: raw.data && typeof raw.data === 'object'
          ? [clone(raw.data)]
          : (Array.isArray(raw.variables) ? clone(raw.variables) : undefined)
      };
      CHAT.push(item);
      created.push(item);
      payload.push({
        role: role,
        name: item.name,
        message: item.mes,
        extra: item.extra,
        data: item.variables && item.variables[0] ? item.variables[0] : undefined,
        is_hidden: raw.is_hidden === true
      });
      // 可写时新楼层已直接落库，不再交给 generate() 重复发送；
      // 只读沙箱没有落库能力，仍保留旧的「由宿主确认发送」兜底。
      if (!CAP_WRITE && item.is_user && item.mes.trim()) PENDING_SEND = item.mes;
    }
    emit(window.tavern_events.MESSAGE_SENT, window.getLastMessageId());
    if (!CAP_WRITE) return Promise.resolve(created);
    // 关键：新楼层必须落到宿主会话，后续 generate() 才会带上这段上下文。
    return requestMessagePatch([], payload).then(function(result){
      var rows = result && Array.isArray(result.created) ? result.created : [];
      for (var j = 0; j < rows.length; j += 1) {
        var row = rows[j] || {};
        var target = created[j];
        if (target && row.id) target.id = String(row.id);
      }
      return created;
    });
  }
  function getChatHistoryDetail(files){
    var out = {};
    var list = Array.isArray(files) ? files : (files ? [files] : []);
    var fallback = (CHAT_ID || 'chat') + '.jsonl';
    if (!list.length) {
      out[fallback] = CHAT;
      return Promise.resolve(out);
    }
    for (var i = 0; i < list.length; i += 1) {
      var name = list[i] && list[i].file_name ? String(list[i].file_name) : fallback;
      out[name] = CHAT;
    }
    return Promise.resolve(out);
  }
  var pendingMainGenerates = {};
  var mainGenerateSeq = 0;
  function requestGenerateMain(quietPrompt){
    if (!CAP_ACTIONS) { readOnly(); return Promise.resolve(false); }
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
          type: GENERATE_EVENT,
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
  var pendingTextGenerates = {};
  var textGenerateSeq = 0;
  // ST chat-completions 兼容：按显式 messages 请求模型，不落主聊天楼层。
  function requestGenerateText(messages, maxTokens, signal){
    if (!CAP_ACTIONS) {
      return Promise.reject(new Error(CODE_READ_ONLY));
    }
    var id = 't' + (++textGenerateSeq);
    return new Promise(function(resolve, reject){
      var timer = setTimeout(function(){
        delete pendingTextGenerates[id];
        reject(new Error('flyai-generate-timeout'));
      }, 180000);
      pendingTextGenerates[id] = { resolve: resolve, reject: reject, timer: timer };
      if (signal && typeof signal.addEventListener === 'function') {
        signal.addEventListener('abort', function(){
          var pending = pendingTextGenerates[id];
          if (!pending) return;
          delete pendingTextGenerates[id];
          clearTimeout(timer);
          reject(new Error('aborted'));
        }, { once: true });
      }
      try {
        HOST.postMessage({
          v: VERSION,
          type: GENERATE_TEXT_EVENT,
          id: id,
          messages: clone(Array.isArray(messages) ? messages : []),
          max_tokens: Number(maxTokens) || 0
        }, '*');
      } catch (e) {
        clearTimeout(timer);
        delete pendingTextGenerates[id];
        reject(e);
      }
    });
  }
  function settleTextGenerate(data){
    if (!data || data.id == null) return;
    var item = pendingTextGenerates[data.id];
    if (!item) return;
    delete pendingTextGenerates[data.id];
    clearTimeout(item.timer);
    if (data.ok) item.resolve(String(data.text || ''));
    else item.reject(new Error(String(data.error || 'generate-failed')));
  }
  function uninjectPrompts(ids){
    var list = Array.isArray(ids) ? ids.map(String) : [];
    try {
      HOST.postMessage({
        v: VERSION, type: INJECT_PROMPTS_EVENT, prompts: [], remove: list
      }, '*');
    } catch (e) {}
  }
  function injectPrompts(prompts, options){
    var list = Array.isArray(prompts) ? clone(prompts) : [];
    try {
      HOST.postMessage({
        v: VERSION, type: INJECT_PROMPTS_EVENT, prompts: list, remove: []
      }, '*');
    } catch (e) {}
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
  window.injectPrompts = injectPrompts;
  window.uninjectPrompts = uninjectPrompts;

  function generateFromComposer(type, options){
    // 酒馆 context.generate('normal', { quiet_prompt })：走主聊天生成，
    // 不产生可见 user 楼层；没有 quiet_prompt 时退回旧的输入框发送路径。
    if (options && typeof options === 'object' &&
        typeof options.quiet_prompt === 'string' && options.quiet_prompt) {
      return requestGenerateMain(options.quiet_prompt);
    }
    var text = PENDING_SEND;
    PENDING_SEND = '';
    if (!text) text = composer ? composer.value : '';
    text = String(text == null ? '' : text);
    if (!text.trim()) {
      // 纯 generate()（常见于先 createChatMessages 再 generate）：按酒馆语义
      // 基于已落库的历史生成回复，不额外追加 user 楼层。
      if (!CAP_ACTIONS) { readOnly(); return Promise.resolve(false); }
      if (type === 'normal' || type == null) return requestGenerateMain('');
      return Promise.resolve(false);
    }
    if (!CAP_ACTIONS) { readOnly(); return Promise.resolve(false); }
    // ST 的 generate() 在 AI 回复落地后才 resolve；卡会据此检查聊天楼层。
    // 宿主只负责批准发送，这里继续等聊天镜像出现新楼层（或超时兜底）。
    var startLen = CHAT.length;
    var settle = null;
    var promise = new Promise(function(resolve, reject){
      var timer = setTimeout(function(){ finish(true); }, 120000);
      function finish(ok, err){
        if (!settle) return;
        settle = null;
        PENDING_GENERATE = null;
        clearTimeout(timer);
        if (err) reject(err); else resolve(ok);
      }
      settle = finish;
    });
    PENDING_GENERATE = {
      startLen: startLen,
      finish: function(){ if (settle) settle(true); }
    };
    requestAction({ kind: 'action', action: 'send', text: text }).catch(function(err){
      if (settle) settle(false, err);
    });
    return promise;
  }

  // ── 酒馆世界书 HTTP / context 兼容层 ──────────────────────────────
  // 约会大作战等卡直接调用 /api/worldinfo/get + saveWorldInfo 同步世界书。
  // 这里用本地对象模拟读写；保存时仍通过 requestAction(world) 交给宿主确认。
  var WORLDBOOKS = {};
  function primaryWorldName(){
    return CHAR_WORLD_NAME || WORLD_NAME || CHAR_NAME || 'FlyAI';
  }
  // 角色卡自带世界书（CC character_book）被导入到 character.table，但酒馆
  // 卡常按 uid 读取「主世界书」条目。部分卡（如约会大作战）会在脚本里声明
  // slots（uid + title），这里按标题把 table 映射成 uid→entry 的 ST 形状，
  // 让 /api/worldinfo/get 能返回这些条目；没有 manifest 时用稳定序号兜底。
  function characterWorldSlots(){
    try {
      var manifest = window.DALOpeningWorldbook;
      var slots = manifest && Array.isArray(manifest.slots) ? manifest.slots : [];
      var out = {};
      for (var i = 0; i < slots.length; i += 1) {
        var slot = slots[i] || {};
        if (slot.uid == null) continue;
        if (slot.title) out[String(slot.title)] = slot;
        // 约会大作战的 manifest 用 path 声明槽位；酒馆世界书按文件名识别，
        // 两种写法都要能对上，否则角色书里的标题匹配不到 uid。
        if (typeof slot.path === 'string') {
          var base = slot.path.split('/').pop() || '';
          if (base.slice(-3) === '.md') base = base.slice(0, -3);
          if (base && !out[base]) out[base] = slot;
        }
      }
      return out;
    } catch (e) {
      return {};
    }
  }
  // 剧情清单（故事目录）：部分酒馆卡（如约会大作战）把剧情条目的 uid 表
  // 打包在脚本里，运行时挂在 window 上（对象带 OWNED_PLOT_UIDS，节点带
  // entryComment/uid）。导入角色卡时我们丢掉了酒馆世界书的原始 uid，行号
  // 又因空条目被跳过而漂移，卡按 uid 核对启用状态就会报「回读核对不一致」。
  // 这里按条目标题还原卡期望的 uid；没有这种目录的卡按原序号兜底。
  var STORY_CATALOG_CACHE = null;
  function storyCatalog(){
    try {
      var direct = window.DALPanelStoryCatalog;
      if (direct && typeof direct === 'object' &&
          Array.isArray(direct.OWNED_PLOT_UIDS)) {
        return direct;
      }
    } catch (e) {}
    try {
      var seen = 0;
      for (var key in window) {
        if (!Object.prototype.hasOwnProperty.call(window, key)) continue;
        seen += 1;
        if (seen > 6000) break;
        var value;
        try { value = window[key]; } catch (e) { continue; }
        if (value && typeof value === 'object' && !Array.isArray(value) &&
            Array.isArray(value.OWNED_PLOT_UIDS)) {
          return value;
        }
      }
    } catch (e) {}
    return null;
  }
  function catalogUidMap(){
    if (STORY_CATALOG_CACHE) return STORY_CATALOG_CACHE;
    var catalog = storyCatalog();
    if (!catalog) return null;
    var byTitle = {};
    function visit(node, depth){
      if (!node || typeof node !== 'object' || depth > 6) return;
      if (Array.isArray(node)) {
        for (var i = 0; i < node.length; i += 1) visit(node[i], depth + 1);
        return;
      }
      var uid = Number(node.uid);
      var title = node.entryComment;
      if (isFinite(uid) && uid >= 0 && typeof title === 'string' && title &&
          byTitle[title] == null) {
        byTitle[title] = uid;
      }
      for (var key in node) {
        if (!Object.prototype.hasOwnProperty.call(node, key)) continue;
        var value = node[key];
        if (value && typeof value === 'object') visit(value, depth + 1);
      }
    }
    visit(catalog, 0);
    var owned = [];
    var rawOwned = Array.isArray(catalog.OWNED_PLOT_UIDS)
      ? catalog.OWNED_PLOT_UIDS : [];
    for (var i = 0; i < rawOwned.length; i += 1) {
      var uid = Number(rawOwned[i]);
      if (isFinite(uid) && uid >= 0 && owned.indexOf(uid) < 0) owned.push(uid);
    }
    STORY_CATALOG_CACHE = { byTitle: byTitle, owned: owned };
    return STORY_CATALOG_CACHE;
  }
  function reservedWorldUids(named, catalog){
    var reserved = {};
    var key;
    for (key in named) {
      if (!Object.prototype.hasOwnProperty.call(named, key)) continue;
      if (named[key] && named[key].uid != null) {
        reserved[Number(named[key].uid)] = true;
      }
    }
    if (catalog) {
      for (key in catalog.byTitle) {
        if (!Object.prototype.hasOwnProperty.call(catalog.byTitle, key)) continue;
        reserved[Number(catalog.byTitle[key])] = true;
      }
      for (var i = 0; i < catalog.owned.length; i += 1) {
        reserved[catalog.owned[i]] = true;
      }
    }
    return reserved;
  }
  function worldbookFromCharacter(){
    return buildWorldbookFromRows(CHARACTER_FIELDS && Array.isArray(CHARACTER_FIELDS.table)
      ? CHARACTER_FIELDS.table
      : []);
  }
  function buildWorldbookFromRows(table){
    var named = characterWorldSlots();
    var catalog = catalogUidMap();
    var titleUids = catalog ? catalog.byTitle : null;
    var reserved = reservedWorldUids(named, catalog);
    var entries = {};
    // 先按 manifest 预置槽位：即使角色书里暂时没有对应条目，卡的 readSlots
    // 也能读到完整 uid 列表，不会因为缺少槽位直接失败。
    for (var key in named) {
      if (!Object.prototype.hasOwnProperty.call(named, key)) continue;
      var pre = named[key];
      if (!pre || pre.uid == null) continue;
      var preUid = Number(pre.uid);
      if (!isFinite(preUid) || entries[preUid]) continue;
      entries[preUid] = {
        uid: preUid,
        comment: String(pre.title || key),
        key: [],
        content: '',
        constant: !!pre.constant,
        disable: false,
        position: 0,
        depth: 4,
        order: 100,
        probability: 100
      };
    }
    for (var i = 0; i < table.length; i += 1) {
      var item = table[i] || {};
      var title = String(item.title == null ? '' : item.title);
      var slot = named[title];
      var content = String(item.content == null ? '' : item.content);
      var keys = Array.isArray(item.keys) ? item.keys.slice() : [];
      if (slot && slot.uid != null && entries[Number(slot.uid)]) {
        var target = entries[Number(slot.uid)];
        target.content = content;
        target.key = keys;
        target.depth = Number(item.depth == null ? 4 : item.depth);
        target.order = Number(item.insert_order == null ? 100 : item.insert_order);
        continue;
      }
      // uid 优先级：剧情目录里的原始 uid（按标题匹配）> 会话副本里已保存的
      // uid > 顺序号兜底。剧情目录是卡核对启用状态的基准，必须优先。
      var uid = null;
      if (titleUids && title && titleUids[title] != null) {
        uid = Number(titleUids[title]);
        if (!isFinite(uid)) uid = null;
      }
      if (uid == null && item.uid != null && item.uid !== '' &&
          isFinite(Number(item.uid))) {
        uid = Number(item.uid);
      }
      if (uid == null) {
        uid = i + 1;
        while (entries[uid] || reserved[uid]) uid += 1;
      } else if (entries[uid]) {
        // 同一 uid 已由槽位预置/重复标题占住：补内容即可
        var filled = entries[uid];
        if (!filled.content && content) {
          filled.content = content;
          filled.key = keys;
          filled.depth = Number(item.depth == null ? 4 : item.depth);
          filled.order = Number(item.insert_order == null ? 100 : item.insert_order);
        }
        continue;
      }
      entries[uid] = {
        uid: uid,
        comment: title || String(uid),
        key: keys,
        content: content,
        constant: !!item.pinned,
        disable: item.enabled === false,
        position: 0,
        depth: Number(item.depth == null ? 4 : item.depth),
        order: Number(item.insert_order == null ? 100 : item.insert_order),
        probability: 100
      };
    }
    if (catalog) {
      // 卡会按 uid 启用自己剧情清单里的条目；没映射到标题的条目（如扩展
      // 设定）补一条禁用占位，否则卡启用后回读不到该 uid，会判定同步失败。
      for (var oi = 0; oi < catalog.owned.length; oi += 1) {
        var ownedUid = catalog.owned[oi];
        if (entries[ownedUid]) continue;
        entries[ownedUid] = {
          uid: ownedUid,
          comment: 'UID ' + ownedUid,
          key: [],
          content: '',
          constant: false,
          disable: true,
          position: 0,
          depth: 4,
          order: 100,
          probability: 100
        };
      }
    }
    return { entries: entries };
  }
  function worldbookOf(name){
    var key = String(name || primaryWorldName());
    var book = WORLDBOOKS[key];
    var hasEntries = book && book.entries && Object.keys(book.entries).length > 0;
    if (hasEntries) return book;
    if (key === primaryWorldName()) {
      // 宿主副本优先：会话世界书副本（写回目标）比角色卡原始 table 新，
      // 重载/重建 iframe 后应读到刚写进去的内容。
      if (Array.isArray(WORLD_ENTRIES) && WORLD_ENTRIES.length) {
        book = buildWorldbookFromRows(WORLD_ENTRIES);
      } else if (CHARACTER_FIELDS && Array.isArray(CHARACTER_FIELDS.table)) {
        book = worldbookFromCharacter();
      } else {
        book = book || { entries: {} };
      }
      WORLDBOOKS[key] = book;
    }
    return book || { entries: {} };
  }
  function worldbookEntries(data){
    return STCORE.worldbookHostEntries(data);
  }
  function saveWorldbook(name, data){
    if (!CAP_WRITE) {
      readOnly();
      return Promise.reject(new Error('flyai-card-read-only'));
    }
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
    var list = worldEntriesHost(STCORE.worldbookEntryList(book));
    if (WORLD_NAME) WORLD_ENTRIES = clone(list);
    // 始终按“世界书写回”提交：后端在未绑定世界书时会 fork/派生世界副本，
    // 不再把 /api/worldinfo/edit 误写成角色卡 table。
    return requestAction({ kind: 'world', entries: list }).then(function(result){
      if (result && Array.isArray(result.entries)) {
        if (WORLD_NAME) WORLD_ENTRIES = clone(result.entries);
        return clone(result.entries);
      }
      return clone(list);
    });
  }
  function jsonResponse(data){
    var text = JSON.stringify(data == null ? {} : data);
    try {
      return new Response(text, {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      });
    } catch (e) {
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        json: function(){ return Promise.resolve(data); },
        text: function(){ return Promise.resolve(text); }
      };
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

  function ensureSendButton(){
    if (sendButton) return sendButton;
    try {
      sendButton = document.createElement('button');
      sendButton.id = 'send_but';
      sendButton.type = 'button';
      sendButton.textContent = 'send';
      sendButton.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;opacity:0;';
      sendButton.addEventListener('click', function(){
        requestSend(composer ? composer.value : '');
      });
    } catch (e) { sendButton = null; }
    return sendButton;
  }
  function ensureSendForm(){
    if (sendForm) return sendForm;
    try {
      sendForm = document.createElement('form');
      sendForm.id = 'send_form';
      sendForm.style.cssText = 'position:absolute;left:-9999px;top:0;width:1px;height:1px;opacity:0;';
      sendForm.addEventListener('submit', function(e){
        if (e && e.preventDefault) e.preventDefault();
        requestSend(composer ? composer.value : '');
      });
      var area = ensureComposer();
      var button = ensureSendButton();
      if (area) sendForm.appendChild(area);
      if (button) sendForm.appendChild(button);
    } catch (e) { sendForm = null; }
    return sendForm;
  }
  function mountComposer(){
    try {
      if (!document.body) return;
      var form = ensureSendForm();
      var target = form || ensureComposer();
      if (target && !target.isConnected) document.body.appendChild(target);
      if (!form) {
        var button = ensureSendButton();
        if (button && !button.isConnected) document.body.appendChild(button);
      }
    } catch (e) {}
  }
  try {
    var nativeGetById = document.getElementById.bind(document);
    document.getElementById = function(id){
      if (id === 'send_textarea') return ensureComposer();
      if (id === 'send_but') return ensureSendButton();
      if (id === 'send_form') return ensureSendForm();
      return nativeGetById(id);
    };
    var nativeQuerySelector = document.querySelector.bind(document);
    document.querySelector = function(selector){
      if (selector === '#send_textarea' || selector === 'textarea#send_textarea') return ensureComposer();
      if (selector === '#send_but' || selector === 'button#send_but') return ensureSendButton();
      if (selector === '#send_form' || selector === 'form#send_form') return ensureSendForm();
      return nativeQuerySelector(selector);
    };
  } catch (e) {}
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountComposer);
  } else {
    mountComposer();
  }

  window.SillyTavern = window.SillyTavern || {};
  var ST_CONTEXT = {
      extensionSettings: extensionSettings,
      saveSettingsDebounced: function(){},
      saveChat: saveChat,
      generate: generateFromComposer,
      variables: clone(VARIABLES),
      chat: CHAT,
      chatMetadata: CHAT_METADATA,
      updateChatMetadata: updateChatMetadata,
      saveMetadata: saveMetadata,
      accountStorage: accountStorage(),
      chatId: CHAT_ID,
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
      name1: USER_NAME,
      name2: CHAR_NAME,
      groupId: null,
      groups: [],
      eventSource: eventSource,
      getCurrentMessageId: function(){ return 0; },
      getRequestHeaders: function(){ return {}; },
      loadWorldInfo: function(name){ return Promise.resolve(worldbookOf(name)); },
      saveWorldInfo: function(name, data){ return saveWorldbook(name, data); }
  };
  window.SillyTavern.getContext = function(){ return ST_CONTEXT; };

  var helper = window.TavernHelper || window.tavernHelper || {};
  helper.getChatMessages = getChatMessages;
  helper.createChatMessages = createChatMessages;
  helper.setChatMessages = window.setChatMessages;
  helper.deleteChatMessages = window.deleteChatMessages;
  helper.rotateChatMessages = window.rotateChatMessages;
  helper.getChatHistoryDetail = getChatHistoryDetail;
  helper.getCurrentMessageId = window.getCurrentMessageId;
  helper.getLastMessageId = window.getLastMessageId;
  helper.updateChatMetadata = updateChatMetadata;
  helper.saveMetadata = saveMetadata;
  window.updateChatMetadata = updateChatMetadata;
  window.saveMetadata = saveMetadata;
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
  helper.eventMakeFirst = window.eventMakeFirst;
  helper.eventMakeLast = window.eventMakeLast;
  helper.eventOff = window.eventOff;
  helper.eventEmit = window.eventEmit;
  helper.eventEmitAndWait = window.eventEmitAndWait;
  helper.eventRemoveListener = window.eventRemoveListener;
  helper.eventClearEvent = window.eventClearEvent;
  helper.eventClearListener = window.eventClearListener;
  helper.eventClearAll = window.eventClearAll;
  helper.initializeGlobal = window.initializeGlobal;
  helper.getCurrentCharacterId = window.getCurrentCharacterId;
  helper.getCurrentCharacterName = window.getCurrentCharacterName;
  helper.getCharacterNames = window.getCharacterNames;
  helper.getCharacterIds = window.getCharacterIds;
  helper.getCharacter = window.getCharacter;
  helper.getPresetNames = window.getPresetNames;
  helper.getChatWorldbookName = window.getChatWorldbookName;
  var HELPER_SHARED = [
    'getLastMessage', 'setVariables', 'insertVariables', 'insertOrAssignVariables',
    'deleteVariable', 'getWorldbookNames', 'getCharWorldbookNames',
    'getGlobalWorldbookNames', 'getWorldbook', 'updateWorldbookWith',
    'replaceWorldbook', 'getCharName', 'getCurrentCharName', 'getCharAvatar',
    'getCurrentPersonaName', 'getCurrentPersonaId', 'retrieveDisplayedMessage',
    'injectPrompts', 'uninjectPrompts'
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
  var proxyFetch = null;
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
    proxyFetch = function(input, init){
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

  // /api/worldinfo/* 是酒馆的本地世界书接口，不应走外网代理；
  // 其余 URL 仅在宿主授予 net 能力时交给受控代理，否则直接拒绝。
  function chatFileData(){
    // 与作者面板一致：chat_metadata.variables 以当前 stat_data 为准，
    // 手机用 /api/chats/get 回读 dal_phone/dal_phone_config 做落盘校验。
    var metadata = clone(CHAT_METADATA);
    var flatVars = (VARIABLES && VARIABLES.stat_data && typeof VARIABLES.stat_data === 'object')
      ? clone(VARIABLES.stat_data) : {};
    var storedVars = metadata.variables && typeof metadata.variables === 'object'
      ? clone(metadata.variables) : {};
    metadata.variables = Object.assign({}, storedVars, flatVars);
    var out = [{
      user_name: USER_NAME,
      character_name: CHAR_NAME,
      create_date: '',
      chat_metadata: metadata
    }];
    for (var i = 0; i < CHAT.length; i += 1) {
      var item = CHAT[i] || {};
      var text = String(item.mes == null ? '' : item.mes);
      out.push({
        name: item.name || (item.is_user ? USER_NAME : CHAR_NAME),
        is_user: !!item.is_user,
        is_system: !!item.is_system,
        send_date: '',
        mes: text,
        extra: item.extra && typeof item.extra === 'object' ? clone(item.extra) : {},
        swipe_id: typeof item.swipe_id === 'number' ? item.swipe_id : 0,
        swipes: Array.isArray(item.swipes) ? clone(item.swipes) : [text],
        swipe_info: Array.isArray(item.swipe_info) && item.swipe_info.length
          ? clone(item.swipe_info)
          : [{}],
        variables: Array.isArray(item.variables) ? clone(item.variables) : [{}]
      });
    }
    return out;
  }
  // ST chat-completions 响应；卡运行时只读 choices[0].message.content。
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
      var signal = init && init.signal ? init.signal : null;
      return requestGenerateText(chatMessages, maxTokens, signal).then(function(text){
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
    // 设置 / 扩展 / 预设 / tokenizer 的只读宿主面（P1）。
    if (path.indexOf('/api/settings/get') >= 0) {
      var nativeState = window.__FLYAI_ST_NATIVE__ || {};
      return Promise.resolve(jsonResponse({
        settings: clone(nativeState.oaiSettings || {}),
        power_user: clone(nativeState.powerUser || {}),
        world_names: (nativeState.worldInfo && nativeState.worldInfo.names) || [],
        preset_names: { openai: [] },
        koboldai_settings: [],
        koboldai_setting_names: []
      }));
    }
    if (path.indexOf('/api/settings/save') >= 0) {
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (path.indexOf('/api/extensions/discover') >= 0) {
      return Promise.resolve(jsonResponse([
        { name: 'JS-Slash-Runner', type: 'local', enabled: true, version: '1.12.6' },
        { name: 'ST-Prompt-Template', type: 'local', enabled: true, version: '1.0.0' }
      ]));
    }
    if (path.indexOf('/api/extensions/version') >= 0) {
      return Promise.resolve(jsonResponse({}));
    }
    if (path.indexOf('/api/extensions/') >= 0) {
      return Promise.resolve(jsonResponse([]));
    }
    if (path.indexOf('/api/presets/') >= 0) {
      return Promise.resolve(jsonResponse(path.indexOf('/default') >= 0 ? [] : { ok: true }));
    }
    if (path.indexOf('/api/tokenizers/') >= 0) {
      var tokenText = '';
      if (Array.isArray(data.messages)) {
        tokenText = data.messages.map(function(item){
          return String((item && item.content) || '');
        }).join('\\n');
      } else if (typeof data.text === 'string') {
        tokenText = data.text;
      }
      if (path.indexOf('/encode') >= 0) {
        return Promise.resolve(jsonResponse(
          Array.from(tokenText).map(function(ch){ return ch.charCodeAt(0); })
        ));
      }
      if (path.indexOf('/decode') >= 0) {
        var tokenIds = Array.isArray(data.tokens) ? data.tokens : [];
        return Promise.resolve(jsonResponse(tokenIds.map(function(id){
          return String.fromCharCode(Number(id) || 0);
        }).join('')));
      }
      return Promise.resolve(jsonResponse({ count: Math.ceil(tokenText.length / 2) }));
    }
    if (path.indexOf('/api/chats/get') >= 0) {
      return Promise.resolve(jsonResponse(chatFileData()));
    }
    if (path.indexOf('/api/chats/save') >= 0) {
      var chat = data.chat;
      if (Array.isArray(chat) && chat[0] && chat[0].chat_metadata &&
          typeof chat[0].chat_metadata === 'object') {
        CHAT_METADATA = ensureChatIntegrity(clone(chat[0].chat_metadata));
        syncChatMetadata();
        if (CAP_WRITE) saveMetadata().catch(function(){});
      }
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (path.indexOf('/api/chats/rename') >= 0 ||
        path.indexOf('/api/chats/delete') >= 0) {
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
      var fname = String(data.fileName || data.file_name || '');
      fname = fname.replace(/[\\\\/:*?"<>|]/g, '_').replace(/^\\s+|\\s+$/g, '');
      return Promise.resolve(jsonResponse({ fileName: fname }));
    }
    return null;
  }
  window.fetch = function(input, init){
    var url = typeof input === 'string'
      ? input
      : (input && input.url ? String(input.url) : String(input));
    var local = worldinfoFetch(url, init);
    if (local) return local;
    var chatApi = chatApiFetch(url, init);
    if (chatApi) return chatApi;
    if (proxyFetch) return proxyFetch(input, init);
    return Promise.reject(new TypeError('Failed to fetch'));
  };

  // 只接受真实父窗口的合法消息；变量未变化时忽略（ready/load 重放去重）。
  window.addEventListener('message', function(e){
    if (!e || e.source !== HOST) return;
    var data = e.data;
    if (!data || typeof data !== 'object' || data.v !== VERSION) return;
    if (data.type === VARS) { applyVariables(data.variables); return; }
    if (data.type === CHAT_EVENT) {
      if (data.chat && typeof data.chat === 'object') {
        if (typeof data.chat.userName === 'string') USER_NAME = data.chat.userName;
        if (typeof data.chat.chatId === 'string') CHAT_ID = data.chat.chatId;
        if (ST_CONTEXT) {
          ST_CONTEXT.chatId = CHAT_ID;
          ST_CONTEXT.name1 = USER_NAME;
          if (data.chat.metadata && typeof data.chat.metadata === 'object') {
            CHAT_METADATA = ensureChatIntegrity(clone(data.chat.metadata));
          }
          ST_CONTEXT.chatMetadata = CHAT_METADATA;
        }
        applyChatUpdate(data.chat.messages);
      }
      return;
    }
    if (data.type === MESSAGE_EVENT) {
      CURRENT_MESSAGE = data.message == null ? '' : String(data.message);
      return;
    }
    if (data.type === ACTION_RESULT_EVENT) { settleAction(data); return; }
    if (data.type === METADATA_EVENT) {
      if (data.metadata && typeof data.metadata === 'object') {
        CHAT_METADATA = ensureChatIntegrity(clone(data.metadata));
        syncChatMetadata();
      }
      return;
    }
    if (data.type === METADATA_RESULT_EVENT) { settleMetadata(data); return; }
    if (data.type === MESSAGES_RESULT_EVENT) { settleMessages(data); return; }
    if (data.type === GENERATE_RESULT_EVENT) { settleMainGenerate(data); return; }
    if (data.type === GENERATE_TEXT_RESULT_EVENT) { settleTextGenerate(data); return; }
    if (data.type === WRITE_CTX_EVENT) { applyWriteContext(data.context); return; }
    if (data.type === DRAFT_EVENT) {
      var area = ensureComposer();
      if (area) {
        var next = typeof data.text === 'string' ? data.text : '';
        area.value = next;
        lastComposed = next;
      }
      return;
    }
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

/**
 * 卡文档已有 import map 时把原生模块条目并进去，避免出现第二张 map 覆盖
 * 卡片自己的 CDN 映射。返回 `merged=false` 表示应由调用方新注入一张 map。
 */
const mergeNativeImportMap = (
  doc: string,
  entries: Record<string, string>
): { doc: string; merged: boolean } => {
  const re = /<script\b[^>]*\btype\s*=\s*["']importmap["'][^>]*>([\s\S]*?)<\/script>/i
  const match = re.exec(doc)
  if (!match) {
    return { doc, merged: false }
  }
  try {
    const parsed = JSON.parse(match[1]) as Record<string, unknown> & {
      imports?: Record<string, string>
    }
    const imports: Record<string, string> = { ...(parsed.imports ?? {}) }
    for (const [key, value] of Object.entries(entries)) {
      if (!(key in imports)) {
        imports[key] = value
      }
    }
    const tag = `<script type="importmap">${JSON.stringify({
      ...parsed,
      imports,
    }).replace(/<\//g, '<\\/')}</script>`
    return {
      doc: doc.slice(0, match.index) + tag + doc.slice(match.index + match[0].length),
      merged: true,
    }
  } catch {
    return { doc, merged: false }
  }
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
  /** 聊天快照：SillyTavern.getContext().chat / 楼层检测 */
  chat?: CardAppChat | null
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
  // 原生宿主面：卡片运行时 import 的 ST 模块走 import map；已有 map 时并入。
  const nativeImports = buildNativeImportMapEntries()
  const mergedMap = mergeNativeImportMap(doc, nativeImports)
  doc = mergedMap.doc
  const nativeImportMap = mergedMap.merged
    ? ''
    : `<script type="importmap">${JSON.stringify({
        imports: nativeImports,
      }).replace(/<\//g, '<\\/')}</script>`
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
      options.capabilities,
      options.chat
    )}</script>` +
    inlineScript(ST_NATIVE_SNAPSHOT_SOURCE) +
    inlineScript(buildEjsTemplateSource()) +
    nativeImportMap

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
