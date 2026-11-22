/**
 * FlyAI 后端 SSE 事件协议（对照 flyai/web/static/js/chat-stream.js 的处理函数逐一对齐）。
 * 帧格式：event: <名称>\nid: <序号>\ndata: <JSON>，由 sse.ts 的 WHATWG 解析器切分。
 */

export type Usage = {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

export type RecallItem = {
  /** 召回来源标题/角色名等，字段以后端实际载荷为准，未知字段原样保留 */
  [key: string]: unknown
}

export type ToolCallPayload = {
  id: string
  name: string
}

export type ToolStartPayload = {
  id: string
  name?: string
  status_text?: string
}

export type ToolEndPayload = {
  id: string
  name?: string
  ok: boolean
  /** 流式期间的预览文本（可能含图片/语音/Pixiv 标记） */
  preview?: string
  /** 历史回放时的完整输出文本 */
  output?: string
  /** Pixiv 候选墙的揭晓映射 */
  revealed?: Record<string, unknown>
}

/** 归约器消费的事件联合；data 在入口处做窄化校验，缺字段按空值兜底 */
export type ChatStreamEvent =
  | {
      type: 'message_start'
      sessionId?: string
      threadId?: string
      /** 本轮已落盘的用户消息 id；续写/续接等不新增用户消息时为空 */
      userMessageId?: string
      attributes?: Record<string, unknown>
    }
  | { type: 'speaker'; name?: string; memberId?: string }
  | { type: 'speaker_skip' }
  | { type: 'recall_notice'; items: RecallItem[] }
  /** 窗口字数跨过预警阈值（20万/40万/60万/80万），提示用户可考虑压缩 */
  | { type: 'context_warning'; chars: number; threshold: number; thresholds: number[] }
  | { type: 'thinking_delta'; text: string }
  | { type: 'content_delta'; text: string }
  | { type: 'content_replace'; text: string }
  | { type: 'tool_calls'; calls: ToolCallPayload[] }
  | { type: 'tool_start'; id: string; name?: string; statusText?: string }
  | {
      type: 'tool_end'
      id: string
      name?: string
      ok: boolean
      preview?: string
      output?: string
      revealed?: Record<string, unknown>
    }
  | { type: 'attributes'; attributes: Record<string, unknown> }
  /**
   * EJS 提示词侧：服务端在调用模型前下发待渲染单元格，客户端沙箱执行后
   * POST /api/template/prompt-commit。服务端会等待最多 timeoutMs。
   */
  | {
      type: 'template_prepare'
      prepareId: string
      runId?: string
      cells: Array<{ id: string; text: string; role?: string }>
      context: Record<string, unknown>
      limits?: Record<string, unknown>
      contextVersion: string
      timeoutMs?: number
    }
  /** 首轮后台自动标题生成完毕（可能在 done 之前到达；之后到达的不会再送） */
  | { type: 'session_title'; sessionId?: string; title: string }
  | { type: 'error'; message: string; fatal: boolean }
  /** titlePending：终态时标题仍在后台生成，客户端收尾后需轮询补取 */
  | { type: 'done'; usage?: Usage; terminal: boolean; titlePending?: boolean }

const asRecord = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {}

const asString = (value: unknown): string => (typeof value === 'string' ? value : '')

/** id 后端理论上是字符串，兼容个别帧直接给数字序号的情况 */
const asId = (value: unknown): string => {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

/** 把后端帧的 event 名 + data JSON 转成归约器事件；未知事件返回 null */
export const decodeChatEvent = (event: string, rawData: unknown): ChatStreamEvent | null => {
  const d = asRecord(rawData)
  switch (event) {
    case 'message_start':
      return {
        type: 'message_start',
        sessionId: asString(d.session_id) || undefined,
        threadId: asString(d.thread_id) || undefined,
        userMessageId: asId(d.user_message_id) || undefined,
        attributes:
          d.attributes && typeof d.attributes === 'object'
            ? (d.attributes as Record<string, unknown>)
            : undefined,
      }
    case 'speaker':
      return {
        type: 'speaker',
        name: asString(d.name) || undefined,
        memberId: asString(d.member_id) || undefined,
      }
    case 'speaker_skip':
      return { type: 'speaker_skip' }
    case 'recall_notice':
      return { type: 'recall_notice', items: Array.isArray(d.items) ? (d.items as RecallItem[]) : [] }
    case 'context_warning': {
      const chars = typeof d.chars === 'number' ? d.chars : 0
      const threshold = typeof d.threshold === 'number' ? d.threshold : 0
      if (threshold <= 0) {
        return null
      }
      return {
        type: 'context_warning',
        chars,
        threshold,
        thresholds: Array.isArray(d.thresholds)
          ? d.thresholds.filter((value): value is number => typeof value === 'number')
          : [threshold],
      }
    }
    case 'thinking_delta':
      return { type: 'thinking_delta', text: asString(d.text) }
    case 'content_delta':
      return { type: 'content_delta', text: asString(d.text) }
    case 'content_replace':
      return { type: 'content_replace', text: asString(d.text) }
    case 'tool_calls':
      return {
        type: 'tool_calls',
        calls: Array.isArray(d.calls)
          ? (d.calls as unknown[]).map((c) => {
              const rec = asRecord(c)
              return { id: asId(rec.id), name: asString(rec.name) }
            })
          : [],
      }
    case 'tool_start':
      return {
        type: 'tool_start',
        id: asId(d.id),
        name: asString(d.name) || undefined,
        statusText: asString(d.status_text) || undefined,
      }
    case 'tool_end':
      return {
        type: 'tool_end',
        id: asId(d.id),
        name: asString(d.name) || undefined,
        ok: Boolean(d.ok),
        preview: d.preview !== undefined ? asString(d.preview) : undefined,
        output: d.output !== undefined ? asString(d.output) : undefined,
        revealed:
          d.revealed && typeof d.revealed === 'object'
            ? (d.revealed as Record<string, unknown>)
            : undefined,
      }
    case 'attributes':
      return {
        type: 'attributes',
        attributes:
          d.attributes && typeof d.attributes === 'object'
            ? (d.attributes as Record<string, unknown>)
            : {},
      }
    case 'template_prepare': {
      const prepareId = asString(d.prepare_id)
      const cells = Array.isArray(d.cells)
        ? d.cells
            .map((item) => {
              const cell = asRecord(item)
              return {
                id: asString(cell.id),
                text: asString(cell.text),
                role: asString(cell.role) || undefined,
              }
            })
            .filter((cell) => cell.id && cell.text)
        : []
      if (!prepareId || cells.length === 0) {
        return null
      }
      return {
        type: 'template_prepare',
        prepareId,
        runId: asString(d.run_id) || undefined,
        cells,
        context:
          d.context && typeof d.context === 'object'
            ? (d.context as Record<string, unknown>)
            : {},
        limits:
          d.limits && typeof d.limits === 'object'
            ? (d.limits as Record<string, unknown>)
            : undefined,
        contextVersion: asString(d.context_version),
        timeoutMs: typeof d.timeout_ms === 'number' ? d.timeout_ms : undefined,
      }
    }
    case 'session_title':
      return {
        type: 'session_title',
        sessionId: asString(d.session_id) || undefined,
        title: asString(d.title),
      }
    case 'error':
      // 缺省按致命错误处理；fatal:false（如工具轮数上限）后还会跟 done
      return { type: 'error', message: asString(d.message) || '发生错误', fatal: d.fatal !== false }
    case 'done':
      return {
        type: 'done',
        usage: d.usage && typeof d.usage === 'object' ? (d.usage as Usage) : undefined,
        // 群聊单个 AI 结束时 terminal=false；缺省视为终态
        terminal: d.terminal !== false,
        titlePending: d.title_pending === true,
      }
    default:
      return null
  }
}