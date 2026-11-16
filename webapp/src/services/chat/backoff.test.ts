import { describe, expect, it } from 'vitest'
import { backoffDelay, MAX_RECONNECT_ATTEMPTS } from './backoff.js'

describe('backoffDelay', () => {
  it('与旧版一致：500 起步、1.5 倍递增、8 秒封顶', () => {
    expect(backoffDelay(1)).toBe(500)
    expect(backoffDelay(2)).toBe(750)
    expect(backoffDelay(3)).toBe(1125)
    expect(backoffDelay(4)).toBe(1688)
    expect(backoffDelay(5)).toBe(2531)
    expect(backoffDelay(6)).toBe(3797)
    expect(backoffDelay(7)).toBe(5695)
    // 500 * 1.5^7 ≈ 8543 → 封顶 8000
    expect(backoffDelay(8)).toBe(8000)
    expect(backoffDelay(20)).toBe(8000)
  })

  it('最多重连 8 次', () => {
    expect(MAX_RECONNECT_ATTEMPTS).toBe(8)
  })
})
