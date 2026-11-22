/**
 * 卡片静态资源本地缓存（IndexedDB）。
 *
 * 卡内 fetch / 外链插图代理下载过的静态资源（JS、CSS、图片、字体、音视频、
 * 静态 JSON 等）落盘保存；之后同一 URL 直接读本地，不再请求服务器。
 *
 * 约束：
 * - 只缓存 GET + 200 + 未截断的静态类型响应，POST/动态接口不缓存；
 * - 单条网络资源 ≤ 32MB、图片 ≤ 8MB（与服务端上限一致）；两类各 2GB，
 *   按写入时间 FIFO 淘汰；
 * - 缓存不可用（隐私模式/配额不足）时静默降级，不影响正常请求。
 */

import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { NetProxyResult } from './st.js'

const DB_NAME = 'flyai-card-resources'
const DB_VERSION = 1

const NET_CAP = 2 * 1024 * 1024 * 1024
const IMAGE_CAP = 2 * 1024 * 1024 * 1024
// 与服务端上限对齐：net-proxy 响应最大 32MB，图片代理最大 8MB
const NET_ENTRY_MAX = 32 * 1024 * 1024
const IMAGE_ENTRY_MAX = 8 * 1024 * 1024

type NetEntry = {
  key: string
  status: number
  headers: Record<string, string>
  body_base64: string
  truncated: boolean
  size: number
  savedAt: number
}

type IndexEntry = {
  /** net:<key> / img:<key>，避免两类资源键冲突 */
  id: string
  kind: 'net' | 'image'
  key: string
  size: number
  savedAt: number
}

interface ResourceDB extends DBSchema {
  net: { key: string; value: NetEntry }
  images: { key: string; value: Blob }
  index: {
    key: string
    value: IndexEntry
    indexes: { savedAt: number }
  }
  meta: { key: string; value: { key: string; bytes: number } }
}

let dbPromise: Promise<IDBPDatabase<ResourceDB>> | null = null

const getDB = (): Promise<IDBPDatabase<ResourceDB>> => {
  if (!dbPromise) {
    const open = () => {
      try {
        return openDB<ResourceDB>(DB_NAME, DB_VERSION, {
          upgrade(db) {
            if (!db.objectStoreNames.contains('net')) {
              db.createObjectStore('net', { keyPath: 'key' })
            }
            if (!db.objectStoreNames.contains('images')) {
              db.createObjectStore('images')
            }
            if (!db.objectStoreNames.contains('index')) {
              const index = db.createObjectStore('index', { keyPath: 'id' })
              index.createIndex('savedAt', 'savedAt')
            }
            if (!db.objectStoreNames.contains('meta')) {
              db.createObjectStore('meta')
            }
          },
        })
      } catch (error) {
        return Promise.reject(error)
      }
    }
    dbPromise = open().catch((error: unknown) => {
      dbPromise = null
      throw error
    })
  }
  return dbPromise
}

const normalizeUrl = (url: string): string => {
  try {
    const parsed = new URL(url)
    parsed.hash = ''
    return parsed.href
  } catch {
    return url
  }
}

/**
 * 网络代理缓存的键：GET + URL + 会影响内容的两个透传头。
 * 非 GET 返回空串（不缓存）。
 */
export const netCacheKey = (payload: {
  method?: string
  url?: string
  headers?: Record<string, string>
}): string => {
  const method = String(payload.method || 'GET').toUpperCase()
  const url = String(payload.url || '')
  if (method !== 'GET' || !url) {
    return ''
  }
  const headers = payload.headers || {}
  const accept = String(
    headers.accept || headers.Accept || ''
  ).toLowerCase()
  const contentType = String(
    headers['content-type'] || headers['Content-Type'] || ''
  ).toLowerCase()
  return `${normalizeUrl(url)}|a=${accept}|c=${contentType}`
}

const STATIC_TYPE_RE =
  /^(image\/|font\/|audio\/|video\/|text\/css$|text\/javascript$|application\/javascript$|application\/x-javascript$|application\/wasm$|application\/json$)/

/** 该响应是否值得落盘（静态资源 + 完整 200）。 */
export const isCacheableNetResponse = (
  result: NetProxyResult | null | undefined,
  url: string
): boolean => {
  if (!result || result.status !== 200 || result.truncated) {
    return false
  }
  const contentType = String(
    (result.headers && result.headers['content-type']) || ''
  )
    .split(';', 1)[0]
    .trim()
    .toLowerCase()
  if (STATIC_TYPE_RE.test(contentType)) {
    // 动态 JSON 接口通常也返回 application/json；只缓存明显的静态 .json 文件
    if (contentType === 'application/json') {
      return /\.json(?:$|[?#])/i.test(url)
    }
    return true
  }
  // 服务器未给出类型时按扩展名兜底
  return /\.(js|mjs|css|png|jpe?g|gif|webp|avif|svg|woff2?|ttf|otf|wasm|mp3|wav|ogg|mp4|webm|json)(?:$|[?#])/i.test(
    url
  )
}

const base64Bytes = (text: string): number =>
  Math.floor((String(text || '').length * 3) / 4)

const writeEntry = async (
  kind: 'net' | 'image',
  id: string,
  key: string,
  value: NetEntry | Blob,
  size: number,
  cap: number
): Promise<void> => {
  const db = await getDB()
  const tx = db.transaction(['net', 'images', 'index', 'meta'], 'readwrite')
  const indexStore = tx.objectStore('index')
  const metaStore = tx.objectStore('meta')
  const metaKey = kind === 'net' ? 'net:total' : 'image:total'
  const old = await indexStore.get(id)
  const meta = await metaStore.get(metaKey)
  let total = (meta?.bytes || 0) - (old?.size || 0) + size

  // 动态 store 名在 idb 的类型层不好收窄，这里统一走结构化的 any
  const store = (tx as unknown as {
    objectStore: (name: string) => {
      put: (value: unknown, key?: string) => Promise<unknown>
      delete: (key: string) => Promise<unknown>
    }
  }).objectStore(kind === 'net' ? 'net' : 'images')
  if (kind === 'net') {
    // net 使用 keyPath，不能再额外传键
    await store.put(value)
  } else {
    await store.put(value, key)
  }
  await indexStore.put({
    id,
    kind,
    key,
    size,
    savedAt: Date.now(),
  })

  if (total > cap) {
    const all = await indexStore.getAll()
    const victims = all
      .filter((entry) => entry.kind === kind && entry.id !== id)
      .sort((a, b) => a.savedAt - b.savedAt)
    for (const victim of victims) {
      if (total <= cap) {
        break
      }
      await store.delete(victim.key)
      await indexStore.delete(victim.id)
      total -= victim.size
    }
  }
  await metaStore.put({ key: metaKey, bytes: Math.max(0, total) }, metaKey)
  await tx.done
}

export const readCachedNetProxy = async (payload: {
  method?: string
  url?: string
  headers?: Record<string, string>
}): Promise<NetProxyResult | null> => {
  const key = netCacheKey(payload)
  if (!key) {
    return null
  }
  try {
    const entry = await (await getDB()).get('net', key)
    if (!entry) {
      return null
    }
    return {
      status: entry.status,
      headers: entry.headers,
      body_base64: entry.body_base64,
      truncated: entry.truncated,
    }
  } catch {
    return null
  }
}

export const writeCachedNetProxy = async (
  payload: { method?: string; url?: string; headers?: Record<string, string> },
  result: NetProxyResult
): Promise<void> => {
  const key = netCacheKey(payload)
  const url = String(payload.url || '')
  if (!key || !isCacheableNetResponse(result, url)) {
    return
  }
  const size = base64Bytes(result.body_base64)
  if (size <= 0 || size > NET_ENTRY_MAX) {
    return
  }
  try {
    await writeEntry(
      'net',
      `net:${key}`,
      key,
      {
        key,
        status: result.status,
        headers: result.headers,
        body_base64: result.body_base64,
        truncated: result.truncated,
        size,
        savedAt: Date.now(),
      },
      size,
      NET_CAP
    )
  } catch {
    // 配额/隐私模式：静默降级
  }
}

export const readCachedImage = async (url: string): Promise<Blob | null> => {
  const key = normalizeUrl(String(url || ''))
  if (!key) {
    return null
  }
  try {
    const blob = await (await getDB()).get('images', key)
    return typeof Blob !== 'undefined' && blob instanceof Blob && blob.size > 0
      ? blob
      : null
  } catch {
    return null
  }
}

export const writeCachedImage = async (url: string, blob: Blob): Promise<void> => {
  const key = normalizeUrl(String(url || ''))
  if (
    !key ||
    typeof Blob === 'undefined' ||
    !(blob instanceof Blob) ||
    blob.size <= 0 ||
    blob.size > IMAGE_ENTRY_MAX
  ) {
    return
  }
  try {
    await writeEntry('image', `img:${key}`, key, blob, blob.size, IMAGE_CAP)
  } catch {
    // 静默降级
  }
}

export const clearCardResourceCache = async (): Promise<void> => {
  try {
    const db = await getDB()
    const tx = db.transaction(['net', 'images', 'index', 'meta'], 'readwrite')
    await tx.objectStore('net').clear()
    await tx.objectStore('images').clear()
    await tx.objectStore('index').clear()
    await tx.objectStore('meta').clear()
    await tx.done
  } catch {
    // 缓存本来不可用
  }
}

export const cardResourceCacheStats = async (): Promise<{
  bytes: number
  count: number
}> => {
  try {
    const db = await getDB()
    const [net, image, count] = await Promise.all([
      db.get('meta', 'net:total'),
      db.get('meta', 'image:total'),
      db.count('index'),
    ])
    return { bytes: (net?.bytes || 0) + (image?.bytes || 0), count }
  } catch {
    return { bytes: 0, count: 0 }
  }
}
