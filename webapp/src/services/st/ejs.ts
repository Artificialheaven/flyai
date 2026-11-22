/**
 * 酒馆 EJS 子集编译器（方案 B：只在客户端 null-origin 沙箱里执行）。
 *
 * 支持：
 * - `<% code %>` 语句、`<%= expr %>`（HTML 转义）、`<%- expr %>`（原样）
 * - `<%# comment %>` 注释
 * - `<%_` / `_%>` / `-%>` 空白裁剪
 * - `<#escape-ejs>…</#escape-ejs>` 转义区
 *
 * 不执行 include / 异步 / import；输出长度由调用方限额。
 */

export type EjsRender = (template: string, context: Record<string, unknown>) => string

type Segment =
  | { kind: 'text'; value: string }
  | { kind: 'code'; value: string }
  | { kind: 'escaped'; value: string }
  | { kind: 'raw'; value: string }

const MAX_TEMPLATE_CHARS = 2 * 1024 * 1024

const findClose = (text: string, from: number): number => {
  let quote = ''
  let lineComment = false
  let blockComment = false
  for (let i = from; i < text.length - 1; i += 1) {
    const ch = text[i]
    const next = text[i + 1]
    if (lineComment) {
      if (ch === '\n') {
        lineComment = false
      }
      continue
    }
    if (blockComment) {
      if (ch === '*' && next === '/') {
        blockComment = false
        i += 1
      }
      continue
    }
    if (quote) {
      if (ch === '\\') {
        i += 1
        continue
      }
      if (ch === quote) {
        quote = ''
      }
      continue
    }
    if (ch === '/' && next === '/') {
      lineComment = true
      i += 1
      continue
    }
    if (ch === '/' && next === '*') {
      blockComment = true
      i += 1
      continue
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      quote = ch
      continue
    }
    if (ch === '%' && next === '>') {
      return i
    }
  }
  return -1
}

/** 把模板切成“文本 / 代码 / 输出”段；未闭合标记按错误处理。 */
export const scanEjs = (template: string): Segment[] => {
  if (typeof template !== 'string') {
    return []
  }
  if (template.length > MAX_TEMPLATE_CHARS) {
    throw new Error('模板超过 2MB 上限')
  }
  // 转义区先替换成哨兵，避免内部 <% 被当成标签
  const escapedParts: string[] = []
  const prepared = template.replace(
    /<#escape-ejs>([\s\S]*?)<\/#escape-ejs>/g,
    (_m, body: string) => {
      const index = escapedParts.push(body) - 1
      return `\u0000EJS_ESCAPE_${index}\u0000`
    }
  )
  const segments: Segment[] = []
  let pos = 0
  while (pos < prepared.length) {
    const start = prepared.indexOf('<%', pos)
    if (start < 0) {
      segments.push({ kind: 'text', value: prepared.slice(pos) })
      break
    }
    if (start > pos) {
      segments.push({ kind: 'text', value: prepared.slice(pos, start) })
    }
    let cursor = start + 2
    let kind: Segment['kind'] = 'code'
    const mark = prepared[cursor]
    if (mark === '=' || mark === '-' || mark === '#' || mark === '_') {
      kind = mark === '=' ? 'escaped' : mark === '-' ? 'raw' : mark === '#' ? 'code' : 'code'
      cursor += 1
    }
    const close = findClose(prepared, cursor)
    if (close < 0) {
      throw new Error('未闭合的模板标记')
    }
    let closeAt = close
    let trimAfter: '' | '-' | '_' = ''
    if (prepared[closeAt - 1] === '-' || prepared[closeAt - 1] === '_') {
      trimAfter = prepared[closeAt - 1] as '-' | '_'
      closeAt -= 1
    }
    let code = prepared.slice(cursor, closeAt)
    if (mark === '_') {
      const prev = segments[segments.length - 1]
      if (prev && prev.kind === 'text') {
        prev.value = prev.value.replace(/\s+$/u, '')
      }
    }
    if (kind !== 'code' || mark !== '#') {
      if (mark === '#') {
        // 注释：不产生任何输出
      } else if (kind === 'code') {
        segments.push({ kind: 'code', value: code })
      } else if (kind === 'escaped') {
        segments.push({ kind: 'escaped', value: code })
      } else {
        segments.push({ kind: 'raw', value: code })
      }
    }
    pos = close + 2
    if (trimAfter === '_') {
      while (pos < prepared.length && /\s/u.test(prepared[pos])) {
        pos += 1
      }
    } else if (trimAfter === '-') {
      if (prepared[pos] === '\r' && prepared[pos + 1] === '\n') {
        pos += 2
      } else if (prepared[pos] === '\n') {
        pos += 1
      }
    }
  }
  // 还原转义区文本
  const restore = (value: string): string =>
    value.replace(/\u0000EJS_ESCAPE_(\d+)\u0000/g, (_m, index: string) => {
      return escapedParts[Number(index)] ?? ''
    })
  return segments.map((segment) =>
    segment.kind === 'text' ? { ...segment, value: restore(segment.value) } : segment
  )
}

export type EjsRuntime = {
  render: EjsRender
}

/**
 * 自包含的 EJS 运行时工厂。`toString()` 会直接嵌进沙箱 iframe 的 srcdoc，
 * 因此内部不能引用这个模块里的任何其他变量。
 */
export const createEjsRuntime = (): EjsRuntime => {
  const MAX_TEMPLATE = 2 * 1024 * 1024
  const MAX_OUTPUT = 256 * 1024

  const escapeHtml = (value: unknown): string =>
    String(value ?? '').replace(/[&<>"']/g, (ch) => {
      switch (ch) {
        case '&':
          return '&amp;'
        case '<':
          return '&lt;'
        case '>':
          return '&gt;'
        case '"':
          return '&quot;'
        default:
          return '&#39;'
      }
    })

  const findClose = (text: string, from: number): number => {
    let quote = ''
    let lineComment = false
    let blockComment = false
    for (let i = from; i < text.length - 1; i += 1) {
      const ch = text[i]
      const next = text[i + 1]
      if (lineComment) {
        if (ch === '\n') lineComment = false
        continue
      }
      if (blockComment) {
        if (ch === '*' && next === '/') {
          blockComment = false
          i += 1
        }
        continue
      }
      if (quote) {
        if (ch === '\\') {
          i += 1
          continue
        }
        if (ch === quote) quote = ''
        continue
      }
      if (ch === '/' && next === '/') {
        lineComment = true
        i += 1
        continue
      }
      if (ch === '/' && next === '*') {
        blockComment = true
        i += 1
        continue
      }
      if (ch === '"' || ch === "'" || ch === '`') {
        quote = ch
        continue
      }
      if (ch === '%' && next === '>') return i
    }
    return -1
  }

  const scan = (template: string): Array<Record<string, string>> => {
    if (typeof template !== 'string') return []
    if (template.length > MAX_TEMPLATE) throw new Error('模板超过 2MB 上限')
    const escaped: string[] = []
    const prepared = template.replace(/<#escape-ejs>([\s\S]*?)<\/#escape-ejs>/g, (
      _m: string,
      body: string
    ) => {
      const index = escaped.push(body) - 1
      return `\u0000EJS_ESCAPE_${index}\u0000`
    })
    const segments: Array<Record<string, string>> = []
    let pos = 0
    while (pos < prepared.length) {
      const start = prepared.indexOf('<%', pos)
      if (start < 0) {
        segments.push({ kind: 'text', value: prepared.slice(pos) })
        break
      }
      if (start > pos) segments.push({ kind: 'text', value: prepared.slice(pos, start) })
      let cursor = start + 2
      const mark = prepared[cursor]
      let kind = 'code'
      if (mark === '=' || mark === '-' || mark === '#' || mark === '_') {
        kind = mark === '=' ? 'escaped' : mark === '-' ? 'raw' : 'code'
        cursor += 1
      }
      const close = findClose(prepared, cursor)
      if (close < 0) throw new Error('未闭合的模板标记')
      let closeAt = close
      let trimAfter = ''
      if (prepared[closeAt - 1] === '-' || prepared[closeAt - 1] === '_') {
        trimAfter = prepared[closeAt - 1]
        closeAt -= 1
      }
      const code = prepared.slice(cursor, closeAt)
      if (mark === '_') {
        const prev = segments[segments.length - 1]
        if (prev && prev.kind === 'text') prev.value = prev.value.replace(/\s+$/u, '')
      }
      if (mark !== '#') segments.push({ kind, value: code })
      pos = close + 2
      if (trimAfter === '_') {
        while (pos < prepared.length && /\s/u.test(prepared[pos])) pos += 1
      } else if (trimAfter === '-') {
        if (prepared[pos] === '\r' && prepared[pos + 1] === '\n') pos += 2
        else if (prepared[pos] === '\n') pos += 1
      }
    }
    const restore = (value: string): string =>
      value.replace(/\u0000EJS_ESCAPE_(\d+)\u0000/g, (_m: string, index: string) =>
        escaped[Number(index)] ?? ''
      )
    return segments.map((segment) =>
      segment.kind === 'text' ? { ...segment, value: restore(segment.value) } : segment
    )
  }

  const compile = (template: string): Function => {
    const lines: string[] = ['var __out = "";']
    for (const segment of scan(template)) {
      if (segment.kind === 'text') {
        if (segment.value) lines.push(`__out += ${JSON.stringify(segment.value)};`)
      } else if (segment.kind === 'code') {
        lines.push(segment.value)
      } else if (segment.kind === 'escaped') {
        lines.push(`__out += __escape(${segment.value});`)
      } else {
        lines.push(`__out += String((${segment.value}) ?? "");`)
      }
    }
    lines.push('return __out;')
    return new Function('ctx', `with (ctx) {\n${lines.join('\n')}\n}`)
  }

  const cache = new Map<string, Function>()
  const render: EjsRender = (template, context) => {
    let fn = cache.get(template)
    if (!fn) {
      fn = compile(template)
      if (cache.size >= 256) cache.clear()
      cache.set(template, fn)
    }
    const base = context || {}
    const ctx = Object.create(base)
    if (!('__escape' in base)) ctx.__escape = escapeHtml
    const out = String(fn(ctx) ?? '')
    if (out.length > MAX_OUTPUT) throw new Error('模板输出超过 256KB')
    return out
  }

  return { render }
}

/** 嵌进沙箱 iframe 的自包含运行时源码。 */
export const EJS_RUNTIME_SOURCE = `(${createEjsRuntime.toString()})()`

/** 主线程调试/测试用；生产渲染走沙箱里的同源实现。 */
export const renderEjs = (template: string, context: Record<string, unknown>): string =>
  createEjsRuntime().render(template, context)
