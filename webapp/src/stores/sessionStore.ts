import { create } from 'zustand'
import {
  batchDeleteSessions,
  createSession as apiCreateSession,
  deleteSession,
  listSessions,
  patchSession,
} from '../services/api/sessions.js'
import { showToast } from '../components/base/toastState.js'
import type { SessionSummary } from '../types/session.js'

/** 纯函数：置顶在前，组内按 updated_at 倒序（与后端列表默认顺序一致，本地变更后重排用） */
export const sortSessions = (sessions: SessionSummary[]): SessionSummary[] =>
  sessions
    .slice()
    .sort((a, b) => {
      if (a.pinned !== b.pinned) {
        return a.pinned ? -1 : 1
      }
      return b.updated_at.localeCompare(a.updated_at)
    })

/** 纯函数：会话搜索是前端标题过滤（后端无搜索端点），空关键词返回全量 */
export const filterSessions = (sessions: SessionSummary[], keyword: string): SessionSummary[] => {
  const trimmed = keyword.trim().toLowerCase()
  if (!trimmed) {
    return sessions
  }
  return sessions.filter((session) => session.title.toLowerCase().includes(trimmed))
}

type SessionStore = {
  sessions: SessionSummary[]
  loading: boolean
  keyword: string
  batchMode: boolean
  batchIds: string[]
  refresh: () => Promise<void>
  create: (title?: string) => Promise<SessionSummary>
  rename: (sid: string, title: string) => Promise<void>
  setPinned: (sid: string, pinned: boolean) => Promise<void>
  remove: (sid: string) => Promise<void>
  batchRemove: () => Promise<void>
  setKeyword: (keyword: string) => void
  toggleBatchMode: (on?: boolean) => void
  toggleBatchId: (sid: string) => void
  /** 本地直接替换/插入一条（建壳、终态刷新 mtime 等场景，避免整列表 loading 闪烁） */
  upsertLocal: (session: SessionSummary) => void
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessions: [],
  loading: false,
  keyword: '',
  batchMode: false,
  batchIds: [],

  refresh: async () => {
    set({ loading: true })
    try {
      const sessions = await listSessions()
      set({ sessions: sortSessions(sessions), loading: false })
    } catch (error) {
      set({ loading: false })
      showToast(`会话列表加载失败：${(error as Error).message}`, true)
    }
  },

  create: async (title) => {
    const session = await apiCreateSession(title)
    get().upsertLocal(session)
    return session
  },

  rename: async (sid, title) => {
    const trimmed = title.trim()
    if (!trimmed) {
      return
    }
    const previous = get().sessions
    // 乐观更新：输入框确认后立即响应，失败再回滚
    set({ sessions: sortSessions(previous.map((s) => (s.id === sid ? { ...s, title: trimmed } : s))) })
    try {
      const updated = await patchSession(sid, { title: trimmed })
      set({ sessions: sortSessions(get().sessions.map((s) => (s.id === sid ? updated : s))) })
    } catch (error) {
      set({ sessions: previous })
      showToast(`重命名失败：${(error as Error).message}`, true)
    }
  },

  setPinned: async (sid, pinned) => {
    const previous = get().sessions
    set({ sessions: sortSessions(previous.map((s) => (s.id === sid ? { ...s, pinned } : s))) })
    try {
      const updated = await patchSession(sid, { pinned })
      set({ sessions: sortSessions(get().sessions.map((s) => (s.id === sid ? updated : s))) })
    } catch (error) {
      set({ sessions: previous })
      showToast(`置顶操作失败：${(error as Error).message}`, true)
    }
  },

  remove: async (sid) => {
    const previous = get().sessions
    set({ sessions: get().sessions.filter((s) => s.id !== sid) })
    try {
      await deleteSession(sid)
    } catch (error) {
      set({ sessions: previous })
      showToast(`删除失败：${(error as Error).message}`, true)
    }
  },

  batchRemove: async () => {
    const ids = get().batchIds
    if (ids.length === 0) {
      return
    }
    const previous = get().sessions
    const idSet = new Set(ids)
    set({ sessions: get().sessions.filter((s) => !idSet.has(s.id)), batchIds: [], batchMode: false })
    try {
      await batchDeleteSessions(ids)
      showToast(`已删除 ${ids.length} 个会话`)
    } catch (error) {
      set({ sessions: previous, batchIds: ids, batchMode: true })
      showToast(`批量删除失败：${(error as Error).message}`, true)
    }
  },

  setKeyword: (keyword) => set({ keyword }),

  toggleBatchMode: (on) =>
    set((state) => ({ batchMode: on ?? !state.batchMode, batchIds: [] })),

  toggleBatchId: (sid) =>
    set((state) => ({
      batchIds: state.batchIds.includes(sid)
        ? state.batchIds.filter((id) => id !== sid)
        : [...state.batchIds, sid],
    })),

  upsertLocal: (session) => {
    const rest = get().sessions.filter((s) => s.id !== session.id)
    set({ sessions: sortSessions([session, ...rest]) })
  },
}))
