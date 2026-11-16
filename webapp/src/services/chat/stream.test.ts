import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatStreamController, type ChatSendPayload } from './stream.js'
import { markerStorageKey } from './runMarker.js'
import type { LiveRunState } from './streamReducer.js'

const frame = (id: number, event: string, data: unknown): string =>
  `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`

const sseResponse = (frames: string[], headers: Record<string, string> = {}): Response =>
  new Response(
    new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder()
        for (const f of frames) controller.enqueue(encoder.encode(f))
        controller.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream', ...headers } }
  )

const jsonResponse = (body: unknown, init: ResponseInit = {}): Response =>
  new Response(JSON.stringify(body), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  })

const payload = (over: Partial<ChatSendPayload> = {}): ChatSendPayload => ({
  message: '你好',
  session_id: 's1',
  thread_id: 't1',
  tools_enabled: true,
  images: [],
  ...over,
})

const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0))

type Harness = {
  controller: ChatStreamController
  states: LiveRunState[]
  toasts: Array<[string, boolean | undefined]>
  reverted: ChatSendPayload[]
  terminal: Promise<void>
  statuses: Array<[string, boolean]>
  runIds: string[]
}

const makeHarness = (): Harness => {
  const states: LiveRunState[] = []
  const toasts: Array<[string, boolean | undefined]> = []
  const reverted: ChatSendPayload[] = []
  const statuses: Array<[string, boolean]> = []
  const runIds: string[] = []
  let resolveTerminal: () => void = () => {}
  const terminal = new Promise<void>((resolve) => {
    resolveTerminal = resolve
  })
  const controller = new ChatStreamController({
    onState: (s) => states.push(s),
    onStatus: (status, busy) => statuses.push([status, busy]),
    onToast: (message, error) => toasts.push([message, error]),
    onRevertDraft: (p) => reverted.push(p),
    onTerminal: () => resolveTerminal(),
    onRunId: (id) => runIds.push(id),
  })
  return { controller, states, toasts, reverted, terminal, statuses, runIds }
}

describe('ChatStreamController', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('正常一轮：读 X-Run-Id、逐帧归约、终态清标记', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/chat') {
        return sseResponse(
          [
            frame(1, 'message_start', { session_id: 's1', thread_id: 't1' }),
            frame(2, 'speaker', { member_id: 'm1', name: '小爱' }),
            frame(3, 'content_delta', { text: '你好' }),
            frame(4, 'done', { usage: { total_tokens: 12 } }),
          ],
          { 'X-Run-Id': 'r1' }
        )
      }
      throw new Error(`unexpected ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const h = makeHarness()
    h.controller.start(payload())
    await h.terminal

    expect(h.runIds).toEqual(['r1'])
    const last = h.states[h.states.length - 1]
    expect(last.terminal).toBe(true)
    expect(last.assistants[0].content).toBe('你好')
    expect(last.usage?.total_tokens).toBe(12)
    // 收到 X-Run-Id 立即落标记，终态后清除
    expect(localStorage.getItem(markerStorageKey(null))).toBeNull()
    expect(h.statuses).toContainEqual(['idle', false])
    h.controller.dispose()
  })

  it('断流后按 after=seq 续读，探活成功才重连', async () => {
    const calls: string[] = []
    const fetchMock = vi.fn(async (url: string) => {
      calls.push(url)
      if (url === '/api/chat') {
        // 收到 2 帧后连接中断，没有终态
        return sseResponse(
          [
            frame(1, 'speaker', { member_id: 'm1', name: '小爱' }),
            frame(2, 'content_delta', { text: '半截' }),
          ],
          { 'X-Run-Id': 'r1' }
        )
      }
      if (url === '/api/chat/runs/r1') {
        return jsonResponse({ id: 'r1', status: 'running' })
      }
      if (url.startsWith('/api/chat/runs/r1/events')) {
        expect(url).toBe('/api/chat/runs/r1/events?after=2')
        return sseResponse([frame(3, 'content_delta', { text: '正文' }), frame(4, 'done', {})])
      }
      throw new Error(`unexpected ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const h = makeHarness()
    h.controller.start(payload())
    await h.terminal

    const last = h.states[h.states.length - 1]
    expect(last.assistants[0].content).toBe('半截正文')
    expect(calls.some((u) => u === '/api/chat/runs/r1')).toBe(true)
    expect(calls.some((u) => u.startsWith('/api/chat/runs/r1/events?after=2'))).toBe(true)
    h.controller.dispose()
  })

  it('POST 撞 409：发现活跃 run 并全量回放续读', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/chat') {
        return new Response('{"detail":"busy"}', { status: 409 })
      }
      if (url.startsWith('/api/chat/active-run')) {
        expect(url).toContain('session_id=s1')
        return jsonResponse({ run_id: 'r9' })
      }
      if (url.startsWith('/api/chat/runs/r9/events')) {
        return sseResponse([
          frame(1, 'speaker', { name: '别人的回答' }),
          frame(2, 'content_delta', { text: '续读成功' }),
          frame(3, 'done', {}),
        ])
      }
      throw new Error(`unexpected ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const h = makeHarness()
    h.controller.start(payload())
    await h.terminal

    // POST 被拒说明本次消息未入会话：回填草稿保住文字，同时续读别人的回答
    expect(h.reverted).toHaveLength(1)
    expect(h.reverted[0].message).toBe(payload().message)
    expect(h.states[h.states.length - 1].assistants[0].content).toBe('续读成功')
    h.controller.dispose()
  })

  it('run 建立前 POST 失败：回填草稿且不进入忙碌', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"boom"}', { status: 500 }))
    )
    const h = makeHarness()
    const p = payload()
    h.controller.start(p)
    await h.terminal

    expect(h.reverted).toEqual([p])
    expect(h.toasts.some(([msg, err]) => err && msg.includes('请求失败'))).toBe(true)
    h.controller.dispose()
  })

  it('主动停止：先调 cancel 再断流，半截状态收尾', async () => {
    const cancelSeen: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        if (url.includes('/cancel')) {
          cancelSeen.push(url)
          return Promise.resolve(jsonResponse({ cancelled: true }))
        }
        if (url === '/api/chat') {
          // 响应头先到达（拿到 X-Run-Id），body 阻塞到 abort
          const stream = new ReadableStream({
            pull() {
              return new Promise((_resolve, reject) => {
                init?.signal?.addEventListener('abort', () => {
                  const err = new Error('aborted')
                  err.name = 'AbortError'
                  reject(err)
                })
              })
            },
          })
          return Promise.resolve(
            new Response(stream, {
              headers: { 'Content-Type': 'text/event-stream', 'X-Run-Id': 'r1' },
            })
          )
        }
        throw new Error(`unexpected ${url}`)
      })
    )

    const h = makeHarness()
    h.controller.start(payload())
    await tick()
    h.controller.cancel()
    await h.terminal

    expect(cancelSeen).toEqual(['/api/chat/runs/r1/cancel'])
    h.controller.dispose()
  })

  it('长跑排队：200 JSON 不按 SSE 解析，提示后收尾', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ queued: true, loop_id: 3, pending: 2 })))
    const h = makeHarness()
    h.controller.start(payload())
    await h.terminal

    expect(h.toasts.some(([msg]) => msg.includes('队列'))).toBe(true)
    expect(h.states[h.states.length - 1].assistants).toHaveLength(0)
    h.controller.dispose()
  })

  it('探活 404：服务重启后提示并收尾，不无限重连', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/chat') {
        return sseResponse([frame(1, 'speaker', { name: '小爱' })], { 'X-Run-Id': 'r1' })
      }
      if (url === '/api/chat/runs/r1') {
        return new Response('{"detail":"运行不存在或已过期"}', { status: 404 })
      }
      throw new Error(`unexpected ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const h = makeHarness()
    h.controller.start(payload())
    await h.terminal

    expect(h.toasts.some(([msg, err]) => err && msg.includes('回答已中断'))).toBe(true)
    h.controller.dispose()
  })
})
