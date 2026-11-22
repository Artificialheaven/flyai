/**
 * 酒馆卡状态面板：把开场白里的占位符渲染成完整面板 HTML，
 * 并在没有卡内模板时把 stat_data 拍平成键值行（原生兜底）。
 *
 * 渲染仍走展示侧正则引擎，结果再经 SanitizedHtml 白名单清洗；
 * 绝不执行卡内 JavaScript。
 */

import type { RegexScript } from '../flyai/types.js'
import { applyDisplayScripts, type MacroNames } from './regex.js'

/** 消息正文的合成“移除占位符”脚本不能进入面板渲染。 */
export const withoutAuthorRemovals = (
  scripts: readonly RegexScript[] | undefined
): RegexScript[] | undefined =>
  scripts?.filter((script) => script.flyaiAuthorRemoval !== true)

/** 渲染面板：占位符 + 展示侧正则 + 变量宏 → HTML 文本 */
export const renderPanel = (
  token: string,
  scripts: readonly RegexScript[] | undefined,
  variables: Record<string, unknown> | undefined,
  macroNames?: MacroNames
): string =>
  // 面板走白名单清洗，不抽沙箱应用（否则会残留哨兵）
  applyDisplayScripts(token, scripts, undefined, variables, {
    cardApps: false,
    macroNames,
  })

export type StatRow = { key: string; value: string }

const MAX_ROWS = 200
const MAX_VALUE = 500

const scalarText = (value: unknown): string => {
  if (value === null || value === undefined) {
    return ''
  }
  if (typeof value === 'string') {
    return value.length > MAX_VALUE ? `${value.slice(0, MAX_VALUE)}…` : value
  }
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

const isScalarArray = (value: unknown[]): boolean =>
  value.every(
    (item) =>
      item === null || item === undefined || typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean'
  )

/**
 * 把嵌套 stat_data 拍平成 `键路径 → 值` 行：
 * 对象递归成点路径，标量数组用「、」连接，对象数组按下标展开。
 */
export const statDataRows = (data: unknown, limit = MAX_ROWS): StatRow[] => {
  const rows: StatRow[] = []
  const push = (key: string, value: string) => {
    if (rows.length < limit) {
      rows.push({ key, value })
    }
  }
  const walk = (value: unknown, path: string) => {
    if (rows.length >= limit) {
      return
    }
    if (value === null || value === undefined) {
      push(path || '—', '')
      return
    }
    if (Array.isArray(value)) {
      if (value.length === 0) {
        push(path, '')
        return
      }
      if (isScalarArray(value)) {
        push(path, value.map((item) => scalarText(item)).join('、'))
        return
      }
      value.forEach((item, index) => walk(item, `${path}[${index}]`))
      return
    }
    if (typeof value === 'object') {
      const entries = Object.entries(value as Record<string, unknown>)
      if (entries.length === 0) {
        push(path || '—', '')
        return
      }
      for (const [key, child] of entries) {
        walk(child, path ? `${path}.${key}` : key)
      }
      return
    }
    push(path || '—', scalarText(value))
  }
  walk(data, '')
  return rows
}
