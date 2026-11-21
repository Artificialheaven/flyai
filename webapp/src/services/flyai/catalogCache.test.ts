import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Cache = typeof import('./catalogCache.js')

let cache: Cache

beforeEach(async () => {
  // 每个用例拿一份全新的模块（缓存/监听表都是模块级单例）
  vi.resetModules()
  cache = await import('./catalogCache.js')
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('loadCatalog', () => {
  it('新鲜期内命中缓存，不再请求', async () => {
    const fetcher = vi.fn(async () => ['a'])
    await expect(cache.loadCatalog('characters', fetcher)).resolves.toEqual(['a'])
    await expect(cache.loadCatalog('characters', fetcher)).resolves.toEqual(['a'])
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(cache.peekCatalog('characters')).toEqual(['a'])
  })

  it('过期后重新请求并更新缓存', async () => {
    vi.useFakeTimers()
    let value = 1
    const fetcher = vi.fn(async () => value)
    await cache.loadCatalog('worlds', fetcher)
    value = 2
    vi.advanceTimersByTime(60_001)
    await expect(cache.loadCatalog('worlds', fetcher)).resolves.toBe(2)
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(cache.peekCatalog('worlds')).toBe(2)
  })

  it('force 跳过新鲜期', async () => {
    let value = 1
    const fetcher = vi.fn(async () => value)
    await cache.loadCatalog('presets', fetcher)
    value = 2
    await expect(cache.loadCatalog('presets', fetcher, { force: true })).resolves.toBe(2)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('同一 key 的并发请求合并为一次', async () => {
    let resolve!: (value: string[]) => void
    const fetcher = vi.fn(
      () =>
        new Promise<string[]>((done) => {
          resolve = done
        })
    )
    const first = cache.loadCatalog('characters', fetcher)
    const second = cache.loadCatalog('characters', fetcher)
    resolve(['x'])
    await expect(Promise.all([first, second])).resolves.toEqual([['x'], ['x']])
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('失败时回退旧数据；没有旧数据则抛出', async () => {
    const ok = vi.fn(async () => 'old')
    const bad = vi.fn(async () => {
      throw new Error('boom')
    })
    // 无旧数据：错误交给调用方
    await expect(cache.loadCatalog('worlds', bad)).rejects.toThrow('boom')

    await cache.loadCatalog('worlds', ok)
    vi.useFakeTimers()
    vi.advanceTimersByTime(60_001)
    // 有旧数据：静默回退，并恢复使用旧值
    await expect(cache.loadCatalog('worlds', bad)).resolves.toBe('old')
    expect(cache.peekCatalog('worlds')).toBe('old')
  })
})

describe('invalidateCatalog', () => {
  it('失效后下次读取重新请求', async () => {
    const fetcher = vi.fn(async () => ['a'])
    await cache.loadCatalog('characters', fetcher)
    cache.invalidateCatalog('characters')
    expect(cache.peekCatalog('characters')).toBeUndefined()
    await cache.loadCatalog('characters', fetcher)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('失效前发出的在途响应不写回缓存', async () => {
    let resolveOld!: (value: string) => void
    const oldFetch = vi.fn(
      () =>
        new Promise<string>((done) => {
          resolveOld = done
        })
    )
    const stale = cache.loadCatalog('characters', oldFetch)
    cache.invalidateCatalog('characters')
    await expect(cache.loadCatalog('characters', async () => 'new')).resolves.toBe('new')
    resolveOld('old')
    await expect(stale).resolves.toBe('old')
    // 旧响应已被版本号拦下，缓存仍是新数据
    expect(cache.peekCatalog('characters')).toBe('new')
  })
})

describe('updateCatalog / subscribeCatalog', () => {
  it('就地更新缓存并通知订阅者', async () => {
    await cache.loadCatalog('presets', async () => ({ presets: [] as string[], activeId: 'a' }))
    const seen: unknown[] = []
    const unsubscribe = cache.subscribeCatalog('presets', (data) => seen.push(data))
    cache.updateCatalog<{ presets: string[]; activeId: string }>('presets', (current) => ({
      ...current,
      activeId: 'b',
    }))
    expect(cache.peekCatalog('presets')).toEqual({ presets: [], activeId: 'b' })
    expect(seen).toEqual([{ presets: [], activeId: 'b' }])

    unsubscribe()
    cache.updateCatalog<{ presets: string[]; activeId: string }>('presets', (current) => ({
      ...current,
      activeId: 'c',
    }))
    // 取消订阅后不再收到通知，但缓存仍更新
    expect(seen).toHaveLength(1)
    expect(cache.peekCatalog('presets')).toEqual({ presets: [], activeId: 'c' })
  })

  it('缓存为空时 update 不创建条目', () => {
    cache.updateCatalog('worlds', () => 'anything')
    expect(cache.peekCatalog('worlds')).toBeUndefined()
  })
})
