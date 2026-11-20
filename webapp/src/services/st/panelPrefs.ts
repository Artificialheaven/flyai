/**
 * 状态面板的本机偏好（存 localStorage）：
 * 是否把面板 HTML 自动追加到每条助手回复下方（开关 C）。
 */

import { create } from 'zustand'

type PanelPrefs = {
  /** 每条助手消息下方自动追加当前状态面板 */
  autoAppend: boolean
}

const KEY = 'chat-demo:st-panel-auto-append'

const read = (): boolean => {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

export const usePanelPrefs = create<PanelPrefs>(() => ({ autoAppend: read() }))

export const setPanelAutoAppend = (value: boolean) => {
  usePanelPrefs.setState({ autoAppend: value })
  try {
    if (value) {
      localStorage.setItem(KEY, '1')
    } else {
      localStorage.removeItem(KEY)
    }
  } catch {
    // 存储不可用时只在内存中生效
  }
}
