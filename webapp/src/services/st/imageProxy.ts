/**
 * 外链插图的服务端代理（方案 4）。
 *
 * 代理接口要求 Bearer 令牌，`<img src>` 无法直接携带请求头，因此这里用
 * fetch 取回图片字节并转成 blob: URL 再交给 `<img>`。同一地址按载荷缓存，
 * 避免滚动/重渲时重复请求；缓存超限时回收最早的 blob URL。
 */

import {
  readCachedImage,
  writeCachedImage,
} from '../flyai/cardResourceCache.js'
import { apiUrl, authHeaders } from '../flyai/client.js'

const MAX_CACHE = 64

const cache = new Map<string, string>()
const pending = new Map<string, Promise<string>>()

/** 协议相对地址（//host/x）按当前站点协议补全，后端只接受 http/https。 */
const normalize = (url: string): string =>
  url.startsWith('//') ? `https:${url}` : url

const remember = (remote: string, blobUrl: string): void => {
  cache.set(remote, blobUrl)
  if (cache.size <= MAX_CACHE) {
    return
  }
  const oldest = cache.keys().next()
  if (!oldest.done) {
    const stale = cache.get(oldest.value)
    cache.delete(oldest.value)
    if (stale) {
      URL.revokeObjectURL(stale)
    }
  }
}

/** 取回代理图片的 blob URL；失败时抛出，调用方降级为 alt 文本。 */
export const fetchProxyImage = (remote: string): Promise<string> => {
  const key = normalize(remote || '')
  const hit = cache.get(key)
  if (hit) {
    return Promise.resolve(hit)
  }
  const inflight = pending.get(key)
  if (inflight) {
    return inflight
  }
  const task = (async () => {
    // 先读 IndexedDB：之前下载过的图片不再请求服务器
    const saved = await readCachedImage(key)
    if (saved) {
      const blobUrl = URL.createObjectURL(saved)
      remember(key, blobUrl)
      return blobUrl
    }
    const resp = await fetch(
      apiUrl(`/api/st/img-proxy?url=${encodeURIComponent(key)}`),
      { headers: authHeaders() }
    )
    if (!resp.ok) {
      throw new Error(`图片代理失败（HTTP ${resp.status}）`)
    }
    const blob = await resp.blob()
    const blobUrl = URL.createObjectURL(blob)
    remember(key, blobUrl)
    void writeCachedImage(key, blob)
    return blobUrl
  })()
  pending.set(key, task)
  task
    .catch(() => undefined)
    .finally(() => {
      pending.delete(key)
    })
  return task
}

/** 清空本页内存缓存（IndexedDB 由 cardResourceCache 单独清理）。 */
export const clearImageProxyCache = (): void => {
  for (const url of cache.values()) {
    URL.revokeObjectURL(url)
  }
  cache.clear()
  pending.clear()
}
