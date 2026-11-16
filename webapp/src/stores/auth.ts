import { create } from 'zustand'
import { request } from '../services/api/client.js'
import { showToast } from '../components/base/toastState.js'
import { navigate } from '../app/router.js'

export type Role = 'admin' | 'user'

export type AuthUser = {
  id: string
  name: string
  role: Role
  level: number
  /** 普通用户有次数额度视图，管理员接口不返回 */
  quota?: unknown
}

type LoginResponse = AuthUser & { token: string }
type MeResponse = AuthUser

/** 与旧站完全一致的本地键名，切换前端时登录态可平滑继承 */
const TOKEN_KEY = 'flyai-token'
const UID_KEY = 'flyai-uid'
const ROLE_KEY = 'flyai-role'
const LEVEL_KEY = 'flyai-level'

type AuthState = {
  token: string | null
  user: AuthUser | null
  /** 启动恢复（GET /me）完成前为 false，避免登录页闪烁 */
  ready: boolean
  login: (name: string, password: string) => Promise<void>
  logout: () => Promise<void>
  /** 启动时用本地令牌恢复会话并向后端校验有效性 */
  restore: () => Promise<void>
  /** 任意请求遇到 401 时的统一收口 */
  handleSessionExpired: () => void
}

const persistLogin = (data: LoginResponse) => {
  localStorage.setItem(TOKEN_KEY, data.token)
  localStorage.setItem(UID_KEY, data.id)
  localStorage.setItem(ROLE_KEY, data.role)
  localStorage.setItem(LEVEL_KEY, String(data.level ?? 0))
}

const clearStorage = () => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(UID_KEY)
  localStorage.removeItem(ROLE_KEY)
  localStorage.removeItem(LEVEL_KEY)
}

const readStoredUser = (): AuthUser | null => {
  const id = localStorage.getItem(UID_KEY)
  if (!id) {
    return null
  }
  const role = localStorage.getItem(ROLE_KEY) === 'admin' ? 'admin' : 'user'
  const level = Number(localStorage.getItem(LEVEL_KEY) ?? 0)
  return { id, name: id, role, level }
}

export const useAuth = create<AuthState>((set, get) => ({
  token: localStorage.getItem(TOKEN_KEY),
  user: null,
  ready: false,

  login: async (name, password) => {
    const data = await request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ name, password }),
      silent401: true,
    })
    persistLogin(data)
    const { token, ...user } = data
    set({ token, user })
    navigate('/')
  },

  logout: async () => {
    try {
      await request('/api/auth/logout', { method: 'POST' })
    } catch {
      // 即使后端吊销失败（断网/令牌已失效），本地也要登出干净
    }
    clearStorage()
    set({ token: null, user: null })
    navigate('/login')
  },

  restore: async () => {
    const token = get().token
    if (!token) {
      set({ ready: true })
      return
    }
    // 先用本地缓存占位，再由 /me 校验并补全显示名等信息
    set({ user: readStoredUser() })
    try {
      const me = await request<MeResponse>('/api/auth/me')
      set({ token, user: me })
    } catch {
      clearStorage()
      set({ token: null, user: null })
    } finally {
      set({ ready: true })
    }
  },

  handleSessionExpired: () => {
    if (!get().token) {
      return
    }
    clearStorage()
    set({ token: null, user: null })
    showToast('登录已失效，请重新登录', true)
    navigate('/login')
  },
}))
