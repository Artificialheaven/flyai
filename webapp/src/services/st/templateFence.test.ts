import { describe, expect, it } from 'vitest'
import { splitFencedSegments } from './templatePending.js'

describe('EJS output code fences', () => {
  it('keeps fenced blocks literal and outside text renderable', () => {
    const text = 'A <%= 1 %>\n```ejs\n<%= 2 %>\n```\nB <%= 3 %>'
    const segments = splitFencedSegments(text)
    expect(segments.map((item) => item.literal)).toEqual([false, true, false])
    expect(segments[1].text).toContain('<%= 2 %>')
    expect(segments.map((item) => item.text).join('\n')).toBe(text)
  })

  it('supports tilde fences and unterminated blocks', () => {
    const text = 'x\n~~~\n<%= hidden %>\ny'
    const segments = splitFencedSegments(text)
    expect(segments[0].literal).toBe(false)
    expect(segments[1].literal).toBe(true)
    expect(segments[1].text).toContain('<%= hidden %>')
  })
})
