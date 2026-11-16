/** 内容库 / 角色卡 / 世界观 API，对应 flyai/api/routes_lore.py 与 routes_gen.py。 */
import { apiUrl, authHeaders, request } from './client.js'
import type {
  Character,
  CharacterPayload,
  World,
  WorldPayload,
  RuntimeConfigView,
} from '../../types/lore.js'

const enc = encodeURIComponent

// ---- 角色卡 ----

export const listCharacters = async (): Promise<Character[]> => {
  const data = await request<{ characters: Character[] }>('/api/characters')
  return data.characters ?? []
}

export const createCharacter = async (payload: CharacterPayload): Promise<Character> =>
  request<Character>('/api/characters', { method: 'POST', body: JSON.stringify(payload) })

export const updateCharacter = async (cid: string, payload: CharacterPayload): Promise<Character> =>
  request<Character>(`/api/characters/${enc(cid)}`, { method: 'PUT', body: JSON.stringify(payload) })

export const deleteCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${enc(cid)}`, { method: 'DELETE' })
}

export const forkCharacter = async (cid: string): Promise<Character> =>
  request<Character>(`/api/characters/${enc(cid)}/fork`, { method: 'POST' })

export const publishCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${enc(cid)}/publish`, { method: 'POST' })
}

export const unpublishCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${enc(cid)}/unpublish`, { method: 'POST' })
}

/** 导出为 flyai-character JSON（{type,version,name,data}） */
export const exportCharacter = async (cid: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/characters/${enc(cid)}/export`)

export const importCharacter = async (body: { type?: string; data: Record<string, unknown> }): Promise<Character> =>
  request<Character>('/api/characters/import', { method: 'POST', body: JSON.stringify(body) })

/** 酒馆 CCv2/v3 卡（PNG 或 JSON 原样 multipart 上传） */
export const importCharacterCard = async (file: File): Promise<Character> => {
  const form = new FormData()
  form.append('file', file, file.name || 'card.png')
  return request<Character>('/api/characters/import-card', {
    method: 'POST',
    body: form,
    formData: true,
  })
}

// ---- 世界观 ----

export const listWorlds = async (): Promise<World[]> => {
  const data = await request<{ worlds: World[] }>('/api/worlds')
  return data.worlds ?? []
}

export const createWorld = async (payload: WorldPayload): Promise<World> =>
  request<World>('/api/worlds', { method: 'POST', body: JSON.stringify(payload) })

export const updateWorld = async (wid: string, payload: WorldPayload): Promise<World> =>
  request<World>(`/api/worlds/${enc(wid)}`, { method: 'PUT', body: JSON.stringify(payload) })

export const deleteWorld = async (wid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/worlds/${enc(wid)}`, { method: 'DELETE' })
}

export const exportWorld = async (wid: string): Promise<Record<string, unknown>> =>
  request<Record<string, unknown>>(`/api/worlds/${enc(wid)}/export`)

export const importWorld = async (body: { type?: string; data: Record<string, unknown> }): Promise<World> =>
  request<World>('/api/worlds/import', { method: 'POST', body: JSON.stringify(body) })

export type WorldbookStats = {
  entries?: number
  depth?: number
  [key: string]: unknown
}

/** 酒馆世界书原始 JSON 上传（服务端容错解析脏 JSON） */
export const importWorldbook = async (file: File): Promise<{ world: World; stats: WorldbookStats }> => {
  const form = new FormData()
  form.append('file', file, file.name || 'worldbook.json')
  return request<{ world: World; stats: WorldbookStats }>('/api/worlds/import-worldbook', {
    method: 'POST',
    body: form,
    formData: true,
  })
}

// ---- 头像上传 / AI 生成 ----

export const uploadAvatar = async (file: File): Promise<string> => {
  const form = new FormData()
  form.append('file', file, file.name || 'avatar.png')
  const data = await request<{ url: string }>('/api/upload', { method: 'POST', body: form, formData: true })
  return data.url
}

export const generateAttributes = async (body: {
  name: string
  intro: string
  background: string
  existing: unknown[]
}): Promise<unknown[]> => {
  const data = await request<{ attributes: unknown[] }>('/api/gen/attributes', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return data.attributes ?? []
}

export const generateCharacterFromText = async (text: string): Promise<Character> => {
  const data = await request<{ character: Character }>('/api/gen/character-from-text', {
    method: 'POST',
    body: JSON.stringify({ text }),
  })
  return data.character
}

/** 运行期配置（P2 只取 providers/tts.voices；P4 设置中心扩展完整类型） */
export const getRuntimeConfig = async (): Promise<RuntimeConfigView> =>
  request<RuntimeConfigView>('/api/config')

/** 导出对象落盘（浏览器直接下载 JSON） */
export const downloadJson = (obj: unknown, filename: string): void => {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export { apiUrl, authHeaders }