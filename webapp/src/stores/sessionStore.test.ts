import { describe, expect, it } from 'vitest'
import { filterSessions, sortSessions } from './sessionStore.js'
import type { SessionSummary } from '../types/session.js'

const make = (id: string, over: Partial<SessionSummary> = {}): SessionSummary => ({
  id,
  title: id,
  pinned: false,
  mode: 'normal',
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-01T00:00:00',
  ...over,
})

describe('sortSessions', () => {
  it('置顶在前，组内按 updated_at 倒序', () => {
    const sorted = sortSessions([
      make('a', { updated_at: '2026-03-01T00:00:00' }),
      make('b', { pinned: true, updated_at: '2026-01-01T00:00:00' }),
      make('c', { updated_at: '2026-05-01T00:00:00' }),
      make('d', { pinned: true, updated_at: '2026-02-01T00:00:00' }),
    ])
    expect(sorted.map((s) => s.id)).toEqual(['d', 'b', 'c', 'a'])
  })

  it('不修改原数组', () => {
    const input = [make('a'), make('b')]
    const sorted = sortSessions(input)
    expect(sorted).not.toBe(input)
    expect(input).toHaveLength(2)
  })
})

describe('filterSessions', () => {
  const sessions = [make('a', { title: '小爱聊天记录' }), make('b', { title: '日常' })]

  it('按标题包含过滤且忽略大小写', () => {
    expect(filterSessions(sessions, '小爱').map((s) => s.id)).toEqual(['a'])
    expect(filterSessions(sessions, '  ')).toBe(sessions)
  })
})
