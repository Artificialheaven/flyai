import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearRunMarker,
  MARKER_TTL_MS,
  markerStorageKey,
  readRunMarker,
  saveRunMarker,
  type RunMarker,
} from './runMarker.js'

const uid = 'u1'
const marker = (over: Partial<RunMarker> = {}): RunMarker => ({
  run_id: 'r1',
  sid: 's1',
  tid: 't1',
  seq: 7,
  ts: Date.now(),
  ...over,
})

describe('runMarker', () => {
  beforeEach(() => localStorage.clear())

  it('key 带用户 id，未登录兜底 x', () => {
    expect(markerStorageKey('u1')).toBe('flyai-run-u1')
    expect(markerStorageKey(null)).toBe('flyai-run-x')
  })

  it('保存后能原样读回', () => {
    // 同一 fixture 只取一次时间戳，两次 marker() 的 Date.now() 可能差 1ms
    const saved = marker()
    saveRunMarker(uid, saved)
    expect(readRunMarker(uid)).toEqual(saved)
  })

  it('超过 30 分钟 TTL 视为无效并清除', () => {
    saveRunMarker(uid, marker({ ts: Date.now() - MARKER_TTL_MS - 1000 }))
    expect(readRunMarker(uid)).toBeNull()
    expect(localStorage.getItem(markerStorageKey(uid))).toBeNull()
  })

  it('脏 JSON / 缺字段返回 null', () => {
    localStorage.setItem(markerStorageKey(uid), '{not-json')
    expect(readRunMarker(uid)).toBeNull()
    localStorage.setItem(markerStorageKey(uid), JSON.stringify({ run_id: 'r1' }))
    expect(readRunMarker(uid)).toBeNull()
  })

  it('clearRunMarker 删除且不抛错', () => {
    saveRunMarker(uid, marker())
    clearRunMarker(uid)
    expect(readRunMarker(uid)).toBeNull()
    expect(() => clearRunMarker('nobody')).not.toThrow()
  })

  it('不同用户的标记互不干扰', () => {
    saveRunMarker('u1', marker({ run_id: 'r1' }))
    saveRunMarker('u2', marker({ run_id: 'r2' }))
    expect(readRunMarker('u1')?.run_id).toBe('r1')
    expect(readRunMarker('u2')?.run_id).toBe('r2')
  })
})
