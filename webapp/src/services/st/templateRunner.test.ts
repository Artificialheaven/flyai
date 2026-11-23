import { afterEach, describe, expect, it } from 'vitest'
import { createEjsRuntime } from './ejs.js'
import { __sandboxRunnerSource } from './templateRunner.js'

type Listener = (event: { data: unknown }) => void

const withSandbox = async (
  run: (send: (payload: Record<string, unknown>) => void) => void
): Promise<Record<string, unknown>[]> => {
  const posted: Record<string, unknown>[] = []
  let listener: Listener | null = null
  const fakeWindow = {
    __ejsRuntime: createEjsRuntime(),
    addEventListener: (_type: string, fn: Listener) => {
      listener = fn
    },
  }
  const previousWindow = (globalThis as Record<string, unknown>).window
  const previousParent = (globalThis as Record<string, unknown>).parent
  ;(globalThis as Record<string, unknown>).window = fakeWindow
  ;(globalThis as Record<string, unknown>).parent = {
    postMessage: (message: Record<string, unknown>) => posted.push(message),
  }
  try {
    new Function(__sandboxRunnerSource)()
    if (!listener) {
      throw new Error('runner 未注册 message 监听')
    }
    run((payload) => (listener as Listener)({ data: payload }))
    // runner 现在按单元格 await，等一个宏任务让 postMessage 落地
    await new Promise((resolve) => setTimeout(resolve, 0))
    return posted
  } finally {
    ;(globalThis as Record<string, unknown>).window = previousWindow
    ;(globalThis as Record<string, unknown>).parent = previousParent
  }
}

afterEach(() => {
  // 每次测试自行恢复，这里只作为可读性提示
})

describe('模板沙箱 runner', () => {
  it('执行单元格并记录变量 ops', async () => {
    const posted = await withSandbox((send) => {
      send({
        __flyaiTemplateRequest: 1,
        id: 'req1',
        cells: [
          {
            id: 'c1',
            text:
              '<%= getvar("hp") %><% setvar("hp", getvar("hp") + 1) %><% incvar("count", 1, { scope: "local" }) %><%= variables.hp %>',
          },
        ],
        context: {
          messageId: 'm1',
          scopes: {
            message: { hp: 3 },
            local: { count: 1 },
            global: { g: 1 },
          },
          defaultScope: 'message',
          escapeOutput: true,
          char: { name: '小猫' },
          user: { name: '用户' },
        },
      })
    })
    expect(posted).toHaveLength(1)
    const result = posted[0] as Record<string, any>
    expect(result.type).toBe('done')
    expect(result.cells.c1).toBe('34')
    expect(result.ops).toEqual([
      { op: 'vars.set', scope: 'message', path: 'hp', value: 4 },
      { op: 'vars.set', scope: 'local', path: 'count', value: 2 },
    ])
    expect(result.errors).toEqual([])
  })

  it('支持模板内的 await', async () => {
    const posted = await withSandbox((send) => {
      send({
        __flyaiTemplateRequest: 1,
        id: 'req5',
        cells: [
          {
            id: 'c1',
            text: '<% let v = ""; try { v = await Promise.resolve("ok") } catch (_) {} -%><%= v %>',
          },
        ],
        context: { messageId: 'm1', scopes: {}, escapeOutput: true },
      })
    })
    const result = posted[0] as Record<string, any>
    expect(result.type).toBe('done')
    expect(result.cells.c1).toBe('ok')
    expect(result.errors).toEqual([])
  })

  it('输出侧禁止注入类 API', async () => {
    const posted = await withSandbox((send) => {
      send({
        __flyaiTemplateRequest: 1,
        id: 'req2',
        cells: [{ id: 'c1', text: '<% injectPrompt("x") %>' }],
        context: { messageId: 'm1', scopes: {}, escapeOutput: true },
      })
    })
    const result = posted[0] as Record<string, any>
    expect(result.cells.c1).toBe('')
    expect(result.errors[0].message).toContain('不允许')
  })

  it('手机主线摘要通过 globalThis.DALPhoneMainline 注入', async () => {
    const posted = await withSandbox((send) => {
      send({
        __flyaiTemplateRequest: 1,
        id: 'req-phone',
        cells: [
          {
            id: 'c1',
            text: "<%= await globalThis.DALPhoneMainline.read() %>",
          },
        ],
        context: {
          messageId: 'm1',
          scopes: {},
          escapeOutput: true,
          phoneMainline: '【手机往来】十香：在吗',
        },
      })
    })
    const result = posted[0] as Record<string, any>
    expect(result.type).toBe('done')
    expect(result.cells.c1).toBe('【手机往来】十香：在吗')
    expect(result.errors).toEqual([])
  })

  it('只读 stat_data 可通过 getvar 读取', async () => {
    const posted = await withSandbox((send) => {
      send({
        __flyaiTemplateRequest: 1,
        id: 'req4',
        cells: [{ id: 'c1', text: '<%= getvar("stat_data.hp", { defaults: 0 }) %>' }],
        context: {
          messageId: 'm1',
          scopes: { message: {} },
          readonly: { stat_data: { hp: 7 } },
        },
      })
    })
    const result = posted[0] as Record<string, any>
    expect(result.cells.c1).toBe('7')
    expect(result.ops).toEqual([])
  })

  it('默认转义输出，escapeOutput=false 时原样', async () => {
    const posted = await withSandbox((send) => {
      send({
        __flyaiTemplateRequest: 1,
        id: 'req3',
        cells: [{ id: 'c1', text: '<%= variables.html %>' }],
        context: { messageId: 'm1', scopes: { message: { html: '<b>x</b>' } } },
      })
    })
    const result = posted[0] as Record<string, any>
    expect(result.cells.c1).toBe('&lt;b&gt;x&lt;/b&gt;')
  })
})
