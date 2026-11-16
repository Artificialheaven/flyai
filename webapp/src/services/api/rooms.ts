import { request } from './client.js'
import type { Character, RoomMember, SessionDetail, Thread } from '../../types/session.js'

export type RoomCreateBody = {
  title?: string
  character_ids: string[]
  world_id?: string
  mode?: 'normal' | 'galgame'
}

/** 新建房间：每个 AI 角色自动获得一条私聊窗口；返回完整房间（含消息） */
export const createRoom = async (body: RoomCreateBody): Promise<SessionDetail> =>
  request<SessionDetail>('/api/rooms', { method: 'POST', body: JSON.stringify(body) })

export type MemberAddBody = {
  character_id: string
  provider_id?: string
  memory_mode?: '' | 'rw' | 'ro'
}

export const addMember = async (rid: string, body: MemberAddBody): Promise<RoomMember> =>
  request<RoomMember>(`/api/rooms/${encodeURIComponent(rid)}/members`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export type MemberUpdateBody = {
  memory?: string
  attributes?: Record<string, unknown>
  override?: Record<string, unknown>
  character_id?: string
  provider_id?: string
  memory_mode?: '' | 'rw' | 'ro'
  memory_char_id?: string
}

export const updateMember = async (
  rid: string,
  mid: string,
  body: MemberUpdateBody
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

/** 把座位角色（override 副本）发布到内容库 */
export const publishMemberCharacter = async (rid: string, mid: string): Promise<Character> => {
  const data = await request<{ character: Character }>(
    `/api/rooms/${encodeURIComponent(rid)}/members/${encodeURIComponent(mid)}/character/publish`,
    { method: 'POST' }
  )
  return data.character
}

/** 读单个窗口的完整历史（含 edits） */
export const getThread = async (rid: string, tid: string): Promise<Thread> =>
  request<Thread>(`/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}`)

export const renameThread = async (rid: string, tid: string, title: string): Promise<Thread> =>
  request<Thread>(`/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })

export const deleteThread = async (rid: string, tid: string): Promise<void> => {
  await request<{ removed: string }>(
    `/api/rooms/${encodeURIComponent(rid)}/threads/${encodeURIComponent(tid)}`,
    { method: 'DELETE' }
  )
}

export type GroupCreateBody = {
  member_ids: string[]
  title?: string
}

/** 新建群聊窗口 */
export const createGroupThread = async (rid: string, body: GroupCreateBody): Promise<Thread> =>
  request<Thread>(`/api/rooms/${encodeURIComponent(rid)}/groups`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
