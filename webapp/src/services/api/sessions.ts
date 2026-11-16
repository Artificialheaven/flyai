import { request } from './client.js'
import type { Character, SessionDetail, SessionSummary } from '../../types/session.js'

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

// ---- 会话专属角色副本（scoped） ----

export const forkSessionCharacter = async (
  sid: string
): Promise<{ character: Character; scoped: boolean }> =>
  request<{ character: Character; scoped: boolean }>(
    `/api/sessions/${encodeURIComponent(sid)}/character/fork`,
    { method: 'POST' }
  )

export const updateSessionCharacter = async (
  sid: string,
  character: Record<string, unknown>
): Promise<Character> =>
  request<Character>(`/api/sessions/${encodeURIComponent(sid)}/character`, {
    method: 'PUT',
    body: JSON.stringify({ character }),
  })

/** 丢弃会话副本，恢复绑定的内容库角色卡 */
export const deleteSessionCharacterOverride = async (
  sid: string
): Promise<{ ok: boolean; character: Character | null; scoped: boolean }> =>
  request<{ ok: boolean; character: Character | null; scoped: boolean }>(
    `/api/sessions/${encodeURIComponent(sid)}/character/override`,
    { method: 'DELETE' }
  )

export const publishSessionCharacter = async (sid: string): Promise<Character> => {
  const data = await request<{ character: Character }>(
    `/api/sessions/${encodeURIComponent(sid)}/character/publish`,
    { method: 'POST' }
  )
  return data.character
}

export const exportSessionCharacter = async (sid: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(
    `/api/sessions/${encodeURIComponent(sid)}/character/export`
  )
