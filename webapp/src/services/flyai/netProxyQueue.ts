/**
 * 卡内网络代理的客户端节流层。
 *
 * 服务端 `/api/st/net-proxy` 按用户限制并发（4）与每分钟请求数（默认 60），
 * 卡片一次性抓几十个文件时会直接撞 429。这里做两件事：
 * 1. 全页面共用并发上限 3（低于服务端 4），请求按到达顺序排队；
 * 2. 429 时按 Retry-After（没有则指数退避）重试，避免把限流错误直接抛给卡片。
 *
 * 服务端在 429 前就拒绝了请求，上游没有执行，因此重试是安全的（含 POST）。
 */

const MAX_CONCURRENT = 3
const MAX_RETRIES = 3
const MAX_RETRY_WAIT_MS = 30000

type Start = () => void

let active = 0
const waiting: Start[] = []

const pump = (): void => {
  while (active < MAX_CONCURRENT) {
    const start = waiting.shift()
    if (!start) {
      return
    }
    active += 1
    start()
  }
}

const abortError = (): Error => {
  if (typeof DOMException !== 'undefined') {
    return new DOMException('The operation was aborted.', 'AbortError')
  }
  const error = new Error('The operation was aborted.')
  error.name = 'AbortError'
  return error
}

const sleep = (ms: number, signal?: AbortSignal): Promise<void> =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    let timer: ReturnType<typeof setTimeout>
    const cleanup = (): void => {
      signal?.removeEventListener('abort', onAbort)
    }
    const onAbort = (): void => {
      clearTimeout(timer)
      cleanup()
      reject(abortError())
    }
    timer = setTimeout(() => {
      cleanup()
      resolve()
    }, Math.max(0, ms))
    signal?.addEventListener('abort', onAbort, { once: true })
  })

/** 只关心 429；错误对象形状与 FlyApiError 兼容，不直接依赖 client.ts */
const isRateLimited = (
  error: unknown
): error is { status: number; retryAfter?: number } =>
  Boolean(
    error &&
      typeof error === 'object' &&
      (error as { status?: unknown }).status === 429
  )

const retryDelay = (
  error: { status: number; retryAfter?: number },
  attempt: number
): number => {
  if (typeof error.retryAfter === 'number' && error.retryAfter >= 0) {
    // 服务端会给出滑动窗口剩余秒数；封顶避免卡片 60s 超时前一直等待
    return Math.min(error.retryAfter * 1000, MAX_RETRY_WAIT_MS)
  }
  return Math.min(4000, 400 * 2 ** attempt) + Math.floor(Math.random() * 200)
}

/** 429 退避重试；其它错误原样抛出，signal 取消时立即停止。 */
export const retryOnRateLimit = async <T>(
  task: () => Promise<T>,
  signal?: AbortSignal
): Promise<T> => {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await task()
    } catch (error) {
      if (
        signal?.aborted ||
        !isRateLimited(error) ||
        attempt >= MAX_RETRIES
      ) {
        throw error
      }
      await sleep(retryDelay(error, attempt), signal)
    }
  }
}

/**
 * 在页面级并发闸门内执行网络代理请求。
 * 排队期间取消会直接从队列移除，不会发出请求。
 */
export const runNetProxy = <T>(
  task: () => Promise<T>,
  signal?: AbortSignal
): Promise<T> =>
  new Promise<T>((resolve, reject) => {
    let done = false
    let started = false

    const finish = (): void => {
      if (done) {
        return
      }
      done = true
      signal?.removeEventListener('abort', onAbort)
      if (started) {
        active = Math.max(0, active - 1)
        pump()
      }
    }

    const start = (): void => {
      if (done) {
        finish()
        return
      }
      started = true
      if (signal?.aborted) {
        finish()
        reject(abortError())
        return
      }
      retryOnRateLimit(task, signal).then(
        (value) => {
          if (done) {
            return
          }
          finish()
          resolve(value)
        },
        (error) => {
          if (done) {
            return
          }
          finish()
          reject(error)
        }
      )
    }

    const onAbort = (): void => {
      if (started) {
        return
      }
      const index = waiting.indexOf(start)
      if (index >= 0) {
        waiting.splice(index, 1)
      }
      finish()
      reject(abortError())
    }

    if (signal?.aborted) {
      onAbort()
      return
    }
    signal?.addEventListener('abort', onAbort, { once: true })
    waiting.push(start)
    pump()
  })

/** 测试用：清空队列与并发计数 */
export const __resetNetProxyQueue = (): void => {
  active = 0
  waiting.length = 0
}
