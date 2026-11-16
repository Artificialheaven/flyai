import { create } from 'zustand'
import { getSession, patchSession, type SessionPatch } from '../services/api/sessions.js'
import {
  ChatStreamController,
  type ChatImagePayload,
  type ChatSendPayload,
  type StreamStatus,
} from '../services/chat/stream.js'
import type { LiveRunState } from '../services/chat/streamReducer.js'
import { discoverActiveRun } from '../services/chat/activeRun.js'
import { readRunMarker } from '../services/chat/runMarker.js'
import { clearDraft, loadDraft, saveDraft } from '../services/chat/draft.js'
import { parseMentionTargets, type MentionMember } from '../services/chat/mentions.js'
import { IMAGE_MAX_COUNT, processImageFile } from '../utils/image.js'
import { showToast } from '../components/base/toastState.js'
import { useAuth } from './auth.js'
import { useSessionStore } from './sessionStore.js'
import type { RoomMember, SessionDetail, Thread } from '../types/session.js'

/** 从房间成员构造 @ 候选：群聊窗口内的非用户座位，显示名优先、座位名兜底 */
export const buildMentionMembers = (detail: SessionDetail, thread: Thread | undefined): MentionMember[] => {
  if (!thread || thread.kind !== 'group') {
    return []
  }
  const memberIds = new Set(thread.member_ids)
  return detail.members
    .filter((member: RoomMember) => memberIds.has(member.id) && !member.is_user)
    .map((member) => ({
      id: member.id,
      name: member.name,
      aliases: member.override?.name ? [String(member.override.name)] : [],
    }))
}

export type LastSent = {
  content: string
  images: ChatImagePayload[]
}

/** 群聊发言顺序开关按会话记忆（旧站同名键，切换前端后保持用户设置） */
export const shuffleKey = (sid: string): string => `flyai-shuffle-${sid}`

export const readShuffle = (sid: string): boolean => localStorage.getItem(shuffleKey(sid)) === '1'

export const writeShuffle = (sid: string, shuffle: boolean): void => {
  if (shuffle) {
    localStorage.setItem(shuffleKey(sid), '1')
  } else {
    localStorage.removeItem(shuffleKey(sid))
  }
}

/** 本地 SD 生图开关沿用旧站键（"sd" / "off"），换前端后保持用户选择 */
export const readSdEngine = (): boolean =>
  typeof localStorage !== 'undefined' && localStorage.getItem('flyai-sd-engine') === 'sd'

/** 生图相关附加字段：发送/重生成/编辑重发都携带，服务端按空值回落到默认云端模型 */
const imageFields = (sdEngine: boolean) => ({
  image_model_id:
    typeof localStorage === 'undefined' ? '' : localStorage.getItem('flyai-image-model') || '',
  image_engine: sdEngine ? 'sd' : '',
})

type ChatStore = {
  sid: string | null
  tid: string | null
  detail: SessionDetail | null
  loading: boolean
  live: LiveRunState | null
  streamStatus: StreamStatus
  busy: boolean
  pendingImages: ChatImagePayload[]
  deepThinking: boolean
  /** 群聊随机发言顺序（仅群聊窗口且未 @ 时生效） */
  shuffle: boolean
  /** 本地 SD 生图引擎（配置未开启时强制复位，见 ChatInput） */
  sdEngine: boolean
  draft: string
  /** 发送后到终态落盘前的乐观用户气泡 */
  lastSent: LastSent | null
  openSession: (sid: string) => Promise<void>
  selectThread: (tid: string) => void
  setDraft: (text: string) => void
  addImageFiles: (files: File[]) => Promise<void>
  removeImage: (index: number) => void
  toggleDeepThinking: () => void
  toggleShuffle: () => void
  setSdEngine: (enabled: boolean) => void
  /** 用户点击切换：写记忆并提示（配置关闭时的静默复位走 setSdEngine） */
  toggleSdEngine: () => void
  /** 房间级设置（绑定角色/世界、上限、工具等）：PATCH 后重拉完整房间 */
  patchRoom: (patch: SessionPatch) => Promise<void>
  /** 成员 PATCH 返回后本地合并，避免整房间重拉打断流式现场 */
  applyMember: (member: RoomMember) => void
  /** 成员/窗口等结构变更后统一重拉（调用方已完成 API 请求） */
  reloadAfterMutation: () => Promise<void>
  send: (text?: string) => Promise<void>
  stop: () => void
  regenerate: (messageId: string) => void
  editMessage: (messageId: string, text: string) => void
  reloadDetail: () => Promise<void>
  reset: () => void
}

/** 传输控制器是进程级单例：zustand 状态可重置，run 生命周期不随 store 重建而丢失 */
let controller: ChatStreamController | null = null

const ensureController = (): ChatStreamController => {
  if (controller) {
    return controller
  }
  controller = new ChatStreamController({
    onState: (live) => useChatStore.setState({ live }),
    onStatus: (streamStatus, busy) => useChatStore.setState({ streamStatus, busy }),
    onToast: (message, error) => showToast(message, error),
    onRevertDraft: (payload) => {
      // run 建立前失败：消息没进会话，文字与图片全部回填
      useChatStore.setState((state) => ({
        draft: payload.message,
        pendingImages: payload.images.length ? payload.images : state.pendingImages,
      }))
      const { sid, tid } = useChatStore.getState()
      if (sid && tid) {
        saveDraft(sid, tid, payload.message)
      }
    },
    onTerminal: () => {
      const state = useChatStore.getState()
      useChatStore.setState({ lastSent: null })
      if (state.sid) {
        // 终态后消息已落盘（含工具结果/半截内容），以服务端线程为准重拉
        void state.reloadDetail()
      }
    },
  })
  return controller
}

const initialThread = (detail: SessionDetail): Thread | undefined => detail.threads[0]

export const useChatStore = create<ChatStore>((set, get) => ({
  sid: null,
  tid: null,
  detail: null,
  loading: false,
  live: null,
  streamStatus: 'idle',
  busy: false,
  pendingImages: [],
  deepThinking: false,
  shuffle: false,
  sdEngine: readSdEngine(),
  draft: '',
  lastSent: null,

  openSession: async (sid) => {
    if (get().sid === sid && get().detail) {
      return
    }
    set({ sid, tid: null, detail: null, loading: true, live: null, lastSent: null, pendingImages: [], shuffle: readShuffle(sid) })
    try {
      const detail = await getSession(sid)
      const thread = initialThread(detail)
      const tid = thread?.id ?? null
      set({
        detail,
        tid,
        loading: false,
        shuffle: readShuffle(sid),
        draft: tid ? loadDraft(sid, tid) : '',
      })
      useSessionStore.getState().upsertLocal(detail)
      void maybeResume(sid, tid)
    } catch (error) {
      set({ loading: false })
      showToast(`会话打开失败：${(error as Error).message}`, true)
    }
  },

  selectThread: (tid) => {
    const { sid, detail } = get()
    if (!sid || !detail || tid === get().tid) {
      return
    }
    set({ tid, draft: loadDraft(sid, tid), lastSent: null })
  },

  setDraft: (text) => {
    const { sid, tid } = get()
    set({ draft: text })
    if (sid && tid) {
      saveDraft(sid, tid, text)
    }
  },

  addImageFiles: async (files) => {
    const images = files.filter((file) => file.type.startsWith('image/'))
    for (const file of images) {
      if (get().pendingImages.length >= IMAGE_MAX_COUNT) {
        showToast(`每条消息最多附带 ${IMAGE_MAX_COUNT} 张图片`)
        return
      }
      try {
        const payload = await processImageFile(file)
        // 串行处理期间用户可能已停止/切换，追加前再校验一次容量
        set((state) =>
          state.pendingImages.length >= IMAGE_MAX_COUNT
            ? state
            : { pendingImages: [...state.pendingImages, payload] }
        )
      } catch (error) {
        showToast(`图片处理失败（${file.name}）：${(error as Error).message}`, true)
      }
    }
  },

  removeImage: (index) =>
    set((state) => ({ pendingImages: state.pendingImages.filter((_, i) => i !== index) })),

  toggleDeepThinking: () => set((state) => ({ deepThinking: !state.deepThinking })),

  toggleShuffle: () => {
    const { sid, shuffle } = get()
    const next = !shuffle
    set({ shuffle: next })
    if (sid) {
      writeShuffle(sid, next)
    }
  },

  setSdEngine: (enabled) => {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('flyai-sd-engine', enabled ? 'sd' : 'off')
    }
    set({ sdEngine: enabled })
  },

  toggleSdEngine: () => {
    const next = !get().sdEngine
    get().setSdEngine(next)
    showToast(
      next
        ? '已切换为本地 Stable Diffusion 生图（Anything V5）'
        : '已切换为云端生图模型'
    )
  },

  patchRoom: async (patch) => {
    const sid = get().sid
    if (!sid) {
      return
    }
    try {
      const updated = await patchSession(sid, patch)
      useSessionStore.getState().upsertLocal(updated)
      await get().reloadAfterMutation()
    } catch (error) {
      showToast(`设置失败：${(error as Error).message}`, true)
    }
  },

  applyMember: (member) =>
    set((state) =>
      state.detail
        ? {
            detail: {
              ...state.detail,
              members: state.detail.members.map((item) => (item.id === member.id ? member : item)),
            },
          }
        : state
    ),

  reloadAfterMutation: async () => {
    await get().reloadDetail()
  },

  send: async (text) => {
    const state = get()
    const { detail, sid, tid } = state
    if (!sid || !detail || state.busy) {
      return
    }
    const content = (text ?? state.draft).trim()
    if (!content && state.pendingImages.length === 0) {
      return
    }
    const thread = detail.threads.find((item) => item.id === tid)
    const mentionMembers = buildMentionMembers(detail, thread)
    const payload: ChatSendPayload = {
      message: content,
      session_id: sid,
      thread_id: thread?.id,
      tools_enabled: true,
      images: state.pendingImages,
      deep_thinking: state.deepThinking || undefined,
      disabled_tools: detail.disabled_tools,
      ...imageFields(state.sdEngine),
    }
    // @ 点名优先于随机顺序：被点名者先答，其余按窗口顺序跟后
    payload.shuffle_speakers = mentionMembers.length > 0 ? false : state.shuffle
    if (mentionMembers.length > 0) {
      payload.target_member_ids = parseMentionTargets(content, mentionMembers)
    }
    set({
      lastSent: { content, images: state.pendingImages },
      pendingImages: [],
      draft: '',
    })
    if (tid) {
      clearDraft(sid, tid)
    }
    ensureController().start(payload)
  },

  stop: () => ensureController().cancel(),

  regenerate: (messageId) => {
    const state = get()
    if (!state.sid || !state.detail || state.busy) {
      return
    }
    const thread = state.detail.threads.find((item) => item.id === state.tid)
    if (!thread || thread.kind !== 'dm') {
      showToast('仅单聊窗口支持重新生成', true)
      return
    }
    ensureController().start({
      message: '',
      session_id: state.sid,
      thread_id: thread.id,
      tools_enabled: true,
      images: [],
      regenerate_message_id: messageId,
      disabled_tools: state.detail.disabled_tools,
      ...imageFields(state.sdEngine),
    })
  },

  editMessage: (messageId, text) => {
    const state = get()
    const content = text.trim()
    if (!state.sid || !state.detail || state.busy || !content) {
      return
    }
    const thread = state.detail.threads.find((item) => item.id === state.tid)
    if (!thread || thread.kind !== 'dm') {
      showToast('仅单聊窗口支持编辑重发', true)
      return
    }
    set({ draft: '', lastSent: { content, images: [] } })
    clearDraft(state.sid, thread.id)
    ensureController().start({
      message: content,
      session_id: state.sid,
      thread_id: thread.id,
      tools_enabled: true,
      images: [],
      edit_message_id: messageId,
      disabled_tools: state.detail.disabled_tools,
      ...imageFields(state.sdEngine),
    })
  },

  reloadDetail: async () => {
    const sid = get().sid
    if (!sid) {
      return
    }
    try {
      const detail = await getSession(sid)
      const tidStillExists = detail.threads.some((thread) => thread.id === get().tid)
      set({
        detail,
        tid: tidStillExists ? get().tid : initialThread(detail)?.id ?? null,
        live: null,
      })
      useSessionStore.getState().upsertLocal(detail)
    } catch {
      // 终态重拉失败保留现场，下一次进入会话自然恢复
    }
  },

  reset: () => {
    controller?.dispose()
    controller = null
    set({
      sid: null,
      tid: null,
      detail: null,
      loading: false,
      live: null,
      streamStatus: 'idle',
      busy: false,
      pendingImages: [],
      deepThinking: false,
      shuffle: false,
      sdEngine: readSdEngine(),
      draft: '',
      lastSent: null,
    })
  },
}))

/**
 * 进入会话后的运行发现（对照旧版 maybeResumeRun）：
 * 本地标记优先（带 seq 可序号续读），标记缺失/过期再问服务端 active-run（全量回放）。
 * 模块级函数，仅 openSession 调用；函数声明提升保证 store 定义处可引用。
 */
async function maybeResume(sid: string, tid: string | null): Promise<void> {
  const userId = useAuth.getState().user?.id ?? null
  const marker = readRunMarker(userId)
  try {
    if (marker && marker.sid === sid) {
      const targetTid = marker.tid || tid
      if (targetTid && targetTid !== useChatStore.getState().tid) {
        useChatStore.getState().selectThread(targetTid)
      }
      ensureController().resume(marker.run_id, { sid, tid: marker.tid, after: marker.seq })
      return
    }
    const runId = await discoverActiveRun(sid)
    if (runId && useChatStore.getState().sid === sid) {
      ensureController().resume(runId, { sid, tid: tid ?? undefined, after: 0 })
    }
  } catch {
    // 404/断网：无活动 run，静默留在历史视图
  }
}
