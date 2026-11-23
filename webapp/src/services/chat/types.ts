import type { TranslationMessage } from '../../i18n/index.js'
import type { RoomMember, StPayload } from '../flyai/types.js'
import type { ApiErrorKind } from '../api/client.js'
import type {
  ChatModel,
  MessageUsage,
  ServerToolBlock,
  StopDetails,
  StopReason,
  TextCitation,
} from '../api/types.js'

/**
 * 消息附件。id 是本地标识（IndexedDB files 表的主键，原件按它存储，所有引用同一 id 的消息共用一份），
 * 导入的附件沿用导出产物里的文件 ID；本地没有原件的附件只展示文件名，不进入请求。
 * serverId 是 Files API 返回的 file_id，导入的数据或尚未上传成功的文件为 null，发送前补传。
 */
export type FileAttachment = {
  id: string
  serverId: string | null
  name: string
  size: number
  /** 附件类别；导入时沿用归档的 is_image，不由 MIME 识别结果决定。 */
  isImage: boolean
  /** 具体 MIME；导入时没有原件或无法识别的为空字符串，不影响图片身份。 */
  mimeType: string
}

/** 本地消息的分支关系；字段缺省时按线性消息列表处理。 */
type MessageBranch = {
  parentId?: string | null
  /** 当前选中的下一条消息；切换版本时保留每条分支原有的选择。 */
  selectedChildId?: string
}

/**
 * 后端历史版本（thread.edits）在消息上的投影：
 * 用户消息按 edit 版本计，助手回复按当前 edit 内的 reply 变体计。
 * 切换时都由锚点用户消息 id 定位整轮。
 */
export type ServerVersions = {
  anchor: string
  kind: 'edit' | 'reply'
  count: number
  index: number
}

export type UserMessage = MessageBranch & {
  id: string
  role: 'user'
  content: string
  files: FileAttachment[]
  createdAt: number
  serverVersions?: ServerVersions
  /** 发言者角色卡 id（酒馆正则展示侧按卡查表用） */
  characterId?: string
  /** 该条消息对应的 MVU 变量快照 */
  statData?: Record<string, unknown>
  /** 消息来源：卡片在宿主确认后发送的消息为 'card'，缺省为用户手动输入 */
  origin?: 'card'
  /** 酒馆卡写入的 message.extra（如开场建档标记），刷新后由卡内 getChatMessages 读回 */
  stExtra?: Record<string, unknown>
}

/** 思考块：thinking_delta 累积，签名随 signature_delta 到达；历史轮次的思考服务端会丢弃，不回传 */
export type ThinkingPart = {
  type: 'thinking'
  thinking: string
  signature?: string
  /** content_block_start 的时间；没有事件时间的历史块缺省 */
  startedAt?: number
  /** content_block_stop（或回复结束）的时间 */
  endedAt?: number
  /** 仅有耗时、没有事件时间时保存秒数。 */
  elapsedSeconds?: number
}

/** 文本块：text_delta 累积，citations_delta 追加引用 */
export type TextPart = {
  type: 'text'
  text: string
  citations?: TextCitation[]
  /** 服务端提示文案，仅用于展示，不作为模型回答参与上下文或续写。 */
  isTemplate?: boolean
}

/**
 * FlyAI 后端工具调用：tool_calls 登记、tool_start 补充进行中文案、tool_end 标记结果。
 * 历史消息按后端保存的 tool_calls / tool 结果补全为完成态，preview 为结果摘录。
 */
export type ToolPart = {
  type: 'tool'
  id: string
  name: string
  /** 后端给出的进行中文案（如「正在搜索 xxx」） */
  statusText?: string
  status: 'wip' | 'done' | 'failed'
  /** 调用结果摘录，可展开查看 */
  preview?: string
}

/** 服务端工具调用与结果块按协议原样保存；历史请求只回传 read_link 调用与结果 */
export type ReplyBlock = ThinkingPart | TextPart | ServerToolBlock | ToolPart

/**
 * pending：已发出请求，还没收到事件；streaming：正在接收；
 * done：收到 message_stop；stopped：用户停止或页面关闭；error：请求或流出错
 */
export type ReplyStatus = 'pending' | 'streaming' | 'done' | 'stopped' | 'error'

/** 自动回忆条目：source 是来源前缀（记忆簿 / 旧对话·昵称），text 是召回的旧事实 */
export type RecallItem = {
  source?: string
  text?: string
}

export type AssistantMessage = MessageBranch & {
  id: string
  role: 'assistant'
  createdAt: number
  /** 未记录模型时为 null，续写使用当前默认模型。 */
  model: ChatModel | null
  /** 请求时是否开启深度思考 */
  thinking?: boolean
  /** 请求时是否声明 deepseek_web_search；关闭时仍声明 deepseek_read_url */
  search?: boolean
  /** 本轮请求的系统信息快照；各回答版本独立保存，续写沿用。历史消息中缺省的快照保持为空。 */
  latestReminder?: string
  /** 群聊发言者（后端消息的 name/actor_id）；私聊不展示 */
  speakerName?: string
  speakerId?: string
  /** 发言者角色卡 id（酒馆正则展示侧按卡查表用） */
  characterId?: string
  /** 该条回复生成后的 MVU 变量快照 */
  statData?: Record<string, unknown>
  /** 本轮自动注入的旧事实；仅作折叠展示，不进上下文 */
  recall?: RecallItem[]
  /** 消息来源：卡片在宿主确认后发起的动作为 'card'，缺省为用户手动操作 */
  origin?: 'card'
  /** EJS 模板原文（仅输出侧功能开启且含模板时存在） */
  rawContent?: string
  /** false=等待客户端渲染；true=已渲染；缺省=不含模板 */
  templateRendered?: boolean
  /** 该消息的 message 作用域模板变量 */
  templateVars?: Record<string, unknown>
  /** 后端返回的已渲染标记（用于补渲染幂等） */
  templateRenderId?: string
  /** 是否允许作为续写前缀；仍需满足回复状态与内容限制。 */
  prefix: boolean
  status: ReplyStatus
  /** 可识别的内容块按接收顺序排列；续写产生的块追加在已有块之后 */
  blocks: ReplyBlock[]
  stopReason?: StopReason
  stopDetails?: StopDetails
  /** 最近一次请求的用量，续写后更新 */
  usage?: MessageUsage
  /** 后端 reply 变体信息；同一轮只在首个气泡上挂载 */
  serverVersions?: ServerVersions
  error?: {
    /** upload：发送前补传附件失败，请求没有发出 */
    kind: ApiErrorKind | 'upload'
    message: string
    status?: number
    localMessage?: TranslationMessage
    /** 仅 upload：重新上传是否可能成功（网络、限流、服务端繁忙）；过大、格式不支持为 false */
    retryable?: boolean
  }
}

/** 撤回后留下的墓碑：保留被撤回原文与图片，供「重新编辑」回填；不进入上下文。 */
export type TombstoneMessage = MessageBranch & {
  id: string
  role: 'tombstone'
  createdAt: number
  recalled: {
    content: string
    images: { mediaType: string; data: string }[]
    name?: string
  }
}

export type Message = UserMessage | AssistantMessage | TombstoneMessage

/** 房间窗口的精简视图（群聊标签页/私聊切换用，不含消息体） */
export type ThreadSummary = {
  id: string
  kind: 'dm' | 'group'
  title: string
  memberIds: string[]
}

export type Session = {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  /** 置顶（后端列表排序用，本地操作后同步） */
  pinned?: boolean
  /** 上下文用量：prompt/completion/total/cached（后端 SessionDetail.token_usage） */
  tokenUsage?: {
    prompt_tokens?: number
    completion_tokens?: number
    total_tokens?: number
    /** 输入中命中缓存的 token 数（已含在 prompt_tokens 内，用于算缓存命中率） */
    cached_tokens?: number
  }
  /** 单窗口上下文上限；0 表示跟随全局默认 */
  maxTokens?: number
  /** 本会话额外禁用的工具名（会话级覆盖） */
  disabledTools?: string[]
  /** 包含所有分支的消息，按创建顺序存储。 */
  messages: Message[]
  /** undefined 表示线性消息列表；null 表示空会话。 */
  rootMessageId?: string | null
  /** FlyAI 后端线程 id；编辑/重新生成与发送都会带上，避免群聊里解析错线程 */
  threadId?: string
  /** 后端线程类型；群聊窗口不支持编辑/重新生成 */
  threadKind?: 'dm' | 'group'
  /** 会话绑定的角色（单 AI 房间的顶层镜像；群聊时取第一个 AI 座位） */
  characterId?: string
  characterName?: string
  /** 用户 persona 名（无 persona 时为“用户”），展示侧展开 {{user}} 用 */
  userName?: string
  characterAvatar?: string
  /** 绑定世界观 */
  worldId?: string
  worldName?: string
  /** 当前对话窗口对应的 AI 座位（群聊选人、座位设置需要） */
  aiMemberId?: string
  /** 房间全部座位（群聊 UI 需要） */
  members?: RoomMember[]
  /** 房间全部窗口（群聊标签页需要） */
  threads?: ThreadSummary[]
  /** 当前绑定的是会话专属角色副本（而非内容库卡） */
  scoped?: boolean
  /** 只读会话（如历史 galgame 记录），不展示输入框 */
  readOnly?: boolean
  /** 酒馆扩展数据（正则脚本/HTML 开关/MVU 状态）；旧后端不返回 */
  st?: StPayload
}

/** 会话列表元数据；消息只在打开会话时加载。 */
export type SessionSummary = Pick<Session, 'id' | 'title' | 'createdAt' | 'updatedAt'> & {
  pinned?: boolean
  characterId?: string
  characterName?: string
  characterAvatar?: string
  worldName?: string
}

/** 显式提取字段，避免将完整会话的消息带进列表。 */
export const toSessionSummary = (session: SessionSummary): SessionSummary => ({
  id: session.id,
  title: session.title,
  createdAt: session.createdAt,
  updatedAt: session.updatedAt,
  pinned: session.pinned,
  characterId: session.characterId,
  characterName: session.characterName,
  characterAvatar: session.characterAvatar,
  worldName: session.worldName,
})
