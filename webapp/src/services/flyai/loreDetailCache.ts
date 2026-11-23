/**
 * 内容库整卡/世界观本地缓存（IndexedDB）。
 *
 * 列表接口只回摘要 + 文件 md5；打开编辑器时若本地缓存的 md5 与列表一致，
 * 直接用本地整卡，不再下载十几 MB 的 JSON。md5 由服务端按文件 stat 缓存，
 * 文件被改写后摘要变化，自动落回网络并覆盖本地副本。
 *
 * 缓存不可用（隐私模式/配额不足）时静默降级，不影响正常请求。
 */

import { openDB, type DBSchema, type IDBPDatabase } from 'idb'

const DB_NAME = 'flyai-lore-details'
const DB_VERSION = 1
/** 整卡/世界观缓存总量上限：按写入时间 FIFO 淘汰 */
const CAP = 256 * 1024 * 1024

export type LoreDetailKind = 'character' | 'world'

type DetailEntry = {
  id: string
  md5: string
  data: unknown
  size: number
  savedAt: number
}

interface DetailDB extends DBSchema {
  details: {
    key: string
    value: DetailEntry
    indexes: { savedAt: number }
  }
  meta: { key: string; value: { key: string; bytes: number } }
}

let dbPromise: Promise<IDBPDatabase<DetailDB>> | null = null

const getDB = (): Promise<IDBPDatabase<DetailDB>> => {
  if (!dbPromise) {
    const open = () => {
      try {
        return openDB<DetailDB>(DB_NAME, DB_VERSION, {
          upgrade(db) {
            if (!db.objectStoreNames.contains('details')) {
              const store = db.createObjectStore('details', { keyPath: 'id' })
              store.createIndex('savedAt', 'savedAt')
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

const entryKey = (kind: LoreDetailKind, id: string): string => `${kind}:${id}`

/** 粗略字节数：只用于容量淘汰，不需要精确 */
const approxBytes = (data: unknown): number => {
  try {
    return JSON.stringify(data).length * 2
  } catch {
    return 0
  }
}

/** 读取本地整卡；md5 不一致视为失效（返回 null）。 */
export const readLoreDetail = async <T>(
  kind: LoreDetailKind,
  id: string,
  md5: string
): Promise<T | null> => {
  if (!id || !md5) {
    return null
  }
  try {
    const entry = await (await getDB()).get('details', entryKey(kind, id))
    if (!entry || entry.md5 !== md5) {
      return null
    }
    return entry.data as T
  } catch {
    return null
  }
}

export const writeLoreDetail = async (
  kind: LoreDetailKind,
  id: string,
  md5: string,
  data: unknown
): Promise<void> => {
  if (!id || !md5) {
    return
  }
  const size = approxBytes(data)
  if (size <= 0 || size > CAP) {
    return
  }
  try {
    const db = await getDB()
    const tx = db.transaction(['details', 'meta'], 'readwrite')
    const store = tx.objectStore('details')
    const metaStore = tx.objectStore('meta')
    const key = entryKey(kind, id)
    const old = await store.get(key)
    const meta = await metaStore.get('total')
    let total = (meta?.bytes || 0) - (old?.size || 0) + size

    await store.put({ id: key, md5, data, size, savedAt: Date.now() })

    if (total > CAP) {
      const all = await store.getAll()
      const victims = all
        .filter((entry) => entry.id !== key)
        .sort((a, b) => a.savedAt - b.savedAt)
      for (const victim of victims) {
        if (total <= CAP) {
          break
        }
        await store.delete(victim.id)
        total -= victim.size
      }
    }
    await metaStore.put({ key: 'total', bytes: Math.max(0, total) }, 'total')
    await tx.done
  } catch {
    // 配额/隐私模式：静默降级
  }
}

export const clearLoreDetailCache = async (): Promise<void> => {
  try {
    const db = await getDB()
    const tx = db.transaction(['details', 'meta'], 'readwrite')
    await tx.objectStore('details').clear()
    await tx.objectStore('meta').clear()
    await tx.done
  } catch {
    // 缓存本来不可用
  }
}

export const loreDetailCacheStats = async (): Promise<{
  bytes: number
  count: number
}> => {
  try {
    const db = await getDB()
    const [meta, count] = await Promise.all([
      db.get('meta', 'total'),
      db.count('details'),
    ])
    return { bytes: meta?.bytes || 0, count }
  } catch {
    return { bytes: 0, count: 0 }
  }
}
