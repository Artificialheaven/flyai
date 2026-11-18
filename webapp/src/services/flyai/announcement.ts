/**
 * 启动公告：每次打开应用（含登录页）拉取并弹出，不做已读持久化。
 *
 * 服务端在管理后台编辑公告内容；离线、旧服务端（404）、未启用或内容为空一律静默，
 * 绝不阻塞启动。公告需要登录前可见，走公开接口 /api/app/announcement。
 */
import { create } from 'zustand'
import { apiUrl } from './client.js'

export type AnnouncementData = {
  /** 为空时展示前端默认标题 */
  title: string
  content: string
}

type AnnouncementState = {
  data: AnnouncementData | null
  /** 本次启动已点「我知道了」；刷新/重开应用后重新弹出 */
  dismissed: boolean
}

export const useAnnouncement = create<AnnouncementState>(() => ({
  data: null,
  dismissed: false,
}))

export const dismissAnnouncement = () => useAnnouncement.setState({ dismissed: true })

const REQUEST_TIMEOUT_MS = 8000

let loading = false

/** 启动时调用一次；内部吞掉所有异常，重复调用不会重开已关闭的公告 */
export const loadAnnouncement = async (): Promise<void> => {
  const state = useAnnouncement.getState()
  if (loading || state.data !== null || state.dismissed) {
    return
  }
  loading = true
  // 不用 AbortSignal.timeout：兼容旧 WebView
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(apiUrl('/api/app/announcement'), {
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!res.ok) {
      return
    }
    const data = (await res.json()) as Record<string, unknown>
    if (data.enabled !== true) {
      return
    }
    const content = typeof data.content === 'string' ? data.content : ''
    if (!content.trim()) {
      return
    }
    useAnnouncement.setState({
      data: {
        title: typeof data.title === 'string' ? data.title : '',
        content,
      },
      dismissed: false,
    })
  } catch {
    // 公告拉取失败不打扰用户
  } finally {
    window.clearTimeout(timer)
    loading = false
  }
}
