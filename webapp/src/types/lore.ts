/** 内容库类型：字段与 flyai/lore/models.py 各 to_dict 逐项对齐（snake_case 原样）。 */

export type LorePosition = 'system' | 'depth'
export type LoreRole = 'system' | 'user' | 'assistant'

/** 世界书/角色卡内容表条目（编辑器一行） */
export type LoreEntry = {
  title: string
  content: string
  pinned: boolean
  /** 触发关键词，后端按空白/逗号/顿号/分号/斜杠拆分后入库 */
  keys: string[]
  position: LorePosition
  /** depth 模式下插入深度（0-50） */
  depth: number
  insert_order: number
  role: LoreRole
  /** 触发概率 1-100 */
  probability: number
}

/** 角色属性定义（动态数值，AI 可通过工具修改） */
export type AttributeDef = {
  key: string
  label?: string
  min?: number
  max?: number
  initial?: number
  [key: string]: unknown
}

export type Character = {
  id: string
  name: string
  avatar: string
  intro: string
  background: string
  mes_example: string
  table: LoreEntry[]
  attributes: AttributeDef[]
  prompt_format: string
  prompt_tools: string
  system_prompt: string
  post_history_instructions: string
  first_mes: string
  parent_id: string
  alias: string
  voice: string
  author: string
  public: boolean
}

export type World = {
  id: string
  name: string
  avatar: string
  content: string
  table: LoreEntry[]
  prompt_format: string
  prompt_tools: string
}

/** 编辑器提交体（后端 CharacterPayload；id 只在更新时带） */
export type CharacterPayload = Omit<Character, 'id' | 'author' | 'public'> & { id?: string }
export type WorldPayload = Omit<World, 'id'> & { id?: string }

/** 空内容，用于新建编辑器初始值 */
export const emptyLoreEntry = (): LoreEntry => ({
  title: '',
  content: '',
  pinned: false,
  keys: [],
  position: 'system',
  depth: 4,
  insert_order: 100,
  role: 'system',
  probability: 100,
})

export const emptyCharacter = (): Character => ({
  id: '',
  name: '',
  avatar: '',
  intro: '',
  background: '',
  mes_example: '',
  table: [],
  attributes: [],
  prompt_format: '',
  prompt_tools: '',
  system_prompt: '',
  post_history_instructions: '',
  first_mes: '',
  parent_id: '',
  alias: '',
  voice: '',
  author: '',
  public: false,
})

export const emptyWorld = (): World => ({
  id: '',
  name: '',
  avatar: '',
  content: '',
  table: [],
  prompt_format: '',
  prompt_tools: '',
})

/** GET /api/config 里 P2 需要的子集（P4 设置中心再扩展） */
export type ProviderView = {
  id: string
  type?: string
  base_url?: string
  api_key?: string
  model: string
  vision?: boolean
  owner?: string
  shared?: boolean
  min_level?: number
  rate?: number
}

export type VoiceOption = {
  name: string
  id: string
}

export type RuntimeConfigView = {
  active_provider?: string
  providers: ProviderView[]
  tts?: {
    enabled?: boolean
    voices?: VoiceOption[]
    default_speaker?: string
    min_level?: number
  }
  roleplay?: {
    format_instruction?: string
    tool_instruction?: string
  }
  stable_diffusion?: {
    enabled?: boolean
  }
  [key: string]: unknown
}