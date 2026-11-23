import { create } from 'zustand'
import { createId } from '../../utils/id.js'
import type { MessageUsage } from '../api/types.js'
import type { ChatModel } from '../api/types.js'
import { useUploads } from '../attachments.js'
import { showToast } from '../../components/base/toastState.js'
import { useContextWarning } from './contextWarning.js'
import { translate } from '../../i18n/index.js'
import { useSettings } from '../settings.js'
import { useAuth } from '../flyai/auth.js'
import { assetUrl, FlyApiError } from '../flyai/client.js'
import { setRunWatch } from '../native.js'
import {
  cancelRun,
  discoverActiveRun,
  openChatStream,
  probeRun,
  readChatFrames,
  resumeChatStream,
} from '../flyai/chat.js'
import type { ChatImagePayload, ChatSendPayload } from '../flyai/chat.js'
import type { ChatStreamEvent, Usage as FlyUsage } from '../flyai/events.js'
import {
  batchDeleteSessions,
  createSession as createRemoteSession,
  deleteMessage as recallRemoteMessage,
  deleteSession as deleteRemoteSession,
  getSession as fetchSession,
  getSessionConditional,
  listSessions,
  patchSession,
  searchSessions,
  switchTurnVersion,
} from '../flyai/sessions.js'
import type { SessionPatch } from '../flyai/sessions.js'
import {
  addMember,
  createGroup,
  createRoom,
  deleteThread as deleteRoomThreadRemote,
  publishMemberCharacter,
  removeMember,
  renameThread as renameRoomThreadRemote,
  updateMember,
} from '../flyai/rooms.js'
import type {
  Message as FlyMessage,
  SessionDetail,
  SessionSummary as FlySummary,
  Thread,
} from '../flyai/types.js'
import { importFlyaiConversation, importSessionFile } from '../flyai/transfer.js'
import { commitTemplateOutput, loadTemplateGlobalVars } from '../flyai/templates.js'
import { pickCardCharacterFields } from '../st/cardActions.js'
import {
  pendingAssistantMessages,
  renderPendingMessage,
} from '../st/templatePending.js'
import { handleTemplatePrepare } from '../st/templatePrompt.js'
import * as db from './db.js'
import {
  addDraftFiles,
  clearAllDrafts,
  clearDraft,
  clearSentDraft,
  getDraft,
  prepareDraftMessage,
  restoreDraftAttachments,
  setDraftText,
} from './drafts.js'
import {
  appendBranch,
  getVisibleMessages,
  reconcileFailedReplies,
  rekeyMessage,
} from './branches.js'
import { describeReply } from './describe.js'
import { canContinueReply, settleReply } from './reply.js'
import {
  toSessionSummary,
  type AssistantMessage,
  type FileAttachment,
  type Message,
  type RecallItem,
  type ReplyBlock,
  type Session,
  type SessionSummary,
  type TombstoneMessage,
  type ToolPart,
  type UserMessage,
} from './types.js'

/** chat-demo 的模型标识；FlyAI 实际模型由后端路由决定，这里只用于展示占位 */
const MODEL: ChatModel = 'deepseek-flash'
/** 保留最近打开的详情；当前会话、生成中及正在删除的会话不淘汰。 */
const SESSION_CACHE_LIMIT = 5
/** 停止后等待后端落盘再回读，避免拿不到半截回复 */
const STOP_REFRESH_DELAY = 1000
/** 断线重连：最多尝试次数、指数退避上限（毫秒） */
const MAX_RECONNECT_ATTEMPTS = 8
const MAX_RECONNECT_DELAY = 8000
/** 断点标记有效期；过期视作运行已结束 */
const MARKER_TTL = 30 * 60 * 1000
/** 标记落盘节流：流式增量频繁，避免每个事件都写 localStorage */
const MARKER_SAVE_THROTTLE = 400

type ChatState = {
  ready: boolean
  initError: boolean
  /** 全部删除期间锁定交互 */
  importingHistory: boolean
  /** 仅列表元数据，按 updatedAt 降序；流式消息更新不会改变此数组。 */
  sessions: SessionSummary[]
  sessionCache: Record<string, Session>
  sessionLoadErrors: Record<string, boolean>
  /** 后端时代不再本地保存会话，保留字段以兼容界面 */
  sessionSaveErrors: Record<string, boolean>
  /** null 表示新对话（欢迎页） */
  activeId: string | null
  /** 正在生成回复的会话 */
  streaming: Record<string, AbortController>
}

export const useChatStore = create<ChatState>(() => ({
  ready: false,
  initError: false,
  importingHistory: false,
  sessions: [],
  sessionCache: {},
  sessionLoadErrors: {},
  sessionSaveErrors: {},
  activeId: null,
  streaming: {},
}))

const { getState: get, setState: set } = useChatStore

const sortSessions = (sessions: SessionSummary[]) =>
  [...sessions].sort((a, b) => {
    if (Boolean(a.pinned) !== Boolean(b.pinned)) {
      return a.pinned ? -1 : 1
    }
    return b.updatedAt - a.updatedAt
  })

const getSession = (id: string) => {
  const cache = get().sessionCache
  return Object.hasOwn(cache, id) ? cache[id] : undefined
}

const getStreamController = (id: string) => {
  const streams = get().streaming
  return Object.hasOwn(streams, id) ? streams[id] : undefined
}

const sessionAccess = new Map<string, number>()
const deletingSessions = new Set<string>()
/** 正在发送中的会话；新建会话用保留键。sendMessage 在首个 await 前同步占位，
 * 直到 streamReply 注册好 controller 才释放，堵住连点/回车+点击的重复发送。 */
const SEND_GUARD_NEW = '__new__'
const sendsInFlight = new Set<string>()
const streamFlushes = new Map<string, () => void>()
/** 每个会话当前的后台运行号，停止/删除时通知后端取消 */
const runIds = new Map<string, string>()
/** 每个会话当前选中的窗口（群聊标签页切换；回读时保持同一窗口） */
const activeThreadIds = new Map<string, string>()
let accessSequence = 0
let historyEpoch = 0
let initTask: Promise<void> | undefined
const sessionLoads = new Map<string, { promise: Promise<void> }>()

/** 写穿缓存：会话快照去抖写入 IndexedDB（约 400ms）；流式中的会话跳过。 */
const PERSIST_DELAY = 400
const persistTimers = new Map<string, number>()

const cancelPersist = (id: string) => {
  const timer = persistTimers.get(id)
  if (timer !== undefined) {
    window.clearTimeout(timer)
    persistTimers.delete(id)
  }
}

const flushPersist = (id: string) => {
  cancelPersist(id)
  const session = getSession(id)
  // 流式中途或正在删除：半截快照不能当成缓存权威，等收尾后的回读再写
  if (!session || getStreamController(id) || deletingSessions.has(id)) {
    return
  }
  void db.saveSession(session).catch(db.reportStorageError('persist session'))
}

const persistSession = (id: string) => {
  if (getStreamController(id) || deletingSessions.has(id)) {
    return
  }
  cancelPersist(id)
  persistTimers.set(
    id,
    window.setTimeout(() => {
      persistTimers.delete(id)
      flushPersist(id)
    }, PERSIST_DELAY)
  )
}

/** 第 n 次重连前的等待：500ms 起步，1.5 倍递增，封顶 8 秒 */
const backoffDelay = (attempt: number) =>
  Math.min(MAX_RECONNECT_DELAY, Math.round(500 * Math.pow(1.5, attempt - 1)))

const abortError = () => new DOMException('aborted', 'AbortError')

/** 可被停止按钮打断的等待 */
const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError())
      return
    }
    const onAbort = () => {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      reject(abortError())
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal.addEventListener('abort', onAbort)
  })

const storageGet = (key: string): string | null => {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

const storageSet = (key: string, value: string | null) => {
  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // 隐私模式或配额已满：断点续读降级为不跨刷新
  }
}

/** 按登录用户隔离断点标记与最后打开的会话 */
const localUserKey = () =>
  String(useAuth.getState().user?.id ?? storageGet('flyai-uid') ?? 'anon')

const runMarkerKey = (sessionId: string) => `flyai-run-${localUserKey()}-${sessionId}`
const activeSessionKey = () => `flyai-active-session-${localUserKey()}`

/** 后台运行号 + 已收到的最后一个事件序号；用于刷新/切后台后续读 */
type RunMarker = { runId: string; sessionId: string; seq: number; ts: number }

const clearRunMarker = (sessionId: string) => storageSet(runMarkerKey(sessionId), null)

const readRunMarker = (sessionId: string): RunMarker | null => {
  const key = runMarkerKey(sessionId)
  const raw = storageGet(key)
  if (!raw) {
    return null
  }
  try {
    const data = JSON.parse(raw) as Partial<RunMarker>
    if (
      typeof data.runId !== 'string' ||
      !data.runId ||
      data.sessionId !== sessionId ||
      typeof data.seq !== 'number' ||
      typeof data.ts !== 'number' ||
      Date.now() - data.ts > MARKER_TTL
    ) {
      storageSet(key, null)
      return null
    }
    return { runId: data.runId, sessionId, seq: data.seq, ts: data.ts }
  } catch {
    storageSet(key, null)
    return null
  }
}

const writeRunMarker = (marker: RunMarker) =>
  storageSet(runMarkerKey(marker.sessionId), JSON.stringify(marker))

const readActiveSession = () => storageGet(activeSessionKey())
const saveActiveSession = (sessionId: string | null) =>
  storageSet(activeSessionKey(), sessionId)

/** 原生 App 切后台（Capacitor appStateChange）时 document 可能仍显示 visible */
let appPaused = false
const appHidden = () => appPaused || document.visibilityState === 'hidden'

/** 等到重新可见（回前台）或停止；后台期间不计重连次数 */
const waitForForeground = (signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      document.removeEventListener('visibilitychange', check)
      window.removeEventListener('flyai:app-resume', onResume)
      window.clearInterval(timer)
      signal.removeEventListener('abort', onAbort)
    }
    const check = () => {
      if (!appHidden()) {
        cleanup()
        resolve()
      }
    }
    const onResume = () => {
      appPaused = false
      check()
    }
    const onAbort = () => {
      cleanup()
      reject(abortError())
    }
    if (signal.aborted) {
      reject(abortError())
      return
    }
    // 10 秒安全轮询：个别 WebView 不派发 visibilitychange
    const timer = window.setInterval(check, 10000)
    document.addEventListener('visibilitychange', check)
    window.addEventListener('flyai:app-resume', onResume)
    signal.addEventListener('abort', onAbort)
  })

const touchSession = (id: string) => sessionAccess.set(id, ++accessSequence)

const trimSessionCache = () => {
  const state = get()
  const ids = Object.keys(state.sessionCache)
  if (ids.length <= SESSION_CACHE_LIMIT) {
    return
  }
  const removable = ids
    .filter(
      (id) =>
        id !== state.activeId &&
        !Object.hasOwn(state.streaming, id) &&
        !deletingSessions.has(id)
    )
    .sort((a, b) => (sessionAccess.get(a) ?? 0) - (sessionAccess.get(b) ?? 0))
  const evicted = new Set(removable.slice(0, ids.length - SESSION_CACHE_LIMIT))
  for (const id of evicted) {
    sessionAccess.delete(id)
  }
  if (evicted.size > 0) {
    set({
      sessionCache: Object.fromEntries(
        Object.entries(state.sessionCache).filter(([id]) => !evicted.has(id))
      ),
    })
  }
}

const updateSession = (id: string, update: (session: Session) => Session) => {
  set((state) => {
    const previous = Object.hasOwn(state.sessionCache, id) ? state.sessionCache[id] : undefined
    if (!previous) {
      return state
    }
    const session = update(previous)
    if (session === previous) {
      return state
    }
    return {
      sessionCache: { ...state.sessionCache, [id]: session },
      sessions: sortSessions(
        state.sessions.map((summary) =>
          summary.id === id ? { ...summary, ...toSessionSummary(session) } : summary
        )
      ),
    }
  })
  persistSession(id)
}

/** 群聊换人发言时追加一条新的 AI 回复；与发送时的追加保持一致（线性消息列表）。 */
const appendReply = (sessionId: string, reply: AssistantMessage) =>
  set((state) => {
    const previous = state.sessionCache[sessionId]
    if (!previous) {
      return state
    }
    return {
      sessionCache: {
        ...state.sessionCache,
        [sessionId]: {
          ...previous,
          messages: [...previous.messages, reply],
          updatedAt: Date.now(),
        },
      },
    }
  })

/** 流式更新只改会话缓存，不碰列表排序，避免每个增量都重排侧栏。 */
const updateReply = (sessionId: string, reply: AssistantMessage) =>
  set((state) => {
    const previous = state.sessionCache[sessionId]
    if (!previous) {
      return state
    }
    return {
      sessionCache: {
        ...state.sessionCache,
        [sessionId]: {
          ...previous,
          messages: previous.messages.map((message) =>
            message.id === reply.id
              ? { ...reply, parentId: message.parentId, selectedChildId: message.selectedChildId }
              : message
          ),
        },
      },
    }
  })

/** 后端时间戳转毫秒；缺失或非法时退回当前时间，保证列表可排序 */
const toEpoch = (value: string | undefined): number => {
  const ms = value ? Date.parse(value) : NaN
  return Number.isFinite(ms) ? ms : Date.now()
}

const toSummary = (source: FlySummary): SessionSummary => ({
  id: source.id,
  title: source.title,
  createdAt: toEpoch(source.created_at),
  updatedAt: toEpoch(source.updated_at),
  pinned: source.pinned,
})

const mediaExt = (mediaType: string) => {
  const slash = mediaType.indexOf('/')
  const ext = slash >= 0 ? mediaType.slice(slash + 1) : mediaType
  if (ext === 'jpeg') {
    return 'jpg'
  }
  return ext || 'png'
}

const base64ToBlob = (data: string, mediaType: string): Blob | null => {
  try {
    const binary = atob(data)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i)
    }
    return new Blob([bytes], { type: mediaType || 'application/octet-stream' })
  } catch {
    return null
  }
}

/** 后端召回条目只保留展示所需字段；文本为空的行跳过 */
const toRecallItems = (items: readonly Record<string, unknown>[]): RecallItem[] =>
  items
    .map((item) => ({
      source: typeof item.source === 'string' ? item.source : undefined,
      text: typeof item.text === 'string' ? item.text : '',
    }))
    .filter((item) => item.text)

type MappedMessage = { message: Message; files: db.StoredFile[] }

/** 后端消息转 chat-demo 消息；system/tool/tombstone 与 hidden 消息不展示 */
const mapMessage = (
  source: FlyMessage,
  toolResults: Map<string, FlyMessage>
): MappedMessage | null => {
  if (source.hidden) {
    return null
  }
  if (source.role === 'tombstone' && source.tombstone) {
    const message: TombstoneMessage = {
      id: source.id,
      role: 'tombstone',
      createdAt: 0,
      recalled: {
        content: source.tombstone.content ?? '',
        images: (source.tombstone.images ?? []).map((image) => ({
          mediaType: image.media_type,
          data: image.data,
        })),
        ...(source.tombstone.name ? { name: source.tombstone.name } : {}),
      },
    }
    return { message, files: [] }
  }
  if (source.role === 'user') {
    const files: FileAttachment[] = []
    const stored: db.StoredFile[] = []
    ;(source.images ?? []).forEach((image, index) => {
      const blob = base64ToBlob(image.data, image.media_type)
      if (!blob) {
        return
      }
      const id = `img-${source.id}-${index}`
      stored.push({ id, blob })
      files.push({
        id,
        serverId: id,
        name: `${index + 1}.${mediaExt(image.media_type)}`,
        size: blob.size,
        isImage: true,
        mimeType: image.media_type,
      })
    })
    const message: UserMessage = {
      id: source.id,
      role: 'user',
      content: source.content ?? '',
      files,
      createdAt: 0,
      ...(source.character_id ? { characterId: source.character_id } : {}),
      ...(source.stat_data ? { statData: source.stat_data } : {}),
      ...(source.origin === 'card' ? { origin: 'card' as const } : {}),
      ...(source.st_extra && typeof source.st_extra === 'object'
        ? { stExtra: source.st_extra }
        : {}),
    }
    return { message, files: stored }
  }
  if (source.role === 'assistant') {
    const blocks: ReplyBlock[] = []
    if (source.thinking) {
      blocks.push({
        type: 'thinking',
        thinking: source.thinking,
        // 后端落库的思考用时；旧消息没有该字段时标题退回中性文案
        ...(source.thinking_secs == null ? {} : { elapsedSeconds: source.thinking_secs }),
      })
    }
    if (source.content) {
      blocks.push({ type: 'text', text: source.content })
    }
    // assistant 的 tool_calls 对应后续 tool 消息；历史回读时补成完成态，界面与流式结束时一致。
    for (const call of source.tool_calls ?? []) {
      const result = toolResults.get(call.id)
      blocks.push({
        type: 'tool',
        id: call.id,
        name: call.name,
        status: result && result.ok === false ? 'failed' : result ? 'done' : 'failed',
        preview: result?.content?.slice(0, 4000) || undefined,
      })
    }
    const message: AssistantMessage = {
      id: source.id,
      role: 'assistant',
      createdAt: 0,
      model: MODEL,
      thinking: Boolean(source.thinking),
      search: false,
      // 后端给每条 AI 回复打了发言者标记；群聊窗口展示，私聊不使用
      speakerName: source.name ?? undefined,
      speakerId: source.actor_id ?? undefined,
      characterId: source.character_id ?? undefined,
      statData: source.stat_data ?? undefined,
      ...(source.raw_content ? { rawContent: source.raw_content } : {}),
      ...(source.template_rendered === null || source.template_rendered === undefined
        ? {}
        : { templateRendered: Boolean(source.template_rendered) }),
      ...(source.template_vars ? { templateVars: source.template_vars } : {}),
      ...(source.template_render_id
        ? { templateRenderId: source.template_render_id }
        : {}),
      ...(source.origin === 'card' ? { origin: 'card' as const } : {}),
      ...(source.st_extra && typeof source.st_extra === 'object'
        ? { stExtra: source.st_extra }
        : {}),
      // 自动回忆挂在本次发言的回复上，重画/刷新后保持同样的折叠组
      recall: source.recall?.length ? toRecallItems(source.recall) : undefined,
      // 后端支持续写：历史消息同样可作为前缀，能力限制交给 canContinueReply
      prefix: true,
      status: 'done',
      blocks,
    }
    return { message, files: [] }
  }
  return null
}

/** 群聊里只有同一发言者的轮次可以并成一条回复 */
const speakerKeyOf = (message: AssistantMessage): string =>
  `${message.speakerId ?? ''}\u0000${message.speakerName ?? ''}`

/**
 * 历史里一次回复被后端按工具轮拆成多条 assistant 消息（每轮一条 + tool 结果）。
 * 流式期间前端把它们画在一个气泡里，回读时也照同样方式合并，否则中途轮会单独
 * 成为一个「已停止」的回复区，思考/正文看起来像被工具调用吃掉了。
 */
const mergeAssistantRounds = (
  base: AssistantMessage,
  next: AssistantMessage
): AssistantMessage => {
  const recall = [...(base.recall ?? []), ...(next.recall ?? [])]
  return {
    ...base,
    thinking: base.thinking || next.thinking,
    blocks: [...base.blocks, ...next.blocks],
    // 合并后的气泡代表整轮：状态快照取最新一轮（后续轮次的变量更新已包含前文）
    ...(next.statData ? { statData: next.statData } : {}),
    ...(recall.length > 0 ? { recall } : {}),
  }
}

/** 对话窗口优先取指定窗口；未指定（或已删除）时取 dm，再退回第一条线程 */
const pickThread = (detail: SessionDetail, preferred?: string): Thread | undefined =>
  (preferred ? detail.threads.find((thread) => thread.id === preferred) : undefined) ??
  detail.threads.find((thread) => thread.kind === 'dm') ??
  detail.threads[0]

type TurnVersionEntry = { count: number; index: number }

/** 从 thread.edits 汇总每个锚点轮的 edit / reply 版本数与活动序号（防御旧数据）。 */
const readTurnVersions = (thread: Thread | undefined) => {
  const edits = new Map<string, TurnVersionEntry>()
  const replies = new Map<string, TurnVersionEntry>()
  for (const [anchor, raw] of Object.entries(thread?.edits ?? {})) {
    if (!raw || typeof raw !== 'object') {
      continue
    }
    const entry = raw as { edits?: unknown; edit_active?: unknown }
    const slots = Array.isArray(entry.edits) ? entry.edits : []
    if (slots.length === 0) {
      continue
    }
    const active = typeof entry.edit_active === 'number' ? entry.edit_active : 0
    edits.set(anchor, { count: slots.length, index: active })
    const slot = slots[active] as
      | { reply_variants?: unknown; reply_active?: unknown }
      | undefined
    const variants = Array.isArray(slot?.reply_variants) ? slot.reply_variants : []
    replies.set(anchor, {
      count: variants.length,
      index: typeof slot?.reply_active === 'number' ? slot.reply_active : 0,
    })
  }
  return { edits, replies }
}

const mapSession = async (detail: SessionDetail, preferredThreadId?: string): Promise<Session> => {
  const thread = pickThread(detail, preferredThreadId)
  // 记住本次选中的窗口，后续刷新（发送后回读等）不再跳回私聊
  if (thread) {
    activeThreadIds.set(detail.id, thread.id)
  } else {
    activeThreadIds.delete(detail.id)
  }
  const sourceMessages = thread?.messages ?? detail.messages ?? []
  const toolResults = new Map<string, FlyMessage>()
  for (const source of sourceMessages) {
    if (source.role === 'tool' && source.tool_call_id) {
      toolResults.set(source.tool_call_id, source)
    }
  }
  const { edits: editVersions, replies: replyVersions } = readTurnVersions(thread)
  const messages: Message[] = []
  const stored: db.StoredFile[] = []
  // 缓冲当前回合的回复；遇到下一条用户消息/换人发言时落盘
  let pendingReply: AssistantMessage | undefined
  // 当前轮锚点与「回复版本已挂到首个气泡」标记，避免同轮后续气泡重复展示切换器
  let turnAnchor: string | null = null
  let replyVersionAttached = false
  const flushReply = () => {
    if (pendingReply) {
      messages.push(pendingReply)
      pendingReply = undefined
    }
  }
  for (const source of sourceMessages) {
    const mapped = mapMessage(source, toolResults)
    if (!mapped) {
      continue
    }
    stored.push(...mapped.files)
    const message = mapped.message
    if (message.role !== 'assistant') {
      flushReply()
      if (message.role === 'user') {
        turnAnchor = message.id
        replyVersionAttached = false
        const info = editVersions.get(message.id)
        if (info && info.count > 1) {
          message.serverVersions = { anchor: message.id, kind: 'edit', ...info }
        }
      }
      messages.push(message)
      continue
    }
    if (
      pendingReply !== undefined &&
      speakerKeyOf(pendingReply) === speakerKeyOf(message) &&
      pendingReply.blocks.some((block) => block.type === 'tool')
    ) {
      pendingReply = mergeAssistantRounds(pendingReply, message)
    } else {
      flushReply()
      pendingReply = message
      if (!replyVersionAttached && turnAnchor) {
        replyVersionAttached = true
        const info = replyVersions.get(turnAnchor)
        if (info && info.count > 1) {
          message.serverVersions = { anchor: turnAnchor, kind: 'reply', ...info }
        }
      }
    }
  }
  flushReply()
  if (stored.length > 0) {
    // 图片原件照旧落盘（暖启动渲染依赖它），但不挡首帧
    void db.saveFiles(stored).catch(db.reportStorageError('save session files'))
  }
  // 当前窗口对应的 AI 座位：优先按线程成员找，群聊标签页切换时也随之变化
  const seat =
    detail.members.find((member) => !member.is_user && thread?.member_ids.includes(member.id)) ??
    detail.members.find((member) => !member.is_user)
  // 顶层角色（后端已为单 AI 房间同步）；旧房间没有顶层镜像时退回座位副本
  const character = detail.character ?? seat?.override ?? null
  // L4-B：卡片写入读侧快照（世界书条目与角色白名单字段）。这里只下发副本的
  // 有效内容；根卡/公共世界只读，写入由后端在会话副本上 fork 后落盘。
  // 角色卡自带世界书的条目在 character.table，名称取自 CC 的 extensions.world，
  // 让卡片 getWorldbookNames() 能检出卡内书名（导入后的挂载世界书名可能带后缀）。
  const characterExtensions =
    character &&
    typeof (character as { extensions?: unknown }).extensions === 'object' &&
    (character as { extensions?: Record<string, unknown> }).extensions
      ? (character as { extensions?: Record<string, unknown> }).extensions ?? null
      : null
  const characterWorldName =
    characterExtensions && typeof characterExtensions.world === 'string'
      ? characterExtensions.world.trim()
      : ''
  // 酒馆每个聊天文件都有 chat_metadata.integrity；约会大作战小手机用它派生
  // IndexedDB 房间键。宿主会话没有该字段，用稳定的会话 id 补上，刷新后不变。
  const stAuthor =
    detail.st && typeof detail.st.author === 'object'
      ? (detail.st.author as { chat_metadata?: Record<string, unknown> | null })
      : null
  if (stAuthor?.chat_metadata && typeof stAuthor.chat_metadata === 'object') {
    if (
      typeof stAuthor.chat_metadata.integrity !== 'string' ||
      !stAuthor.chat_metadata.integrity
    ) {
      stAuthor.chat_metadata.integrity = detail.id
    }
  }
  const writeContext = detail.st
    ? {
        worldbook: detail.world
          ? { name: detail.world.name, entries: detail.world.table }
          : null,
        characterWorldName,
        character: pickCardCharacterFields(
          character as unknown as Record<string, unknown> | null
        ),
        chatMetadata:
          detail.st?.author && typeof detail.st.author === 'object'
            ? (detail.st.author as { chat_metadata?: Record<string, unknown> }).chat_metadata ?? null
            : null,
      }
    : undefined
  return {
    id: detail.id,
    title: detail.title,
    createdAt: toEpoch(detail.created_at),
    updatedAt: toEpoch(detail.updated_at),
    messages,
    threadId: thread?.id,
    threadKind: thread?.kind,
    readOnly: detail.mode === 'galgame',
    pinned: detail.pinned,
    tokenUsage: detail.token_usage,
    maxTokens: detail.max_tokens,
    disabledTools: detail.disabled_tools,
    characterId: character?.id || seat?.character_id || undefined,
    characterName: character?.name || seat?.name || undefined,
    userName: detail.user_name || undefined,
    characterAvatar: character?.avatar || undefined,
    worldId: detail.world?.id || detail.world_id || undefined,
    worldName: detail.world?.name || undefined,
    aiMemberId: seat?.id,
    scoped: Boolean(detail.scoped || seat?.override),
    st: detail.st
      ? { ...detail.st, ...(writeContext ? { write_context: writeContext } : {}) }
      : detail.st,
    members: detail.members,
    threads: detail.threads.map((item) => ({
      id: item.id,
      kind: item.kind,
      title: item.title,
      memberIds: item.member_ids,
    })),
  }
}

const applySession = (session: Session) => {
  const summary = toSessionSummary(session)
  set((state) => {
    const exists = state.sessions.some((item) => item.id === summary.id)
    return {
      sessions: sortSessions(
        exists
          ? state.sessions.map((item) => (item.id === summary.id ? { ...item, ...summary } : item))
          : [...state.sessions, summary]
      ),
      sessionCache: { ...state.sessionCache, [session.id]: session },
      sessionLoadErrors: { ...state.sessionLoadErrors, [session.id]: false },
    }
  })
  trimSessionCache()
  // 写穿缓存：下一次冷启动直接读这份快照
  persistSession(session.id)
}

/**
 * 回读后端权威消息（真实消息 id/标题/半截回复）。
 * 后端没有保存本轮（例如请求被拒）时保留本地气泡，避免错误提示凭空消失。
 */
const refreshSession = async (id: string, epoch: number, force = false) => {
  try {
    // 非强制刷新先走条件请求：会话没变时服务端回 304，整个大 JSON 不下载
    const detail = await getSessionConditional(id, force)
    if (!detail) {
      return
    }
    if (epoch !== historyEpoch || deletingSessions.has(id) || getStreamController(id)) {
      return
    }
    const remote = await mapSession(detail, activeThreadIds.get(id))
    if (epoch !== historyEpoch || deletingSessions.has(id) || getStreamController(id)) {
      return
    }
    const local = getSession(id)
    // force：服务端权威变更（撤回/回放终态），即使本地更长也以远端为准。
    // 窗口已经切换时本地消息属于另一个窗口，不能拿来覆盖远端
    const keepLocal =
      !force &&
      local !== undefined &&
      local.threadId === remote.threadId &&
      local.messages.length > remote.messages.length
    // 本地多出来的若是失败轮气泡，采用服务端 id 重建并保留提示；
    // 其余「本地更长」的情况（还没落盘的流式内容）仍以本地为准。
    const reconciled =
      keepLocal && local ? reconcileFailedReplies(local, remote) : null
    const merged: Session =
      reconciled ??
      (keepLocal && local
        ? {
            ...remote,
            messages: local.messages,
            threadId: remote.threadId ?? local.threadId,
            threadKind: remote.threadKind ?? local.threadKind,
          }
        : remote)
    touchSession(id)
    applySession(merged)
    if (!templateSessionInFlight.has(id)) {
      void processPendingTemplates(id)
    }
  } catch (error) {
    db.reportStorageError('refresh session')(error)
  }
}

/** 输出侧 EJS 模板：把当前会话里 pending 的助手消息补渲染并回传。
 *  失败不阻塞聊天；下次刷新会话会重试。 */
const templateSessionInFlight = new Set<string>()

async function processPendingTemplates(id: string): Promise<void> {
  if (templateSessionInFlight.has(id)) {
    return
  }
  const session = getSession(id)
  if (!session?.st?.template?.output_enabled || getStreamController(id)) {
    return
  }
  const pending = pendingAssistantMessages(session)
  if (pending.length === 0) {
    return
  }
  templateSessionInFlight.add(id)
  try {
    const global = await loadTemplateGlobalVars()
    if (!global.enabled || global.output_enabled === false) {
      return
    }
    const globalVars = global.vars || {}
    let committed = 0
    for (const message of pending) {
      try {
        const rendered = await renderPendingMessage(session, message, globalVars)
        await commitTemplateOutput(id, message.id, {
          content: rendered.content,
          ops: rendered.ops,
          errors: rendered.errors,
          raw_hash: rendered.rawHash,
          render_id: `${message.id}:${rendered.rawHash.slice(0, 16)}`,
        })
        committed += 1
      } catch (error) {
        db.reportStorageError('render output template')(error)
      }
    }
    if (committed > 0) {
      await refreshSession(id, historyEpoch, true)
    }
  } catch (error) {
    db.reportStorageError('render output template')(error)
  } finally {
    templateSessionInFlight.delete(id)
  }
}

/** 首轮 AI 标题任务在回答结束后才完成时补取：终态帧之后生成的事件不会再推给
 *  客户端（SSE 已收尾），收尾后短轮询会话详情把新标题写回侧栏。 */
const AUTO_TITLE_POLL_DELAY = 1500
const AUTO_TITLE_POLL_ATTEMPTS = 10

const pollAutoTitle = async (id: string, epoch: number) => {
  for (let attempt = 0; attempt < AUTO_TITLE_POLL_ATTEMPTS; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, AUTO_TITLE_POLL_DELAY))
    if (epoch !== historyEpoch || deletingSessions.has(id) || getStreamController(id)) {
      return
    }
    let title: string
    try {
      title = (await fetchSession(id)).title
    } catch {
      continue
    }
    if (!title || title === getSession(id)?.title) {
      continue
    }
    updateSession(id, (session) => (session.title === title ? session : { ...session, title }))
    return
  }
}

/** 后端时代不再有本地保存失败，保留空实现以兼容调用方 */
export const retrySaveSessions = () => {}

/** 撤回整轮：后端原位留墓碑，随后强制回读，让本地多出的回答气泡一并消失。 */
export const recallTurn = async (sessionId: string, messageId: string): Promise<boolean> => {
  if (
    get().importingHistory ||
    deletingSessions.has(sessionId) ||
    getStreamController(sessionId) ||
    !getSession(sessionId)
  ) {
    return false
  }
  const epoch = historyEpoch
  try {
    await recallRemoteMessage(sessionId, messageId)
    if (epoch !== historyEpoch) {
      return true
    }
    await refreshSession(sessionId, epoch, true)
    showToast({ key: 'recallTurnDone' })
    return true
  } catch (error) {
    if (epoch === historyEpoch) {
      db.reportStorageError('recall message')(error)
      showToast({ key: 'recallTurnFailed', type: 'error' })
    }
    return false
  }
}

/** 把撤回内容回填到输入框（文字 + 图片），不自动发送。 */
export const restoreRecalledDraft = (
  sessionId: string,
  recalled: { content: string; images: { mediaType: string; data: string }[]; name?: string }
) => {
  if (get().importingHistory) {
    return
  }
  clearDraft(sessionId)
  setDraftText(sessionId, recalled.content)
  const files = recalled.images.flatMap((image, index) => {
    const blob = base64ToBlob(image.data, image.mediaType)
    if (!blob) {
      return []
    }
    const ext = image.mediaType.split('/')[1] ?? 'png'
    const name =
      recalled.name && recalled.images.length === 1 ? recalled.name : `recalled-${index + 1}.${ext}`
    return [new File([blob], name, { type: image.mediaType })]
  })
  if (files.length > 0) {
    // 回填的图片同样走压缩流程；失败时提示但不影响文字回填
    void addDraftFiles(sessionId, files)
      .then((error) => {
        if (error) {
          showToast(error)
        }
      })
      .catch(() => showToast({ key: 'uploadFileStatusFailed' }))
  }
  // 输入框在下一帧才有最新草稿数据，focus 放在帧后。
  window.requestAnimationFrame(() => {
    const input = document.querySelector<HTMLTextAreaElement>('[data-chat-input]')
    if (input) {
      input.focus({ preventScroll: true })
      const end = input.value.length
      input.setSelectionRange(end, end)
    }
  })
}

/** 离开前先合并尚未绘制的增量；隐藏期间浏览器可能暂停动画帧。 */
export const bindChatPersistence = () => {
  const flush = () => {
    for (const task of streamFlushes.values()) {
      task()
    }
  }
  const onVisibilityChange = () => {
    if (document.visibilityState === 'hidden') {
      flush()
    }
  }
  const onPause = () => {
    appPaused = true
    flush()
  }
  const onResume = () => {
    appPaused = false
  }
  document.addEventListener('visibilitychange', onVisibilityChange)
  window.addEventListener('pagehide', flush)
  window.addEventListener('flyai:app-pause', onPause)
  window.addEventListener('flyai:app-resume', onResume)
  // 原生 APK：切后台时 WebView 会暂停动画帧，先合并增量并把重连挂起
  type AppPlugin = {
    addListener: (
      name: 'appStateChange',
      handler: (state: { isActive: boolean }) => void
    ) => { remove: () => Promise<void> | void } | Promise<{ remove: () => Promise<void> | void }>
  }
  const Capacitor = (window as unknown as { Capacitor?: { Plugins?: { App?: AppPlugin } } })
    .Capacitor
  const handle = Capacitor?.Plugins?.App?.addListener('appStateChange', ({ isActive }) => {
    window.dispatchEvent(new Event(isActive ? 'flyai:app-resume' : 'flyai:app-pause'))
  })
  return () => {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    window.removeEventListener('pagehide', flush)
    window.removeEventListener('flyai:app-pause', onPause)
    window.removeEventListener('flyai:app-resume', onResume)
    void Promise.resolve(handle)
      .then((listener) => listener?.remove())
      .catch(() => {})
  }
}

/** 按当前开关创建回复：深度思考对应 deep_thinking，智能搜索控制联网工具 */
const createReply = (): AssistantMessage => {
  const { thinking, search } = useSettings.getState()
  return {
    id: createId(),
    role: 'assistant',
    createdAt: Date.now(),
    model: MODEL,
    thinking,
    search,
    // 后端已支持续写：结束且未达限制的回复可把本消息作为前缀继续生成
    prefix: true,
    status: 'pending',
    blocks: [],
  }
}

export const initChat = (force = false): Promise<void> => {
  if (get().ready && !force) {
    return Promise.resolve()
  }
  if (initTask) {
    return initTask
  }
  const epoch = historyEpoch
  set({ initError: false })
  initTask = (async () => {
    try {
      const summaries = (await listSessions()).map(toSummary)
      // 换账号/缓存版本变化时先清空本地快照，避免把上一个账号的会话渲染出来
      await db.syncCacheOwner(localUserKey()).catch(db.reportStorageError('sync chat cache'))
      if (epoch === historyEpoch) {
        set({ ready: true, sessions: sortSessions(summaries), initError: false })
        // 服务端已不存在的会话与无引用原件从本地缓存清掉（列表成功后调用）
        const validIds = new Set(summaries.map((session) => session.id))
        void db
          .pruneCachedSessions(validIds)
          .catch(db.reportStorageError('prune chat cache'))
        // 回到上次打开的会话；刷新页面后由 selectSession 触发运行续读
        const last = readActiveSession()
        if (last && summaries.some((session) => session.id === last)) {
          selectSession(last)
        }
      }
    } catch (error) {
      if (epoch === historyEpoch) {
        set({ initError: true })
        if (!(error instanceof FlyApiError && error.status === 401)) {
          db.reportStorageError('load sessions')(error)
        }
      }
    } finally {
      if (epoch === historyEpoch) {
        initTask = undefined
      }
    }
  })()
  return initTask
}

/** 退出登录后清空内存态；下一次登录重新 `initChat`。 */
export const resetChat = () => {
  historyEpoch += 1
  initTask = undefined
  sessionLoads.clear()
  sessionAccess.clear()
  for (const id of [...persistTimers.keys()]) {
    cancelPersist(id)
  }
  for (const controller of Object.values(get().streaming)) {
    controller.abort()
  }
  runIds.clear()
  clearAllDrafts()
  set({
    ready: false,
    initError: false,
    importingHistory: false,
    sessions: [],
    sessionCache: {},
    sessionLoadErrors: {},
    sessionSaveErrors: {},
    activeId: null,
    streaming: {},
  })
}

/** 同一会话合并并发读取；切换只改变 activeId，旧请求不会把界面切回去。 */
const ensureSessionLoaded = (id: string): Promise<void> => {
  if (getSession(id)) {
    return Promise.resolve()
  }
  const pending = sessionLoads.get(id)
  if (pending) {
    return pending.promise
  }
  const epoch = historyEpoch
  set((state) => ({ sessionLoadErrors: { ...state.sessionLoadErrors, [id]: false } }))
  const load = { promise: Promise.resolve() }
  sessionLoads.set(id, load)
  load.promise = (async () => {
    try {
      // 暖启动：先画本地快照，再在后台用服务端复核（refreshSession 自带守卫）
      const cached = await db.loadSession(id).catch(() => undefined)
      if (epoch !== historyEpoch || sessionLoads.get(id) !== load) {
        return
      }
      if (cached) {
        touchSession(id)
        applySession(cached)
        // 回填窗口选择，避免后台刷新 pickThread 跳回私聊
        if (cached.threadId && !activeThreadIds.has(id)) {
          activeThreadIds.set(id, cached.threadId)
        }
        if (get().activeId === null) {
          selectSession(id)
        }
        void refreshSession(id, epoch, false)
        return
      }
      const detail = await fetchSession(id)
      if (epoch !== historyEpoch || sessionLoads.get(id) !== load) {
        return
      }
      const session = await mapSession(detail)
      if (epoch !== historyEpoch || sessionLoads.get(id) !== load) {
        return
      }
      touchSession(id)
      applySession(session)
    } catch (error) {
      // 已经画出缓存快照时网络失败保持现状，只有完全没有快照才报错
      if (epoch === historyEpoch && sessionLoads.get(id) === load && !getSession(id)) {
        set((state) => ({ sessionLoadErrors: { ...state.sessionLoadErrors, [id]: true } }))
        if (!(error instanceof FlyApiError && error.status === 401)) {
          db.reportStorageError('read session')(error)
        }
      }
    } finally {
      if (sessionLoads.get(id) === load) {
        sessionLoads.delete(id)
      }
    }
  })()
  return load.promise
}

/** 等所有回复的收尾完成 */
const waitForIdle = (sessionId?: string) =>
  new Promise<void>((resolve) => {
    const isIdle = (state: ChatState) =>
      sessionId === undefined
        ? Object.keys(state.streaming).length === 0
        : !Object.hasOwn(state.streaming, sessionId)
    if (isIdle(get())) {
      resolve()
      return
    }
    const unsubscribe = useChatStore.subscribe((state) => {
      if (isIdle(state)) {
        unsubscribe()
        resolve()
      }
    })
  })

/**
 * 兼容旧的数据管理入口：后端时代不再支持本地历史导入/导出，
 * `write` 仍写入暂存后立即丢弃，随后按服务端列表刷新界面。
 */
export const replaceHistory = async (write: () => Promise<void>): Promise<number> => {
  if (get().importingHistory) {
    throw new Error('History replacement is already in progress')
  }
  set({ importingHistory: true })
  try {
    for (const controller of Object.values(get().streaming)) {
      controller.abort()
    }
    await waitForIdle()
    await db.clearStaging()
    try {
      await write()
    } finally {
      await db.clearStaging().catch(db.reportStorageError('clear history staging'))
    }
    historyEpoch += 1
    initTask = undefined
    sessionLoads.clear()
    sessionAccess.clear()
    clearAllDrafts()
    for (const session of get().sessions) {
      clearRunMarker(session.id)
    }
    saveActiveSession(null)
    const summaries = (await listSessions()).map(toSummary)
    set({
      ready: true,
      initError: false,
      sessions: sortSessions(summaries),
      sessionCache: {},
      sessionLoadErrors: {},
      sessionSaveErrors: {},
      activeId: null,
    })
    return summaries.length
  } finally {
    set({ importingHistory: false })
  }
}

/** 删除服务端全部会话与本地图片原件 */
export const removeAllSessions = async (): Promise<boolean> => {
  if (get().importingHistory) {
    return false
  }
  set({ importingHistory: true })
  const ids = get().sessions.map((session) => session.id)
  try {
    for (const [id, controller] of Object.entries(get().streaming)) {
      const runId = runIds.get(id)
      if (runId) {
        await cancelRun(runId).catch(() => {})
      }
      controller.abort()
    }
    await waitForIdle()
    if (ids.length > 0) {
      await batchDeleteSessions(ids)
    }
    const fileIds = Object.values(get().sessionCache).flatMap((session) =>
      session.messages.flatMap((message) =>
        message.role === 'user' ? message.files.map((file) => file.id) : []
      )
    )
    for (const id of [...persistTimers.keys()]) {
      cancelPersist(id)
    }
    await Promise.all([
      db.deleteFiles([...new Set(fileIds)]),
      db.clearStaging(),
      db.pruneCachedSessions(new Set()),
    ]).catch(db.reportStorageError('delete local files'))
    clearAllDrafts()
    for (const id of ids) {
      clearRunMarker(id)
    }
    saveActiveSession(null)
    set({
      sessions: [],
      sessionCache: {},
      sessionLoadErrors: {},
      sessionSaveErrors: {},
      activeId: null,
    })
    return true
  } catch (error) {
    db.reportStorageError('delete all sessions')(error)
    return false
  } finally {
    set({ importingHistory: false })
  }
}

export const newChat = () => {
  if (!get().importingHistory) {
    set({ activeId: null })
    saveActiveSession(null)
    trimSessionCache()
  }
}

export const selectSession = (id: string) => {
  if (!get().importingHistory && get().sessions.some((session) => session.id === id)) {
    touchSession(id)
    set({ activeId: id })
    saveActiveSession(id)
    void ensureSessionLoaded(id).then(() => {
      if (get().activeId === id) {
        void maybeResumeActive(id)
      }
    })
    trimSessionCache()
  }
}

/**
 * 以角色卡开新对话：POST /api/rooms 建房间（自动落 first_mes 开场白）。
 * 返回会话 id；失败时抛错由调用方提示。
 */
export const startRoomChat = async (
  characterIds: string[],
  worldId?: string,
  greetingIndex?: number,
  openingEntry?: string
): Promise<string> => {
  if (get().importingHistory || characterIds.length === 0) {
    throw new Error('无法创建对话')
  }
  const detail = await createRoom({
    character_ids: characterIds,
    world_id: worldId || undefined,
    greeting_index: greetingIndex && greetingIndex > 0 ? greetingIndex : undefined,
    opening_entry: openingEntry?.trim() || undefined,
  })
  const session = await mapSession(detail)
  applySession(session)
  touchSession(session.id)
  set({ activeId: session.id })
  saveActiveSession(session.id)
  trimSessionCache()
  return session.id
}

export type SessionBindingPatch = {
  characterId?: string
  worldId?: string
  maxTokens?: number
  disabledTools?: string[]
}

/**
 * 会话设置：换角色要 PATCH 房间 AI 座位（对话/压缩读座位），
 * 世界观与限额走会话 PATCH。改完强制回读，角色名/头像与设置面板同步。
 */
export const updateSessionBinding = async (
  sessionId: string,
  patch: SessionBindingPatch
): Promise<void> => {
  if (get().importingHistory || getStreamController(sessionId)) {
    throw new Error('回答进行中，暂时无法修改会话设置')
  }
  let seatId = getSession(sessionId)?.aiMemberId
  if (patch.characterId !== undefined) {
    if (!seatId) {
      const detail = await fetchSession(sessionId)
      seatId = detail.members.find((member) => !member.is_user)?.id
    }
    if (!seatId) {
      throw new Error('该会话没有可切换的 AI 座位')
    }
    await updateMember(sessionId, seatId, { character_id: patch.characterId })
  }
  const body: SessionPatch = {}
  if (patch.worldId !== undefined) {
    body.world_id = patch.worldId
  }
  if (patch.maxTokens !== undefined) {
    body.max_tokens = patch.maxTokens
  }
  if (patch.disabledTools !== undefined) {
    body.disabled_tools = patch.disabledTools
  }
  if (Object.keys(body).length > 0) {
    await patchSession(sessionId, body)
  }
  await refreshSession(sessionId, historyEpoch, true)
}

/** 外部（会话副本编辑器）改动后回读权威内容 */
export const reloadSession = async (sessionId: string): Promise<void> => {
  await refreshSession(sessionId, historyEpoch, true)
}

/**
 * 切换房间窗口（群聊标签页 / 私聊）：房间详情本就带全部窗口消息，
 * 回读后按目标窗口重新映射即可，不必单独取窗口。
 */
export const switchThread = async (sessionId: string, threadId: string): Promise<boolean> => {
  if (get().importingHistory || getStreamController(sessionId)) {
    return false
  }
  const session = getSession(sessionId)
  if (!session) {
    return false
  }
  if (session.threadId === threadId) {
    return true
  }
  const epoch = historyEpoch
  try {
    const detail = await fetchSession(sessionId)
    if (
      epoch !== historyEpoch ||
      getStreamController(sessionId) ||
      !detail.threads.some((thread) => thread.id === threadId)
    ) {
      return false
    }
    const mapped = await mapSession(detail, threadId)
    applySession(mapped)
    touchSession(sessionId)
    return true
  } catch (error) {
    db.reportStorageError('switch thread')(error)
    return false
  }
}

/** 在房间内新建群聊窗口（成员必须是该房间的 AI 座位），建好后直接切过去 */
export const createGroupThread = async (
  sessionId: string,
  memberIds: string[],
  title?: string
): Promise<string | null> => {
  if (get().importingHistory || getStreamController(sessionId) || memberIds.length === 0) {
    return null
  }
  const thread = await createGroup(sessionId, memberIds, title || undefined)
  await switchThread(sessionId, thread.id)
  return thread.id
}

export const renameRoomThread = async (
  sessionId: string,
  threadId: string,
  title: string
): Promise<void> => {
  const trimmed = title.trim()
  if (!trimmed) {
    return
  }
  await renameRoomThreadRemote(sessionId, threadId, trimmed)
  updateSession(sessionId, (current) => ({
    ...current,
    threads: current.threads?.map((thread) =>
      thread.id === threadId ? { ...thread, title: trimmed } : thread
    ),
  }))
}

/** 删除窗口（含该窗口全部历史）；删除当前窗口时自动切到剩下的第一个 */
export const deleteRoomThread = async (
  sessionId: string,
  threadId: string
): Promise<void> => {
  const session = getSession(sessionId)
  if (!session || getStreamController(sessionId)) {
    return
  }
  await deleteRoomThreadRemote(sessionId, threadId)
  const remaining = (session.threads ?? []).filter((thread) => thread.id !== threadId)
  const next = remaining[0]
  if (session.threadId === threadId && next) {
    await switchThread(sessionId, next.id)
  } else {
    updateSession(sessionId, (current) => ({
      ...current,
      threads: current.threads?.filter((thread) => thread.id !== threadId),
    }))
    await refreshSession(sessionId, historyEpoch, true)
  }
}

export type RoomMemberPatch = {
  characterId?: string
  memory?: string
  attributes?: Record<string, unknown>
  providerId?: string | null
  memoryMode?: '' | 'rw' | 'ro' | null
  memoryCharId?: string | null
}

/** 房间座位管理：增删成员、换角色、改记忆/属性，改完强制回读 */
export const addRoomMember = async (sessionId: string, characterId: string): Promise<void> => {
  if (get().importingHistory || getStreamController(sessionId)) {
    throw new Error('回答进行中，暂时无法修改房间成员')
  }
  await addMember(sessionId, { character_id: characterId })
  await refreshSession(sessionId, historyEpoch, true)
}

export const updateRoomMember = async (
  sessionId: string,
  memberId: string,
  patch: RoomMemberPatch
): Promise<void> => {
  if (get().importingHistory || getStreamController(sessionId)) {
    throw new Error('回答进行中，暂时无法修改房间成员')
  }
  await updateMember(sessionId, memberId, {
    character_id: patch.characterId,
    memory: patch.memory,
    attributes: patch.attributes,
    provider_id: patch.providerId,
    memory_mode: patch.memoryMode,
    memory_char_id: patch.memoryCharId,
  })
  await refreshSession(sessionId, historyEpoch, true)
}

export const removeRoomMember = async (sessionId: string, memberId: string): Promise<void> => {
  if (get().importingHistory || getStreamController(sessionId)) {
    throw new Error('回答进行中，暂时无法修改房间成员')
  }
  await removeMember(sessionId, memberId)
  await refreshSession(sessionId, historyEpoch, true)
}

/** 把座位上的会话专属角色另存为内容库新卡（不改座位绑定） */
export const publishRoomMember = async (
  sessionId: string,
  memberId: string
): Promise<void> => {
  await publishMemberCharacter(sessionId, memberId)
}

/** 从文本里的 @名字 解析群聊点名成员；只认当前窗口里的 AI 座位 */
export const mentionTargets = (sessionId: string, text: string): string[] => {
  const session = getSession(sessionId)
  if (!session || session.threadKind !== 'group') {
    return []
  }
  const thread = session.threads?.find((item) => item.id === session.threadId)
  const candidates = (session.members ?? [])
    .filter(
      (member) =>
        !member.is_user &&
        Boolean(member.name) &&
        (!thread || thread.memberIds.includes(member.id))
    )
    .sort((a, b) => b.name.length - a.name.length)
  const ids: string[] = []
  for (const member of candidates) {
    if (text.includes(`@${member.name}`) && !ids.includes(member.id)) {
      ids.push(member.id)
    }
  }
  return ids
}

export const renameSession = (id: string, title: string) => {
  if (get().importingHistory || deletingSessions.has(id)) {
    return
  }
  const previous = get().sessions.find((session) => session.id === id)?.title
  const applyTitle = (value: string) =>
    set((state) => ({
      sessions: state.sessions.map((session) =>
        session.id === id ? { ...session, title: value } : session
      ),
      sessionCache: Object.hasOwn(state.sessionCache, id)
        ? { ...state.sessionCache, [id]: { ...state.sessionCache[id], title: value } }
        : state.sessionCache,
    }))
  applyTitle(title)
  void patchSession(id, { title }).catch((error: unknown) => {
    db.reportStorageError('rename session')(error)
    if (previous !== undefined) {
      applyTitle(previous)
    }
    showToast(error instanceof FlyApiError ? error.message : '重命名失败')
  })
}

/** 置顶 / 取消置顶：先改本地列表，失败回滚并提示 */
export const pinSession = (id: string, pinned: boolean) => {
  if (get().importingHistory || deletingSessions.has(id)) {
    return
  }
  const previous = get().sessions.find((session) => session.id === id)?.pinned
  const applyPinned = (value: boolean) =>
    set((state) => {
      const sessions = sortSessions(
        state.sessions.map((session) =>
          session.id === id ? { ...session, pinned: value } : session
        )
      )
      return {
        sessions,
        sessionCache: Object.hasOwn(state.sessionCache, id)
          ? { ...state.sessionCache, [id]: { ...state.sessionCache[id], pinned: value } }
          : state.sessionCache,
      }
    })
  applyPinned(pinned)
  void patchSession(id, { pinned }).catch((error: unknown) => {
    db.reportStorageError('pin session')(error)
    if (previous !== undefined) {
      applyPinned(Boolean(previous))
    }
    showToast(error instanceof FlyApiError ? error.message : translate('sessionPinFailed'))
  })
}

/** 会话全文搜索：后端按标题/正文匹配，返回片段与命中次数 */
export type SessionSearchResult = {
  session: SessionSummary
  snippet: string
  matchCount: number
}

export const searchSessionList = async (query: string): Promise<SessionSearchResult[]> => {
  const hits = await searchSessions(query)
  return hits.map((hit) => ({
    session: toSummary(hit),
    snippet: hit.snippet ?? '',
    matchCount: hit.match_count ?? 0,
  }))
}

/** 重新拉取服务端会话列表（导入/外部变更后） */
export const refreshSessionList = async (): Promise<void> => {
  const summaries = (await listSessions()).map(toSummary)
  void db
    .pruneCachedSessions(new Set(summaries.map((session) => session.id)))
    .catch(db.reportStorageError('prune chat cache'))
  set((state) => ({
    ready: true,
    initError: false,
    sessions: sortSessions(summaries),
    activeId:
      state.activeId && summaries.some((session) => session.id === state.activeId)
        ? state.activeId
        : null,
  }))
}

/** 批量删除会话；先取消进行中的运行，再一次性通知后端。返回实际删除条数。 */
export const removeSessions = async (ids: string[]): Promise<number> => {
  if (get().importingHistory) {
    return 0
  }
  const targets = [...new Set(ids)].filter(
    (id) => !deletingSessions.has(id) && get().sessions.some((session) => session.id === id)
  )
  if (targets.length === 0) {
    return 0
  }
  targets.forEach((id) => deletingSessions.add(id))
  const epoch = historyEpoch
  try {
    for (const id of targets) {
      const controller = getStreamController(id)
      if (controller) {
        const runId = runIds.get(id)
        if (runId) {
          await cancelRun(runId).catch(() => {})
        }
        controller.abort()
      }
    }
    await Promise.all(targets.map((id) => waitForIdle(id)))
    if (epoch !== historyEpoch) {
      return 0
    }
    const deleted = await batchDeleteSessions(targets)
    if (epoch !== historyEpoch) {
      return deleted
    }
    const fileIds = targets.flatMap((id) => {
      const session = getSession(id)
      return (
        session?.messages.flatMap((message) =>
          message.role === 'user' ? message.files.map((file) => file.id) : []
        ) ?? []
      )
    })
    await db
      .deleteFiles([...new Set(fileIds)])
      .catch(db.reportStorageError('delete local files'))
    await Promise.all(
      targets.map((id) => {
        cancelPersist(id)
        return db.deleteSession(id).catch(db.reportStorageError('delete cached session'))
      })
    )
    for (const id of targets) {
      sessionLoads.delete(id)
      sessionAccess.delete(id)
      runIds.delete(id)
      clearRunMarker(id)
      clearDraft(id)
    }
    set((state) => ({
      sessions: state.sessions.filter((session) => !targets.includes(session.id)),
      sessionCache: Object.fromEntries(
        Object.entries(state.sessionCache).filter(([key]) => !targets.includes(key))
      ),
      sessionLoadErrors: Object.fromEntries(
        Object.entries(state.sessionLoadErrors).filter(([key]) => !targets.includes(key))
      ),
      activeId:
        state.activeId && targets.includes(state.activeId) ? null : state.activeId,
    }))
    if (get().activeId === null) {
      saveActiveSession(null)
    }
    return deleted
  } catch (error) {
    db.reportStorageError('delete sessions')(error)
    showToast(error instanceof FlyApiError ? error.message : translate('sessionDeleteFailed'))
    return 0
  } finally {
    targets.forEach((id) => deletingSessions.delete(id))
  }
}

/** 导入会话文件（flyai JSON / ZIP / Chatbox）；成功后刷新列表。返回导入条数。 */
export const importSessions = async (file: File): Promise<number> => {
  if (get().importingHistory) {
    throw new Error('Import is already in progress')
  }
  set({ importingHistory: true })
  try {
    const count = await importSessionFile(file)
    await refreshSessionList()
    return count
  } finally {
    set({ importingHistory: false })
  }
}

/** 导入前端已转换好的对话载荷（如 DeepSeek 导出）；逐个提交后刷新列表。 */
export const importConversationPayloads = async (
  payloads: unknown[],
  onProgress?: (done: number, total: number) => void
): Promise<number> => {
  if (get().importingHistory) {
    throw new Error('Import is already in progress')
  }
  if (payloads.length === 0) {
    return 0
  }
  set({ importingHistory: true })
  let done = 0
  try {
    for (const payload of payloads) {
      await importFlyaiConversation(payload)
      done += 1
      onProgress?.(done, payloads.length)
    }
    await refreshSessionList()
    return done
  } finally {
    set({ importingHistory: false })
  }
}

/** 删除会话；本地图片原件在服务端删除后一并清理。 */
export const removeSession = (id: string): Promise<boolean> => {
  if (get().importingHistory || deletingSessions.has(id)) {
    return Promise.resolve(false)
  }
  deletingSessions.add(id)
  return removeSessionData(id).finally(() => deletingSessions.delete(id))
}

const removeSessionData = async (id: string): Promise<boolean> => {
  const epoch = historyEpoch
  try {
    const controller = getStreamController(id)
    if (controller) {
      const runId = runIds.get(id)
      if (runId) {
        await cancelRun(runId).catch(() => {})
      }
      controller.abort()
      // 等本会话的流收尾，避免删除之后又被回读写回来。
      await waitForIdle(id)
    }
    if (epoch !== historyEpoch) {
      return false
    }
    await deleteRemoteSession(id)
    cancelPersist(id)
    if (epoch !== historyEpoch) {
      return true
    }
    const session = getSession(id)
    const fileIds =
      session?.messages.flatMap((message) =>
        message.role === 'user' ? message.files.map((file) => file.id) : []
      ) ?? []
    await db.deleteFiles([...new Set(fileIds)]).catch(db.reportStorageError('delete local files'))
    await db
      .deleteSession(id)
      .catch(db.reportStorageError('delete cached session'))
    sessionLoads.delete(id)
    sessionAccess.delete(id)
    runIds.delete(id)
    clearRunMarker(id)
    clearDraft(id)
    set((state) => ({
      sessions: state.sessions.filter((session) => session.id !== id),
      sessionCache: Object.fromEntries(
        Object.entries(state.sessionCache).filter(([key]) => key !== id)
      ),
      sessionLoadErrors: Object.fromEntries(
        Object.entries(state.sessionLoadErrors).filter(([key]) => key !== id)
      ),
      activeId: state.activeId === id ? null : state.activeId,
    }))
    if (get().activeId === null) {
      saveActiveSession(null)
    }
    return true
  } catch (error) {
    db.reportStorageError('delete session')(error)
    showToast(error instanceof FlyApiError ? error.message : '删除对话失败')
    return false
  }
}

export const stopReply = (sessionId: string) => {
  const controller = getStreamController(sessionId)
  if (!controller) {
    return
  }
  const runId = runIds.get(sessionId)
  if (runId) {
    void cancelRun(runId).catch(() => {})
  }
  controller.abort()
}

const blobToBase64 = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error ?? new Error('read image failed'))
    reader.readAsDataURL(blob)
  })

const toFlyUsage = (usage: FlyUsage | undefined): MessageUsage | undefined =>
  usage
    ? {
        input_tokens: usage.prompt_tokens ?? 0,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 0,
        output_tokens: usage.completion_tokens ?? 0,
      }
    : undefined

const appendThinking = (reply: AssistantMessage, text: string, now: number): AssistantMessage => {
  const blocks = [...reply.blocks]
  const last = blocks[blocks.length - 1]
  // 已收尾的思考块不再追加（同一回复里思考可能被正文/工具隔成多段）
  if (last?.type === 'thinking' && last.endedAt === undefined) {
    blocks[blocks.length - 1] = { ...last, thinking: last.thinking + text }
  } else {
    blocks.push({ type: 'thinking', thinking: text, startedAt: now })
  }
  return { ...reply, status: 'streaming', blocks }
}

/**
 * FlyAI 协议只有 thinking_delta 增量帧，没有思考结束帧（对照 DeepSeek 协议的
 * content_block_stop）；任何非思考内容到达即视为本段思考结束，记下用时。
 * 工具调用后的下一轮思考会另起一个思考块（见 appendThinking）。
 */
const closeThinking = (reply: AssistantMessage, now: number): AssistantMessage => {
  const blocks = [...reply.blocks]
  const last = blocks[blocks.length - 1]
  if (last?.type !== 'thinking' || last.startedAt === undefined || last.endedAt !== undefined) {
    return reply
  }
  blocks[blocks.length - 1] = { ...last, endedAt: now }
  return { ...reply, blocks }
}

const appendText = (reply: AssistantMessage, text: string): AssistantMessage => {
  const blocks = [...reply.blocks]
  const last = blocks[blocks.length - 1]
  if (last?.type === 'text') {
    blocks[blocks.length - 1] = { ...last, text: last.text + text }
  } else {
    blocks.push({ type: 'text', text })
  }
  return { ...reply, status: 'streaming', blocks }
}

const replaceText = (reply: AssistantMessage, text: string): AssistantMessage => {
  const blocks = [...reply.blocks]
  let index = -1
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i].type === 'text') {
      index = i
      break
    }
  }
  if (index >= 0) {
    blocks[index] = { type: 'text', text }
  } else {
    blocks.push({ type: 'text', text })
  }
  return { ...reply, status: 'streaming', blocks }
}

const findToolIndex = (blocks: ReplyBlock[], id: string) =>
  blocks.findIndex((block) => block.type === 'tool' && block.id === id)

/** tool_calls：登记本轮全部调用（多个调用串行执行，结果逐个到达） */
const applyToolCalls = (
  reply: AssistantMessage,
  calls: { id: string; name: string }[]
): AssistantMessage => {
  if (calls.length === 0) {
    return reply
  }
  const blocks = [...reply.blocks]
  for (const call of calls) {
    const index = findToolIndex(blocks, call.id)
    const part: ToolPart = { type: 'tool', id: call.id, name: call.name, status: 'wip' }
    if (index >= 0) {
      blocks[index] = { ...(blocks[index] as ToolPart), ...part }
    } else {
      blocks.push(part)
    }
  }
  return { ...reply, status: 'streaming', blocks }
}

/** tool_start：补上后端渲染的进行中文案 */
const applyToolStart = (
  reply: AssistantMessage,
  id: string,
  name: string | undefined,
  statusText: string | undefined
): AssistantMessage => {
  if (!id) {
    return reply
  }
  const blocks = [...reply.blocks]
  const index = findToolIndex(blocks, id)
  if (index >= 0) {
    const existing = blocks[index] as ToolPart
    blocks[index] = {
      ...existing,
      name: name || existing.name,
      statusText: statusText ?? existing.statusText,
      status: 'wip',
    }
  } else {
    blocks.push({ type: 'tool', id, name: name ?? '', statusText, status: 'wip' })
  }
  return { ...reply, status: 'streaming', blocks }
}

/** tool_end：记录成功/失败与结果摘录 */
const applyToolEnd = (
  reply: AssistantMessage,
  update: { id: string; name?: string; ok: boolean; preview?: string; output?: string }
): AssistantMessage => {
  if (!update.id) {
    return reply
  }
  const status = update.ok ? 'done' : 'failed'
  const preview = update.preview ?? update.output
  const blocks = [...reply.blocks]
  const index = findToolIndex(blocks, update.id)
  if (index >= 0) {
    const existing = blocks[index] as ToolPart
    blocks[index] = {
      ...existing,
      name: update.name || existing.name,
      status,
      preview: preview ?? existing.preview,
    }
  } else {
    blocks.push({ type: 'tool', id: update.id, name: update.name ?? '', status, preview })
  }
  return { ...reply, status: 'streaming', blocks }
}

type StreamRequest = {
  text: string
  images: { mediaType: string; blob: Blob }[]
  editMessageId?: string
  regenerateMessageId?: string
  /** 续写：后端把目标回复作为 prefill，增量并回同一条消息 */
  continueMessageId?: string
  /** 酒馆 context.generate：quiet_prompt 只进本轮提示词，不落用户消息 */
  quietPrompt?: string
  /** 发起方标记（卡片动作） */
  origin?: 'card'
  /** 群聊点名：被 @ 的座位排到本轮发言顺序最前 */
  targetMemberIds?: string[]
  /** 群聊剩余发言顺序随机 */
  shuffleSpeakers?: boolean
  /** 断线续读：不发新请求，直接回放该运行号之后的事件 */
  resume?: { runId: string; after: number }
  /** 本次新用户消息的本地 id：随请求提交为 client_message_id，失败后仍可撤回/重发 */
  questionId?: string
  /**
   * 首发请求在服务端落用户消息之前就失败（余额不足/参数错误/连不上）时的回滚：
   * 调用方撤掉本地乐观气泡，把内容放回输入框。仅在没收到 message_start 时触发。
   */
  onUnsent?: () => void
}

const errorKind = (error: unknown): 'http' | 'network' | 'stream' => {
  if (error instanceof FlyApiError) {
    return 'http'
  }
  if (error instanceof TypeError) {
    return 'network'
  }
  return 'stream'
}

const streamReply = async (
  sessionId: string,
  initialReply: AssistantMessage,
  request: StreamRequest
) => {
  const controller = new AbortController()
  const epoch = historyEpoch
  let reply = initialReply
  /** 群聊里是否已收到过本轮的发言者标记；第二位发言者起按新消息切分 */
  let speakerSeen = false
  let frame = 0
  /** 后台运行号：来自 POST 响应头或续读地址 */
  let runId = request.resume?.runId ?? null
  /** 已消费到的最后一个事件序号，续读时的 after 锚点 */
  let seq = request.resume?.after ?? 0
  let markerTimer = 0
  /** 收到终态帧，本轮正常结束 */
  let terminal = false
  /** 终态时首轮 AI 标题仍在后台生成（done.title_pending），收尾后需轮询补取 */
  let titlePending = false
  /** 运行已不在后台（服务重启/运行过期），本地已渲染内容即最终结果 */
  let authoritative = false
  /** 重连次数耗尽：保留已渲染内容并提示手动刷新 */
  let reconnectFailed = false
  let fatal: string | null = null
  let failure: AssistantMessage['error'] | null = null
  /** 是否已收到 message_start：drive 在此之后才跑，可据此确认用户消息已落盘 */
  let persisted = false

  set((state) => ({ streaming: { ...state.streaming, [sessionId]: controller } }))
  const flush = () => {
    cancelAnimationFrame(frame)
    frame = 0
    updateReply(sessionId, reply)
  }
  streamFlushes.set(sessionId, flush)
  const finish = (status: 'done' | 'stopped' | 'error', error?: AssistantMessage['error']) => {
    reply = settleReply(reply, status, Date.now(), error)
  }
  const alive = () => epoch === historyEpoch && !controller.signal.aborted

  const writeMarker = () => {
    if (runId && alive() && !terminal) {
      writeRunMarker({ runId, sessionId, seq, ts: Date.now() })
    }
  }
  /** 序号推进节流落盘；刷新页面后据此探活续读 */
  const scheduleMarker = () => {
    if (markerTimer) {
      return
    }
    markerTimer = window.setTimeout(() => {
      markerTimer = 0
      writeMarker()
    }, MARKER_SAVE_THROTTLE)
  }
  /**
   * 后台兜底提醒（仅 APK）：流式连接在后台常被系统回收，由原生线程轮询
   * run 状态，正常结束时发本地通知；Web 端为 no-op。
   */
  const armRunWatch = () => {
    if (!runId) {
      return
    }
    const current = getSession(sessionId)
    setRunWatch({
      runId,
      title: current?.characterName || current?.title || 'FlyAI',
      body: translate('nativeReplyDone'),
      token: useAuth.getState().token ?? '',
      iconUrl: current?.characterAvatar ? assetUrl(current.characterAvatar) : undefined,
    })
  }

  const toFailure = (error: unknown): AssistantMessage['error'] => ({
    kind: errorKind(error),
    message: error instanceof Error ? error.message : String(error),
    status: error instanceof FlyApiError ? error.status : undefined,
  })

  /**
   * 服务端已落盘本轮用户消息：把本地乐观 id 换成服务端 id。
   * 供应商报错时后端只保存用户消息、不保存失败的回复，若不同步 id，
   * 撤回/编辑/重新生成会带着本地随机 id 请求，404 后变成脏数据。
   */
  const adoptUserMessageId = (userMessageId: string) => {
    const session = getSession(sessionId)
    if (!session) {
      return
    }
    const replyIndex = session.messages.findIndex((message) => message.id === reply.id)
    if (replyIndex < 0) {
      return
    }
    for (let index = replyIndex - 1; index >= 0; index -= 1) {
      const candidate = session.messages[index]
      if (candidate.role !== 'user') {
        continue
      }
      if (candidate.id !== userMessageId) {
        updateSession(sessionId, (current) => rekeyMessage(current, candidate.id, userMessageId))
      }
      return
    }
  }

  const applyEvent = (event: ChatStreamEvent) => {
    if (reply.status === 'pending') {
      reply = { ...reply, status: 'streaming' }
    }
    // 正文或工具调用开始即思考结束；各事件分支不再重复处理。
    if (
      event.type === 'content_delta' ||
      event.type === 'content_replace' ||
      event.type === 'tool_calls' ||
      event.type === 'tool_start' ||
      event.type === 'tool_end'
    ) {
      reply = closeThinking(reply, Date.now())
    }
    switch (event.type) {
      case 'message_start':
        persisted = true
        if (event.userMessageId) {
          adoptUserMessageId(event.userMessageId)
        }
        if (event.threadId && event.threadId !== getSession(sessionId)?.threadId) {
          updateSession(sessionId, (current) => ({ ...current, threadId: event.threadId }))
        }
        break
      case 'template_prepare':
        // 提示词侧模板：沙箱执行后回传，服务端在模型调用前等待。
        // 不阻塞 SSE 消费；本地也校验一次功能开关，避免误执行。
        if (getSession(sessionId)?.st?.template?.prompt_enabled === true) {
          void handleTemplatePrepare(event, runId)
        }
        break
      case 'session_title':
        // 首轮 AI 自动标题（后台生成，可能先于 done 到达）；同步更新侧栏摘要
        if (event.title) {
          updateSession(sessionId, (current) =>
            current.title === event.title ? current : { ...current, title: event.title }
          )
        }
        break
      case 'speaker': {
        // 群聊窗口内多位 AI 依次发言：换人时收尾上一条回复并另起一条。
        // 私聊（dm）只有一个座位，沿用同一条回复即可。
        if (!event.name || getSession(sessionId)?.threadKind !== 'group') {
          break
        }
        if (!speakerSeen) {
          speakerSeen = true
          reply = { ...reply, speakerName: event.name, speakerId: event.memberId }
          break
        }
        flush()
        reply = settleReply(reply, 'done', Date.now())
        updateReply(sessionId, reply)
        reply = { ...createReply(), speakerName: event.name, speakerId: event.memberId }
        appendReply(sessionId, reply)
        break
      }
      case 'recall_notice': {
        // 自动回忆在本轮发言前注入；同一座位多次通知时合并展示
        const items = toRecallItems(event.items)
        if (items.length > 0) {
          reply = { ...reply, recall: [...(reply.recall ?? []), ...items] }
        }
        break
      }
      case 'context_warning':
        useContextWarning.getState().show({
          chars: event.chars,
          threshold: event.threshold,
        })
        break
      case 'thinking_delta':
        reply = appendThinking(reply, event.text, Date.now())
        break
      case 'content_delta':
        reply = appendText(reply, event.text)
        break
      case 'content_replace':
        reply = replaceText(reply, event.text)
        break
      case 'tool_calls':
        reply = applyToolCalls(reply, event.calls)
        break
      case 'tool_start':
        reply = applyToolStart(reply, event.id, event.name, event.statusText)
        break
      case 'tool_end':
        reply = applyToolEnd(reply, event)
        break
      case 'error':
        if (event.fatal) {
          fatal = event.message
          terminal = true
        }
        break
      case 'done':
        reply = { ...reply, usage: toFlyUsage(event.usage) ?? reply.usage }
        if (event.terminal) {
          terminal = true
        }
        titlePending = titlePending || event.titlePending === true
        break
      default:
        break
    }
  }

  /** 消费一条 SSE 响应直到终态；中途断流则正常返回，由调用方决定续读 */
  const consume = async (response: Response) => {
    for await (const chatFrame of readChatFrames(response, controller.signal)) {
      if (!alive()) {
        return
      }
      if (chatFrame.seq > 0) {
        seq = chatFrame.seq
        scheduleMarker()
      }
      applyEvent(chatFrame.event)
      if (appHidden()) {
        flush()
      } else if (frame === 0) {
        frame = requestAnimationFrame(flush)
      }
      if (terminal) {
        return
      }
    }
  }

  /** 探活：running 继续续读；gone 说明运行已被回收（服务重启/过期） */
  const probe = async (): Promise<'running' | 'gone' | 'ended' | 'unknown'> => {
    if (!runId) {
      return 'ended'
    }
    try {
      return (await probeRun(runId)).status === 'running' ? 'running' : 'ended'
    } catch (error) {
      if (error instanceof FlyApiError) {
        return error.status === 404 ? 'gone' : 'unknown'
      }
      return 'unknown'
    }
  }

  /** 断线后的自动重连：指数退避 + 探活，后台期间挂起不消耗次数 */
  const follow = async () => {
    if (!runId) {
      return
    }
    let attempts = 0
    while (alive() && !terminal) {
      if (appHidden()) {
        try {
          await waitForForeground(controller.signal)
        } catch {
          return
        }
        continue
      }
      attempts += 1
      if (attempts > MAX_RECONNECT_ATTEMPTS) {
        reconnectFailed = true
        showToast(translate('reconnectFailed'))
        return
      }
      const delay = backoffDelay(attempts)
      showToast(translate('reconnectWaiting', { seconds: Math.max(1, Math.round(delay / 1000)) }))
      try {
        await sleep(delay, controller.signal)
      } catch {
        return
      }
      if (!alive() || terminal) {
        return
      }
      const state = await probe()
      if (!alive() || terminal) {
        return
      }
      if (state === 'unknown') {
        continue
      }
      if (state === 'gone') {
        authoritative = true
        showToast(translate('runExpired'))
        return
      }
      const endedBefore = state === 'ended'
      try {
        const response = await resumeChatStream(runId, seq, controller.signal)
        // 连上即重新计数，避免偶发断流累计耗尽总次数
        attempts = 0
        await consume(response)
      } catch (error) {
        if (!alive() || terminal) {
          return
        }
        if (error instanceof FlyApiError && error.status === 404) {
          authoritative = true
          showToast(translate('runExpired'))
          return
        }
        continue
      }
      if (!terminal && endedBefore) {
        // 运行已结束且回放没有终态帧：本地内容即最终结果
        authoritative = true
        return
      }
    }
  }

  /** 首发建立流；409 表示别处正在生成，发现运行号后从头回放 */
  const open = async (): Promise<Response | null> => {
    if (request.resume) {
      try {
        return await resumeChatStream(
          request.resume.runId,
          request.resume.after,
          controller.signal
        )
      } catch (error) {
        if (error instanceof FlyApiError && error.status === 404) {
          authoritative = true
          showToast(translate('runExpired'))
          return null
        }
        throw error
      }
    }
    const images: ChatImagePayload[] = []
    for (const image of request.images) {
      images.push({ media_type: image.mediaType, data: await blobToBase64(image.blob) })
    }
    const { thinking, search, activeProviderId } = useSettings.getState()
    const payload: ChatSendPayload = {
      message: request.text,
      session_id: sessionId,
      thread_id: getSession(sessionId)?.threadId,
      provider_id: activeProviderId || undefined,
      tools_enabled: true,
      images,
      deep_thinking: thinking,
      target_member_ids: request.targetMemberIds ?? [],
      shuffle_speakers: request.shuffleSpeakers ?? false,
      disabled_tools: search ? [] : ['web_search', 'web_fetch'],
      edit_message_id: request.editMessageId,
      regenerate_message_id: request.regenerateMessageId,
      continue_message_id: request.continueMessageId,
      quiet_prompt: request.quietPrompt,
      origin: request.origin,
      client_message_id: request.questionId,
    }
    try {
      const opened = await openChatStream(payload, controller.signal)
      runId = opened.runId ?? runId
      return opened.response
    } catch (error) {
      if (error instanceof FlyApiError && error.status === 409) {
        const active = await discoverActiveRun(sessionId).catch(() => null)
        if (active) {
          runId = active
          // 后端只在运行结束才落盘本轮回答，从 0 回放才能拼出整轮内容
          authoritative = true
          return await resumeChatStream(active, 0, controller.signal)
        }
        // 409 与查询之间上一轮恰好结束：后端在发完终态帧后还要做收尾才释放
        // 单飞锁。稍等再试一次，别把这个瞬态冲突当成本轮发送失败。
        await new Promise((resolve) => window.setTimeout(resolve, 400))
        if (!controller.signal.aborted && alive()) {
          try {
            const retried = await openChatStream(payload, controller.signal)
            runId = retried.runId ?? runId
            return retried.response
          } catch (retryError) {
            if (!(retryError instanceof FlyApiError && retryError.status === 409)) {
              throw retryError
            }
          }
        }
      }
      throw error
    }
  }

  try {
    const response = await open()
    if (response) {
      if (runId) {
        runIds.set(sessionId, runId)
        writeMarker()
        armRunWatch()
      }
      const contentType = response.headers.get('Content-Type') ?? ''
      if (contentType.indexOf('application/json') === 0) {
        // 长跑队列：消息已进入排队（未落盘），等本轮结束由后端接着跑
        const data = (await response.json().catch(() => null)) as { pending?: unknown } | null
        const pending = typeof data?.pending === 'number' ? data.pending : 0
        showToast(translate('longRunQueued', { count: pending }))
        terminal = true
        authoritative = true
      } else {
        await consume(response)
        if (!terminal && alive()) {
          await follow()
        }
      }
    }
  } catch (error) {
    if (controller.signal.aborted || epoch !== historyEpoch) {
      // 主动停止或会话已切走/删除，由 settle 统一收尾
    } else if (!runId && !request.resume && errorKind(error) === 'network') {
      // 首发传输失败：服务端可能已经建好运行，发现后从头续读
      const active = await discoverActiveRun(sessionId).catch(() => null)
      if (active && alive()) {
        runId = active
        runIds.set(sessionId, runId)
        writeMarker()
        armRunWatch()
        authoritative = true
        try {
          await consume(await resumeChatStream(active, 0, controller.signal))
          if (!terminal && alive()) {
            await follow()
          }
        } catch (inner) {
          if (inner instanceof FlyApiError && inner.status === 404) {
            authoritative = true
            showToast(translate('runExpired'))
          } else if (!controller.signal.aborted && epoch === historyEpoch) {
            failure = toFailure(inner)
          }
        }
      } else {
        failure = toFailure(error)
      }
    } else {
      failure = toFailure(error)
    }
  } finally {
    streamFlushes.delete(sessionId)
    cancelAnimationFrame(frame)
    if (markerTimer) {
      window.clearTimeout(markerTimer)
      markerTimer = 0
    }
    if (fatal) {
      finish('error', { kind: 'stream', message: fatal })
    } else if (controller.signal.aborted) {
      finish('stopped')
    } else if (reconnectFailed) {
      finish('error', { kind: 'network', message: translate('reconnectFailed'), retryable: true })
    } else if (failure) {
      finish('error', failure)
    } else {
      finish('done')
    }
    // 请求在服务端落用户消息之前就失败（没收到 message_start，也没有运行号）：
    // 本轮什么都没保存，撤掉本地乐观气泡并把内容放回输入框，避免留下服务端
    // 不认识的脏消息（撤回/重新生成都会 404）。
    if (
      reply.status === 'error' &&
      !persisted &&
      !runId &&
      !controller.signal.aborted &&
      epoch === historyEpoch &&
      request.onUnsent !== undefined
    ) {
      request.onUnsent?.()
      const notice = describeReply(reply)
      showToast(notice?.text ?? translate('unknownError'))
    }
    // 仍有未收尾的运行（重连失败/网络中断）时保留标记，下次进入可再续
    const keepMarker = !terminal && !authoritative && !controller.signal.aborted && !!runId
    if (epoch === historyEpoch) {
      updateReply(sessionId, reply)
      if (keepMarker) {
        writeMarker()
      } else {
        clearRunMarker(sessionId)
      }
    }
    set((state) => ({
      streaming: Object.fromEntries(
        Object.entries(state.streaming).filter(([id]) => id !== sessionId)
      ),
    }))
    runIds.delete(sessionId)
    // 本轮已收尾：取消后台轮询（失败/中断时本轮也不会再通知）
    setRunWatch(null, runId ?? undefined)
    if (epoch === historyEpoch) {
      if (reply.status === 'stopped') {
        window.setTimeout(() => {
          if (epoch === historyEpoch) {
            void refreshSession(sessionId, epoch, authoritative)
          }
        }, STOP_REFRESH_DELAY)
      } else {
        void refreshSession(sessionId, epoch, authoritative)
      }
      if (terminal && titlePending) {
        void pollAutoTitle(sessionId, epoch)
      }
    }
  }
}

/** 会话尾部接续一条本地回复，并从后台运行续读事件 */
const resumeRun = (sessionId: string, runId: string, after: number): boolean => {
  const session = getSession(sessionId)
  if (!session || session.readOnly || getStreamController(sessionId)) {
    return false
  }
  const reply = createReply()
  updateSession(sessionId, (current) => {
    const path = getVisibleMessages(current)
    return appendBranch(current, path.length > 0 ? path[path.length - 1].id : null, [reply])
  })
  void streamReply(sessionId, reply, {
    text: '',
    images: [],
    resume: { runId, after },
  })
  return true
}

/**
 * 回到会话时续上仍在后台进行的回答：断点标记优先，其次主动发现。
 * 后端只在运行结束才落盘助手消息，因此一律从 0 回放整轮。
 */
const maybeResumeActive = async (sessionId: string): Promise<void> => {
  const epoch = historyEpoch
  const alive = () =>
    epoch === historyEpoch &&
    get().activeId === sessionId &&
    !get().importingHistory &&
    !deletingSessions.has(sessionId) &&
    !getStreamController(sessionId)
  if (!alive()) {
    return
  }
  const marker = readRunMarker(sessionId)
  if (marker) {
    let status: string
    try {
      status = (await probeRun(marker.runId)).status
    } catch (error) {
      if (error instanceof FlyApiError && error.status === 404) {
        clearRunMarker(sessionId)
        void refreshSession(sessionId, epoch, true)
      }
      // 网络错误：保留标记，等下次进入再探
      return
    }
    if (!alive()) {
      return
    }
    if (status === 'running') {
      resumeRun(sessionId, marker.runId, 0)
      return
    }
    clearRunMarker(sessionId)
    void refreshSession(sessionId, epoch, true)
    return
  }
  const active = await discoverActiveRun(sessionId).catch(() => null)
  if (!active || !alive()) {
    return
  }
  resumeRun(sessionId, active, 0)
}

/**
 * 提交指定会话的最新草稿；null 表示新建会话。
 * 新会话先在后端建壳再插入列表，返回 false 表示没有发出请求（草稿保留）。
 */
export const sendMessage = async (
  targetSessionId: string | null,
  options: { targetMemberIds?: string[]; shuffleSpeakers?: boolean } = {}
): Promise<boolean> => {
  const state = get()
  if (state.importingHistory || !state.ready) {
    return false
  }
  // 同步占位：下面从建会话/存图到 streamReply 注册 controller 还有多个 await，
  // 只查 getStreamController 挡不住连点或回车+点击的重复提交
  const guardKey = targetSessionId ?? SEND_GUARD_NEW
  if (sendsInFlight.has(guardKey) || getStreamController(guardKey)) {
    return false
  }
  const prepared = prepareDraftMessage(getDraft(targetSessionId), useUploads.getState().tasks)
  if (!prepared) {
    return false
  }
  sendsInFlight.add(guardKey)
  try {
    const { text, files } = prepared
    let sessionId = targetSessionId
    if (sessionId === null) {
      try {
        const created = await createRemoteSession()
        sessionId = created.id
        const summary = toSummary(created)
        saveActiveSession(created.id)
        set((current) => ({
          activeId: created.id,
          sessions: sortSessions([
            ...current.sessions.filter((item) => item.id !== created.id),
            summary,
          ]),
          sessionCache: {
            ...current.sessionCache,
            [created.id]: {
              id: created.id,
              title: created.title,
              createdAt: summary.createdAt,
              updatedAt: summary.updatedAt,
              messages: [],
            },
          },
        }))
      } catch (error) {
        db.reportStorageError('create session')(error)
        showToast(error instanceof FlyApiError ? error.message : '新建对话失败')
        return false
      }
    }
    const session = getSession(sessionId)
    if (!session || session.readOnly || getStreamController(sessionId)) {
      return false
    }
    const question: UserMessage = {
      id: createId(),
      role: 'user',
      content: text,
      files: files.map((file) => ({
        id: file.id,
        serverId: file.serverId,
        name: file.name,
        size: file.size,
        isImage: file.isImage,
        mimeType: file.mimeType,
      })),
      createdAt: Date.now(),
    }
    const reply = createReply()
    try {
      await db.saveFiles(files.map((file) => ({ id: file.id, blob: file.blob })))
    } catch (error) {
      db.reportStorageError('save images')(error)
    }
    const previousRoot = getSession(sessionId)?.rootMessageId
    updateSession(sessionId, (current) => ({
      ...current,
      messages: [...current.messages, question, reply],
      updatedAt: Date.now(),
      rootMessageId: undefined,
    }))
    clearSentDraft(targetSessionId)
    // 请求被服务端拒绝（未落盘）时撤回本地乐观气泡，文字与附件回填输入框
    const rollback = () => {
      updateSession(sessionId, (current) => ({
        ...current,
        messages: current.messages.filter(
          (message) => message.id !== question.id && message.id !== reply.id
        ),
        updatedAt: Date.now(),
        rootMessageId: previousRoot,
      }))
      setDraftText(sessionId, text)
      restoreDraftAttachments(sessionId, files)
    }
    // streamReply 首段是同步的：这里返回时 controller 已登记，后续发送会被拦住
    void streamReply(sessionId, reply, {
      text,
      images: files.map((file) => ({ mediaType: file.mimeType, blob: file.blob })),
      targetMemberIds: options.targetMemberIds,
      shuffleSpeakers: options.shuffleSpeakers,
      questionId: question.id,
      onUnsent: rollback,
    })
    return true
  } finally {
    sendsInFlight.delete(guardKey)
  }
}

/**
 * 卡片授权发送：文本来自卡内确认弹窗，不读写聊天草稿，也不新建会话。
 * 用户消息与回答都标记 `origin:'card'`，界面据此显示来源徽标；
 * 只读/群聊/已有生成任务时拒绝。
 */
export const sendCardMessage = (sessionId: string, text: string): boolean => {
  const state = get()
  const content = text.trim()
  if (state.importingHistory || !state.ready || !content) {
    return false
  }
  if (sendsInFlight.has(sessionId) || getStreamController(sessionId)) {
    return false
  }
  const session = getSession(sessionId)
  if (!session || session.readOnly || session.threadKind === 'group') {
    return false
  }
  const question: UserMessage = {
    id: createId(),
    role: 'user',
    content,
    files: [],
    createdAt: Date.now(),
    origin: 'card',
  }
  const reply = { ...createReply(), origin: 'card' as const }
  updateSession(sessionId, (current) => ({
    ...current,
    messages: [...current.messages, question, reply],
    updatedAt: Date.now(),
    rootMessageId: undefined,
  }))
  // 卡片消息没有输入框草稿可回填；请求被拒时只撤掉本地乐观气泡
  void streamReply(sessionId, reply, {
    text: content,
    images: [],
    origin: 'card',
    questionId: question.id,
    onUnsent: () => {
      updateSession(sessionId, (current) => ({
        ...current,
        messages: current.messages.filter(
          (message) => message.id !== question.id && message.id !== reply.id
        ),
        updatedAt: Date.now(),
      }))
    },
  })
  return true
}

/**
 * 酒馆卡 `context.generate('normal', {quiet_prompt})`：只追加 AI 回复楼层，
 * 不产生可见用户消息；quiet_prompt 仅进本轮出站提示词。
 */
export const generateCardReply = (
  sessionId: string,
  options: { quietPrompt?: string; automaticTrigger?: boolean } = {}
): boolean => {
  const state = get()
  if (state.importingHistory || !state.ready) {
    return false
  }
  if (sendsInFlight.has(sessionId) || getStreamController(sessionId)) {
    return false
  }
  const session = getSession(sessionId)
  if (!session || session.readOnly || session.threadKind === 'group') {
    return false
  }
  const reply = { ...createReply(), origin: 'card' as const }
  updateSession(sessionId, (current) => ({
    ...current,
    messages: [...current.messages, reply],
    updatedAt: Date.now(),
    rootMessageId: undefined,
  }))
  void streamReply(sessionId, reply, {
    text: '',
    images: [],
    quietPrompt: options.quietPrompt ?? '',
    origin: 'card',
    onUnsent: () => {
      updateSession(sessionId, (current) => ({
        ...current,
        messages: current.messages.filter((message) => message.id !== reply.id),
        updatedAt: Date.now(),
      }))
    },
  })
  return true
}

/**
 * 重新生成：锚点是轮次的用户消息。后端只替换该轮回答，后续轮次保持不变；
 * 这里先在原位置放一个等待气泡，结束后再回读权威内容。
 */
export const regenerateReply = (sessionId: string, messageId?: string): boolean => {
  if (get().importingHistory || getStreamController(sessionId)) {
    return false
  }
  const session = getSession(sessionId)
  if (!session || session.readOnly || session.threadKind === 'group') {
    return false
  }
  const path = getVisibleMessages(session)
  const index = messageId
    ? path.findIndex((message) => message.id === messageId)
    : path.length - 1
  const target = path[index]
  const question = index > 0 ? path[index - 1] : undefined
  if (target?.role !== 'assistant' || question?.role !== 'user') {
    return false
  }
  const reply = createReply()
  updateSession(sessionId, (current) => ({
    ...current,
    messages: getVisibleMessages(current).map((message) =>
      message.id === target.id ? reply : message
    ),
    updatedAt: Date.now(),
  }))
  void streamReply(sessionId, reply, {
    text: '',
    images: [],
    regenerateMessageId: question.id,
  })
  return true
}

/**
 * 编辑用户消息：锚点是该用户消息 id，后端保留旧版本并重新生成回答。
 * 只支持 dm；后续轮次会被后端截断。
 */
export const editMessage = (sessionId: string, messageId: string, content: string): boolean => {
  if (get().importingHistory || getStreamController(sessionId)) {
    return false
  }
  const session = getSession(sessionId)
  if (!session || session.readOnly || session.threadKind === 'group') {
    return false
  }
  const text = content.trim()
  if (!text || !session.messages.some((message) => message.id === messageId)) {
    return false
  }
  const path = getVisibleMessages(session)
  const index = path.findIndex((message) => message.id === messageId)
  const target = path[index]
  if (target?.role !== 'user') {
    return false
  }
  const question: UserMessage = { ...target, content: text }
  const reply = createReply()
  updateSession(sessionId, (current) => ({
    ...current,
    messages: [...path.slice(0, index), question, reply],
    updatedAt: Date.now(),
    rootMessageId: undefined,
  }))
  void streamReply(sessionId, reply, {
    text,
    images: [],
    editMessageId: messageId,
  })
  return true
}

/**
 * 切换历史版本：MessageList 传入编码 id（anchor~eN 切 edit / anchor~rN 切 reply），
 * 后端只改活动版本不触发模型，随后强制回读权威历史。
 */
export const selectMessageVersion = async (
  sessionId: string,
  messageId: string
): Promise<boolean> => {
  const match = /^(.*)~(e|r)(\d+)$/.exec(messageId)
  if (!match) {
    return false
  }
  const session = getSession(sessionId)
  if (!session || !session.threadId || session.readOnly || getStreamController(sessionId)) {
    return false
  }
  const index = Number(match[3])
  if (!Number.isInteger(index)) {
    return false
  }
  const epoch = historyEpoch
  try {
    await switchTurnVersion(
      sessionId,
      session.threadId,
      match[1],
      match[2] === 'e' ? { edit_index: index } : { reply_index: index }
    )
    if (epoch !== historyEpoch) {
      return true
    }
    await refreshSession(sessionId, epoch, true)
    return true
  } catch (error) {
    if (epoch === historyEpoch) {
      const detail = error instanceof FlyApiError ? error.message : translate('unknownError')
      showToast({ key: 'switchVersionFailed', params: { message: detail }, type: 'error' })
    }
    return false
  }
}

/**
 * 续写最后一条 AI 回复：后端把原内容作为 assistant prefill，增量并回同一条消息，
 * 不新增消息。这里先就地标记为流式（保留已有内容块），事件按原消息 id 原位更新，
 * 结束后回读权威历史对齐变量与版本快照。
 */
export const continueReply = (sessionId: string, messageId: string): boolean => {
  if (get().importingHistory || getStreamController(sessionId)) {
    return false
  }
  const session = getSession(sessionId)
  if (!session || session.readOnly || session.threadKind === 'group') {
    return false
  }
  const path = getVisibleMessages(session)
  const index = path.findIndex((message) => message.id === messageId)
  const target = path[index]
  // 后端只接受本窗口最后一条可见 AI 回复，且要求无工具调用、内容非空
  if (target?.role !== 'assistant' || index !== path.length - 1 || !canContinueReply(target)) {
    return false
  }
  const reply: AssistantMessage = {
    ...target,
    status: 'streaming',
    error: undefined,
    stopReason: undefined,
    stopDetails: undefined,
  }
  updateSession(sessionId, (current) => ({
    ...current,
    messages: current.messages.map((message) => (message.id === target.id ? reply : message)),
    updatedAt: Date.now(),
  }))
  void streamReply(sessionId, reply, {
    text: '',
    images: [],
    continueMessageId: messageId,
    origin: 'card',
  })
  return true
}

/** 图片在本地生成，附件不存在上传失败重试。 */
export const retryUpload = (_sessionId: string, _messageId: string) => {}