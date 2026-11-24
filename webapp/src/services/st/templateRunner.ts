import { EJS_RUNTIME_SOURCE } from './ejs.js'

/**
 * EJS 模板执行器（方案 B）：null-origin sandbox iframe。
 *
 * 主窗口只负责投递单元格与只读上下文、收集渲染结果和变量 ops；
 * 执行器没有同源权限、没有网络（CSP connect-src 'none'），拿不到
 * token/localStorage/parent。执行完即销毁 iframe。
 */

export type TemplateCell = { id: string; text: string }

export type TemplateScopes = {
  message?: Record<string, unknown>
  local?: Record<string, unknown>
  global?: Record<string, unknown>
}

export type TemplateOp = {
  op: string
  scope?: string
  path: string
  value?: unknown
  delta?: number
}

export type TemplateContext = {
  cellId?: string
  messageId?: string
  scopes?: TemplateScopes
  /** 只读变量视图（如 MVU stat_data）；可读不可写 */
  readonly?: Record<string, unknown>
  defaultScope?: 'message' | 'local'
  char?: Record<string, unknown>
  user?: Record<string, unknown>
  group?: unknown[]
  chat?: Array<Record<string, unknown>>
  /** 提示词侧 <%%= %> 不转义；输出侧默认转义 */
  escapeOutput?: boolean
  limits?: { perCellMs?: number; totalMs?: number; maxOutput?: number }
}

export type TemplateRunResult = {
  cells: Record<string, string>
  ops: TemplateOp[]
  errors: Array<{ cellId?: string; message: string }>
  stats: { ms: number; count: number }
}

const DEFAULT_TOTAL_MS = 2500
const MAX_CELLS = 500

/* eslint-disable no-new-func */
const sandboxMain = () => {
  const runtime = (window as unknown as { __ejsRuntime: { render: Function } }).__ejsRuntime
  const MAX_OPS = 500

  const clone = (value: unknown): any => {
    if (value === undefined) return undefined
    try {
      return JSON.parse(JSON.stringify(value))
    } catch {
      return undefined
    }
  }

  const parts = (path: unknown): string[] => {
    if (typeof path !== 'string') return []
    return path
      .replace(/\[/g, '.')
      .replace(/\]/g, '.')
      .split('.')
      .map((part) => part.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean)
  }

  const getPath = (root: any, path: string[]): any => {
    let cur = root
    for (const part of path) {
      if (cur && typeof cur === 'object') cur = cur[part]
      else return undefined
    }
    return cur
  }

  const unsetPath = (root: any, path: string[]): void => {
    if (!path.length) return
    let cur = root
    for (const part of path.slice(0, -1)) {
      cur = cur && typeof cur === 'object' ? cur[part] : undefined
      if (!cur || typeof cur !== 'object') return
    }
    delete cur[path[path.length - 1]]
  }

  const findScope = (scopes: any, name: string): any => {
    if (name === 'global' || name === 'local' || name === 'message') return scopes[name] || {}
    return scopes.local || {}
  }

  const scopeName = (opts: unknown, fallback: string): string => {
    if (typeof opts === 'string') return opts
    if (opts && typeof opts === 'object' && (opts as any).scope) {
      return String((opts as any).scope)
    }
    return fallback
  }

  const optionsOf = (opts: unknown): any => {
    if (opts && typeof opts === 'object' && !Array.isArray(opts)) return opts as any
    return {}
  }

  async function run(payload: any): Promise<void> {
    const started = Date.now()
    const cells = Array.isArray(payload.cells) ? payload.cells.slice(0, 500) : []
    const context = payload.context || {}
    // 卡片世界书 EJS 会调用 globalThis.DALPhoneMainline.read() 取手机往来；
    // 服务端把作者面板同步的主线摘要放进 context.phoneMainline。
    const phoneMainline =
      typeof context.phoneMainline === 'string' ? context.phoneMainline : ''
    const phoneBridge = { read: async () => phoneMainline }
    ;(window as unknown as { DALPhoneMainline: unknown }).DALPhoneMainline = phoneBridge
    ;(globalThis as unknown as { DALPhoneMainline: unknown }).DALPhoneMainline =
      phoneBridge
    const scopes = {
      message: clone(context.scopes?.message) || {},
      local: clone(context.scopes?.local) || {},
      global: clone(context.scopes?.global) || {},
    }
    const readonly = clone(context.readonly) || {}
    const defaultScope = context.defaultScope === 'message' ? 'message' : 'local'
    const ops: any[] = []
    const errors: Array<{ cellId?: string; message: string }> = []
    const results: Record<string, string> = {}
    const messageId = String(context.messageId || '')

    const merged = (): any =>
      Object.assign({}, readonly, scopes.global, scopes.local, scopes.message)

    const pushOp = (op: any): void => {
      if (ops.length >= MAX_OPS) return
      ops.push(op)
    }

    const getvar = (name: unknown, opts?: unknown): unknown => {
      const optsObj = optionsOf(opts)
      const scope = findScope(scopes, scopeName(optsObj, defaultScope))
      const value = getPath(scope, parts(name))
      if (value !== undefined) return clone(value)
      const fallback = getPath(readonly, parts(name))
      if (fallback !== undefined) return clone(fallback)
      if ('defaults' in optsObj) return clone(optsObj.defaults)
      return clone(opts)
    }

    const setvar = (name: unknown, value: unknown, opts?: unknown): unknown => {
      const scopeNameValue = scopeName(opts, defaultScope)
      const scope = findScope(scopes, scopeNameValue)
      const path = parts(name)
      const clean = clone(value)
      setPath(scope, path, clean)
      pushOp({ op: 'vars.set', scope: scopeNameValue, path: path.join('.'), value: clean })
      return clean
    }

    const numeric = (name: unknown, delta: unknown, opts: unknown, sign: number): number => {
      const current = Number(getvar(name, opts))
      const base = Number.isFinite(current) ? current : 0
      const step = Number(delta)
      const value = Math.round((base + (Number.isFinite(step) ? step : 1) * sign) * 1e10) / 1e10
      setvar(name, value, opts)
      return value
    }

    const unsetvar = (name: unknown, opts?: unknown): void => {
      const scopeNameValue = scopeName(opts, defaultScope)
      const scope = findScope(scopes, scopeNameValue)
      const path = parts(name)
      unsetPath(scope, path)
      pushOp({ op: 'vars.unset', scope: scopeNameValue, path: path.join('.') })
    }

    const hasvar = (name: unknown, opts?: unknown): boolean =>
      getPath(findScope(scopes, scopeName(opts, defaultScope)), parts(name)) !== undefined ||
      getPath(readonly, parts(name)) !== undefined

    // 提示词侧可以按任意楼层读取变量；输出侧没有对应楼层时回退当前 message 作用域。
    const getMessageVar = (id: unknown, name: unknown): unknown => {
      const key = String(id ?? messageId ?? '')
      const bucket = key ? scopes.message[key] : scopes.message[messageId]
      const value = getPath(bucket && typeof bucket === 'object' ? bucket : {}, parts(name))
      if (value !== undefined) return clone(value)
      return messageId && key === messageId ? clone(getPath(scopes.message, parts(name))) : undefined
    }

    const setMessageVar = (id: unknown, name: unknown, value: unknown): unknown => {
      const key = String(id ?? messageId ?? '')
      if (!key) return value
      return setvar(name, value, { scope: 'message', message_id: key })
    }

    const substituteParams = (text: unknown): string => {
      const char = String(context.char?.name || '')
      const user = String(context.user?.name || '')
      const group = Array.isArray(context.group)
        ? context.group.map((item: any) => String(item?.name || '')).filter(Boolean).join('、')
        : ''
      return String(text ?? '')
        .replace(/\{\{\s*char\s*\}\}/gi, char)
        .replace(/\{\{\s*user\s*\}\}/gi, user)
        .replace(/\{\{\s*group\s*\}\}/gi, group)
        .replace(/\{\{\s*time\s*\}\}/gi, new Date().toLocaleTimeString())
        .replace(/\{\{\s*date\s*\}\}/gi, new Date().toLocaleDateString())
        .replace(/\{\{\s*newline\s*\}\}/gi, '\n')
        .replace(/\{\{\s*random::([^}]+)\}\}/gi, (_match, options) => {
          const list = String(options).split(',').map((item) => item.trim()).filter(Boolean)
          return list.length ? list[Math.floor(Math.random() * list.length)] : ''
        })
    }

    const readOnly = () => {
      // 输出侧：注入/激活没有意义，上游也只是不生效，不应中断渲染。
      // 提示词侧目前还没有宿主注入通道，返回 false 并告警（不 throw）。
      try { console.warn('[ejs] 注入/激活类 API 在当前侧不生效') } catch (ignore) {}
      return false
    }

    const worldbook = Array.isArray((context as any).worldbook)
      ? ((context as any).worldbook as any[])
      : []
    const preset = (context as any).preset || {}

    const getwi = (nameOrUid: unknown) => {
      if (!worldbook.length) return {}
      const key = String(nameOrUid ?? '')
      for (const entry of worldbook) {
        if (!entry || typeof entry !== 'object') continue
        if (String((entry as any).comment ?? '') === key || String((entry as any).title ?? '') === key) return clone(entry)
        if ((entry as any).uid != null && String((entry as any).uid) === key) return clone(entry)
      }
      return {}
    }

    const char = context.char || {}
    const user = context.user || {}
    const group = Array.isArray(context.group) ? context.group : []
    const chat = Array.isArray(context.chat) ? context.chat : []

    const api: any = {
      get variables() {
        return merged()
      },
      getvar,
      setvar,
      getGlobalVar: (name: unknown, opts?: unknown) =>
        getvar(name, Object.assign({}, optionsOf(opts), { scope: 'global' })),
      setGlobalVar: (name: unknown, value: unknown) =>
        setvar(name, value, { scope: 'global' }),
      incvar: (name: unknown, delta?: unknown, opts?: unknown) =>
        numeric(name, delta, opts, 1),
      decvar: (name: unknown, delta?: unknown, opts?: unknown) =>
        numeric(name, delta, opts, -1),
      unsetvar,
      hasvar,
      getMessageVar,
      setMessageVar,
      getchar: () => clone(char),
      getuser: () => clone(user),
      getgroup: () => clone(group),
      getwi,
      getpreset: (name?: unknown) => {
        if (!preset || typeof preset !== 'object') return {}
        if (name == null) return clone(preset)
        const key = String(name)
        return (preset as any)[key] === undefined ? {} : clone((preset as any)[key])
      },
      getChatMessage: (index: unknown) => {
        const idx = Number(index)
        return Number.isInteger(idx) && idx >= 0 && idx < chat.length ? clone(chat[idx]) : null
      },
      getChatMessages: () => clone(chat),
      substituteParams,
      getContext: () => ({
        char: String(char.name || ''),
        user: String(user.name || ''),
        messages: chat.length,
      }),
      injectPrompt: readOnly,
      activewi: readOnly,
      activateRegex: readOnly,
      evalTemplate: readOnly,
    }

    const escapeRaw = (value: unknown): string => String(value ?? '')
    // 上游模板自带 lodash/faker；不注入会让 `_.get(...)` 直接 ReferenceError。
    // 这里提供常用子集（不是完整实现，覆盖模板里的读取/遍历场景）。
    const setPath = (obj: any, path: unknown, value: unknown): any => {
      const parts = Array.isArray(path) ? path.map(String) : String(path ?? '').split('.')
      let cur = obj
      for (let i = 0; i < parts.length - 1; i += 1) {
        const k = parts[i]
        if (!cur[k] || typeof cur[k] !== 'object') cur[k] = {}
        cur = cur[k]
      }
      cur[parts[parts.length - 1]] = value
      return obj
    }
    const lodashShim: any = {
      get: (obj: any, path: unknown, def?: unknown) => {
        const parts = Array.isArray(path) ? path.map(String) : String(path ?? '').split('.')
        let cur = obj
        for (const k of parts) {
          if (cur == null) return def
          cur = cur[k]
        }
        return cur === undefined ? def : cur
      },
      set: setPath,
      has: (obj: any, path: unknown) => {
        const parts = Array.isArray(path) ? path.map(String) : String(path ?? '').split('.')
        let cur = obj
        for (const k of parts) {
          if (cur == null || !(k in Object(cur))) return false
          cur = cur[k]
        }
        return true
      },
      clone: (value: unknown) => clone(value),
      cloneDeep: (value: unknown) => clone(value),
      isEmpty: (value: any) => {
        if (value == null) return true
        if (Array.isArray(value) || typeof value === 'string') return value.length === 0
        if (typeof value === 'object') return Object.keys(value).length === 0
        return false
      },
      isArray: Array.isArray,
      isObject: (value: any) => value != null && typeof value === 'object' && !Array.isArray(value),
      keys: (value: any) => (value && typeof value === 'object' ? Object.keys(value) : []),
      values: (value: any) => (value && typeof value === 'object' ? Object.values(value) : []),
      map: (list: any, fn: any) => (Array.isArray(list) ? list.map(fn) : []),
      filter: (list: any, fn: any) => (Array.isArray(list) ? list.filter(fn) : []),
      find: (list: any, fn: any) => (Array.isArray(list) ? list.find(fn) : undefined),
      forEach: (list: any, fn: any) => { if (Array.isArray(list)) list.forEach(fn) },
      merge: (target: any, ...sources: any[]) => {
        for (const src of sources) {
          if (!src || typeof src !== 'object') continue
          for (const k of Object.keys(src)) {
            const v = src[k]
            if (v && typeof v === 'object' && !Array.isArray(v)) {
              target[k] = lodashShim.merge(target[k] && typeof target[k] === 'object' ? target[k] : {}, v)
            } else {
              target[k] = Array.isArray(v) ? v.slice() : v
            }
          }
        }
        return target
      },
    }
    const fakerShim: any = {
      person: {
        firstName: () => '林',
        lastName: () => '小雨',
        fullName: () => '林小雨',
      },
      name: {
        firstName: () => '林',
        lastName: () => '小雨',
        fullName: () => '林小雨',
      },
      location: { city: () => '滨莲市', streetAddress: () => '中央区 1 号' },
      lorem: { word: () => '风', words: () => '风 雨 夜', sentence: () => '夜色渐深。' },
      number: { int: (opts?: any) => {
        const min = Number(opts && opts.min) || 0
        const max = Number(opts && opts.max)
        const top = Number.isFinite(max) && max > min ? max : min + 100
        return Math.floor(min + Math.random() * (top - min))
      } },
      string: { uuid: () => 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0
        return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
      }) },
      random: { arrayElement: (list: any[]) => (Array.isArray(list) && list.length ? list[Math.floor(Math.random() * list.length)] : undefined) },
    }

    const runner = runtime.render
    for (const cell of cells) {
      const id = String(cell?.id || '')
      try {
        const ctx: any = Object.create(api)
        ctx.char = char
        ctx.user = user
        ctx.group = group
        ctx.chat = chat
        ctx._ = lodashShim
        ctx.lodash = lodashShim
        ctx.faker = fakerShim
        Object.defineProperty(ctx, 'variables', { get: merged, enumerable: true })
        if (context.escapeOutput === false) ctx.__escape = escapeRaw
        const output = await runner(String(cell?.text || ''), ctx)
        results[id] = output
      } catch (error) {
        errors.push({
          cellId: id,
          message: error instanceof Error ? error.message : String(error),
        })
        results[id] = ''
      }
    }
    ;(parent as Window).postMessage(
      {
        __flyaiTemplate: 1,
        id: payload.id,
        type: 'done',
        cells: results,
        ops,
        errors,
        stats: { ms: Date.now() - started, count: cells.length },
      },
      '*'
    )
  }

  window.addEventListener('message', (event) => {
    const data = (event as MessageEvent).data
    if (!data || data.__flyaiTemplateRequest !== 1) return
    run(data).catch((error) => {
      ;(parent as Window).postMessage(
        {
          __flyaiTemplate: 1,
          id: data.id,
          type: 'error',
          cells: {},
          ops: [],
          errors: [
            { message: error instanceof Error ? error.message : String(error) },
          ],
          stats: { ms: 0, count: 0 },
        },
        '*'
      )
    })
  })
}
/* eslint-enable no-new-func */

const RUNNER_SOURCE = `(${sandboxMain.toString()})();`

/** 测试用：验证嵌入 iframe 的源码在独立作用域里仍可执行。 */
export const __sandboxRunnerSource = RUNNER_SOURCE

const sandboxDoc = (): string => `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; img-src data:; style-src 'unsafe-inline'; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'">
</head>
<body>
<script>window.__ejsRuntime = ${EJS_RUNTIME_SOURCE};</script>
<script>${RUNNER_SOURCE}</script>
</body>
</html>`

let sequence = 0

const sanitizeOp = (value: unknown): TemplateOp | null => {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const op = typeof raw.op === 'string' ? raw.op : ''
  const path = typeof raw.path === 'string' ? raw.path : ''
  if (!op || !path) return null
  return {
    op,
    path,
    ...(typeof raw.scope === 'string' ? { scope: raw.scope } : {}),
    ...(raw.value === undefined ? {} : { value: raw.value }),
    ...(typeof raw.delta === 'number' ? { delta: raw.delta } : {}),
  }
}

/** 在沙箱 iframe 中执行一批模板单元格；超时/失败时拒绝。 */
export const runTemplateCells = (
  cells: TemplateCell[],
  context: TemplateContext = {}
): Promise<TemplateRunResult> => {
  if (cells.length === 0) {
    return Promise.resolve({ cells: {}, ops: [], errors: [], stats: { ms: 0, count: 0 } })
  }
  if (typeof document === 'undefined' || !document.body) {
    return Promise.reject(new Error('当前环境没有 DOM，无法执行模板'))
  }
  const totalMs = Math.max(200, Number(context.limits?.totalMs || DEFAULT_TOTAL_MS))
  const requestId = `tpl-${Date.now().toString(36)}-${(sequence += 1)}`
  const iframe = document.createElement('iframe')
  iframe.setAttribute('sandbox', 'allow-scripts')
  iframe.setAttribute('aria-hidden', 'true')
  iframe.style.display = 'none'
  iframe.srcdoc = sandboxDoc()

  return new Promise<TemplateRunResult>((resolve, reject) => {
    let settled = false
    const cleanup = (): void => {
      window.removeEventListener('message', onMessage)
      window.clearTimeout(timer)
      iframe.remove()
    }
    const finish = (fn: () => void): void => {
      if (settled) return
      settled = true
      cleanup()
      fn()
    }
    const onMessage = (event: MessageEvent): void => {
      if (event.source !== iframe.contentWindow) return
      const data = event.data as Record<string, unknown> | null
      if (!data || data.__flyaiTemplate !== 1 || data.id !== requestId) return
      finish(() => {
        if (data.type !== 'done') {
          const errors = Array.isArray(data.errors) ? data.errors : []
          const message = errors.length
            ? String((errors[0] as Record<string, unknown>)?.message || '模板执行失败')
            : '模板执行失败'
          reject(new Error(message))
          return
        }
        const output = data.cells && typeof data.cells === 'object'
          ? (data.cells as Record<string, string>)
          : {}
        const ops = Array.isArray(data.ops)
          ? data.ops
              .slice(0, 500)
              .map(sanitizeOp)
              .filter((item): item is TemplateOp => item !== null)
          : []
        const errors = Array.isArray(data.errors)
          ? data.errors.slice(0, 50).map((item) => {
              const record = (item && typeof item === 'object' ? item : {}) as Record<
                string,
                unknown
              >
              return {
                ...(typeof record.cellId === 'string' ? { cellId: record.cellId } : {}),
                message: String(record.message || '模板执行失败'),
              }
            })
          : []
        const stats = (data.stats && typeof data.stats === 'object'
          ? data.stats
          : {}) as Record<string, unknown>
        resolve({
          cells: output,
          ops,
          errors,
          stats: {
            ms: Number(stats.ms || 0),
            count: Number(stats.count || 0),
          },
        })
      })
    }
    const timer = window.setTimeout(() => {
      finish(() => reject(new Error('模板执行超时')))
    }, totalMs)
    window.addEventListener('message', onMessage)
    document.body.appendChild(iframe)
    iframe.onload = () => {
      try {
        iframe.contentWindow?.postMessage(
          {
            __flyaiTemplateRequest: 1,
            id: requestId,
            cells: cells.slice(0, MAX_CELLS),
            context,
          },
          '*'
        )
      } catch (error) {
        finish(() => reject(error instanceof Error ? error : new Error(String(error))))
      }
    }
  })
}
