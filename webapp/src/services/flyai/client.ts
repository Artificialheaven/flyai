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
const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => window.setTimeout(resolve, ms))

/** 网络抖动/网关错误可安全重试；写请求只在显式调用 requestWithProgress 时重试 */
const RETRYABLE_STATUS = new Set([429, 502, 503, 504])

export const request = async <T>(path: string, options: RequestOptions = {}): Promise<T> => {
  const { silent401 = false, formData = false, headers, body, ...init } = options
  const method = (init.method || 'GET').toUpperCase()
  const attempts = method === 'GET' || method === 'HEAD' ? 3 : 1
  let lastError: unknown
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    let res: Response
    try {
      res = await fetch(apiUrl(path), {
        ...init,
        headers: {
          ...(body !== undefined && !formData ? { 'Content-Type': 'application/json' } : {}),
          ...authHeaders(),
          ...headers,
        },
        body,
      })
    } catch (error) {
      lastError = error
      if (attempt < attempts) {
        await sleep(200 * attempt)
        continue
      }
      throw error
    }

    if (!res.ok) {
      if (res.status === 401 && !silent401) {
        useAuth.getState().handleSessionExpired()
      }
      if (RETRYABLE_STATUS.has(res.status) && attempt < attempts) {
        lastError = new FlyApiError(res.status, await readDetail(res), parseRetryAfter(res))
        await sleep(250 * attempt)
        continue
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
  throw lastError instanceof Error ? lastError : new Error('请求失败')
}

/**
 * 带真实上传进度的写请求：XHR 的 upload 事件给出已发送字节数，
 * 大世界书写回因此能显示真进度而不是假动画；失败按指数退避自动重试。
 */
export const requestWithProgress = <T>(
  path: string,
  options: {
    method: string
    body: string
    headers?: Record<string, string>
    retries?: number
    onProgress?: (percent: number, attempt: number) => void
  }
): Promise<T> => {
  const attempts = Math.max(1, Math.min(5, options.retries ?? 3))
  const send = (attempt: number): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open(options.method, apiUrl(path), true)
      xhr.setRequestHeader('Content-Type', 'application/json')
      const auth = authHeaders()
      for (const key of Object.keys(auth)) {
        xhr.setRequestHeader(key, auth[key])
      }
      for (const key of Object.keys(options.headers || {})) {
        xhr.setRequestHeader(key, (options.headers || {})[key])
      }
      xhr.upload.onprogress = (event) => {
        if (!options.onProgress) return
        // 发送完成只代表开始等服务器处理，留 6% 给服务端应用/落盘
        const percent = event.lengthComputable && event.total > 0
          ? Math.min(94, Math.round((event.loaded / event.total) * 94))
          : 0
        options.onProgress(percent, attempt)
      }
      xhr.upload.onload = () => options.onProgress?.(95, attempt)
      xhr.onerror = () => reject(new FlyApiError(0, '网络错误'))
      xhr.onabort = () => reject(new FlyApiError(0, '请求已取消'))
      xhr.onload = () => {
        if (xhr.status === 401) {
          useAuth.getState().handleSessionExpired()
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = `请求失败（HTTP ${xhr.status}）`
          try {
            const data = xhr.responseText ? JSON.parse(xhr.responseText) : null
            if (data) detail = detailOf(data, xhr.status)
          } catch {
            // 非 JSON 响应体走兜底文案
          }
          reject(new FlyApiError(xhr.status, detail))
          return
        }
        try {
          resolve(xhr.responseText ? (JSON.parse(xhr.responseText) as T) : (undefined as T))
        } catch {
          reject(new FlyApiError(xhr.status, '响应不是合法 JSON'))
        }
      }
      xhr.send(options.body)
    })

  const run = async (attempt: number): Promise<T> => {
    try {
      return await send(attempt)
    } catch (error) {
      const status = error instanceof FlyApiError ? error.status : 0
      const retryable = status === 0 || RETRYABLE_STATUS.has(status)
      if (attempt < attempts && retryable) {
        await sleep(400 * attempt)
        return run(attempt + 1)
      }
      throw error
    }
  }
  return run(1)
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