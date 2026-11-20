import { describe, expect, it } from 'vitest'
import type { LoreEntry } from '../../services/flyai/types.js'
import { openingOptions } from './openingEntries.js'

const entry = (title: string, content: string): LoreEntry => ({
  title,
  content,
  pinned: false,
  keys: [],
  position: 'system',
  depth: 4,
  insert_order: 100,
  role: 'system',
  probability: 100,
})

describe('openingOptions', () => {
  it('only lists 开局/序章 entries with content', () => {
    const options = openingOptions([
      entry(' 序章', '雨夜'),
      entry('开局1', '殡仪馆'),
      entry('开局2', '   '),
      entry('其它设定', '不算'),
      entry('开局1', '重复'),
    ])
    expect(options.map((item) => item.title)).toEqual(['序章', '开局1'])
  })

  it('preview collapses whitespace and truncates', () => {
    const long = '一'.repeat(60)
    const [option] = openingOptions([entry('开局1', `  第一行\n\n第二行${long}`)])
    expect(option.preview.startsWith('第一行 第二行')).toBe(true)
    expect(option.preview.length).toBe(40)
  })

  it('handles missing tables', () => {
    expect(openingOptions(null)).toEqual([])
    expect(openingOptions(undefined)).toEqual([])
    expect(openingOptions([])).toEqual([])
  })
})
