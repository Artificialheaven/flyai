/**
 * 纯函数流事件归约器：把 SSE 事件序列累积成可渲染的运行态。
 * 不碰 DOM、不碰网络，Vitest 可完整覆盖；transport 层（stream.ts）负责喂事件与重连。
 */
import type { ChatStreamEvent, RecallItem, Usage } from '../api/events.js'

export type ToolStatus = 'pending' | 'running' | 'done' | 'error'

export type ToolLiveState = {
  id: string
  name: string
  status: ToolStatus
  statusText?: string
  preview?: string
  output?: string
  revealed?: Record<string, unknown>
}

export type LiveAssistant = {
  /** member_id 优先，单聊用合成序号，保证 key 稳定 */
  key: string
  name: string
  memberId?: string
  thinking: string
  content: string
  tools: ToolLiveState[]
  /** 每次 recall_notice 折叠为一组 */
  recallGroups: RecallItem[][]
  attributes?: Record<string, unknown>
  /** speaker_skip：自评无需发言，UI 整卡隐藏（历史里仍在） */
  skipped: boolean
}

export type LiveRunState = {
  sessionId?: string
  threadId?: string
  assistants: LiveAssistant[]
  currentIndex: number | null
  usage?: Usage
  error?: { message: string; fatal: boolean }
  /** 收到终态 done（terminal!==false）或致命 error 后置真 */
  terminal: boolean
}

export const createLiveRun = (): LiveRunState => ({
  assistants: [],
  currentIndex: null,
  terminal: false,
})

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

/** 群聊每个 speaker 的 done 各带一份用量，逐帧累加 */
const accumulateUsage = (prev: Usage | undefined, incoming: Usage | undefined): Usage | undefined => {
  if (!incoming) return prev
  if (!prev) return { ...incoming }
  return {
    prompt_tokens: num(prev.prompt_tokens) + num(incoming.prompt_tokens),
    completion_tokens: num(prev.completion_tokens) + num(incoming.completion_tokens),
    total_tokens: num(prev.total_tokens) + num(incoming.total_tokens),
  }
}

let syntheticKey = 0
const nextKey = () => `assistant-${++syntheticKey}`

const ensureCurrent = (state: LiveRunState): { state: LiveRunState; index: number } => {
  if (state.currentIndex !== null && state.assistants[state.currentIndex]) {
    return { state, index: state.currentIndex }
  }
  // 没有 speaker 事件就先来内容（单聊兼容）：补一张默认 AI 卡
  const assistant: LiveAssistant = {
    key: nextKey(),
    name: 'AI',
    thinking: '',
    content: '',
    tools: [],
    recallGroups: [],
    skipped: false,
  }
  return {
    state: { ...state, assistants: [...state.assistants, assistant], currentIndex: state.assistants.length },
    index: state.assistants.length,
  }
}

const patchCurrent = (
  state: LiveRunState,
  fn: (assistant: LiveAssistant) => LiveAssistant
): LiveRunState => {
  const ensured = ensureCurrent(state)
  const assistants = ensured.state.assistants.slice()
  assistants[ensured.index] = fn(assistants[ensured.index])
  return { ...ensured.state, assistants, currentIndex: ensured.index }
}

const findToolIndex = (assistant: LiveAssistant, id: string): number =>
  assistant.tools.findIndex((tool) => tool.id === id)

/** 归约单帧；terminal 之后的事件一律忽略（迟到帧防护） */
export const reduceRun = (prev: LiveRunState, event: ChatStreamEvent): LiveRunState => {
  if (prev.terminal && event.type !== 'done') {
    return prev
  }
  switch (event.type) {
    case 'message_start':
      return {
        ...prev,
        sessionId: event.sessionId ?? prev.sessionId,
        threadId: event.threadId ?? prev.threadId,
      }
    case 'speaker': {
      const assistant: LiveAssistant = {
        key: event.memberId || nextKey(),
        name: event.name || 'AI',
        memberId: event.memberId,
        thinking: '',
        content: '',
        tools: [],
        recallGroups: [],
        skipped: false,
      }
      return {
        ...prev,
        assistants: [...prev.assistants, assistant],
        currentIndex: prev.assistants.length,
      }
    }
    case 'speaker_skip':
      if (prev.currentIndex === null) {
        return prev
      }
      return patchCurrent(prev, (assistant) => ({ ...assistant, skipped: true }))
    case 'recall_notice':
      if (!event.items.length) {
        return prev
      }
      return patchCurrent(prev, (assistant) => ({
        ...assistant,
        recallGroups: [...assistant.recallGroups, event.items],
      }))
    case 'thinking_delta':
      return patchCurrent(prev, (assistant) => ({
        ...assistant,
        thinking: assistant.thinking + event.text,
      }))
    case 'content_delta':
      return patchCurrent(prev, (assistant) => ({
        ...assistant,
        content: assistant.content + event.text,
      }))
    case 'content_replace':
      return patchCurrent(prev, (assistant) => ({ ...assistant, content: event.text }))
    case 'tool_calls':
      if (!event.calls.length) {
        return prev
      }
      return patchCurrent(prev, (assistant) => ({
        ...assistant,
        tools: [
          ...assistant.tools,
          ...event.calls.map((call) => ({
            id: call.id,
            name: call.name,
            status: 'pending' as const,
          })),
        ],
      }))
    case 'tool_start':
    case 'tool_end': {
      // 正常在当前发言者卡片上；跨卡迟到帧则全量查找
      const targetIndex =
        prev.currentIndex !== null &&
        findToolIndex(prev.assistants[prev.currentIndex], event.id) >= 0
          ? prev.currentIndex
          : prev.assistants.findIndex((assistant) => findToolIndex(assistant, event.id) >= 0)
      if (targetIndex < 0) {
        return prev
      }
      const assistants = prev.assistants.slice()
      const source = assistants[targetIndex]
      const toolIndex = findToolIndex(source, event.id)
      const tools = source.tools.slice()
      if (event.type === 'tool_start') {
        tools[toolIndex] = {
          ...tools[toolIndex],
          status: 'running',
          name: event.name ?? tools[toolIndex].name,
          statusText: event.statusText,
        }
      } else {
        tools[toolIndex] = {
          ...tools[toolIndex],
          status: event.ok ? 'done' : 'error',
          name: event.name ?? tools[toolIndex].name,
          preview: event.preview ?? tools[toolIndex].preview,
          output: event.output ?? tools[toolIndex].output,
          // 选择帧可能不带 revealed 字段，不能把已回填的原图清掉
          revealed: event.revealed ?? tools[toolIndex].revealed,
        }
      }
      assistants[targetIndex] = { ...source, tools }
      return { ...prev, assistants }
    }
    case 'attributes':
      return patchCurrent(prev, (assistant) => ({
        ...assistant,
        attributes: { ...assistant.attributes, ...event.attributes },
      }))
    case 'error':
      return {
        ...prev,
        error: { message: event.message, fatal: event.fatal },
        terminal: event.fatal,
      }
    case 'done': {
      const next: LiveRunState = {
        ...prev,
        usage: accumulateUsage(prev.usage, event.usage),
        // 群聊 terminal=false：保留现场，等下一位 speaker
        terminal: event.terminal,
      }
      return next
    }
    default:
      return prev
  }
}

/** 便利方法：归约整条事件序列（测试与全量回放共用） */
export const reduceRunAll = (events: ChatStreamEvent[]): LiveRunState =>
  events.reduce(reduceRun, createLiveRun())
