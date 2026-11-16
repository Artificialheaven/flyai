import { describe, expect, it } from 'vitest'
import { buildHistoryItems } from './history.js'
import type { Message } from '../../types/session.js'

let seq = 0
const msg = (over: Partial<Message> & Pick<Message, 'role'>): Message => ({
  id: `m${++seq}`,
  content: null,
  thinking: null,
  tool_calls: null,
  tool_call_id: null,
  name: null,
  ok: null,
  hidden: null,
  actor_id: null,
  recall: null,
  images: null,
  tombstone: null,
  ...over,
})

describe('buildHistoryItems', () => {
  it('普通一轮：用户气泡 + 单条 assistant 链', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: '你好' }),
      msg({ role: 'assistant', content: '在的', name: '小爱' }),
    ])
    expect(items.map((item) => item.type)).toEqual(['user', 'assistant'])
    const chain = items[1]
    expect(chain.type === 'assistant' && chain.contents).toEqual(['在的'])
  })

  it('assistant(tool_calls) → tool → assistant 合并为一条链并挂上工具结果', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: '搜一下' }),
      msg({ role: 'assistant', tool_calls: [{ id: 't1', name: 'web_search' }] }),
      msg({ role: 'tool', tool_call_id: 't1', name: 'web_search', ok: true, content: '结果正文' }),
      msg({ role: 'assistant', content: '搜到了' }),
    ])
    expect(items).toHaveLength(2)
    const chain = items[1]
    expect(chain.type).toBe('assistant')
    if (chain.type === 'assistant') {
      expect(chain.tools).toEqual([
        { id: 't1', name: 'web_search', ok: true, content: '结果正文', hidden: false },
      ])
      expect(chain.contents).toEqual(['搜到了'])
    }
  })

  it('隐藏 assistant 跳过，但带 tool_calls 的隐藏消息保留', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: 'x' }),
      msg({ role: 'assistant', hidden: true, content: '隐形' }),
      msg({
        role: 'assistant',
        hidden: true,
        content: null,
        tool_calls: [{ id: 't2', name: 'generate_image' }],
      }),
      msg({ role: 'tool', tool_call_id: 't2', name: 'generate_image', ok: true, content: null }),
    ])
    expect(items).toHaveLength(2)
    const chain = items[1]
    expect(chain.type).toBe('assistant')
    if (chain.type === 'assistant') {
      expect(chain.tools.map((tool) => tool.id)).toEqual(['t2'])
    }
  })

  it('游离 tool 消息无挂载点时不展示', () => {
    const items = buildHistoryItems([
      msg({ role: 'tool', tool_call_id: 'orphan', content: '没人要' }),
      msg({ role: 'user', content: 'hi' }),
    ])
    expect(items.map((item) => item.type)).toEqual(['user'])
  })

  it('墓碑单独成行并截断后续回复槽位', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', tombstone: { content: '撤回的话', images: null, name: null } }),
      msg({ role: 'assistant', content: '不该出现' }),
      msg({ role: 'user', content: '新一轮' }),
    ])
    expect(items.map((item) => item.type)).toEqual(['tombstone', 'user'])
  })

  it('不同发言者的连续 assistant 不合并', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: '大家好' }),
      msg({ role: 'assistant', actor_id: 'a1', name: '小爱', content: '一' }),
      msg({ role: 'assistant', actor_id: 'a2', name: '阿绫', content: '二' }),
    ])
    const chains = items.filter((item) => item.type === 'assistant')
    expect(chains).toHaveLength(2)
  })

  it('thinking 与 recall 折叠进链', () => {
    const items = buildHistoryItems([
      msg({ role: 'user', content: '想想' }),
      msg({
        role: 'assistant',
        thinking: '内心独白',
        content: '结论',
        recall: [{ source: '记忆簿', text: '旧片段' }],
      }),
    ])
    const chain = items[1]
    if (chain.type !== 'assistant') throw new Error('expected assistant chain')
    expect(chain.thinking).toBe('内心独白')
    expect(chain.recalls).toHaveLength(1)
  })
})
