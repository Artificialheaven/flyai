import { request } from './client.js'
import type { MessageImage, SessionDetail, SessionSummary } from './types.js'

export type SessionPatch = {
  title?: string
  pinned?: boolean
  character_id?: string
  world_id?: string
  max_tokens?: number
  disabled_tools?: string[]
}

/** 轻量会话列表（后端按 mtime 倒序、pinned 置顶） */
export const listSessions = async (): Promise<SessionSummary[]> => {
  const data = await request<{ sessions: SessionSummary[] }>('/api/sessions')
  return data.sessions ?? []
}

/** 会话全文搜索：标题与消息正文，返回命中片段（不含消息体/图片） */
export type SessionSearchHit = SessionSummary & {
  match_count: number
  snippet: string
}

export const searchSessions = async (query: string): Promise<SessionSearchHit[]> => {
  const data = await request<{ query: string; results: SessionSearchHit[] }>(
    `/api/sessions/search?q=${encodeURIComponent(query)}`
  )
  return data.results ?? []
}

/** 传统简单入口：只建壳，首条消息时后端 bootstrap_room 补线程 */
export const createSession = async (title = ''): Promise<SessionSummary> =>
  request<SessionSummary>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })

export const patchSession = async (sid: string, patch: SessionPatch): Promise<SessionSummary> =>
  request<SessionSummary>(`/api/sessions/${encodeURIComponent(sid)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })

export const deleteSession = async (sid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' })
}

export const batchDeleteSessions = async (ids: string[]): Promise<number> => {
  const data = await request<{ ok: boolean; deleted?: number }>('/api/sessions/batch-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
  return data.deleted ?? ids.length
}

export const clearSession = async (sid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sid)}/clear`, { method: 'POST' })
}

/** 完整房间：members/threads/messages 都在这里，列表项拿不到 */
export const getSession = async (sid: string): Promise<SessionDetail> =>
  request<SessionDetail>(`/api/sessions/${encodeURIComponent(sid)}`)

export type RecallResult = {
  ok: boolean
  thread_id: string | null
  anchor: string
  tombstone: { content: string | null; images: MessageImage[] | null; name: string | null }
}

/** 撤回 mid 所在的一整轮：后端原位留下墓碑并返回被撤回内容（供重新编辑回填）。 */
export const deleteMessage = async (sid: string, mid: string): Promise<RecallResult> =>
  request<RecallResult>(
    `/api/sessions/${encodeURIComponent(sid)}/messages/${encodeURIComponent(mid)}`,
    { method: 'DELETE' }
  )

/** 切换某个锚点轮的历史 edit / reply 版本；返回更新后的线程 */
export const switchTurnVersion = async (
  rid: string,
  tid: string,
  anchor: string,
  index: { edit_index?: number; reply_index?: number }
): Promise<unknown> =>
  request<unknown>(
    `/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}/turns/${encodeURIComponent(anchor)}`,
    { method: 'PATCH', body: JSON.stringify(index) }
  )