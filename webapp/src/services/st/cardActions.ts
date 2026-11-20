/**
 * 卡内授权动作的纯函数层：意图白名单、大小校验与每会话限流。
 *
 * 这里只做前端预检（在弹窗确认前尽早拒绝明显非法的请求），最终闸门仍是后端：
 * 会话归属、开关、活动 run、字段白名单与网络策略都在服务端复核。
 */

import {
  CARD_CODE_ACTION_FAILED,
  CARD_CODE_UNAVAILABLE,
  type CardAppCapabilities,
} from './cardApp.js'

/** 发送动作的文本上限（与聊天气泡的实际承载量一致） */
export const MAX_CARD_SEND_TEXT = 32 * 1024
/** 变量写入序列化上限，与后端一致 */
export const MAX_CARD_VARS_BYTES = 128 * 1024
export const MAX_CARD_VARS_OPS = 200
export const MAX_CARD_VARS_PATH = 512
/** 世界书条目数与序列化上限 */
export const MAX_CARD_WORLD_ENTRIES = 500
export const MAX_CARD_WORLD_BYTES = 2 * 1024 * 1024
/** 角色字段写入序列化上限 */
export const MAX_CARD_CHARACTER_BYTES = 64 * 1024

/** 角色副本字段白名单（与后端 routes_session 保持一致） */
export const CARD_CHARACTER_FIELDS = [
  'name',
  'intro',
  'background',
  'mes_example',
  'table',
  'prompt_format',
  'prompt_tools',
  'system_prompt',
  'post_history_instructions',
  'first_mes',
  'alternate_greetings',
] as const

/** 生成下发给卡片的角色白名单字段快照；缺省字段不出现 */
export const pickCardCharacterFields = (
  character: Record<string, unknown> | null | undefined
): Record<string, unknown> | null => {
  if (!character) {
    return null
  }
  const fields: Record<string, unknown> = {}
  for (const key of CARD_CHARACTER_FIELDS) {
    if (character[key] !== undefined) {
      fields[key] = character[key]
    }
  }
  return Object.keys(fields).length > 0 ? fields : null
}

export type CardActionKind = 'send' | 'continue' | 'regenerate'

export type CardIntent =
  | { kind: 'action'; action: CardActionKind; text?: string }
  | { kind: 'vars'; mode: 'patch'; ops: Record<string, unknown>[] }
  | { kind: 'vars'; mode: 'replace'; stat_data: Record<string, unknown> }
  | { kind: 'world'; entries: unknown[] }
  | { kind: 'character'; fields: Record<string, unknown> }

export type CardIntentCheck =
  | { ok: true; intent: CardIntent }
  | { ok: false; code: string; message: string }

const fail = (message: string, code = CARD_CODE_UNAVAILABLE): CardIntentCheck => ({
  ok: false,
  code,
  message,
})

/** 安全序列化：循环/异常返回 null */
export const cardJsonSize = (value: unknown): number | null => {
  try {
    const text = JSON.stringify(value)
    return typeof text === 'string' ? text.length : null
  } catch (err) {
    return null
  }
}

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)

/**
 * 校验卡内意图。只接受协议内定义的类型；其余一律按不可用处理。
 * `capabilities` 用于在宿主侧确认能力确实授权（防止旧版宿主误转发）。
 */
export const checkCardIntent = (
  input: unknown,
  capabilities: CardAppCapabilities
): CardIntentCheck => {
  if (!isPlainObject(input)) {
    return fail('invalid-intent')
  }
  const kind = input.kind
  if (kind === 'action') {
    if (!capabilities.actions) {
      return fail('actions-disabled')
    }
    const action = input.action
    if (action !== 'send' && action !== 'continue' && action !== 'regenerate') {
      return fail('invalid-action')
    }
    if (action === 'send') {
      if (typeof input.text !== 'string' || !input.text.trim()) {
        return fail('empty-text')
      }
      if (input.text.length > MAX_CARD_SEND_TEXT) {
        return fail('text-too-large', CARD_CODE_ACTION_FAILED)
      }
    }
    const intent: CardIntent = { kind: 'action', action }
    if (action === 'send') {
      intent.text = input.text as string
    }
    return { ok: true, intent }
  }
  if (kind === 'vars') {
    if (!capabilities.write) {
      return fail('write-disabled')
    }
    if (input.mode === 'patch') {
      if (!Array.isArray(input.ops) || input.ops.length === 0) {
        return fail('empty-patch')
      }
      if (input.ops.length > MAX_CARD_VARS_OPS) {
        return fail('too-many-ops', CARD_CODE_ACTION_FAILED)
      }
      const ops: Record<string, unknown>[] = []
      for (const raw of input.ops) {
        if (!isPlainObject(raw) || typeof raw.op !== 'string') {
          return fail('invalid-op')
        }
        if (!['add', 'remove', 'replace', 'move', 'copy', 'test'].includes(raw.op)) {
          return fail('invalid-op')
        }
        if (typeof raw.path !== 'string' || raw.path.length > MAX_CARD_VARS_PATH) {
          return fail('invalid-path')
        }
        ops.push(raw)
      }
      const size = cardJsonSize({ stat_data: null, ops })
      if (size === null || size > MAX_CARD_VARS_BYTES) {
        return fail('vars-too-large', CARD_CODE_ACTION_FAILED)
      }
      return { ok: true, intent: { kind: 'vars', mode: 'patch', ops } }
    }
    if (input.mode === 'replace') {
      if (!isPlainObject(input.stat_data)) {
        return fail('invalid-stat-data')
      }
      const size = cardJsonSize(input.stat_data)
      if (size === null || size > MAX_CARD_VARS_BYTES) {
        return fail('vars-too-large', CARD_CODE_ACTION_FAILED)
      }
      return { ok: true, intent: { kind: 'vars', mode: 'replace', stat_data: input.stat_data } }
    }
    return fail('invalid-mode')
  }
  if (kind === 'world') {
    if (!capabilities.write) {
      return fail('write-disabled')
    }
    if (!Array.isArray(input.entries)) {
      return fail('invalid-worldbook')
    }
    if (input.entries.length > MAX_CARD_WORLD_ENTRIES) {
      return fail('worldbook-too-large', CARD_CODE_ACTION_FAILED)
    }
    const size = cardJsonSize(input.entries)
    if (size === null || size > MAX_CARD_WORLD_BYTES) {
      return fail('worldbook-too-large', CARD_CODE_ACTION_FAILED)
    }
    return { ok: true, intent: { kind: 'world', entries: input.entries } }
  }
  if (kind === 'character') {
    if (!capabilities.write) {
      return fail('write-disabled')
    }
    if (!isPlainObject(input.fields)) {
      return fail('invalid-fields')
    }
    const allowed = new Set<string>(CARD_CHARACTER_FIELDS)
    const fields: Record<string, unknown> = {}
    for (const key of Object.keys(input.fields)) {
      if (!allowed.has(key)) {
        return fail(`field-not-allowed:${key}`, CARD_CODE_ACTION_FAILED)
      }
      fields[key] = input.fields[key]
    }
    if (Object.keys(fields).length === 0) {
      return fail('empty-fields')
    }
    const size = cardJsonSize(fields)
    if (size === null || size > MAX_CARD_CHARACTER_BYTES) {
      return fail('fields-too-large', CARD_CODE_ACTION_FAILED)
    }
    return { ok: true, intent: { kind: 'character', fields } }
  }
  return fail('invalid-kind')
}

/** 能力与当前消息上下文共同决定实际授权；任一项不满足即关闭 */
export const resolveCapabilities = (
  settings: CardAppCapabilities,
  host: { editable: boolean; isLast: boolean; streaming: boolean }
): CardAppCapabilities => ({
  actions: settings.actions && host.editable && host.isLast && !host.streaming,
  // 写入不要求末条消息，但流式期间拒绝（避免与后端活动 run 竞争）
  write: settings.write && host.editable && !host.streaming,
  net: settings.net && host.editable && !host.streaming,
})

export type CardActionRateLimiter = {
  /** 尝试占用一次动作额度；超限返回 false */
  tryAcquire: (now: number) => boolean
  /** 当前窗口内的动作时间戳（测试用） */
  snapshot: () => number[]
}

export type CardActionLimiterOptions = {
  /** 相邻两次动作的最小间隔 */
  minIntervalMs?: number
  /** 每分钟上限 */
  perMinute?: number
}

/** 每会话内存级限流：默认 1 次/2 秒、10 次/分钟 */
export const createCardActionLimiter = (
  options: CardActionLimiterOptions = {}
): CardActionRateLimiter => {
  const minIntervalMs = options.minIntervalMs ?? 2000
  const perMinute = options.perMinute ?? 10
  let stamps: number[] = []
  return {
    tryAcquire: (now: number) => {
      stamps = stamps.filter((stamp) => now - stamp < 60_000)
      const last = stamps.at(-1)
      if (last !== undefined && now - last < minIntervalMs) {
        return false
      }
      if (stamps.length >= perMinute) {
        return false
      }
      stamps.push(now)
      return true
    },
    snapshot: () => stamps.slice(),
  }
}
