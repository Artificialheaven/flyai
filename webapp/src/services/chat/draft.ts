/**
 * 输入框草稿：与旧站相同的 localStorage 键 flyai-draft-{sid}-{tid}，纯文本。
 * 切换窗口/刷新不丢字；发送成功后清除。
 */
const keyOf = (sid: string, tid: string): string => `flyai-draft-${sid}-${tid}`

export const loadDraft = (sid: string, tid: string): string => {
  try {
    return localStorage.getItem(keyOf(sid, tid)) || ''
  } catch {
    return ''
  }
}

export const saveDraft = (sid: string, tid: string, text: string): void => {
  try {
    if (text) {
      localStorage.setItem(keyOf(sid, tid), text)
    } else {
      localStorage.removeItem(keyOf(sid, tid))
    }
  } catch {
    // 隐私模式/配额异常：草稿可丢，不阻断输入
  }
}

export const clearDraft = (sid: string, tid: string): void => {
  try {
    localStorage.removeItem(keyOf(sid, tid))
  } catch {
    // 同上
  }
}
