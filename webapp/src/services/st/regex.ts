/**
 * 酒馆（SillyTavern）regex_scripts 的前端展示侧解释器。
 *
 * 只解释卡内的声明式数据（findRegex/replaceString/placement 等），绝不执行
 * 卡内自带的 JavaScript。语义对齐后端 `flyai/st/regex_scripts.py` 的子集：
 * - findRegex 支持裸模式（按全局替换）与 `/pattern/flags` 字面量；
 * - placement 1=用户消息、2=AI 消息，缺省两侧都生效；
 * - markdownOnly 只在展示侧、promptOnly 只在出站 prompt 侧；
 *   两个标记同时为真时两侧都执行（对齐 ST `getRegexedString`）；
 * - trimStrings 在替换前从捕获组里剔除；
 * - 无效正则直接跳过，不影响其它脚本；
 * - 单 pattern ≤ 64KB、单次输出 ≤ 2MB，超限跳过。
 */

import type { RegexScript } from '../flyai/types.js'
import { APP_END, APP_START, isCardAppReplacement } from './cardApp.js'

export type CompiledRegexScript = {
  name: string
  regex: RegExp
  replace: string
  placement: number[]
  trimStrings: string[]
  minDepth: number | null
  maxDepth: number | null
  /** 替换结果是带脚本的整页 HTML：展示侧可包上哨兵交给沙箱 iframe */
  cardApp: boolean
}

export type RegexSide = 'display' | 'prompt'

/**
 * 正则实际生效的侧。与 ST `getRegexedString` 对齐：两个 only 标记同时为真时
 * 两侧都命中各自的判断条件，因此两侧都生效。
 */
export type RegexScriptSideInfo = 'prompt' | 'display' | 'both' | 'inactive'

export const regexScriptSide = (
  raw: RegexScript | null | undefined
): RegexScriptSideInfo => {
  if (!raw || typeof raw !== 'object') {
    return 'inactive'
  }
  if (raw.markdownOnly && raw.promptOnly) {
    return 'both'
  }
  if (raw.promptOnly) {
    return 'prompt'
  }
  if (raw.markdownOnly) {
    return 'display'
  }
  return 'both'
}

const MAX_SCRIPTS = 200
const MAX_PATTERN = 64 * 1024
const MAX_OUTPUT = 2 * 1024 * 1024
const SUPPORTED_FLAGS = /^[gimsuy]*$/

/** `/pattern/flags` 字面量拆分：按最后一个未转义 `/` 分隔。 */
export const splitRegexLiteral = (
  value: string
): { source: string; flags: string } => {
  const text = value.trim()
  if (!text.startsWith('/') || text.length < 2) {
    return { source: text, flags: 'g' }
  }
  let slash = -1
  for (let i = text.length - 1; i > 0; i -= 1) {
    if (text[i] !== '/') {
      continue
    }
    let backslashes = 0
    for (let j = i - 1; j >= 0 && text[j] === '\\'; j -= 1) {
      backslashes += 1
    }
    if (backslashes % 2 === 0) {
      slash = i
      break
    }
  }
  if (slash <= 0) {
    return { source: text, flags: 'g' }
  }
  const flags = text.slice(slash + 1)
  if (!SUPPORTED_FLAGS.test(flags)) {
    return { source: text, flags: 'g' }
  }
  return { source: text.slice(1, slash), flags }
}

const compileOne = (
  raw: RegexScript,
  side: RegexSide
): CompiledRegexScript | null => {
  if (!raw || typeof raw !== 'object' || raw.disabled) {
    return null
  }
  // ST 语义：两个 only 同时为真 → 展示侧与 prompt 侧都执行
  if (side === 'display' && raw.promptOnly && !raw.markdownOnly) {
    return null
  }
  if (side === 'prompt' && raw.markdownOnly && !raw.promptOnly) {
    return null
  }
  const find = typeof raw.findRegex === 'string' ? raw.findRegex : ''
  if (!find || find.length > MAX_PATTERN) {
    return null
  }
  const replace =
    typeof raw.replaceString === 'string' ? raw.replaceString : ''
  const { source, flags } = splitRegexLiteral(find)
  let regex: RegExp
  try {
    regex = new RegExp(source, flags)
  } catch {
    // 卡内脚本本身可能带着无效正则（如漏了转义的反斜杠），跳过即可
    return null
  }
  // ST 默认放置为「AI 输出」，与后端 `_placement` 保持一致
  let placement: number[] = []
  if (Array.isArray(raw.placement)) {
    placement = raw.placement
      .map((item) => Number(item))
      .filter((item) => item === 1 || item === 2)
  } else if (raw.placement === 1 || raw.placement === 2) {
    placement = [raw.placement]
  }
  if (placement.length === 0) {
    placement = [2]
  }
  const trimStrings = Array.isArray(raw.trimStrings)
    ? raw.trimStrings.filter((s): s is string => typeof s === 'string' && s !== '')
    : []
  // 0 / 负数表示无限制（对齐后端 _int_or_none）
  const asDepth = (value: unknown): number | null =>
    typeof value === 'number' && Number.isFinite(value) && value > 0
      ? Math.floor(value)
      : null
  return {
    name: typeof raw.scriptName === 'string' ? raw.scriptName : '',
    regex,
    replace,
    placement,
    trimStrings,
    minDepth: asDepth(raw.minDepth),
    maxDepth: asDepth(raw.maxDepth),
    cardApp: isCardAppReplacement(replace),
  }
}

/** 按原始脚本数组缓存编译结果（数组对象在会话缓存里保持稳定）。 */
const displayCache = new WeakMap<readonly RegexScript[], CompiledRegexScript[]>()
const promptCache = new WeakMap<readonly RegexScript[], CompiledRegexScript[]>()

export const compileRegexScripts = (
  raw: unknown,
  side: RegexSide
): CompiledRegexScript[] => {
  if (!Array.isArray(raw) || raw.length === 0) {
    return []
  }
  const cache = side === 'display' ? displayCache : promptCache
  const cached = cache.get(raw as readonly RegexScript[])
  if (cached) {
    return cached
  }
  const out: CompiledRegexScript[] = []
  for (const item of raw.slice(0, MAX_SCRIPTS)) {
    const compiled = compileOne(item as RegexScript, side)
    if (compiled) {
      out.push(compiled)
    }
  }
  cache.set(raw as readonly RegexScript[], out)
  return out
}

/**
 * 全局（预设）→ 本地（角色卡）顺序合并脚本。
 * 空的一侧直接返回原数组引用，保证 compileRegexScripts 的 WeakMap 缓存可用。
 */
export const mergeRegexScripts = (
  globalScripts: readonly RegexScript[] | null | undefined,
  localScripts: readonly RegexScript[] | null | undefined
): readonly RegexScript[] => {
  const global = Array.isArray(globalScripts) ? globalScripts : []
  const local = Array.isArray(localScripts) ? localScripts : []
  if (local.length === 0) {
    return global
  }
  if (global.length === 0) {
    return local
  }
  return [...global, ...local]
}

const trimValue = (value: string, trims: string[]): string => {
  let out = value
  for (const piece of trims) {
    if (piece) {
      out = out.split(piece).join('')
    }
  }
  return out
}

/** JS 原生 `$` 替换语义；捕获组先经 trimStrings 处理。 */
const expandReplacement = (
  template: string,
  match: string,
  groups: (string | undefined)[],
  named: Record<string, string | undefined> | undefined,
  trims: string[],
  offset: number,
  input: string
): string => {
  const pick = (value: string | undefined): string =>
    value === undefined ? '' : trimValue(value, trims)
  let out = ''
  for (let i = 0; i < template.length; i += 1) {
    const ch = template[i]
    if (ch !== '$' || i + 1 >= template.length) {
      out += ch
      continue
    }
    const next = template[i + 1]
    if (next === '$') {
      out += '$'
      i += 1
    } else if (next === '&') {
      out += trimValue(match, trims)
      i += 1
    } else if (next === '`') {
      out += input.slice(0, offset)
      i += 1
    } else if (next === "'") {
      out += input.slice(offset + match.length)
      i += 1
    } else if (next === '<') {
      const close = template.indexOf('>', i + 2)
      if (close > i + 2 && named) {
        out += pick(named[template.slice(i + 2, close)])
        i = close
      } else {
        out += ch
      }
    } else if (next >= '0' && next <= '9') {
      let num = Number(next)
      let end = i + 1
      if (i + 2 < template.length && template[i + 2] >= '0' && template[i + 2] <= '9') {
        const two = num * 10 + Number(template[i + 2])
        if (two <= groups.length) {
          num = two
          end = i + 2
        }
      }
      if (num === 0) {
        out += trimValue(match, trims)
      } else if (num <= groups.length) {
        out += pick(groups[num - 1])
      }
      i = end
    } else {
      out += ch
    }
  }
  return out
}

const applyOne = (
  text: string,
  script: CompiledRegexScript,
  wrapApps: boolean
): string => {
  // 每次替换都重置 lastIndex，避免带 g/y 的正则跨行残留状态
  script.regex.lastIndex = 0
  return text.replace(
    script.regex,
    (
      match: string,
      ...rest: Array<unknown>
    ): string => {
      // 回调参数：捕获组…、offset、input，有命名组时 groups 在最后
      const last = rest[rest.length - 1]
      const hasNamed = typeof last === 'object' && last !== null
      const named = hasNamed
        ? (last as Record<string, string | undefined>)
        : undefined
      const input = String(rest[rest.length - (hasNamed ? 2 : 1)])
      const offset = Number(rest[rest.length - (hasNamed ? 3 : 2)])
      const args = rest.slice(0, hasNamed ? -3 : -2)
      const groups = args.map((item) =>
        item === undefined || item === null ? undefined : String(item)
      )
      const replacement = expandReplacement(
        script.replace,
        match,
        groups,
        named,
        script.trimStrings,
        offset,
        input
      )
      // 展示侧把整页 HTML 应用包起来，解析前抽成占位符交给沙箱 iframe
      return wrapApps && script.cardApp
        ? `${APP_START}${replacement}${APP_END}`
        : replacement
    }
  )
}

export type ApplyOptions = {
  /** 1=用户消息，2=AI 消息 */
  placement: 1 | 2
  /** 距最新消息的深度；缺省不做深度过滤 */
  depth?: number
  side?: RegexSide
  /** 给整页 HTML 应用的替换结果包上哨兵（仅展示侧消息正文） */
  cardApps?: boolean
}

/** 按脚本顺序依次替换；无效/超限脚本已被 compileRegexScripts 过滤。 */
export const applyRegexScripts = (
  text: string,
  scripts: readonly CompiledRegexScript[] | undefined,
  options: ApplyOptions
): string => {
  if (!text || !scripts || scripts.length === 0) {
    return text
  }
  let out = text
  const depth = options.depth
  const wrapApps = options.cardApps === true
  for (const script of scripts) {
    if (!script.placement.includes(options.placement)) {
      continue
    }
    if (depth !== undefined) {
      if (script.minDepth !== null && depth < script.minDepth) {
        continue
      }
      if (script.maxDepth !== null && depth > script.maxDepth) {
        continue
      }
    }
    try {
      out = applyOne(out, script, wrapApps)
    } catch {
      continue
    }
    if (out.length > MAX_OUTPUT) {
      return out.slice(0, MAX_OUTPUT)
    }
  }
  return out
}

const UPDATE_BLOCK_RE = /<UpdateVariable\b[^>]*>[\s\S]*?<\/UpdateVariable\s*>/gi
// 部分卡把初值 <InitVar> 放在更新块之外（单独一块），同样不该显示给用户
const INIT_VAR_BLOCK_RE = /<InitVar\b[^>]*>[\s\S]*?<\/InitVar\s*>/gi
const VAR_MACRO_RE = /\{\{\s*(?:format|get)_message_variable::([^}]*)\}\}/gi

/** ``stat_data.a.b[0]`` → ["a", "b", "0"]（与后端 _path_parts 对齐）。 */
const variablePathParts = (path: string): string[] =>
  path
    .replace(/\[/g, '.')
    .replace(/\]/g, '.')
    .split('.')
    .map((seg) => seg.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean)

const formatVariable = (value: unknown): string => {
  if (value === undefined || value === null) {
    return ''
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false'
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return ''
    }
  }
  return String(value)
}

const resolveVariable = (
  variables: Record<string, unknown>,
  path: string
): unknown => {
  const root =
    variables.stat_data && typeof variables.stat_data === 'object'
      ? (variables.stat_data as Record<string, unknown>)
      : variables
  let rest = path.trim()
  if (rest === 'stat_data') {
    return root
  }
  if (rest.startsWith('stat_data.') || rest.startsWith('stat_data[')) {
    rest = rest.slice('stat_data'.length)
  }
  let cur: unknown = root
  for (const part of variablePathParts(rest)) {
    if (Array.isArray(cur)) {
      const idx = Number(part)
      if (!Number.isInteger(idx) || idx < 0 || idx >= cur.length) {
        return undefined
      }
      cur = cur[idx]
    } else if (cur && typeof cur === 'object') {
      const obj = cur as Record<string, unknown>
      if (!(part in obj)) {
        return undefined
      }
      cur = obj[part]
    } else {
      return undefined
    }
  }
  return cur
}

export type MacroNames = {
  char?: string
  user?: string
  group?: readonly string[]
}

const NAME_MACRO_RE = /\{\{\s*(char|user|group)\s*\}\}/gi

/**
 * 展开 ``{{char}}/{{user}}/{{group}}``。展示侧替换串里常带这些宏，
 * 与后端 ``render_name_macros`` 保持同一回退值。
 */
export const renderNameMacros = (
  text: string,
  names?: MacroNames | null
): string => {
  if (!text || !names) {
    return text
  }
  const group = (names.group ?? []).join('、')
  return text.replace(NAME_MACRO_RE, (_match, kind: string) => {
    const key = kind.toLowerCase()
    if (key === 'char') {
      return names.char || '角色'
    }
    if (key === 'user') {
      return names.user || '用户'
    }
    return group
  })
}

/**
 * 展开 MVU 变量宏 ``{{format_message_variable::stat_data.<path>}}``。
 * 只影响展示；缺失字段展开为空串，与后端 render_tavern_macros 一致。
 */
export const renderVariableMacros = (
  text: string,
  variables?: Record<string, unknown> | null
): string => {
  if (!text || !variables) {
    return text
  }
  return text.replace(VAR_MACRO_RE, (_match, path: string) =>
    formatVariable(resolveVariable(variables, path))
  )
}

/** 兜底：剥离残留的变量更新块（仅影响显示；存储原文保持不变）。 */
export const stripUpdateBlocks = (text: string): string => {
  if (!text) {
    return text
  }
  const out = text.replace(UPDATE_BLOCK_RE, '').replace(INIT_VAR_BLOCK_RE, '')
  return out.trim() ? out : ''
}

/** 展示侧便捷入口：编译 + 应用（AI 消息）。 */
export const applyDisplayScripts = (
  text: string,
  rawScripts: unknown,
  depth?: number,
  variables?: Record<string, unknown> | null,
  options?: { cardApps?: boolean; macroNames?: MacroNames }
): string => {
  const scripts = compileRegexScripts(rawScripts, 'display')
  const out =
    scripts.length === 0
      ? text
      : applyRegexScripts(text, scripts, {
          placement: 2,
          depth,
          cardApps: options?.cardApps !== false,
        })
  // 卡内没有对应的展示脚本时，<UpdateVariable> 原文不应出现在消息里
  return renderVariableMacros(
    renderNameMacros(stripUpdateBlocks(out), options?.macroNames),
    variables
  )
}

/** 展示侧便捷入口：用户消息（placement=1）。 */
export const applyDisplayScriptsToUser = (
  text: string,
  rawScripts: unknown,
  depth?: number
): string => {
  const scripts = compileRegexScripts(rawScripts, 'display')
  if (scripts.length === 0) {
    return text
  }
  return applyRegexScripts(text, scripts, { placement: 1, depth })
}
