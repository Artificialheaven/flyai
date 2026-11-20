import { openEventStream, openStream, request } from './client.js'
import { decodeChatEvent, type ChatStreamEvent } from './events.js'
import { readServerSentEvents } from './sse.js'

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
  /** 续写锚点：目标必须是该窗口最后一条可见 AI 回复（仅 dm，与编辑/重生成互斥） */
  continue_message_id?: string
  /** 消息来源标记；目前只接受 'card' */
  origin?: 'card'
}

/** 建立 /api/chat 流：返回响应体与后端分配的运行号（停止时用于 cancel） */
export const openChatStream = async (
  payload: ChatSendPayload,
  signal: AbortSignal
): Promise<{ runId: string | null; response: Response }> => {
  const response = await openStream('/api/chat', payload, signal)
  return { runId: response.headers.get('X-Run-Id'), response }
}

/** 带后端事件序号（SSE id 字段）的聊天事件；断线重连时用序号续读 */
export type ChatFrame = {
  event: ChatStreamEvent
  /** 后端帧 id（自增序号）；缺失或非法时为 0 */
  seq: number
}

/** 逐个解出 /api/chat SSE 帧：`id` 即后端事件序号，用于 `after=N` 续读。 */
export async function* readChatFrames(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<ChatFrame> {
  if (!response.body) {
    return
  }
  for await (const frame of readServerSentEvents(response.body, signal)) {
    let data: unknown
    try {
      data = JSON.parse(frame.data)
    } catch {
      continue
    }
    const event = decodeChatEvent(frame.event, data)
    if (event) {
      const seq = Number(frame.lastEventId)
      yield { event, seq: Number.isFinite(seq) && seq > 0 ? seq : 0 }
    }
  }
}

/** 只取事件本体（调用方不关心序号时使用） */
export async function* readChatEvents(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<ChatStreamEvent> {
  for await (const { event } of readChatFrames(response, signal)) {
    yield event
  }
}

/**
 * 续读后台运行：回放 `after` 之后的全部事件并持续跟随，直到终态帧或流结束。
 * 运行不存在/已过期时抛 404，调用方据此收尾。
 */
export const resumeChatStream = (runId: string, after: number, signal: AbortSignal) =>
  openEventStream(
    `/api/chat/runs/${encodeURIComponent(runId)}/events?after=${encodeURIComponent(
      String(Math.max(0, Math.floor(after)))
    )}`,
    signal
  )

/** 主动停止：通知后台取消（半截内容仍会保存），失败了也不影响本地断流 */
export const cancelRun = async (runId: string): Promise<void> => {
  await request(`/api/chat/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

export type RunProbe = {
  status: string
  thread_id?: string
  [key: string]: unknown
}

/** 查询某会话当前是否有进行中的 run（可能由其它标签页/设备发起） */
export const discoverActiveRun = async (sessionId: string): Promise<string | null> => {
  const data = await request<{ run_id?: string }>(
    `/api/chat/active-run?session_id=${encodeURIComponent(sessionId)}`,
    { silent401: true }
  )
  return data && typeof data.run_id === 'string' && data.run_id ? data.run_id : null
}

/** 探活：服务重启/运行过期会 404；status !== 'running' 说明已落盘结束 */
export const probeRun = async (runId: string): Promise<RunProbe> =>
  request<RunProbe>(`/api/chat/runs/${encodeURIComponent(runId)}`, { silent401: true })