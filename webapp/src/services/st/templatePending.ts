import type { AssistantMessage, Session } from '../chat/types.js'
import { runTemplateCells, type TemplateOp, type TemplateRunResult } from './templateRunner.js'

/** 输出侧模板：给一条 pending 助手消息准备上下文并执行。 */

const encoder = new TextEncoder()

export const sha256Hex = async (text: string): Promise<string> => {
  const subtle = typeof crypto !== 'undefined' ? crypto.subtle : undefined
  if (!subtle) {
    throw new Error('当前环境不支持 SHA-256，无法提交模板结果')
  }
  const digest = await subtle.digest('SHA-256', encoder.encode(text))
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}

const messageText = (message: Session['messages'][number]): string => {
  if (message.role === 'user') {
    return message.content
  }
  if (message.role === 'assistant') {
    return message.blocks
      .filter((block) => block.type === 'text')
      .map((block) => (block.type === 'text' ? block.text : ''))
      .join('')
  }
  return ''
}

export const pendingAssistantMessages = (session: Session): AssistantMessage[] => {
  if (!session.st?.template?.output_enabled) {
    return []
  }
  return session.messages.filter(
    (message): message is AssistantMessage =>
      message.role === 'assistant' &&
      message.templateRendered === false &&
      typeof message.rawContent === 'string'
  )
}

export type PendingRender = {
  messageId: string
  content: string
  ops: TemplateOp[]
  errors: TemplateRunResult['errors']
  rawHash: string
}

/** 按 ``` / ~~~ 代码围栏切分：围栏内不执行 EJS，原样保留。 */
export const splitFencedSegments = (text: string): Array<{ text: string; literal: boolean }> => {
  const segments: Array<{ text: string; literal: boolean }> = []
  let buffer: string[] = []
  let literal = false
  let fence = ''
  const flush = () => {
    if (!buffer.length) return
    segments.push({ text: buffer.join('\n'), literal })
    buffer = []
  }
  for (const line of text.split('\n')) {
    const match = /^\s*(`{3,}|~{3,})/.exec(line)
    if (fence) {
      buffer.push(line)
      if (match && line.trim().startsWith(fence)) {
        flush()
        fence = ''
        literal = false
      }
      continue
    }
    if (match) {
      flush()
      literal = true
      fence = match[1]
      buffer.push(line)
      continue
    }
    buffer.push(line)
  }
  flush()
  return segments.length ? segments : [{ text, literal: false }]
}

export const renderPendingMessage = async (
  session: Session,
  message: AssistantMessage,
  globalVars: Record<string, unknown>
): Promise<PendingRender> => {
  const raw = message.rawContent || ''
  const template = session.st?.template
  const scopes = {
    message: {
      ...(template?.vars?.message?.[message.id] || {}),
      ...(message.templateVars || {}),
    },
    local: template?.vars?.local || {},
    global: globalVars,
  }
  const chat = session.messages.map((item, index) => ({
    index,
    role: item.role,
    name:
      item.role === 'assistant'
        ? item.speakerName || session.characterName || ''
        : session.userName || '',
    content: messageText(item),
  }))
  const segments = splitFencedSegments(raw)
  const cells = segments
    .map((segment, index) => ({ segment, index }))
    .filter((item) => !item.segment.literal)
    .map((item) => ({ id: `${message.id}#${item.index}`, text: item.segment.text }))
  const result = await runTemplateCells(cells.length ? cells : [{ id: message.id, text: '' }], {
    messageId: message.id,
    scopes,
    readonly: {
      stat_data: message.statData || session.st?.mvu?.stat_data || {},
    },
    defaultScope: 'message',
    escapeOutput: true,
    char: { name: session.characterName || '' },
    user: { name: session.userName || '' },
    group: (session.members || [])
      .filter((member) => !member.is_user)
      .map((member) => ({ name: member.name })),
    chat,
  })
  if (result.errors.length > 0) {
    // 渲染失败不提交：保留原文（含模板代码），下次刷新会重试；
    // 绝不能把空结果写进消息，否则回复会被“清空”。
    throw new Error(result.errors[0].message || '模板渲染失败')
  }
  const content = segments
    .map((segment, index) =>
      segment.literal ? segment.text : (result.cells[`${message.id}#${index}`] ?? '')
    )
    .join('')
  return {
    messageId: message.id,
    content,
    ops: result.ops,
    errors: result.errors,
    rawHash: await sha256Hex(raw),
  }
}
