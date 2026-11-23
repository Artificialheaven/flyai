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
  /** 面板由卡内酒馆助手脚本生成；正文里的正则结果只是兜底提示 */
  author?: boolean
}

/** 卡内酒馆助手脚本（只读数据；由前端独立 iframe 执行） */
export type StAuthorScript = {
  id: string
  name: string
  content: string
}

export type StAuthorPayload = {
  /** 用户/管理员是否开启作者脚本运行时 */
  enabled?: boolean
  scripts?: StAuthorScript[]
  /** 脚本数量或体积超限，已截断 */
  truncated?: boolean
  /** 酒馆 chatMetadata（作者脚本的地图/手札存档），会话级持久化 */
  chat_metadata?: Record<string, unknown>
  character?: {
    name?: string
    description?: string
    avatar?: string
  }
}

/** 会话打开时下发的酒馆扩展数据（GET /api/sessions/{sid}.st） */
export type StPayload = {
  regex_scripts: RegexScript[]
  /** 启用预设附带的全局正则；先于角色卡正则应用（展示开关关闭时为空） */
  regex_scripts_global?: RegexScript[]
  by_character?: Record<string, RegexScript[]>
  panels?: StPanel[]
  /** 酒馆助手（TavernHelper）作者脚本；开关关闭时 scripts 为空 */
  author?: StAuthorPayload
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
  /** EJS 模板（方案 B）：开关与 local/message 变量快照 */
  template?: {
    prompt_enabled?: boolean
    output_enabled?: boolean
    vars?: {
      local?: Record<string, unknown>
      message?: Record<string, Record<string, unknown>>
    }
  }
  /** 宿主需要下发给卡片的副本快照：世界书条目与角色白名单字段 */
  write_context?: CardWriteContext
}

/** 卡片写入上下文（会话副本快照；根卡/公共卡永不下发可变引用） */
export type CardWriteContext = {
  worldbook?: { name?: string; entries?: unknown[] } | null
  /** 角色卡自带世界书名（CC extensions.world） */
  characterWorldName?: string
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
  /** EJS 模板原文（仅输出侧功能开启且消息含模板时存在） */
  raw_content?: string | null
  /** false=等待客户端渲染；true=已渲染；null/缺省=不含模板 */
  template_rendered?: boolean | null
  /** 该消息的 message 作用域模板变量 */
  template_vars?: Record<string, unknown> | null
  template_render_id?: string | null
  /** 酒馆卡消息扩展字段：卡内 createChatMessages 写入，刷新后读回 */
  st_extra?: Record<string, unknown> | null
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

/** 内容库列表用轻量角色卡（不含 table/extensions 等大字段） */
export type CharacterOpening = { title: string; preview: string }
export type CharacterSummary = Pick<
  Character,
  'id' | 'name' | 'avatar' | 'intro' | 'alias' | 'author' | 'public' | 'parent_id' | 'voice'
> & {
  alternate_greetings?: string[]
  openings?: CharacterOpening[]
  table_size?: number
  /** 整卡文件 md5：本地整卡缓存的校验令牌（旧后端不返回） */
  md5?: string
  /** 256px WebP 缩略图；列表优先用它，避免下载十几 MB 的原图 */
  avatar_thumb?: string
}

/** 内容库列表用轻量世界观 */
export type WorldSummary = Pick<World, 'id' | 'name' | 'avatar' | 'content'> & {
  entry_count?: number
  /** 世界书文件 md5（旧后端不返回） */
  md5?: string
}

/** 预设 prompt 块（酒馆 prompt manager 概念）：main 并入系统提示、depth 插历史、tail 放在历史后 */
export type PresetBlock = {
  name: string
  content: string
  enabled: boolean
  role: 'system' | 'user' | 'assistant'
  position: 'main' | 'depth' | 'tail'
  depth: number
  insert_order: number
  identifier: string
}

/** 模型预设：采样参数 + prompt 块 + assistant 前缀，按用户隔离 */
export type Preset = {
  id: string
  name: string
  note: string
  source: 'tavern' | 'flyai'
  params: Record<string, string | number>
  max_tokens: number
  assistant_prefill: string
  blocks: PresetBlock[]
  /** 预设绑定的酒馆正则（导入 regexes-bindings 块得到；启用即生效） */
  regex_scripts?: RegexScript[]
  created_at: string
  updated_at: string
}

export type PresetImportResult = {
  preset: Preset
  stats: Record<string, unknown>
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
  /** 用户 persona 名（无 persona 时为“用户”） */
  user_name?: string
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