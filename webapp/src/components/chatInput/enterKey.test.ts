import { describe, expect, it } from 'vitest'
import { resolveEnterAction } from './enterKey.js'

const enter = (patch: Record<string, unknown> = {}) => ({
  key: 'Enter',
  ...patch,
})

describe('resolveEnterAction', () => {
  it('Enter 发送模式：回车发送，Ctrl/Cmd+回车换行', () => {
    expect(resolveEnterAction(enter(), true)).toBe('send')
    expect(resolveEnterAction(enter({ ctrlKey: true }), true)).toBe('newline')
    expect(resolveEnterAction(enter({ metaKey: true }), true)).toBe('newline')
  })

  it('Enter 发送模式：Shift+回车交给浏览器换行', () => {
    expect(resolveEnterAction(enter({ shiftKey: true }), true)).toBe('none')
  })

  it('回车换行模式：回车换行，Ctrl/Cmd+回车发送', () => {
    expect(resolveEnterAction(enter(), false)).toBe('none')
    expect(resolveEnterAction(enter({ ctrlKey: true }), false)).toBe('send')
    expect(resolveEnterAction(enter({ metaKey: true }), false)).toBe('send')
    expect(resolveEnterAction(enter({ shiftKey: true }), false)).toBe('none')
  })

  it('非 Enter 与输入法组合中不处理', () => {
    expect(resolveEnterAction({ key: 'a' }, true)).toBe('none')
    expect(resolveEnterAction(enter({ isComposing: true }), true)).toBe('none')
  })
})
