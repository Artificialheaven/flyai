import { describe, expect, it } from 'vitest'
import type { CardAppCapabilities } from './cardApp.js'
import {
  CARD_CHARACTER_FIELDS,
  MAX_CARD_CHARACTER_BYTES,
  MAX_CARD_SEND_TEXT,
  MAX_CARD_VARS_OPS,
  MAX_CARD_VARS_PATH,
  MAX_CARD_WORLD_ENTRIES,
  cardJsonSize,
  checkCardIntent,
  createCardActionLimiter,
  pickCardCharacterFields,
  resolveCapabilities,
} from './cardActions.js'

const ALL: CardAppCapabilities = { actions: true, write: true, net: true }
const NONE: CardAppCapabilities = { actions: false, write: false, net: false }
const ACTION_ONLY: CardAppCapabilities = { actions: true, write: false, net: false }
const WRITE_ONLY: CardAppCapabilities = { actions: false, write: true, net: false }

const failCode = (input: unknown, capabilities = ALL): string | null => {
  const result = checkCardIntent(input, capabilities)
  return result.ok ? null : result.code
}

describe('checkCardIntent：动作', () => {
  it('能力关闭时一律拒绝', () => {
    expect(failCode({ kind: 'action', action: 'send', text: 'hi' }, ACTION_ONLY)).toBeNull()
    expect(failCode({ kind: 'action', action: 'send', text: 'hi' }, NONE)).toBe('unavailable')
    expect(failCode({ kind: 'action', action: 'continue' }, NONE)).toBe('unavailable')
  })

  it('只接受三种动作，发送必须有非空文本', () => {
    const send = checkCardIntent({ kind: 'action', action: 'send', text: '你好' }, ALL)
    expect(send).toEqual({
      ok: true,
      intent: { kind: 'action', action: 'send', text: '你好' },
    })
    const cont = checkCardIntent({ kind: 'action', action: 'continue' }, ALL)
    expect(cont).toEqual({ ok: true, intent: { kind: 'action', action: 'continue' } })

    expect(failCode({ kind: 'action', action: 'delete' })).toBe('unavailable')
    expect(failCode({ kind: 'action', action: 'send', text: '   ' })).toBe('unavailable')
    expect(failCode({ kind: 'action', action: 'send' })).toBe('unavailable')
  })

  it('发送文本超限按动作失败上报', () => {
    const text = 'x'.repeat(MAX_CARD_SEND_TEXT + 1)
    expect(failCode({ kind: 'action', action: 'send', text })).toBe('action-failed')
    expect(
      failCode({ kind: 'action', action: 'send', text: 'x'.repeat(MAX_CARD_SEND_TEXT) })
    ).toBeNull()
  })

  it('非对象与未知 kind 拒绝', () => {
    expect(failCode(null)).toBe('unavailable')
    expect(failCode('send')).toBe('unavailable')
    expect(failCode({ kind: 'network' })).toBe('unavailable')
  })
})

describe('checkCardIntent：变量', () => {
  it('仅 write 能力可用', () => {
    expect(failCode({ kind: 'vars', mode: 'replace', stat_data: {} }, ACTION_ONLY)).toBe(
      'unavailable'
    )
  })

  it('patch 校验 op 白名单、路径长度与数量', () => {
    const ok = checkCardIntent(
      {
        kind: 'vars',
        mode: 'patch',
        ops: [
          { op: 'add', path: '/hp', value: 1 },
          { op: 'test', path: '/hp', value: 1 },
        ],
      },
      WRITE_ONLY
    )
    expect(ok.ok).toBe(true)

    expect(failCode({ kind: 'vars', mode: 'patch', ops: [] }, WRITE_ONLY)).toBe('unavailable')
    expect(
      failCode({ kind: 'vars', mode: 'patch', ops: [{ op: 'merge', path: '/hp' }] }, WRITE_ONLY)
    ).toBe('unavailable')
    expect(
      failCode({ kind: 'vars', mode: 'patch', ops: [{ op: 'add' }] }, WRITE_ONLY)
    ).toBe('unavailable')
    const longPath = `/${'a'.repeat(MAX_CARD_VARS_PATH)}`
    expect(
      failCode(
        { kind: 'vars', mode: 'patch', ops: [{ op: 'add', path: longPath }] },
        WRITE_ONLY
      )
    ).toBe('unavailable')
    const many = Array.from({ length: MAX_CARD_VARS_OPS + 1 }, (_unused, index) => ({
      op: 'add',
      path: `/k${index}`,
      value: index,
    }))
    expect(failCode({ kind: 'vars', mode: 'patch', ops: many }, WRITE_ONLY)).toBe(
      'action-failed'
    )
  })

  it('patch 序列化超限拒绝', () => {
    const ops = [{ op: 'add', path: '/big', value: 'x'.repeat(150 * 1024) }]
    expect(failCode({ kind: 'vars', mode: 'patch', ops }, WRITE_ONLY)).toBe('action-failed')
  })

  it('replace 只接受对象根，且有大小上限', () => {
    const ok = checkCardIntent(
      { kind: 'vars', mode: 'replace', stat_data: { hp: 1 } },
      WRITE_ONLY
    )
    expect(ok).toEqual({ ok: true, intent: { kind: 'vars', mode: 'replace', stat_data: { hp: 1 } } })

    expect(failCode({ kind: 'vars', mode: 'replace', stat_data: [1] }, WRITE_ONLY)).toBe(
      'unavailable'
    )
    expect(failCode({ kind: 'vars', mode: 'replace' }, WRITE_ONLY)).toBe('unavailable')
    expect(
      failCode(
        { kind: 'vars', mode: 'replace', stat_data: { big: 'x'.repeat(150 * 1024) } },
        WRITE_ONLY
      )
    ).toBe('action-failed')
    expect(failCode({ kind: 'vars', mode: 'merge' }, WRITE_ONLY)).toBe('unavailable')
  })
})

describe('checkCardIntent：世界书与角色', () => {
  it('世界书只接受数组，条目数与体积受限', () => {
    const ok = checkCardIntent(
      { kind: 'world', entries: [{ title: 'A', content: 'B' }] },
      WRITE_ONLY
    )
    expect(ok.ok).toBe(true)

    expect(failCode({ kind: 'world', entries: {} }, WRITE_ONLY)).toBe('unavailable')
    expect(
      failCode(
        {
          kind: 'world',
          entries: Array.from({ length: MAX_CARD_WORLD_ENTRIES + 1 }, () => ({})),
        },
        WRITE_ONLY
      )
    ).toBe('action-failed')
    expect(
      failCode({ kind: 'world', entries: [{ content: 'x'.repeat(2 * 1024 * 1024) }] }, WRITE_ONLY)
    ).toBe('action-failed')
  })

  it('角色字段只接受白名单，且不能为空', () => {
    const ok = checkCardIntent(
      { kind: 'character', fields: { name: '甲', intro: '介绍' } },
      WRITE_ONLY
    )
    expect(ok).toEqual({
      ok: true,
      intent: { kind: 'character', fields: { name: '甲', intro: '介绍' } },
    })

    for (const key of ['id', 'avatar', 'public', 'parent_id', 'regex_scripts']) {
      expect(failCode({ kind: 'character', fields: { [key]: 1 } }, WRITE_ONLY)).toBe(
        'action-failed'
      )
    }
    expect(failCode({ kind: 'character', fields: {} }, WRITE_ONLY)).toBe('unavailable')
    expect(failCode({ kind: 'character', fields: 'name' }, WRITE_ONLY)).toBe('unavailable')
    expect(
      failCode(
        { kind: 'character', fields: { system_prompt: 'x'.repeat(MAX_CARD_CHARACTER_BYTES) } },
        WRITE_ONLY
      )
    ).toBe('action-failed')
  })

  it('pickCardCharacterFields 只挑白名单字段', () => {
    expect(pickCardCharacterFields(null)).toBeNull()
    expect(pickCardCharacterFields({ id: 1, public: true })).toBeNull()
    expect(
      pickCardCharacterFields({ name: '甲', background: '背景', id: 1, avatar: 'a.png' })
    ).toEqual({ name: '甲', background: '背景' })
    expect(CARD_CHARACTER_FIELDS).toContain('alternate_greetings')
  })
})

describe('resolveCapabilities', () => {
  it('动作要求可编辑、末条且非流式', () => {
    expect(resolveCapabilities(ALL, { editable: true, isLast: true, streaming: false })).toEqual(
      ALL
    )
    expect(
      resolveCapabilities(ALL, { editable: true, isLast: false, streaming: false }).actions
    ).toBe(false)
    expect(
      resolveCapabilities(ALL, { editable: true, isLast: true, streaming: true }).actions
    ).toBe(false)
    expect(
      resolveCapabilities(ALL, { editable: false, isLast: true, streaming: false }).actions
    ).toBe(false)
  })

  it('写入与网络不要求末条，但可编辑且非流式', () => {
    const caps = resolveCapabilities(ALL, { editable: true, isLast: false, streaming: false })
    expect(caps.write).toBe(true)
    expect(caps.net).toBe(true)
    const blocked = resolveCapabilities(ALL, { editable: false, isLast: true, streaming: false })
    expect(blocked).toEqual(NONE)
    const streaming = resolveCapabilities(ALL, {
      editable: true,
      isLast: true,
      streaming: true,
    })
    expect(streaming).toEqual(NONE)
  })
})

describe('createCardActionLimiter', () => {
  it('默认限制相邻动作间隔 2 秒', () => {
    const limiter = createCardActionLimiter()
    expect(limiter.tryAcquire(0)).toBe(true)
    expect(limiter.tryAcquire(0)).toBe(false)
    expect(limiter.tryAcquire(1999)).toBe(false)
    expect(limiter.tryAcquire(2000)).toBe(true)
    expect(limiter.snapshot()).toEqual([0, 2000])
  })

  it('每分钟窗口内限次，过期后释放额度', () => {
    const limiter = createCardActionLimiter({ minIntervalMs: 0, perMinute: 3 })
    expect(limiter.tryAcquire(0)).toBe(true)
    expect(limiter.tryAcquire(1)).toBe(true)
    expect(limiter.tryAcquire(2)).toBe(true)
    expect(limiter.tryAcquire(3)).toBe(false)
    // 最早的时间戳滑出 60 秒窗口后重新可用
    expect(limiter.tryAcquire(60_000)).toBe(true)
    expect(limiter.snapshot()).toEqual([1, 2, 60_000])
  })
})

describe('cardJsonSize', () => {
  it('正常序列化返回长度，异常返回 null', () => {
    expect(cardJsonSize({ a: 1 })).toBe(7)
    const circular: Record<string, unknown> = {}
    circular.self = circular
    expect(cardJsonSize(circular)).toBeNull()
    expect(cardJsonSize(undefined)).toBeNull()
  })
})
