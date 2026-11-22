import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchProxyImage } from './imageProxy.js'

const okResponse = () => ({ ok: true, status: 200, blob: async () => new Blob(['x']) })
const revoked: string[] = []
let created = 0

beforeEach(() => {
  created = 0
  revoked.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => okResponse())
  )
  vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:${(created += 1)}`)
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation((url: string) => {
    revoked.push(url)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('fetchProxyImage', () => {
  it('取回 blob URL 并按地址缓存', async () => {
    const url = await fetchProxyImage('https://cdn.example.com/a.png')
    expect(url).toBe('blob:1')
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `/api/st/img-proxy?url=${encodeURIComponent('https://cdn.example.com/a.png')}`
    )
    // 再次请求命中缓存，不再发网络请求
    await expect(fetchProxyImage('https://cdn.example.com/a.png')).resolves.toBe('blob:1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('协议相对地址按 https 补全', async () => {
    await fetchProxyImage('//cdn.example.com/b.png')
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      encodeURIComponent('https://cdn.example.com/b.png')
    )
  })

  it('同一地址并发请求只发一次', async () => {
    let resolveFetch: (value: unknown) => void = () => undefined
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            resolveFetch = resolve
          })
      )
    )
    const first = fetchProxyImage('https://cdn.example.com/c.png')
    const second = fetchProxyImage('https://cdn.example.com/c.png')
    // 先等本地缓存查询结束、真正发起 fetch，再回包
    await vi.waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(1)
    })
    resolveFetch(okResponse())
    await expect(first).resolves.toBe('blob:1')
    await expect(second).resolves.toBe('blob:1')
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('非 2xx 时抛错且不缓存', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 502 }))
    )
    await expect(fetchProxyImage('https://cdn.example.com/d.png')).rejects.toThrow('502')
    await expect(fetchProxyImage('https://cdn.example.com/d.png')).rejects.toThrow('502')
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('缓存超限时回收最早的 blob URL', async () => {
    vi.resetModules()
    const { fetchProxyImage: fresh } = await import('./imageProxy.js')
    for (let i = 0; i < 65; i += 1) {
      await fresh(`https://cdn.example.com/evict-${i}.png`)
    }
    expect(revoked).toEqual(['blob:1'])
  })
})
