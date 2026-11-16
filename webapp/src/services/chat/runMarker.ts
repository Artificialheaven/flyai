/**
 * 运行标记：localStorage 里记录未结束的 run，刷新页面/重开 App 后能找回。
 * 对照旧版 chat-stream.js 的 saveRunMarker/loadResumeMarker：
 * key=flyai-run-{uid}，TTL 30 分钟（服务端运行终态只保留 10 分钟，标记宽限到 30 分由探活 404 兜底）。
 */

export type RunMarker = {
  run_id: string
  sid: string
  tid: string
  seq: number
  ts: number
}

export const MARKER_TTL_MS = 30 * 60 * 1000

export const markerStorageKey = (userId: string | number | null | undefined): string =>
  `flyai-run-${userId || 'x'}`

const getStorage = (): Storage | undefined =>
  typeof globalThis !== 'undefined' && (globalThis as { localStorage?: Storage }).localStorage
    ? (globalThis as { localStorage: Storage }).localStorage
    : undefined

export const saveRunMarker = (userId: string | number | null | undefined, marker: RunMarker): void => {
  const storage = getStorage()
  if (!storage || !marker.run_id) return
  try {
    storage.setItem(markerStorageKey(userId), JSON.stringify(marker))
  } catch {
    /* 隐私模式/配额满：标记尽力而为 */
  }
}

export const readRunMarker = (userId: string | number | null | undefined): RunMarker | null => {
  const storage = getStorage()
  if (!storage) return null
  let raw: string | null = null
  try {
    raw = storage.getItem(markerStorageKey(userId))
  } catch {
    return null
  }
  if (!raw) return null
  let marker: RunMarker | null = null
  try {
    const parsed = JSON.parse(raw) as Partial<RunMarker>
    if (parsed && typeof parsed.run_id === 'string' && typeof parsed.sid === 'string') {
      marker = {
        run_id: parsed.run_id,
        sid: parsed.sid,
        tid: typeof parsed.tid === 'string' ? parsed.tid : '',
        seq: typeof parsed.seq === 'number' ? parsed.seq : 0,
        ts: typeof parsed.ts === 'number' ? parsed.ts : 0,
      }
    }
  } catch {
    return null
  }
  if (!marker) return null
  if (Date.now() - marker.ts > MARKER_TTL_MS) {
    clearRunMarker(userId)
    return null
  }
  return marker
}

export const clearRunMarker = (userId: string | number | null | undefined): void => {
  const storage = getStorage()
  if (!storage) return
  try {
    storage.removeItem(markerStorageKey(userId))
  } catch {
    /* ignore */
  }
}
