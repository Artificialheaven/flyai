import { describe, expect, it } from 'vitest'
import { decodeChatEvent } from '../api/events.js'
import { createLiveRun, reduceRun, reduceRunAll } from './streamReducer.js'

const ev = (type: string, data: unknown) => decodeChatEvent(type, data)

describe('decodeChatEvent', () => {
  it('未知事件返回 null', () => {
    expect(decodeChatEvent('something_new', {})).toBeNull()
  })
  it('error 缺省按致命处理，fatal:false 显式非致命', () => {
    expect(decodeChatEvent('error', { message: 'x' })).toMatchObject({ fatal: true })
    expect(decodeChatEvent('error', { message: 'x', fatal: false })).toMatchObject({ fatal: false })
  })
  it('done 缺省 terminal=true，terminal:false 保留群聊语义', () => {
    expect(decodeChatEvent('done', {})).toMatchObject({ terminal: true })
    expect(decodeChatEvent('done', { terminal: false })).toMatchObject({ terminal: false })
  })
  it('tool_calls 缺 calls 不炸、脏数据被兜底', () => {
    expect(decodeChatEvent('tool_calls', null)).toMatchObject({ calls: [] })
    expect(decodeChatEvent('tool_end', { id: 1, ok: 1 })).toMatchObject({
      id: '1',
      ok: true,
    })
  })
})

describe('reduceRun 单聊序列', () => {
  it('完整一轮：思考→工具→正文→done', () => {
    const state = reduceRunAll([
      ev('message_start', { session_id: 's1', thread_id: 't1' })!,
      ev('speaker', { name: '小爱' })!,
      ev('thinking_delta', { text: '先想想' })!,
      ev('thinking_delta', { text: '…' })!,
      ev('tool_calls', { calls: [{ id: 'c1', name: 'web_search' }] })!,
      ev('tool_start', { id: 'c1', status_text: '正在搜索' })!,
      ev('tool_end', { id: 'c1', ok: true, preview: '结果摘要' })!,
      ev('content_delta', { text: '你好' })!,
      ev('content_delta', { text: '世界' })!,
      ev('done', { usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } })!,
    ])
    expect(state.sessionId).toBe('s1')
    expect(state.threadId).toBe('t1')
    expect(state.terminal).toBe(true)
    expect(state.assistants).toHaveLength(1)
    const a = state.assistants[0]
    expect(a.name).toBe('小爱')
    expect(a.thinking).toBe('先想想…')
    expect(a.content).toBe('你好世界')
    expect(a.tools[0]).toMatchObject({ id: 'c1', name: 'web_search', status: 'done', preview: '结果摘要' })
    expect(state.usage?.total_tokens).toBe(15)
  })

  it('没有 speaker 先来正文时补默认 AI 卡', () => {
    const state = reduceRunAll([ev('content_delta', { text: 'hi' })!])
    expect(state.assistants).toHaveLength(1)
    expect(state.assistants[0].name).toBe('AI')
    expect(state.assistants[0].content).toBe('hi')
  })

  it('content_replace 整体覆盖回声噪声', () => {
    const state = reduceRunAll([
      ev('content_delta', { text: '脏数据' })!,
      ev('content_replace', { text: '干净正文' })!,
    ])
    expect(state.assistants[0].content).toBe('干净正文')
  })
})

describe('reduceRun 群聊序列', () => {
  it('terminal:false 后第二位 speaker 新开卡片，终态 done 才收尾', () => {
    const state = reduceRunAll([
      ev('speaker', { member_id: 'm1', name: '甲' })!,
      ev('content_delta', { text: '甲说' })!,
      ev('done', { terminal: false })!,
      ev('speaker', { member_id: 'm2', name: '乙' })!,
      ev('content_delta', { text: '乙说' })!,
      ev('done', {})!,
    ])
    expect(state.terminal).toBe(true)
    expect(state.assistants.map((a) => a.memberId)).toEqual(['m1', 'm2'])
    expect(state.assistants.map((a) => a.content)).toEqual(['甲说', '乙说'])
  })

  it('speaker_skip 标记当前卡跳过、不影响后续 speaker', () => {
    const state = reduceRunAll([
      ev('speaker', { member_id: 'm1', name: '甲' })!,
      ev('speaker_skip', {})!,
      ev('speaker', { member_id: 'm2', name: '乙' })!,
      ev('content_delta', { text: '乙发言' })!,
      ev('done', {})!,
    ])
    expect(state.assistants[0].skipped).toBe(true)
    expect(state.assistants[1].skipped).toBe(false)
  })

  it('群聊每个 done 的 usage 逐份累加', () => {
    const state = reduceRunAll([
      ev('speaker', { member_id: 'm1' })!,
      ev('done', { terminal: false, usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } })!,
      ev('speaker', { member_id: 'm2' })!,
      ev('done', { usage: { prompt_tokens: 20, completion_tokens: 8, total_tokens: 28 } })!,
    ])
    expect(state.usage).toEqual({ prompt_tokens: 30, completion_tokens: 13, total_tokens: 43 })
  })

  it('recall_notice 多组折叠累积', () => {
    const state = reduceRunAll([
      ev('speaker', { name: '甲' })!,
      ev('recall_notice', { items: [{ title: '记忆1' }] })!,
      ev('recall_notice', { items: [] })!,
      ev('recall_notice', { items: [{ title: '记忆2' }] })!,
    ])
    expect(state.assistants[0].recallGroups).toHaveLength(2)
  })
})

describe('reduceRun 错误与迟到帧', () => {
  it('致命 error 立即终态', () => {
    const state = reduceRunAll([
      ev('speaker', { name: '甲' })!,
      ev('error', { message: '炸了', fatal: true })!,
    ])
    expect(state.terminal).toBe(true)
    expect(state.error).toEqual({ message: '炸了', fatal: true })
  })
  it('非致命 error 后仍可接收 done', () => {
    const state = reduceRunAll([
      ev('error', { message: '工具轮数上限', fatal: false })!,
      ev('content_delta', { text: '收尾' })!,
      ev('done', {})!,
    ])
    expect(state.terminal).toBe(true)
    expect(state.assistants[0].content).toBe('收尾')
  })
  it('终态之后的迟到事件被忽略', () => {
    let state = reduceRun(createLiveRun(), ev('done', {})!)
    state = reduceRun(state, ev('content_delta', { text: '迟到' })!)
    expect(state.assistants).toHaveLength(0)
  })
  it('tool_end 跨卡迟到时全量查找工具', () => {
    const state = reduceRunAll([
      ev('speaker', { member_id: 'm1' })!,
      ev('tool_calls', { calls: [{ id: 'c1', name: 't' }] })!,
      ev('speaker', { member_id: 'm2' })!,
      ev('tool_end', { id: 'c1', ok: false })!,
    ])
    expect(state.assistants[0].tools[0].status).toBe('error')
  })
})
