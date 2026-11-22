import { describe, expect, it } from 'vitest'
import {
  isCacheableNetResponse,
  netCacheKey,
  readCachedNetProxy,
} from './cardResourceCache.js'
import type { NetProxyResult } from './st.js'

const result = (headers: Record<string, string>, patch: Partial<NetProxyResult> = {}): NetProxyResult => ({
  status: 200,
  headers,
  body_base64: 'AAAA',
  truncated: false,
  ...patch,
})

describe('卡片资源本地缓存', () => {
  it('只缓存 GET；头部大小写与 hash 归一', () => {
    const a = netCacheKey({
      method: 'get',
      url: 'https://cdn.example.com/a.png#x',
      headers: { Accept: 'image/*' },
    })
    const b = netCacheKey({
      method: 'GET',
      url: 'https://cdn.example.com/a.png',
      headers: { accept: 'image/*' },
    })
    expect(a).toBe(b)
    expect(netCacheKey({ method: 'POST', url: 'https://x/y' })).toBe('')
    expect(netCacheKey({ method: 'GET', url: '' })).toBe('')
  })

  it('缓存静态类型且完整的 200 响应', () => {
    expect(isCacheableNetResponse(result({ 'content-type': 'image/png' }), 'https://x/a')).toBe(true)
    expect(
      isCacheableNetResponse(result({ 'content-type': 'application/javascript' }), 'https://x/a.js')
    ).toBe(true)
    expect(
      isCacheableNetResponse(result({ 'content-type': 'text/css; charset=utf-8' }), 'https://x/a')
    ).toBe(true)
    // JSON 只在明显是静态文件时缓存
    expect(
      isCacheableNetResponse(result({ 'content-type': 'application/json' }), 'https://x/data.json')
    ).toBe(true)
    expect(
      isCacheableNetResponse(result({ 'content-type': 'application/json' }), 'https://x/api/state')
    ).toBe(false)
    // HTML、失败、截断不缓存
    expect(isCacheableNetResponse(result({ 'content-type': 'text/html' }), 'https://x/')).toBe(false)
    expect(isCacheableNetResponse(result({ 'content-type': 'image/png' }, { status: 404 }), 'https://x/a')).toBe(false)
    expect(isCacheableNetResponse(result({ 'content-type': 'image/png' }, { truncated: true }), 'https://x/a')).toBe(false)
    // 没有类型时按扩展名兜底
    expect(isCacheableNetResponse(result({}), 'https://x/a.png?x=1')).toBe(true)
  })

  it('无 IndexedDB 时静默降级', async () => {
    if (typeof indexedDB !== 'undefined') {
      return
    }
    await expect(
      readCachedNetProxy({ method: 'GET', url: 'https://x/a.png' })
    ).resolves.toBeNull()
  })
})
