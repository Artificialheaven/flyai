/**
 * 流式聊天传输控制器（框架无关，React 之外可独立测试）。
 * 六条红线契约对照旧版 chat-stream.js：
 *  1. X-Run-Id 建 run + AbortController，停止先 POST cancel 再断流（已生成部分仍保存）
 *  2. 意外断流指数退避自动重连（8 次封顶，500*1.5^(n-1)，上限 8s），?after=seq 序号续读
 *  3. 启动发现：localStorage 标记 flyai-run-{uid}（30 分钟 TTL、400ms 节流）+ active-run 接口
 *  4. 刷新自动回放（seq 落标记，进入会话全量回放）/ 后台存活（断流不重试，回前台续）
 *  5. 群聊 done terminal:false 不收尾，等下一位 speaker
 *  6. POST 在 run 建立前失败：消息没进会话，onRevertDraft 回填不丢字；409 转发现续读
 */
import { ApiError, apiUrl, authHeaders, request } from '../api/client.js'
import { readServerSentEvents } from '../api/sse.js'
import { decodeChatEvent } from '../api/events.js'
import { createLiveRun, reduceRun, type LiveRunState } from './streamReducer.js'
import { backoffDelay, MAX_RECONNECT_ATTEMPTS, sleep } from './backoff.js'
import { clearRunMarker, saveRunMarker } from './runMarker.js'
import { discoverActiveRun, probeRun } from './activeRun.js'
import { useAuth } from '../../stores/auth.js'

export type ChatImagePayload = {
  /** image/jpeg | image/png | image/webp | image/gif */
  media_type: string
  /** 不含 data: 前缀的 base64；最多 4 张、单张 8MB */
  data: string
}

export type ChatSendPayload = {
  message: string
  session_id: string
  thread_id?: string
  provider_id?: string
  tools_enabled: boolean
  images: ChatImagePayload[]
  image_model_id?: string
  image_engine?: string
  target_member_ids?: string[]
  shuffle_speakers?: boolean
  deep_thinking?: boolean
  disabled_tools?: string[]
  /** 历史编辑锚点（仅 dm，与 regenerate_message_id 互斥） */
  edit_message_id?: string
  /** 重新生成锚点（仅 dm，与 edit_message_id 互斥） */
  regenerate_message_id?: string
}

export type StreamStatus = 'idle' | 'connecting' | 'streaming' | 'reconnecting'

export type StreamControllerCallbacks = {
  /** 每帧归约后的新状态（不可变引用，可直接给 zustand/React） */
  onState: (state: LiveRunState) => void
  onStatus?: (status: StreamStatus, busy: boolean) => void
  onToast?: (message: string, error?: boolean) => void
  /** POST 在 run 建立前失败：把待发内容回填输入框 */
  onRevertDraft?: (payload: ChatSendPayload) => void
  /** 运行终态（正常 done / 致命错误 / 主动停止 / 重连耗尽），store 应重拉历史 */
  onTerminal?: (state: LiveRunState) => void
  onRunId?: (runId: string) => void
}

type StartOptions = {
  /** 记忆存档等复用 SSE 通道的非 /api/chat 入口 */
  url?: string
  /** 续读时的会话/线程上下文（标记落盘用） */
  sid?: string
  tid?: string
}

const MARKER_SAVE_THROTTLE_MS = 400

export class ChatStreamController {
  private readonly cb: StreamControllerCallbacks
  private state: LiveRunState = createLiveRun()
  private abort: AbortController | null = null
  /** 流世代号：回前台主动续读开启新流后，旧流的迟到回调一律作废 */
  private gen = 0
  private runId: string | null = null
  private seq = 0
  private sid = ''
  private tid = ''
  private attempts = 0
  private streamLive = false
  private terminal = false
  private ended = false
  /** 用户主动停止：退避睡眠/前台等待期间也要立即收尾，不得再开新流 */
  private cancelRequested = false
  private markerTimer: ReturnType<typeof setTimeout> | null = null
  private lastPayload: ChatSendPayload | null = null
  private disposed = false
  private appPaused = false
  private readonly onVisibility = (): void => this.handleForeground()
  private readonly onAppResume = (): void => {
    this.appPaused = false
    this.handleForeground()
  }
  private readonly onAppPause = (): void => {
    this.appPaused = true
  }

  constructor(cb: StreamControllerCallbacks) {
    this.cb = cb
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', this.onVisibility)
      document.addEventListener('flyai:app-resume', this.onAppResume)
      document.addEventListener('flyai:app-pause', this.onAppPause)
    }
  }

  get currentRunId(): string | null {
    return this.runId
  }

  get isBusy(): boolean {
    return !this.terminal && !!this.runId
  }

  /** 发起新一轮（POST /api/chat 或自定义 SSE 入口） */
  start(payload: ChatSendPayload, opts: StartOptions = {}): void {
    this.lastPayload = payload
    this.sid = payload.session_id || opts.sid || ''
    this.tid = payload.thread_id || opts.tid || ''
    this.resetRun()
    this.emitStatus('connecting', true)
    void this.consume({
      post: true,
      url: opts.url || '/api/chat',
      payload,
    })
  }

  /** 续读已有 run；after=0 表示刷新后全量回放 */
  resume(runId: string, context: { sid?: string; tid?: string; after?: number } = {}): void {
    this.sid = context.sid || this.sid
    this.tid = context.tid || this.tid
    this.resetRun()
    this.runId = runId
    this.seq = context.after || 0
    this.emitStatus('connecting', true)
    void this.consume({ post: false })
  }

  /** 主动停止：通知后台取消（半截内容仍保存），再断开本地连接 */
  cancel(): void {
    const rid = this.runId
    if (rid) {
      // 取消失败不影响本地断开
      void request(`/api/chat/runs/${encodeURIComponent(rid)}/cancel`, { method: 'POST' }).catch(() => {})
    }
    this.cancelRequested = true
    this.abort?.abort()
    // 处于退避睡眠/前台等待（无在途请求）时，abort 无人响应，直接收尾
    if (!this.streamLive && !this.ended) {
      this.finalize()
    }
  }

  dispose(): void {
    this.disposed = true
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this.onVisibility)
      document.removeEventListener('flyai:app-resume', this.onAppResume)
      document.removeEventListener('flyai:app-pause', this.onAppPause)
    }
    if (this.markerTimer) {
      clearTimeout(this.markerTimer)
      this.markerTimer = null
    }
    this.abort?.abort()
  }

  private resetRun(): void {
    this.state = createLiveRun()
    this.gen += 1
    this.runId = null
    this.seq = 0
    this.attempts = 0
    this.streamLive = false
    this.terminal = false
    this.ended = false
    this.cancelRequested = false
    if (this.markerTimer) {
      clearTimeout(this.markerTimer)
      this.markerTimer = null
    }
    this.cb.onState(this.state)
  }

  private emitStatus(status: StreamStatus, busy: boolean): void {
    this.cb.onStatus?.(status, busy)
  }

  private currentUserId(): string | number | null {
    return useAuth.getState().user?.id ?? null
  }

  private writeMarker(): void {
    if (!this.runId || !this.sid) return
    saveRunMarker(this.currentUserId(), {
      run_id: this.runId,
      sid: this.sid,
      tid: this.tid,
      seq: this.seq,
      ts: Date.now(),
    })
  }

  /** 帧很密（逐字增量），标记写入节流 400ms */
  private scheduleMarkerSave(): void {
    if (this.markerTimer) return
    this.markerTimer = setTimeout(() => {
      this.markerTimer = null
      this.writeMarker()
    }, MARKER_SAVE_THROTTLE_MS)
  }

  private appHidden(): boolean {
    if (this.appPaused) return true
    return typeof document !== 'undefined' && document.hidden
  }

  /** 等待页面/应用回到前台：visibilitychange 与 Capacitor 自定义事件双保险 */
  private waitForResume(): Promise<void> {
    return new Promise((resolve) => {
      if (typeof document === 'undefined') {
        resolve()
        return
      }
      let finished = false
      const finish = (): void => {
        if (finished) return
        finished = true
        document.removeEventListener('visibilitychange', onVis)
        document.removeEventListener('flyai:app-resume', onResumeEv)
        if (safety) clearInterval(safety)
        resolve()
      }
      const onVis = (): void => {
        if (!document.hidden && !this.appPaused) finish()
      }
      const onResumeEv = (): void => finish()
      document.addEventListener('visibilitychange', onVis)
      document.addEventListener('flyai:app-resume', onResumeEv)
      // 兜底：个别机型 resume 事件丢失，每 10s 自查
      const safety = setInterval(onVis, 10000)
    })
  }

  private async consume(opts: { post: boolean; url?: string; payload?: ChatSendPayload }): Promise<void> {
    const myGen = ++this.gen
    const controller = new AbortController()
    this.abort = controller
    this.streamLive = true
    const runIdAtEntry = this.runId
    const runEstablished = !!runIdAtEntry
    let url: string
    const init: RequestInit = { signal: controller.signal }
    if (opts.post) {
      url = opts.url || '/api/chat'
      init.method = 'POST'
      init.headers = { ...authHeaders(), 'Content-Type': 'application/json' }
      init.body = JSON.stringify(opts.payload ?? {})
    } else {
      url = `/api/chat/runs/${encodeURIComponent(runIdAtEntry as string)}/events?after=${this.seq}`
      init.headers = authHeaders()
    }

    let resp: Response
    try {
      resp = await fetch(apiUrl(url), init)
    } catch (e) {
      if (myGen !== this.gen || this.disposed) return
      this.streamLive = false
      if (controller.signal.aborted || (e as Error).name === 'AbortError') {
        this.finalize()
        return
      }
      await this.handleTransportFailure(opts.post, runEstablished, e as Error)
      return
    }

    if (!resp.ok || !resp.body) {
      if (myGen !== this.gen || this.disposed) return
      this.streamLive = false
      const err = new Error(`HTTP ${resp.status}`) as Error & { status?: number }
      err.status = resp.status
      // 409：其它标签页/设备已有进行中的 run —— 不回填不报错，发现并续读
      if (resp.status === 409 && opts.post && !runEstablished && this.sid) {
        await this.handleConflict()
        return
      }
      await this.handleTransportFailure(opts.post, runEstablished, err)
      return
    }

    // 长跑在运行时，消息被投递到打断队列：后端返回 200 JSON 而非 SSE
    // {queued:true, loop_id, pending}，不能按流解析
    const contentType = resp.headers.get('Content-Type') || ''
    if (opts.post && contentType.includes('application/json')) {
      if (myGen !== this.gen || this.disposed) return
      const queued = (await resp.json().catch(() => null)) as { pending?: number } | null
      this.cb.onToast?.(queued?.pending ? `已加入长跑队列（前方 ${queued.pending} 条）` : '已加入长跑队列')
      this.finalize()
      return
    }

    const rid = resp.headers.get('X-Run-Id') || runIdAtEntry
    if (rid) {
      this.runId = rid
      this.cb.onRunId?.(rid)
      this.writeMarker()
    }
    this.emitStatus('streaming', true)

    try {
      for await (const frame of readServerSentEvents(resp.body, controller.signal)) {
        if (myGen !== this.gen || this.disposed) return
        const seqNum = Number.parseInt(frame.lastEventId, 10)
        if (Number.isFinite(seqNum) && seqNum > 0) {
          this.seq = seqNum
          this.scheduleMarkerSave()
        }
        let data: unknown
        try {
          data = frame.data ? JSON.parse(frame.data) : null
        } catch {
          continue
        }
        const event = decodeChatEvent(frame.event, data)
        if (!event) continue
        this.state = reduceRun(this.state, event)
        this.cb.onState(this.state)
        if (event.type === 'error') {
          this.cb.onToast?.(event.message, true)
          if (event.fatal) {
            this.finalize()
            controller.abort()
            return
          }
        } else if (event.type === 'done') {
          if (event.terminal) {
            this.finalize()
            return
          }
          // 群聊单个 AI 结束：只落标记，不收尾
          this.writeMarker()
        }
      }
    } catch (e) {
      if (myGen !== this.gen || this.disposed) return
      this.streamLive = false
      if (controller.signal.aborted || (e as Error).name === 'AbortError') {
        this.finalize()
        return
      }
      await this.autoReconnect(this.runId)
      return
    }

    if (myGen !== this.gen || this.disposed) return
    this.streamLive = false
    if (this.terminal || this.ended) return
    await this.autoReconnect(this.runId)
  }

  /** POST 失败分流：run 未建立 → 回填草稿；已建立 → 走重连 */
  private async handleTransportFailure(isPost: boolean, established: boolean, err: Error): Promise<void> {
    if (isPost && !established) {
      const status = (err as { status?: number }).status
      if (status === 409 && this.sid) {
        await this.handleConflict()
        return
      }
      this.cb.onToast?.(`请求失败：${err.message}`, true)
      if (this.lastPayload) this.cb.onRevertDraft?.(this.lastPayload)
      this.finalize()
      return
    }
    await this.autoReconnect(this.runId)
  }

  /** 409：查服务端活跃 run，查到就续读，查不到则回填草稿 */
  private async handleConflict(): Promise<void> {
    // POST 被拒 = 本次消息未被服务端接收：先回填，续读期间文字不丢
    if (this.lastPayload) this.cb.onRevertDraft?.(this.lastPayload)
    try {
      const rid = await discoverActiveRun(this.sid)
      if (rid && this.gen) {
        this.runId = rid
        this.seq = 0
        this.attempts = 0
        this.consume({ post: false })
        return
      }
    } catch {
      // 落到提示
    }
    this.cb.onToast?.('已有回答进行中，但无法续读，请重新进入会话', true)
    this.finalize()
  }

  /** 连接意外中断后的自动重连（指数退避，最多 8 次） */
  private async autoReconnect(runId: string | null): Promise<void> {
    if (!runId) {
      this.finalize()
      return
    }
    // 后台/锁屏断流：不弹提示、不耗重连次数，回前台再续
    if (this.appHidden()) {
      this.emitStatus('reconnecting', true)
      await this.waitForResume()
      if (this.disposed || this.terminal || this.streamLive || this.runId !== runId) return
      if (this.cancelRequested) {
        this.finalize()
        return
      }
      await this.resumeAfterForeground(runId)
      return
    }
    if (this.cancelRequested) {
      this.finalize()
      return
    }
    this.attempts += 1
    if (this.attempts > MAX_RECONNECT_ATTEMPTS) {
      this.cb.onToast?.('连接中断且多次重连失败，请手动刷新页面', true)
      this.finalize()
      return
    }
    const delay = backoffDelay(this.attempts)
    this.emitStatus('reconnecting', true)
    this.cb.onToast?.(`连接中断，${delay / 1000} 秒后自动重连…`)
    await sleep(delay)
    if (this.disposed || this.terminal || this.cancelRequested) {
      if (this.cancelRequested) this.finalize()
      return
    }
    try {
      const probe = await probeRun(runId)
      if (this.disposed || this.terminal) return
      if (probe.status !== 'running') {
        this.finalize()
        return
      }
      this.attempts = 0
      this.consume({ post: false })
    } catch (e) {
      if (this.disposed || this.terminal) return
      if ((e as ApiError).status === 404) {
        this.cb.onToast?.('回答已中断（服务已重启或运行过期）', true)
        this.finalize()
        return
      }
      await this.autoReconnect(runId)
    }
  }

  /** 回前台主动续读：运行仍在 → 立刻接流；已结束 → 收尾；404 → 收尾 */
  private async resumeAfterForeground(runId: string): Promise<void> {
    try {
      const probe = await probeRun(runId)
      if (probe.status !== 'running') {
        this.finalize()
        return
      }
      this.attempts = 0
      this.consume({ post: false })
    } catch (e) {
      if ((e as ApiError).status === 404) {
        this.finalize()
        return
      }
      await this.autoReconnect(runId)
    }
  }

  private handleForeground(): void {
    // 后台断流的续读统一由 autoReconnect 内的 waitForResume() 驱动；
    // 这里保留 visibility 监听入口用于 appPaused 状态校正，避免双路续读竞态
  }

  private finalize(): void {
    if (this.ended) return
    this.ended = true
    this.terminal = true
    this.streamLive = false
    this.attempts = 0
    if (this.markerTimer) {
      clearTimeout(this.markerTimer)
      this.markerTimer = null
    }
    clearRunMarker(this.currentUserId())
    this.runId = null
    this.seq = 0
    this.emitStatus('idle', false)
    this.cb.onTerminal?.(this.state)
  }
}
