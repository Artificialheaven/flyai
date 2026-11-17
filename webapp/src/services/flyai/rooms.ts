import { apiUrl, authHeaders, FlyApiError, readDetail, request } from './client.js'
import type { Character, RoomMember, SessionDetail, Thread } from './types.js'

/** 房间（会话）参与者 / 窗口：FlyAI 多 AI 群聊的底层入口。 */

export const createRoom = async (body: {
  title?: string
  character_ids: string[]
  world_id?: string | null
  mode?: 'normal' | 'galgame'
}): Promise<SessionDetail> =>
  request<SessionDetail>('/api/rooms', { method: 'POST', body: JSON.stringify(body) })

export const addMember = async (
  rid: string,
  body: { character_id: string; provider_id?: string | null; memory_mode?: string | null }
): Promise<RoomMember> =>
  request<RoomMember>(`/api/rooms/${encodeURIComponent(rid)}/members`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const updateMember = async (
  rid: string,
  mid: string,
  body: {
    character_id?: string
    override?: Character | null
    memory?: string
    attributes?: Record<string, unknown>
    provider_id?: string | null
    memory_mode?: string | null
    memory_char_id?: string | null
  }
): Promise<RoomMember> =>
  request<RoomMember>(
    `/api/rooms/${encodeURIComponent(rid)}/members/${encodeURIComponent(mid)}`,
    { method: 'PATCH', body: JSON.stringify(body) }
  )

export const removeMember = async (rid: string, mid: string): Promise<void> => {
  await request<{ removed: string }>(
    `/api/rooms/${encodeURIComponent(rid)}/members/${encodeURIComponent(mid)}`,
    { method: 'DELETE' }
  )
}

/** 座位角色另存为内容库新卡（不改座位绑定） */
export const publishMemberCharacter = async (rid: string, mid: string): Promise<Character> => {
  const data = await request<{ character: Character }>(
    `/api/rooms/${encodeURIComponent(rid)}/members/${encodeURIComponent(mid)}/character/publish`,
    { method: 'POST' }
  )
  return data.character
}

export const createGroup = async (
  rid: string,
  memberIds: string[],
  title?: string
): Promise<Thread> =>
  request<Thread>(`/api/rooms/${encodeURIComponent(rid)}/groups`, {
    method: 'POST',
    body: JSON.stringify({ member_ids: memberIds, title }),
  })

export const getThread = async (rid: string, tid: string): Promise<Thread> =>
  request<Thread>(
    `/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}`
  )

export const renameThread = async (rid: string, tid: string, title: string): Promise<Thread> =>
  request<Thread>(
    `/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}`,
    { method: 'PATCH', body: JSON.stringify({ title }) }
  )

export const deleteThread = async (rid: string, tid: string): Promise<void> => {
  await request<{ removed: string }>(
    `/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}`,
    { method: 'DELETE' }
  )
}

/** 会话专属角色副本（未入库）：编辑 / 导出 / 发布 / 还原 */
export type ScopedCharacter = { character: Character | null; scoped: boolean }

export const forkSessionCharacter = async (sid: string): Promise<ScopedCharacter> =>
  request<ScopedCharacter>(`/api/sessions/${encodeURIComponent(sid)}/character/fork`, {
    method: 'POST',
  })

export const saveSessionCharacter = async (
  sid: string,
  character: Partial<Character>
): Promise<ScopedCharacter> =>
  request<ScopedCharacter>(`/api/sessions/${encodeURIComponent(sid)}/character`, {
    method: 'PUT',
    body: JSON.stringify({ character }),
  })

export const clearSessionCharacter = async (sid: string): Promise<ScopedCharacter> =>
  request<ScopedCharacter>(`/api/sessions/${encodeURIComponent(sid)}/character/override`, {
    method: 'DELETE',
  })

export const publishSessionCharacter = async (sid: string): Promise<Character> => {
  const data = await request<{ character: Character }>(
    `/api/sessions/${encodeURIComponent(sid)}/character/publish`,
    { method: 'POST' }
  )
  return data.character
}

export const exportSessionCharacter = async (sid: string): Promise<Blob> => {
  const res = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(sid)}/character/export`), {
    headers: authHeaders(),
  })
  if (!res.ok) {
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return await res.blob()
}