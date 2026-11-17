import { apiUrl, authHeaders, FlyApiError, readDetail, request } from './client.js'
import { downloadBlob } from '../native.js'
import type { AttributeDef, Character, World } from './types.js'

/** 角色卡 / 世界观：登录态（Bearer）由 request 统一注入。 */

export const listCharacters = async (): Promise<Character[]> => {
  const data = await request<{ characters: Character[] }>('/api/characters')
  return data.characters ?? []
}

export const createCharacter = async (body: Partial<Character>): Promise<Character> =>
  request<Character>('/api/characters', { method: 'POST', body: JSON.stringify(body) })

export const updateCharacter = async (cid: string, body: Partial<Character>): Promise<Character> =>
  request<Character>(`/api/characters/${encodeURIComponent(cid)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })

export const deleteCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${encodeURIComponent(cid)}`, {
    method: 'DELETE',
  })
}

/** 把可见（含共享）卡复制为自己的独立根角色卡 */
export const forkCharacter = async (cid: string): Promise<Character> =>
  request<Character>(`/api/characters/${encodeURIComponent(cid)}/fork`, { method: 'POST' })

export const publishCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${encodeURIComponent(cid)}/publish`, {
    method: 'POST',
  })
}

export const unpublishCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${encodeURIComponent(cid)}/unpublish`, {
    method: 'POST',
  })
}

export const listWorlds = async (): Promise<World[]> => {
  const data = await request<{ worlds: World[] }>('/api/worlds')
  return data.worlds ?? []
}

export const createWorld = async (body: Partial<World>): Promise<World> =>
  request<World>('/api/worlds', { method: 'POST', body: JSON.stringify(body) })

export const updateWorld = async (wid: string, body: Partial<World>): Promise<World> =>
  request<World>(`/api/worlds/${encodeURIComponent(wid)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })

export const deleteWorld = async (wid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/worlds/${encodeURIComponent(wid)}`, { method: 'DELETE' })
}

const fetchBlob = async (path: string): Promise<Blob> => {
  const res = await fetch(apiUrl(path), { headers: authHeaders() })
  if (!res.ok) {
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return await res.blob()
}

const safeName = (name: string) =>
  (name || 'card').replace(/[\\/:*?"<>|]+/g, '_').trim().slice(0, 60) || 'card'

export const exportCharacter = async (character: Character): Promise<void> => {
  const blob = await fetchBlob(`/api/characters/${encodeURIComponent(character.id)}/export`)
  await downloadBlob(blob, `${safeName(character.name || character.alias)}.json`)
}

export const exportWorld = async (world: World): Promise<void> => {
  const blob = await fetchBlob(`/api/worlds/${encodeURIComponent(world.id)}/export`)
  await downloadBlob(blob, `${safeName(world.name)}.json`)
}

const uploadFile = async <T>(path: string, file: File): Promise<T> => {
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
  return (await res.json()) as T
}

/** 酒馆 CCv2/v3 角色卡（PNG/JSON） */
export const importCharacterCard = async (file: File): Promise<Character> =>
  uploadFile<Character>('/api/characters/import-card', file)

/** FlyAI / 酒馆 v2 角色卡 JSON（导出文件可直接再导入） */
export const importCharacterJson = async (file: File): Promise<Character> => {
  const text = await file.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error('文件不是有效的 JSON')
  }
  const data =
    parsed && typeof parsed === 'object' && 'data' in parsed
      ? (parsed as { data: Partial<Character> }).data
      : (parsed as Partial<Character>)
  return createCharacter(data)
}

export const importWorldbook = async (
  file: File
): Promise<{ world: World; stats: Record<string, unknown> }> =>
  uploadFile('/api/worlds/import-worldbook', file)

export const importWorldJson = async (file: File): Promise<World> => {
  const text = await file.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error('文件不是有效的 JSON')
  }
  const data =
    parsed && typeof parsed === 'object' && 'data' in parsed
      ? (parsed as { data: Partial<World> }).data
      : (parsed as Partial<World>)
  return createWorld(data)
}

/** 头像上传，返回 /uploads/... 路径 */
export const uploadAvatar = async (file: File): Promise<string> => {
  const data = await uploadFile<{ url: string }>('/api/upload', file)
  return data.url
}

/** 从任意文本让 AI 解析出角色卡（编辑器预填，不落库） */
export const generateCharacterFromText = async (text: string): Promise<Character> => {
  const data = await request<{ character: Character }>('/api/gen/character-from-text', {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
  return data.character
}

/** 让 AI 依据角色设定建议属性 */
export const suggestAttributes = async (input: {
  name: string
  intro: string
  background: string
  existing: AttributeDef[]
}): Promise<AttributeDef[]> => {
  const data = await request<{ attributes: AttributeDef[] }>('/api/gen/attributes', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  return data.attributes ?? []
}