/**
 * DeepSeek 数据导出 ZIP 的前端解析。
 *
 * 官方导出包结构（export_format_version=3）：
 *   manifest.json                 {"export_format_version":3}
 *   chat_session_index.json       [{id,title,message_count,updated_at,...}]
 *   chat_sessions/<id>.json       会话全文（消息树 + fragment 列表）
 *   chat_sessions/<id>_files/...  图片等附件（export_path 为包内相对路径）
 *
 * 会话里的消息按 parent_id 组成「编辑 / 重新生成」分支树。这里沿
 * current_message_id 回溯出用户当前看到的那条分支，再转成 FlyAI
 * flyai-conversation v2 的 data 形态（单 AI 私聊：用户座位 + 无名 AI 座位），
 * 不附带角色卡与世界卡，交由既有 /api/sessions/import 重建落库。
 */
import { unzipSync } from 'fflate'
import { createId } from '../../utils/id.js'

/** 一次最多导入的会话数（产品约定）。 */
export const MAX_DEEPSEEK_SESSIONS = 9

export type DeepSeekMessageImage = {
  media_type: string
  /** 不含 data: 前缀的 base64 */
  data: string
}

type FlyaiImportMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string | null
  thinking: string | null
  thinking_blocks: null
  thinking_secs: number | null
  tool_calls: null
  tool_call_id: null
  name: string | null
  ok: null
  hidden: null
  actor_id: null
  character_id: null
  recall: null
  images: DeepSeekMessageImage[] | null
  tombstone: null
}

/** 与后端 /api/sessions/import 的 SessionImportPayload 对齐。 */
export type FlyaiConversationPayload = {
  type: 'flyai-conversation'
  version: 2
  name: string
  data: {
    session: {
      title: string
      created_at: string
      updated_at: string
      attributes: Record<string, never>
    }
    members: {
      id: string
      name: string
      is_user: boolean
      character_id: string
    }[]
    threads: {
      id: string
      kind: 'dm'
      title: string
      member_ids: string[]
      messages: FlyaiImportMessage[]
      edits: Record<string, never>
    }[]
    messages: never[]
    characters: never[]
    character: null
    world: null
  }
}

export type DeepSeekImportSession = {
  /** DeepSeek 会话 id（仅用于前端选择，不会写入新会话） */
  id: string
  title: string
  /** 当前分支里可导入的消息条数 */
  messageCount: number
  updatedAt: string
  payload: FlyaiConversationPayload
}

export type ParsedDeepSeekExport = {
  /** 只包含有可导入消息的会话，顺序与索引一致（通常新→旧） */
  sessions: DeepSeekImportSession[]
}

const MANIFEST_PATH = 'manifest.json'
const INDEX_PATH = 'chat_session_index.json'
const SESSION_DIR = 'chat_sessions/'

const IMAGE_MIME_BY_EXT: Record<string, string> = {
  webp: 'image/webp',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  bmp: 'image/bmp',
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null

const asString = (value: unknown): string => (typeof value === 'string' ? value : '')

const parseJson = (bytes: Uint8Array | undefined): unknown => {
  if (!bytes) {
    return null
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes))
  } catch {
    return null
  }
}

const toBase64 = (bytes: Uint8Array): string => {
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

const imageMime = (fileName: string): string => {
  const ext = fileName.slice(fileName.lastIndexOf('.') + 1).toLowerCase()
  return IMAGE_MIME_BY_EXT[ext] ?? 'image/jpeg'
}

const cleanTitle = (value: unknown, fallback: string): string => {
  const text = asString(value).replace(/\s+/g, ' ').trim()
  return (text || fallback).slice(0, 60)
}

/** 当前分支：有 current_message_id 沿父链回溯；旧包缺失时退化到平坦时间序。 */
const activeBranch = (messages: Record<string, unknown>[], current: unknown): Record<string, unknown>[] => {
  const byId = new Map<string, Record<string, unknown>>()
  for (const message of messages) {
    const id = message.id
    if (typeof id === 'string' || typeof id === 'number') {
      byId.set(String(id), message)
    }
  }
  const walk = (start: string): Record<string, unknown>[] => {
    const chain: Record<string, unknown>[] = []
    const seen = new Set<string>()
    let cursor: string | null = start
    while (cursor !== null && byId.has(cursor) && !seen.has(cursor)) {
      seen.add(cursor)
      const message = byId.get(cursor) as Record<string, unknown>
      chain.push(message)
      const parent = message.parent_id
      cursor =
        typeof parent === 'string' || typeof parent === 'number' ? String(parent) : null
    }
    return chain.reverse()
  }
  const sorted = (list: Record<string, unknown>[]): Record<string, unknown>[] =>
    [...list].sort((a, b) => {
      const ta = asString(a.inserted_at)
      const tb = asString(b.inserted_at)
      if (ta !== tb) {
        return ta < tb ? -1 : 1
      }
      return Number(a.id) - Number(b.id) || 0
    })

  const flat = messages.every((message) => message.parent_id == null)
  if (flat) {
    return sorted(messages)
  }
  if ((typeof current === 'string' || typeof current === 'number') && byId.has(String(current))) {
    const chain = walk(String(current))
    if (chain.length > 0) {
      return chain
    }
  }
  // 索引里没给 current：取没有子节点的最新消息作为叶子再回溯
  const parentIds = new Set(
    messages
      .map((message) => message.parent_id)
      .filter((id) => id !== null && id !== undefined)
      .map((id) => String(id))
  )
  const leaf = [...sorted(messages)].reverse().find((message) => !parentIds.has(String(message.id)))
  return leaf ? walk(String(leaf.id)) : sorted(messages)
}

const collectImages = (
  files: unknown,
  entries: Record<string, Uint8Array>
): DeepSeekMessageImage[] => {
  if (!Array.isArray(files)) {
    return []
  }
  const images: DeepSeekMessageImage[] = []
  for (const raw of files) {
    const file = asRecord(raw)
    if (!file || file.is_image !== true) {
      continue
    }
    const path = asString(file.export_path).replace(/\\/g, '/')
    const bytes = path ? entries[path] : undefined
    if (!bytes || bytes.length === 0) {
      continue
    }
    images.push({
      media_type: imageMime(asString(file.file_name) || path),
      data: toBase64(bytes),
    })
  }
  return images
}

/** 把一条 DeepSeek 消息的 fragments 合成 FlyAI 消息；无可展示内容返回 null。 */
const convertMessage = (
  raw: Record<string, unknown>,
  entries: Record<string, Uint8Array>
): FlyaiImportMessage | null => {
  const role = asString(raw.role).toUpperCase()
  if (role !== 'USER' && role !== 'ASSISTANT') {
    return null
  }
  const fragments = Array.isArray(raw.fragments) ? raw.fragments : []
  const texts: string[] = []
  const thoughts: string[] = []
  let thinkingSecs = 0
  const images: DeepSeekMessageImage[] = []

  for (const item of fragments) {
    const fragment = asRecord(item)
    if (!fragment) {
      continue
    }
    const type = asString(fragment.type)
    if (type === 'REQUEST' || type === 'RESPONSE') {
      const content = asString(fragment.content).trim()
      if (content) {
        texts.push(content)
      }
    } else if (type === 'THINK') {
      const content = asString(fragment.content).trim()
      if (content) {
        thoughts.push(content)
      }
      const secs = fragment.elapsed_secs
      if (typeof secs === 'number' && Number.isFinite(secs) && secs > 0) {
        thinkingSecs += secs
      }
    } else if (type === 'FILE') {
      images.push(...collectImages(fragment.files, entries))
    }
  }

  const content = texts.join('\n\n').trim() || null
  const thinking = thoughts.join('\n\n').trim() || null
  if (!content && !thinking && images.length === 0) {
    return null
  }

  return {
    id: createId(),
    role: role === 'USER' ? 'user' : 'assistant',
    content,
    thinking,
    thinking_blocks: null,
    thinking_secs: thinkingSecs > 0 ? thinkingSecs : null,
    tool_calls: null,
    tool_call_id: null,
    name: null,
    ok: null,
    hidden: null,
    actor_id: null,
    character_id: null,
    recall: null,
    images: images.length > 0 ? images : null,
    tombstone: null,
  }
}

/** 单个 DeepSeek 会话 → flyai-conversation v2 载荷；无有效消息返回 null。 */
const convertSession = (
  raw: Record<string, unknown>,
  fallbackTitle: string,
  entries: Record<string, Uint8Array>
): DeepSeekImportSession | null => {
  const rawMessages = Array.isArray(raw.messages)
    ? raw.messages.map(asRecord).filter((m): m is Record<string, unknown> => m !== null)
    : []
  if (rawMessages.length === 0) {
    return null
  }
  const messages: FlyaiImportMessage[] = []
  for (const message of activeBranch(rawMessages, raw.current_message_id)) {
    const converted = convertMessage(message, entries)
    if (converted) {
      messages.push(converted)
    }
  }
  if (messages.length === 0) {
    return null
  }

  const title = cleanTitle(raw.title, fallbackTitle)
  const userMember = { id: createId(), name: '你', is_user: true, character_id: '' }
  const aiMember = { id: createId(), name: 'AI', is_user: false, character_id: '' }
  const threadId = createId()

  return {
    id: asString(raw.id),
    title,
    messageCount: messages.length,
    updatedAt: asString(raw.updated_at),
    payload: {
      type: 'flyai-conversation',
      version: 2,
      name: title,
      data: {
        session: {
          title,
          created_at: asString(raw.inserted_at),
          updated_at: asString(raw.updated_at),
          attributes: {},
        },
        members: [userMember, aiMember],
        threads: [
          {
            id: threadId,
            kind: 'dm',
            title: '私聊',
            member_ids: [aiMember.id],
            messages,
            edits: {},
          },
        ],
        messages: [],
        characters: [],
        character: null,
        world: null,
      },
    },
  }
}

/** 前端识别：带 manifest + 会话索引的 zip 视为 DeepSeek 数据导出。 */
export const isDeepSeekExport = (entries: Record<string, Uint8Array>): boolean => {
  const manifest = asRecord(parseJson(entries[MANIFEST_PATH]))
  if (!manifest || typeof manifest.export_format_version !== 'number') {
    return false
  }
  return Boolean(entries[INDEX_PATH])
}

/**
 * 解析 DeepSeek 数据导出 ZIP。
 * 不是 DeepSeek 导出包返回 null（调用方回退到原有导入逻辑）；
 * 是导出包但没有任何可导入会话时返回 {sessions: []}。
 */
export const parseDeepSeekExport = (bytes: Uint8Array): ParsedDeepSeekExport | null => {
  let entries: Record<string, Uint8Array>
  try {
    entries = unzipSync(bytes)
  } catch {
    return null
  }
  if (!isDeepSeekExport(entries)) {
    return null
  }
  const index = parseJson(entries[INDEX_PATH])
  if (!Array.isArray(index)) {
    return { sessions: [] }
  }

  const sessions: DeepSeekImportSession[] = []
  const seen = new Set<string>()
  index.forEach((item, position) => {
    const entry = asRecord(item)
    if (!entry) {
      return
    }
    const id = asString(entry.id)
    // 路径只接受安全字符，避免用索引里的 id 拼出包外路径
    if (!id || !/^[\w-]+$/.test(id) || seen.has(id)) {
      return
    }
    seen.add(id)
    const raw = asRecord(parseJson(entries[`${SESSION_DIR}${id}.json`]))
    if (!raw) {
      return
    }
    const converted = convertSession(raw, `DeepSeek 对话 ${position + 1}`, entries)
    if (converted) {
      // 会话文件缺 id 时退回索引 id，保证前端选择列表的 key 唯一
      if (!converted.id) {
        converted.id = id
      }
      sessions.push(converted)
    }
  })
  return { sessions }
}
