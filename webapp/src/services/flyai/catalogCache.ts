/** 内容库目录（角色/世界书/预设）的内存缓存：SWR + 并发去重 + 写操作失效。
 *
 * 只在当前页面生命周期内有效；刷新页面后第一次仍走网络（后端按文件 stat 缓存，
 * 再次请求很快）。对话框关闭再打开、同一页面多处读取都直接命中。
 */

export type CatalogKey = 'characters' | 'worlds' | 'presets'

/** 默认新鲜期：一分钟内重复打开内容库不再请求 */
const DEFAULT_TTL = 60_000

type Entry = {
  data: unknown
  /** 最近一次成功时间；0 表示数据已被失效、仅剩在途请求 */
  at: number
  /** 写操作失效计数：在途请求回来时版本不一致就不写回缓存 */
  version: number
  inflight?: Promise<unknown>
}

const entries = new Map<CatalogKey, Entry>()
const listeners = new Map<CatalogKey, Set<(data: unknown) => void>>()

const notify = (key: CatalogKey, data: unknown) => {
  for (const listener of listeners.get(key) ?? []) {
    listener(data)
  }
}

/** 同步读取最近一次成功的数据，供对话框首帧直接渲染 */
export const peekCatalog = <T>(key: CatalogKey): T | undefined =>
  entries.get(key)?.data as T | undefined

/**
 * 读目录：新鲜期内直接返回缓存；否则发起请求（同 key 并发合并）。
 * 失败时若有旧数据则回退旧数据（不打断界面），没有旧数据才把错误抛给调用方。
 */
export const loadCatalog = <T>(
  key: CatalogKey,
  fetcher: () => Promise<T>,
  options: { ttl?: number; force?: boolean } = {}
): Promise<T> => {
  const { ttl = DEFAULT_TTL, force = false } = options
  const entry = entries.get(key)
  if (!force && entry && entry.data !== undefined && Date.now() - entry.at < ttl) {
    return Promise.resolve(entry.data as T)
  }
  if (entry?.inflight) {
    return entry.inflight as Promise<T>
  }
  const version = entry?.version ?? 0
  const promise = fetcher().then(
    (data) => {
      const current = entries.get(key)
      if (current && current.version === version) {
        current.data = data
        current.at = Date.now()
        current.inflight = undefined
        notify(key, data)
      }
      return data
    },
    (error: unknown) => {
      const current = entries.get(key)
      if (current && current.version === version) {
        current.inflight = undefined
        if (current.data !== undefined) {
          return current.data as T
        }
      }
      throw error
    }
  )
  if (entry && entry.version === version) {
    entry.inflight = promise
  } else {
    entries.set(key, { data: undefined, at: 0, version, inflight: promise })
  }
  return promise
}

/** 目录写操作成功后调用：下次读取重新请求，在途的旧响应不再写回 */
export const invalidateCatalog = (...keys: CatalogKey[]) => {
  for (const key of keys) {
    const entry = entries.get(key)
    if (!entry) {
      continue
    }
    entry.data = undefined
    entry.at = 0
    entry.version += 1
    entry.inflight = undefined
  }
}

/** 就地更新已缓存的目录（如切换预设后同步 activeId），保持界面不闪 */
export const updateCatalog = <T>(
  key: CatalogKey,
  update: (current: T) => T | undefined
): void => {
  const entry = entries.get(key)
  if (!entry || entry.data === undefined) {
    return
  }
  const next = update(entry.data as T)
  if (next === undefined || next === entry.data) {
    return
  }
  entry.data = next
  entry.at = Date.now()
  notify(key, next)
}

/** 订阅目录数据变化（后台刷新完成、写操作就地更新）；返回取消订阅函数 */
export const subscribeCatalog = <T>(
  key: CatalogKey,
  listener: (data: T) => void
): (() => void) => {
  let set = listeners.get(key)
  if (!set) {
    set = new Set()
    listeners.set(key, set)
  }
  const handler = listener as (data: unknown) => void
  set.add(handler)
  return () => {
    set.delete(handler)
    if (set.size === 0) {
      listeners.delete(key)
    }
  }
}
