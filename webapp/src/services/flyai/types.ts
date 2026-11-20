/** 后端会话/房间/线程/消息的前端类型，字段与 flyai schemas、routes_session 对齐（snake_case 原样）。 */

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
  character_id?: string
  world_id?: string
  /** GET /api/sessions 列表会附带消息条数（详情接口不带） */
  message_count?: number
  /** 会话级累计 token 用量（列表与详情都带，旧会话可能为空） */
  token_usage?: TokenUsage
}

export type TokenUsage = {
  prompt_tokens?: number
  /** 提示词中命中缓存的部分（prompt_tokens 已包含它） */
  cached_tokens?: number
  completion_tokens?: number
  total_tokens?: number
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

/** 酒馆正则脚本（声明式数据；前端展示侧原生解释，绝不执行卡内 JS） */
export type RegexScript = {
  scriptName?: string
  findRegex?: string
  replaceString?: string
  placement?: number | number[]
  markdownOnly?: boolean
  promptOnly?: boolean
  runOnEdit?: boolean
  trimStrings?: string[]
  minDepth?: number | null
  maxDepth?: number | null
  disabled?: boolean
  [key: string]: unknown
}

/** 卡内声明的状态面板：开场白里的字面量占位符 → 展示侧正则渲染出的 HTML */
export type StPanel = {
  /** 开场白中的字面量占位符（如 <StatusPlaceHolderImpl/>） */
  token: string
  /** 面板标题，缺省为占位符本身 */
  title?: string
}

/** 会话打开时下发的酒馆扩展数据（GET /api/sessions/{sid}.st） */
export type StPayload = {
  regex_scripts: RegexScript[]
  by_character?: Record<string, RegexScript[]>
  panels?: StPanel[]
  html?: { enabled?: boolean; external_images?: boolean; image_proxy?: boolean }
  flags?: {
    regex_prompt?: boolean
    regex_display?: boolean
    html?: boolean
    external_images?: boolean
    /** 外链图片经服务端代理（需管理员开启且权限等级达标） */
    image_proxy?: boolean
    /** 是否有资格使用图片代理（设置页据此显示开关） */
    image_proxy_available?: boolean
    mvu?: boolean
    /** 整页 HTML + JS 应用放进沙箱 iframe（需管理员开启且用户选择） */
    js_sandbox?: boolean
    /** 卡片可在用户确认后发起发送/继续/重生成（仅单角色 DM 末条消息） */
    card_actions?: boolean
    /** 卡片可在用户确认后写入会话副本（变量/世界书/角色字段） */
    card_write?: boolean
    /** 卡片网络请求经服务端受控代理（需管理员开启且用户选择） */
    net_proxy?: boolean
    /** 是否有资格使用网络代理（设置页据此显示开关） */
    net_proxy_available?: boolean
  }
  mvu?: { enabled?: boolean; stat_data?: Record<string, unknown> }
  /** 宿主需要下发给卡片的副本快照：世界书条目与角色白名单字段 */
  write_context?: CardWriteContext
}

/** 卡片写入上下文（会话副本快照；根卡/公共卡永不下发可变引用） */
export type CardWriteContext = {
  worldbook?: { name?: string; entries?: LoreEntry[] } | null
  character?: Record<string, unknown> | null
}

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool' | 'tombstone'

export type Message = {
  id: string
  role: MessageRole
  content: string | null
  thinking: string | null
  thinking_blocks?: unknown
  thinking_secs?: number | null
  tool_calls: ToolCall[] | null
  tool_call_id: string | null
  name: string | null
  ok: boolean | null
  hidden: boolean | null
  actor_id: string | null
  recall: RecallItem[] | null
  images: MessageImage[] | null
  tombstone: Tombstone | null
  /** 发言者角色卡 id（酒馆正则展示侧按卡查表用） */
  character_id?: string | null
  /** 该条助手消息生成后的 MVU 变量快照（用于展示侧宏展开/回退） */
  stat_data?: Record<string, unknown> | null
  /** 消息来源：卡片在宿主确认后发起/发送的为 'card'，缺省为用户手动操作 */
  origin?: string | null
}

/** 内容库角色卡（当前阶段只用到展示/绑定所需字段，完整编辑器类型后续补齐） */
export type LorePosition = 'system' | 'depth'
export type LoreRole = 'system' | 'user' | 'assistant'

export type LoreEntry = {
  title: string
  content: string
  pinned: boolean
  keys: string[]
  position: LorePosition
  depth: number
  insert_order: number
  role: LoreRole
  probability: number
}

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
  /** 酒馆卡保真字段（旧后端可能不返回，均为可选） */
  regex_scripts?: Record<string, unknown>[]
  extensions?: Record<string, unknown>
  alternate_greetings?: string[]
  assets?: Record<string, unknown>[]
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

/** GET /api/sessions/{sid} 完整房间 */
export type SessionDetail = SessionSummary & {
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
  /** 酒馆扩展数据（正则脚本/HTML 开关/MVU 状态）；旧后端不返回 */
  st?: StPayload
}