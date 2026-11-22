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

  const setPath = (root: any, path: string[], value: unknown): void => {
    if (!path.length) return
    let cur = root
    for (const part of path.slice(0, -1)) {
      if (!cur[part] || typeof cur[part] !== 'object') cur[part] = {}
      cur = cur[part]
    }
    cur[path[path.length - 1]] = value
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

    const getMessageVar = (id: unknown, name: unknown): unknown => {
      if (!messageId || String(id) !== messageId) return undefined
      return clone(getPath(scopes.message, parts(name)))
    }

    const setMessageVar = (id: unknown, name: unknown, value: unknown): unknown => {
      if (!messageId || String(id) !== messageId) return value
      return setvar(name, value, { scope: 'message' })
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
    }

    const readOnly = () => {
      throw new Error('输出侧模板不允许调用注入/激活类 API')
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
      getwi: () => null,
      getpreset: () => null,
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
    const runner = runtime.render
    for (const cell of cells) {
      const id = String(cell?.id || '')
      try {
        const ctx: any = Object.create(api)
        ctx.char = char
        ctx.user = user
        ctx.group = group
        ctx.chat = chat
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
