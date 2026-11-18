import { create } from 'zustand'
import { showToast } from '../../components/base/toastState.js'
import { translate } from '../../i18n/index.js'
import { request } from './client.js'

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

export type EmailCodePurpose = 'register' | 'reset'

/** 登录页能力探测：决定是否显示邀请码/邮箱字段与「忘记密码」入口 */
export type RegisterInfo = {
  /** 旧服务器没有该字段，按「需要邀请码」处理 */
  invite_required: boolean
  email_required: boolean
  password_reset_enabled: boolean
}

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
  /** 邀请码 + 邮箱验证码注册：成功即自动登录，响应形态与 login 一致 */
  register: (
    name: string,
    password: string,
    inviteCode: string,
    email: string,
    emailCode: string
  ) => Promise<void>
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
  },

  register: async (name, password, inviteCode, email, emailCode) => {
    const data = await request<LoginResponse>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        name,
        password,
        invite_code: inviteCode,
        email,
        email_code: emailCode,
      }),
      silent401: true,
    })
    persistLogin(data)
    const { token, ...user } = data
    set({ token, user })
  },

  logout: async () => {
    try {
      await request('/api/auth/logout', { method: 'POST' })
    } catch {
      // 即使后端吊销失败（断网/令牌已失效），本地也要登出干净
    }
    clearStorage()
    set({ token: null, user: null })
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
      const me = await request<AuthUser>('/api/auth/me')
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
    set({ token: null, user: null, ready: true })
    showToast(translate('sessionExpired'))
  },
}))

/** 登录页能力探测；后端关闭邮件验证时两个标志都为 false */
export const fetchRegisterInfo = (): Promise<RegisterInfo> =>
  request<RegisterInfo>('/api/auth/register-info', { silent401: true })

/** 发送邮箱验证码；成功后后端返回 retry_after（秒）作为本地倒计时时长 */
export const sendEmailCode = (
  email: string,
  purpose: EmailCodePurpose
): Promise<{ ok: boolean; retry_after?: number }> =>
  request<{ ok: boolean; retry_after?: number }>('/api/auth/email-code', {
    method: 'POST',
    body: JSON.stringify({ email, purpose }),
    silent401: true,
  })

/** 邮箱验证码找回密码；成功返回用户名供登录页预填 */
export const resetPassword = (
  email: string,
  code: string,
  newPassword: string
): Promise<{ ok: boolean; name: string }> =>
  request<{ ok: boolean; name: string }>('/api/auth/reset-password', {
    method: 'POST',
    body: JSON.stringify({ email, code, new_password: newPassword }),
    silent401: true,
  })