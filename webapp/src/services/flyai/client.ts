import { useAuth } from './auth.js'
import { getApiBase } from './runtimeConfig.js'

/** 后端非 2xx 的统一错误类型，message 取 FastAPI 的 detail 中文文案 */
export class FlyApiError extends Error {
  status: number
  /** 服务端 429 的 Retry-After（秒）；没有该头时为 undefined */
  retryAfter?: number

  constructor(status: number, message: string, retryAfter?: number) {
    super(message)
    this.name = 'FlyApiError'
    this.status = status
    if (retryAfter !== undefined) {
      this.retryAfter = retryAfter
    }
  }
}

/** 解析 Retry-After：仅支持秒数形式，非法值返回 undefined */
const parseRetryAfterValue = (raw: string | null): number | undefined => {
  if (!raw) {
    return undefined
  }
  const seconds = Number(raw.trim())
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined
}

const parseRetryAfter = (res: Response): number | undefined =>
  parseRetryAfterValue(res.headers.get('Retry-After'))

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

const detailOf = (data: unknown, status: number): string => {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') {
      return detail
    }
    // FastAPI 422 校验错误：[{msg, loc, ...}, ...]
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item
            ? String((item as { msg: unknown }).msg)
            : String(item)
        )
        .join('；')
    }
    return String(detail)
  }
  return `请求失败（HTTP ${status}）`
}

export const readDetail = async (res: Response): Promise<string> => {
  try {
    return detailOf(await res.json(), res.status)
  } catch {
    // 响应体不是 JSON，走兜底文案
    return `请求失败（HTTP ${res.status}）`
  }
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
 * 401 一律清登录态并回到登录页；204 无 body 返回 undefined。
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
    throw new FlyApiError(
      res.status,
      await readDetail(res),
      parseRetryAfter(res)
    )
  }
  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

export type UploadProgress = {
  loaded: number
  total: number
  /** 0..1；total 未知时为 0 */
  percent: number
}

type UploadOptions = {
  /** JSON 上传时显式设置；FormData 必须留空由浏览器补 boundary */
  contentType?: string
  onProgress?: (progress: UploadProgress) => void
  signal?: AbortSignal
}

/**
 * 带上传进度的 POST（fetch 无法读取上传进度，这里用 XHR）。
 * 错误解析、401 处理与 request 保持一致。
 */
export const uploadRequest = <T>(
  path: string,
  body: FormData | string,
  options: UploadOptions = {}
): Promise<T> =>
  new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', apiUrl(path))
    if (options.contentType) {
      xhr.setRequestHeader('Content-Type', options.contentType)
    }
    for (const [key, value] of Object.entries(authHeaders())) {
      xhr.setRequestHeader(key, value)
    }
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return
      }
      options.onProgress?.({
        loaded: event.loaded,
        total: event.total,
        percent: event.total > 0 ? event.loaded / event.total : 0,
      })
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        if (xhr.status === 204) {
          resolve(undefined as T)
          return
        }
        try {
          resolve(JSON.parse(xhr.responseText) as T)
        } catch {
          reject(new FlyApiError(xhr.status, '响应解析失败'))
        }
        return
      }
      if (xhr.status === 401) {
        useAuth.getState().handleSessionExpired()
      }
      let detail = `请求失败（HTTP ${xhr.status}）`
      try {
        detail = detailOf(JSON.parse(xhr.responseText), xhr.status)
      } catch {
        // 非 JSON 错误体
      }
      reject(
        new FlyApiError(
          xhr.status,
          detail,
          parseRetryAfterValue(xhr.getResponseHeader('Retry-After'))
        )
      )
    }
    xhr.onerror = () => reject(new FlyApiError(0, '网络错误'))
    xhr.onabort = () => reject(new DOMException('请求已取消', 'AbortError'))
    const signal = options.signal
    if (signal) {
      if (signal.aborted) {
        xhr.abort()
        return
      }
      const abort = () => xhr.abort()
      signal.addEventListener('abort', abort)
      xhr.addEventListener('loadend', () => signal.removeEventListener('abort', abort))
    }
    xhr.send(body)
  })

/** 打开需要流式读取的 GET 请求（run 事件回放/续读）：失败时抛 FlyApiError */
export const openEventStream = async (path: string, signal: AbortSignal): Promise<Response> => {
  const res = await fetch(apiUrl(path), {
    headers: { Accept: 'text/event-stream', ...authHeaders() },
    signal,
  })
  if (!res.ok) {
    if (res.status === 401) {
      useAuth.getState().handleSessionExpired()
    }
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return res
}

/** 打开需要流式读取的 POST 请求（/api/chat）：失败时抛 FlyApiError，成功返回原始 Response */
export const openStream = async (
  path: string,
  body: unknown,
  signal: AbortSignal
): Promise<Response> => {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    if (res.status === 401) {
      useAuth.getState().handleSessionExpired()
    }
    throw new FlyApiError(res.status, await readDetail(res))
  }
  return res
}