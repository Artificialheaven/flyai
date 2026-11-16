/**
 * 历史消息 → 可渲染轮次的纯函数归组（对照旧版 chat-history.js renderHistory）：
 * - 墓碑（tombstone）单独成行
 * - 隐藏的 assistant 消息跳过，但若带 tool_calls 必须保留（工具结果/图片无处挂载）
 * - assistant(tool_calls) → tool* → 同发言者 assistant 的连续链合并为一个气泡
 * - 游离 tool 消息（无 assistant 挂载点）不展示
 */
import type { Message, MessageImage, RecallItem, ToolCall } from '../../types/session.js'

export type ToolHistoryEntry = {
  id: string
  name: string
  ok: boolean
  /** tool 角色消息的文本输出，可能为 null */
  content: string | null
  hidden: boolean
}

export type AssistantChain = {
  type: 'assistant'
  /** 取链首消息 id，保证 React key 稳定 */
  key: string
  name: string | null
  actorId: string | null
  thinking: string
  contents: string[]
  images: MessageImage[]
  tools: ToolHistoryEntry[]
  recalls: RecallItem[][]
  messageIds: string[]
}

export type HistoryItem =
  | { type: 'user'; message: Message }
  | { type: 'tombstone'; message: Message }
  | AssistantChain

// 与旧版 _assistantKey 一致：DM 里 actor_id/name 均空，所有匿名 assistant 同键，
// 工具回灌链接的连续消息才能合并成一个气泡
const assistantKey = (message: Message): string => message.actor_id || message.name || ''

const isHiddenAssistant = (message: Message): boolean =>
  message.role === 'assistant' && !!message.hidden && !(message.tool_calls && message.tool_calls.length)

/** 把一条 assistant 链（含穿插的 tool 消息）折叠成单个渲染项 */
const foldChain = (chain: Message[]): AssistantChain => {
  const first = chain[0]
  const tools: ToolHistoryEntry[] = []
  const toolResultById = new Map<string, Message>()
  for (const message of chain) {
    if (message.role === 'tool' && message.tool_call_id) {
      toolResultById.set(message.tool_call_id, message)
    }
  }
  const seenTool = new Set<string>()
  for (const message of chain) {
    if (message.role !== 'assistant' || !message.tool_calls) {
      continue
    }
    for (const call of message.tool_calls as ToolCall[]) {
      if (seenTool.has(call.id)) {
        continue
      }
      seenTool.add(call.id)
      const result = toolResultById.get(call.id)
      tools.push({
        id: call.id,
        name: call.name,
        ok: result?.ok ?? true,
        content: result?.content ?? null,
        hidden: !!result?.hidden,
      })
    }
  }
  const thinkingParts: string[] = []
  const contents: string[] = []
  const images: MessageImage[] = []
  const recalls: RecallItem[][] = []
  for (const message of chain) {
    if (message.role !== 'assistant') {
      continue
    }
    if (message.thinking) {
      thinkingParts.push(message.thinking)
    }
    if (message.content) {
      contents.push(message.content)
    }
    if (message.images) {
      images.push(...message.images)
    }
    if (message.recall && message.recall.length) {
      recalls.push(message.recall)
    }
  }
  return {
    type: 'assistant',
    key: first.id,
    name: first.name,
    actorId: first.actor_id,
    thinking: thinkingParts.join('\n\n'),
    contents,
    images,
    tools,
    recalls,
    messageIds: chain.filter((message) => message.role === 'assistant').map((message) => message.id),
  }
}

export const buildHistoryItems = (messages: Message[]): HistoryItem[] => {
  const items: HistoryItem[] = []
  let i = 0

  // 开头游离消息（正常不会有）：墓碑单独成行，assistant 兜底成链，tool 丢弃
  while (i < messages.length && messages[i].role !== 'user') {
    const message = messages[i]
    if (message.tombstone) {
      items.push({ type: 'tombstone', message })
      i++
      continue
    }
    if (message.role === 'assistant' && !isHiddenAssistant(message)) {
      items.push(foldChain([message]))
    }
    i++
  }

  while (i < messages.length) {
    const owner = messages[i]
    let end = messages.length
    for (let j = i + 1; j < messages.length; j++) {
      // 墓碑也是轮边界：被撤回轮的回复槽位不渲染
      if (messages[j].role === 'user' || messages[j].tombstone) {
        end = j
        break
      }
    }
    if (owner.tombstone) {
      items.push({ type: 'tombstone', message: owner })
      i = end
      continue
    }
    items.push({ type: 'user', message: owner })

    let k = i + 1
    while (k < end) {
      const head = messages[k]
      if (isHiddenAssistant(head) || head.role !== 'assistant') {
        k++
        continue
      }
      const chain: Message[] = [head]
      // 链尾是否带工具调用：决定能否吞入后续同发言者 assistant
      let linked = !!(head.tool_calls && head.tool_calls.length)
      let p = k + 1
      while (p < end) {
        const next = messages[p]
        if (next.role === 'tool') {
          chain.push(next)
          p++
          continue
        }
        if (next.role === 'assistant' && linked && assistantKey(head) === assistantKey(next)) {
          chain.push(next)
          linked = !!(next.tool_calls && next.tool_calls.length)
          p++
          continue
        }
        break
      }
      items.push(foldChain(chain))
      k = p
    }
    i = end
  }
  return items
}
