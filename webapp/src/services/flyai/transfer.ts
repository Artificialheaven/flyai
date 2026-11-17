import { apiUrl, authHeaders, FlyApiError, readDetail } from './client.js'
import { getSession } from './sessions.js'
import { downloadBlob } from '../native.js'
import type { Message, SessionDetail } from './types.js'

const safeName = (title: string) =>
  (title || 'conversation')
    .replace(/[\\/:*?"<>|]+/g, '_')
    .trim()
    .slice(0, 60) || 'conversation'

const fetchBlob = async (path: string): Promise<Blob> => {
  const res = await fetch(apiUrl(path), { headers: authHeaders() })
  if (!res.ok) {
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return await res.blob()
}

/** 导出 JSON 包（v2，仅文本），后端已带 Content-Disposition，仍由前端命名 */
export const exportSessionJson = async (sid: string, title: string): Promise<void> => {
  const blob = await fetchBlob(`/api/sessions/${encodeURIComponent(sid)}/export`)
  await downloadBlob(blob, `${safeName(title)}.json`)
}

/** 导出 ZIP（v3，含立绘/背景/BGM/语音） */
export const exportSessionZip = async (sid: string, title: string): Promise<void> => {
  const blob = await fetchBlob(`/api/sessions/${encodeURIComponent(sid)}/export-zip`)
  await downloadBlob(blob, `${safeName(title)}.zip`)
}

const messageText = (message: Message): string => {
  const parts: string[] = []
  if (message.content) {
    parts.push(message.content)
  }
  if (message.tombstone && message.tombstone.content) {
    parts.push(`_[已撤回] ${message.tombstone.content}_`)
  }
  return parts.join('\n\n').trim()
}

/** 会话转 Markdown（纯文本，不嵌图片；作为轻量分享/备份） */
const toMarkdown = (detail: SessionDetail): string => {
  const lines: string[] = [`# ${detail.title || '对话'}`, '']
  const messages = (detail.threads[0]?.messages ?? detail.messages ?? []).filter(
    (message) => !message.hidden && message.role !== 'system' && message.role !== 'tool'
  )
  for (const message of messages) {
    const text = messageText(message)
    if (!text) {
      continue
    }
    const speaker = message.name || (message.role === 'user' ? '用户' : 'AI')
    lines.push(
      message.role === 'user' ? `**${speaker}**：${text}` : `### ${speaker}\n\n${text}`,
      ''
    )
    const images = message.images ?? []
    if (images.length > 0) {
      lines.push(`（含 ${images.length} 张图片，请使用 ZIP 导出保留原件）`, '')
    }
  }
  return lines.join('\n')
}

/** 导出 Markdown：先取详情（后端无 md 导出接口），前端拼接 */
export const exportSessionMarkdown = async (sid: string, title: string): Promise<void> => {
  const detail = await getSession(sid)
  const blob = new Blob([toMarkdown(detail)], { type: 'text/markdown;charset=utf-8' })
  await downloadBlob(blob, `${safeName(title)}.md`)
}

const upload = async (path: string, file: File): Promise<unknown> => {
  const form = new FormData()
  form.append('file', file, file.name)
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!res.ok) {
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return await res.json()
}

type ImportedSessions = { sessions?: unknown[] }

/** 导入单个文件：按扩展名/包类型选择 flyai JSON、ZIP 或 Chatbox 接口。返回导入会话数。 */
export const importSessionFile = async (file: File): Promise<number> => {
  const name = file.name.toLowerCase()
  if (name.endsWith('.zip')) {
    await upload('/api/sessions/import-zip', file)
    return 1
  }
  if (name.endsWith('.json')) {
    const text = await file.text()
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      throw new Error('文件不是有效的 JSON')
    }
    const type =
      parsed && typeof parsed === 'object' && 'type' in parsed
        ? String((parsed as { type: unknown }).type)
        : ''
    if (type === 'flyai-conversation') {
      const res = await fetch(apiUrl('/api/sessions/import'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: text,
      })
      if (!res.ok) {
        throw new FlyApiError(res.status, await readDetail(res))
      }
      return 1
    }
    // Chatbox 备份 JSON（可含多个对话）
    const data = (await upload('/api/sessions/import-chatbox', file)) as ImportedSessions
    return Array.isArray(data.sessions) ? data.sessions.length : 1
  }
  // Markdown / 其它文本：交给 Chatbox 导入器解析
  const data = (await upload('/api/sessions/import-chatbox', file)) as ImportedSessions
  return Array.isArray(data.sessions) ? data.sessions.length : 1
}