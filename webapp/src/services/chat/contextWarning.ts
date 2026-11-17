import { create } from 'zustand'

/** 上下文长度预警：服务端在窗口跨过阈值时随聊天流下发，同一阈值只提示一次 */
export type ContextWarning = {
  /** 当前窗口字数 */
  chars: number
  /** 本次跨过的阈值（200000/400000/600000/800000） */
  threshold: number
}

type ContextWarningState = {
  warning: ContextWarning | null
  show: (warning: ContextWarning) => void
  dismiss: () => void
}

export const useContextWarning = create<ContextWarningState>((set) => ({
  warning: null,
  show: (warning) => set({ warning }),
  dismiss: () => set({ warning: null }),
}))
