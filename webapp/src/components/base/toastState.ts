import { create } from 'zustand'

/** 轻提示：同一时间只显示一条，新的替换旧的，到时自动消失；FlyAI 无多语言，文案直接给字符串 */
type ToastState = { id: number; message: string | null; error: boolean }

export const useToast = create<ToastState>(() => ({ id: 0, message: null, error: false }))

const DURATION = 3000
let timer = 0

export const showToast = (message: string, error = false) => {
  window.clearTimeout(timer)
  useToast.setState((state) => ({ id: state.id + 1, message, error }))
  timer = window.setTimeout(() => useToast.setState({ message: null }), DURATION)
}
