import {
  apiUrl,
  authHeaders,
  FlyApiError,
  readDetail,
  request,
  uploadRequest,
  type UploadProgress,
} from './client.js'
import { downloadBlob } from '../native.js'
import { invalidateCatalog, loadCatalog } from './catalogCache.js'
import type {
  AttributeDef,
  Character,
  CharacterSummary,
  World,
  WorldSummary,
} from './types.js'

/** 角色卡 / 世界观：登录态（Bearer）由 request 统一注入。 */

/**
 * 内容库列表：只取轻量摘要（不含 table/extensions 等大字段）。
 * 15MB 级的酒馆卡走全量 to_dict 会把列表接口拖到几十秒，编辑器再按 id 拉详情。
 */
export const listCharacters = (options?: { force?: boolean }): Promise<CharacterSummary[]> =>
  loadCatalog(
    'characters',
    async () => {
      const data = await request<{ characters: CharacterSummary[] }>(
        '/api/characters?summary=1'
      )
      return data.characters ?? []
    },
    options
  )

/** 单张角色卡完整数据（编辑器 / 副本操作前按需拉取） */
export const getCharacter = (cid: string): Promise<Character> =>
  request<Character>(`/api/characters/${encodeURIComponent(cid)}`)

export const createCharacter = async (body: Partial<Character>): Promise<Character> => {
  const character = await request<Character>('/api/characters', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  invalidateCatalog('characters')
  return character
}

export const updateCharacter = async (cid: string, body: Partial<Character>): Promise<Character> => {
  const character = await request<Character>(`/api/characters/${encodeURIComponent(cid)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
  invalidateCatalog('characters')
  return character
}

export const deleteCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${encodeURIComponent(cid)}`, {
    method: 'DELETE',
  })
  invalidateCatalog('characters')
}

/** 把可见（含共享）卡复制为自己的独立根角色卡 */
export const forkCharacter = async (cid: string): Promise<Character> => {
  const character = await request<Character>(`/api/characters/${encodeURIComponent(cid)}/fork`, {
    method: 'POST',
  })
  invalidateCatalog('characters')
  return character
}

export const publishCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${encodeURIComponent(cid)}/publish`, {
    method: 'POST',
  })
  invalidateCatalog('characters')
}

export const unpublishCharacter = async (cid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/characters/${encodeURIComponent(cid)}/unpublish`, {
    method: 'POST',
  })
  invalidateCatalog('characters')
}

export const listWorlds = (options?: { force?: boolean }): Promise<WorldSummary[]> =>
  loadCatalog(
    'worlds',
    async () => {
      const data = await request<{ worlds: WorldSummary[] }>('/api/worlds?summary=1')
      return data.worlds ?? []
    },
    options
  )

/** 单个世界观完整数据（编辑器 / 导出前按需拉取） */
export const getWorld = (wid: string): Promise<World> =>
  request<World>(`/api/worlds/${encodeURIComponent(wid)}`)

export const createWorld = async (body: Partial<World>): Promise<World> => {
  const world = await request<World>('/api/worlds', { method: 'POST', body: JSON.stringify(body) })
  invalidateCatalog('worlds')
  return world
}

export const updateWorld = async (wid: string, body: Partial<World>): Promise<World> => {
  const world = await request<World>(`/api/worlds/${encodeURIComponent(wid)}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
  invalidateCatalog('worlds')
  return world
}

export const deleteWorld = async (wid: string): Promise<void> => {
  await request<{ ok: boolean }>(`/api/worlds/${encodeURIComponent(wid)}`, { method: 'DELETE' })
  invalidateCatalog('worlds')
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

type Exportable = { id: string; name?: string; alias?: string }

export const exportCharacter = async (character: Exportable): Promise<void> => {
  const blob = await fetchBlob(`/api/characters/${encodeURIComponent(character.id)}/export`)
  await downloadBlob(blob, `${safeName(character.name || character.alias || '')}.json`)
}

export const exportWorld = async (world: Exportable): Promise<void> => {
  const blob = await fetchBlob(`/api/worlds/${encodeURIComponent(world.id)}/export`)
  await downloadBlob(blob, `${safeName(world.name || '')}.json`)
}

const uploadFile = <T>(
  path: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<T> => {
  const form = new FormData()
  form.append('file', file, file.name)
  return uploadRequest<T>(path, form, { onProgress })
}

/** 酒馆 CCv2/v3 角色卡（PNG/JSON） */
export const importCharacterCard = async (
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<Character> => {
  const character = await uploadFile<Character>(
    '/api/characters/import-card',
    file,
    onProgress
  )
  invalidateCatalog('characters')
  return character
}

/**
 * FlyAI / 酒馆 v2 角色卡 JSON（导出文件可直接再导入）。
 * 走服务端 /characters/import：保留酒馆保真字段，并还原内嵌头像。
 */
export const importCharacterJson = async (
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<Character> => {
  const text = await file.text()
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error('文件不是有效的 JSON')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('文件内容不是角色卡 JSON')
  }
  const envelope = parsed as { data?: unknown; type?: unknown }
  const data =
    envelope.data && typeof envelope.data === 'object' && !Array.isArray(envelope.data)
      ? envelope.data
      : parsed
  const type = typeof envelope.type === 'string' && envelope.type ? envelope.type : 'character'
  const character = await uploadRequest<Character>(
    '/api/characters/import',
    JSON.stringify({ type, data }),
    { contentType: 'application/json', onProgress }
  )
  invalidateCatalog('characters')
  return character
}

export const importWorldbook = async (
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<{ world: World; stats: Record<string, unknown> }> => {
  const result = await uploadFile<{ world: World; stats: Record<string, unknown> }>(
    '/api/worlds/import-worldbook',
    file,
    onProgress
  )
  invalidateCatalog('worlds')
  return result
}

export const importWorldJson = async (
  file: File,
  onProgress?: (progress: UploadProgress) => void
): Promise<World> => {
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
  const world = await uploadRequest<World>('/api/worlds', JSON.stringify(data), {
    contentType: 'application/json',
    onProgress,
  })
  invalidateCatalog('worlds')
  return world
}

/** 图片上传（头像、背景图），返回 /uploads/... 路径 */
export const uploadImage = async (file: File): Promise<string> => {
  const data = await uploadFile<{ url: string }>('/api/upload', file)
  return data.url
}

export const uploadAvatar = uploadImage

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