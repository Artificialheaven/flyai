import { useAuth } from '../../stores/auth.js'
import { getApiBase } from '../runtimeConfig.js'

/** 后端非 2xx 的统一错误类型，message 取 FastAPI 的 detail 中文文案 */
export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const apiUrl = (path: string): string => `${getApiBase()}${path}`

/**
 * 服务端资源路径（/uploads/.. 头像等）：加 apiBase 前缀；
 * /static 本地内置资源与 data:/blob:/http(s):// 完整地址原样返回（对照旧版 assetUrl）。
 */
export const assetUrl = (url: string | null | undefined): string => {
  if (!url) {
    return ''
  }
  if (url.charAt(0) !== '/' || url.charAt(1) === '/') {
    return url
  }
  if (url.indexOf('/static/') === 0) {
    return url
  }
  return `${getApiBase()}${url}`
}

/** 带 Bearer 随机令牌的请求头；未登录时只返回空对象 */
export const authHeaders = (): Record<string, string> => {
  const token = useAuth.getState().token
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const readDetail = async (res: Response): Promise<string> => {
  try {
    const data: unknown = await res.json()
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === 'string') {
        return detail
      }
      // FastAPI 422 校验错误：[{msg, loc, ...}, ...]
      if (Array.isArray(detail)) {
        return detail
          .map((item) => (item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item)))
          .join('；')
      }
      return String(detail)
    }
  } catch {
    // 响应体不是 JSON，走兜底文案
  }
  return `请求失败（HTTP ${res.status}）`
}

type RequestOptions = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>
  /** 为 true 时 401 不触发全局登录失效跳转（登录接口本身用） */
  silent401?: boolean
  /** multipart 上传：body 为 FormData 时不让浏览器默认 JSON Content-Type 覆盖边界 */
  formData?: boolean
}

/**
 * 统一请求入口：拼接 apiBase、注入令牌、解析 FastAPI 错误体。
 * 401 一律清登录态并回到 #/login；204 无 body 返回 undefined。
 */
export const request = async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
  const { silent401 = false, formData = false, headers, body, ...init } = options
  const res = await fetch(apiUrl(path), {
    ...init,
    headers: {
      ...(body !== undefined && !formData ? { 'Content-Type': 'application/json' } : {}),
      ...authHeaders(),
      ...headers,
    },
    body,
  })

  if (!res.ok) {
    if (res.status === 401 && !silent401) {
      useAuth.getState().handleSessionExpired()
    }
    throw new ApiError(res.status, await readDetail(res))
  }
  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}
