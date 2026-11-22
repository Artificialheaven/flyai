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
  const result = await runTemplateCells([{ id: message.id, text: raw }], {
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
  return {
    messageId: message.id,
    content: result.cells[message.id] ?? '',
    ops: result.ops,
    errors: result.errors,
    rawHash: await sha256Hex(raw),
  }
}
