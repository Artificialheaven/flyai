/** 后端会话/房间/线程/消息的前端类型，字段与 flyai schemas、routes_session 对齐（snake_case 原样）。 */

import type { Character, World } from './lore.js'

export type { Character, World } from './lore.js'

export type SessionMode = 'normal' | 'galgame'

/** GET /api/sessions 列表项（轻量：threads/members 等均为空，不要在列表里依赖） */
export type SessionSummary = {
  id: string
  title: string
  pinned: boolean
  mode: SessionMode
  created_at: string
  updated_at: string
  provider_id?: string
}

export type MessageImage = {
  media_type: string
  /** 不含 data: 前缀的 base64 */
  data: string
}

export type ToolCall = {
  id: string
  name: string
  arguments?: unknown
}

export type RecallItem = {
  source?: string
  text?: string
  [key: string]: unknown
}

export type Tombstone = {
  content: string | null
  images: MessageImage[] | null
  name: string | null
}

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool' | 'tombstone'

export type Message = {
  id: string
  role: MessageRole
  content: string | null
  thinking: string | null
  thinking_blocks?: unknown
  tool_calls: ToolCall[] | null
  tool_call_id: string | null
  name: string | null
  ok: boolean | null
  hidden: boolean | null
  actor_id: string | null
  recall: RecallItem[] | null
  images: MessageImage[] | null
  tombstone: Tombstone | null
}

export type RoomMember = {
  id: string
  name: string
  character_id: string
  is_user: boolean
  /** 会话专属角色副本（override 优先于内容库角色卡） */
  override?: Character | null
  memory?: string
  attributes?: Record<string, unknown>
  provider_id?: string | null
  memory_mode?: '' | 'rw' | 'ro'
  memory_char_id?: string | null
}

export type ThreadKind = 'dm' | 'group'

export type Thread = {
  id: string
  kind: ThreadKind
  title: string
  member_ids: string[]
  messages: Message[]
  edits?: Record<string, unknown>
}

export type TokenUsage = {
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
}

/** GET /api/sessions/{sid} 完整房间 */
export type SessionDetail = SessionSummary & {
  character_id: string
  world_id: string
  character: Character | null
  world: World | null
  /** 房间级动态属性（AI 可写的数值表） */
  attributes?: Record<string, unknown>
  /** 当前绑定的是会话专属角色副本而非内容库卡 */
  scoped?: boolean
  members: RoomMember[]
  threads: Thread[]
  /** POST /api/rooms 返回带顶层消息；GET 的线程内已有历史 */
  messages?: Message[]
  token_usage: TokenUsage
  max_tokens: number
  disabled_tools: string[]
  galgame: Record<string, unknown>
  galgame_tts: Record<string, unknown>
}
